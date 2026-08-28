# Revisión del bot de eventos (Discord)

Archivos revisados: `main.py`, `creador_eventos.py`, `modulos_eventos.py`, `modulo_calendario.py`,
`modulo_valoraciones.py`, `database.py`, `formatters.py`, `Requirement.txt`, `.env`, `eventos.db`.
Todo compila (`py_compile` OK). No lo ejecuté contra Discord (haría falta un token válido y un servidor de pruebas).

---

## 0. URGENTE — seguridad

1. **Token de Discord expuesto.** El `.env` que compartiste incluye `DISCORD_TOKEN` en claro.
   Revocá/regenerá el token ya en el Developer Portal (Bot → Reset Token) y no vuelvas a compartir ese archivo.
   Agregá `.env` y `eventos.db` a un `.gitignore`.
2. **IDs de rol hardcodeados como default** en `main.py` (`ROL_NUEVO`, `ROL_VERIFICADO`): deberían venir
   solo del `.env`, sin valor por defecto de un servidor real.

---

## 1. Bugs funcionales (el bot no hace lo que promete)

### 1.1 Código muerto: funcionalidad anunciada que nunca se ejecuta
| Elemento | Estado |
|---|---|
| `gestionar_voz_temporal` (modulos_eventos.py:110) | importada en `main.py`, **nunca se llama** → el canal de voz automático nunca se crea, aunque el embed dice "Canal de voz temporal (Automatico)" |
| `calcular_siguiente_ocurrencia` (main.py:36) | **nunca se llama** → los eventos "Diariamente / Semanalmente / Mensualmente" nunca se repiten |
| `publicar_o_actualizar_mensaje_evento` (main.py:119) | **nunca se llama** (el creador publica por su cuenta) |
| `cargar_roles` / `ROLES_BLOQUEADOS` | se cargan y no se usan nunca → las restricciones de rol no se aplican |
| tabla `evento_restricciones` | se crea, nunca se escribe ni se lee (`restricted_roles` del creador se descarta) |
| tabla `asistencia` | solo se lee en el CSV; no hay forma de marcar asistencia |
| `generar_enlace_google_calendar` | nunca se usa |
| `registrar_comandos_valoraciones` | es un `pass` |
| Botón "✏️ Editar Evento" | responde "aún no está disponible" y lo ve todo el mundo |

### 1.2 Opciones del asistente que se guardan pero se ignoran
- **`allow_waitlist`**: `inscribirse` (main.py:161) siempre manda a lista de espera al llenarse la opción,
  aunque el creador haya elegido "No". Falta leer `evento["allow_waitlist"]` y rechazar la inscripción.
- **`multiple_registrations`**: la única validación (main.py:155) impide repetir *la misma* opción; un usuario
  siempre puede apuntarse a varias opciones aunque se haya configurado "Una sola inscripción por usuario".

### 1.3 Un evento se marca "FINALIZADO" al empezar
`crear_embed_publicado` (main.py:81) e `inscribirse`/`cancelar_inscripcion` usan `ahora() >= inicio`.
Debería ser `inicio + timedelta(minutes=duration_minutes)`. Hoy, en cuanto arranca el evento, se cierra
todo (inscripciones, cancelaciones) y el embed dice finalizado.

### 1.4 El calendario compara fechas como texto y mezcla servidores
`modulo_calendario.py:32` filtra `WHERE start_time >= ? AND start_time <= ?` sobre strings ISO **con offset**.
Entre invierno y verano el offset cambia (`+01:00` / `+02:00`), así que la comparación lexicográfica falla en
los bordes del mes. Guardá timestamps UTC (o epoch) y comparalos. Además **falta `AND guild_id = ?`**: el
calendario muestra eventos de todos los servidores donde esté el bot (mismo problema en varias queries).

### 1.5 Nombres de mes en inglés
`calendar.month_name[self.mes]` depende del locale del sistema → normalmente "August", no "Agosto".
Usá una lista propia de nombres en español.

### 1.6 El loop de recordatorios puede morir para siempre
`tareas_eventos` (main.py:265) no tiene `try/except` alrededor del envío. Cualquier excepción
(mensaje >2000 caracteres por acumular menciones, hilo archivado, permisos, 429) **detiene el `tasks.loop`**
y no se envía ningún recordatorio más hasta reiniciar. Envolvé el cuerpo del `for` en `try/except Exception`
y agregá `@tareas_eventos.error`. Además, trocead las menciones en bloques (<2000 chars).

### 1.7 Interacciones sin `defer` → "La aplicación no respondió"
En `inscribirse` (main.py:142) se hacen: 5 queries + posible `create_thread` + `hilo.send` **antes** de
`interaction.response.send_message`. Discord da 3 segundos. Con crear hilo de por medio se pasa fácil.
Solución: `await interaction.response.defer(ephemeral=True)` al entrar y `followup.send` al final.
Peor aún: si `hilo.send` lanza excepción, la inscripción ya se guardó pero el usuario ve error y nunca
recibe respuesta (no hay `try/except`). Mismo patrón en `cancelar_inscripcion`.

### 1.8 Carrera al ocupar la última plaza
`inscribirse` cuenta confirmados y luego inserta, sin transacción exclusiva ni índice único.
Dos clics simultáneos pueden superar `max_slots`. Falta:
```sql
CREATE UNIQUE INDEX ux_inscripciones ON inscripciones(event_id, option_id, user_id);
```
y hacer el conteo+insert dentro de `BEGIN IMMEDIATE`.

### 1.9 Límites de embed de Discord no controlados
`crear_embed_publicado` agrega un field por opción sin límite: >25 fields, o una lista de participantes
que supere 1024 caracteres (≈40 menciones), lanzan `HTTPException` y el mensaje deja de actualizarse.
Truncá el cuerpo y paginá/limitá las opciones.

### 1.10 Paso 13 del asistente (ubicación) no acepta cancelar
`creador_eventos.py:270-276`: la respuesta al submenú de canales no comprueba `CANCEL`/`TIMEOUT`,
así que escribir `cancel` cae en "Canal no valido" y vuelve a preguntar. Además solo muestra los
primeros 15 canales sin paginación (el paso 1 sí pagina).

### 1.11 El asistente puede romperse en silencio
`ejecutar_creador_lineal` corre en `asyncio.create_task` sin `try/except` global: si el usuario tiene los
DMs cerrados después del paso 0, o falla la publicación, la excepción se pierde en el task y el usuario
no recibe nada. Envolvé todo en `try/except Exception` y avisá por el canal original.

### 1.12 `on_ready` se ejecuta en cada reconexión
`bot.tree.sync()` y el re-registro de vistas están en `on_ready` (main.py:310), que se dispara también en
cada reconexión → sincronizaciones repetidas del árbol de comandos (rate limit) y vistas duplicadas.
Movelo a `setup_hook()` (o protegé con un flag `self._listo`).

---

## 2. Base de datos

- **Sin claves foráneas reales.** `PRAGMA foreign_keys=ON` no sirve de nada porque ninguna tabla hija declara
  `REFERENCES eventos(id) ON DELETE CASCADE`. Al borrar un evento quedan huérfanos en `opciones_inscripcion`,
  `inscripciones`, `recordatorios`, `feedback`.
- **Sin unicidad en `feedback`** (`event_id`,`user_id`): un usuario puede votar N veces, y como
  `generar_csv_evento` hace `LEFT JOIN feedback` + `LEFT JOIN asistencia`, **las filas del CSV se multiplican**
  (producto cartesiano) si hay más de un registro por usuario.
- **Faltan índices** por `option_id`+`status` (los usa el conteo de plazas) y por `eventos(guild_id, start_time)`.
- **SQLite síncrono dentro del event loop.** Todas las llamadas son bloqueantes; con `timeout=30` un lock
  puede congelar el bot entero 30 s. Envolvé en `asyncio.to_thread` (o pasá a `aiosqlite`).
- **Conexiones abiertas a través de `await`**: en `inscribirse` el `finally: conn.close()` cubre llamadas de red;
  y `promover_lista_espera` abre una *segunda* conexión mientras la primera sigue abierta → riesgo de
  `database is locked`. Cerrá la conexión antes de hacer I/O de red.
- Estado actual de `eventos.db`: 3 eventos, 7 opciones, 2 inscripciones, 11 recordatorios, 2 feedback,
  0 asistencias, 0 roles configurados.

---

## 3. Estructura y mantenimiento

- **`FeedbackModal` está duplicado** e idéntico en `modulos_eventos.py:85` y `modulo_valoraciones.py:5`.
  `main.py` importa el de valoraciones; el otro es una bomba de desincronización. Dejá uno solo.
- **Import circular disimulado**: `creador_eventos.py:333` hace `import main` dentro de la función porque
  `main` ya importa `creador_eventos`. Funciona, pero es frágil. Movelo a un módulo `vistas.py` /
  `embeds.py` compartido, o pasá `crear_embed_publicado`/`EventoView` como parámetros a
  `configurar_creador_eventos`.
- **Imports sin usar** en `main.py`: `sqlite3`, `parsear_fecha`, `gestionar_voz_temporal`.
- **`Requirement.txt`**: renombralo a `requirements.txt` y **fijá versiones** (`discord.py==2.x.y`, etc.);
  sin pin, un cambio mayor de discord.py rompe el bot en el siguiente deploy.
- Falta cualquier logging estructurado (`logging` en vez de `print`), tests y `.gitignore`.
- Estilo: muchas líneas con `;` y sentencias múltiples (`main.py:19`, `creador_eventos.py:99`) — difícil de
  leer y de depurar; conviene un formateo estándar (black/ruff).

---

## 4. Orden sugerido para arreglarlo

1. Rotar el token y sacar `.env`/`eventos.db` del control de versiones.
2. `defer()` + `try/except` en las interacciones (1.7) y `try/except` en el loop de recordatorios (1.6).
3. Respetar `allow_waitlist` y `multiple_registrations` (1.2) y arreglar el "finalizado" con duración (1.3).
4. Migración de BD: FKs con `ON DELETE CASCADE`, índices únicos, guardar fechas en UTC (1.4, sección 2).
5. Filtrar por `guild_id` en todas las queries (1.4).
6. Conectar lo que está muerto: voz automática, recurrencia, restricciones de rol, asistencia (1.1) —
   o quitarlo del asistente y de los embeds para no prometer lo que no hay.
7. Deduplicar `FeedbackModal`, romper el ciclo `main` ↔ `creador_eventos`, pinear dependencias (sección 3).
