# Cambios aplicados

## Seguridad
- `.gitignore` nuevo (`.env`, `*.db`, `__pycache__`). **Revoca y regenera el `DISCORD_TOKEN` que compartiste.**
- `Requirement.txt` → `requirements.txt` con versiones fijas.
- La ruta de la BD se puede cambiar con `DATABASE_PATH`.

## Base de datos (`database.py`)
- Claves foráneas con `ON DELETE CASCADE` en todas las tablas hijas (se reconstruyen las tablas antiguas conservando los datos).
- Índices únicos: una inscripción por (evento, opción, usuario), una asistencia y una valoración por (evento, usuario). Antes se deduplica lo existente.
- Fechas migradas a ISO-8601 en UTC (el formato anterior con offset local rompía las comparaciones de texto al cambiar el horario de verano).
- Columna `next_created` para no duplicar eventos recurrentes.
- Migración idempotente y transaccional, controlada con `PRAGMA user_version`.
- Se añaden automáticamente las columnas que falten en bases creadas con esquemas antiguos (`thread_id`, `status`, `position`, etc.) y se rellenan los `NULL` antes de aplicar las restricciones.

## Inscripciones (`vistas_eventos.py`, módulo nuevo)
- `EventoView`, el embed y la lógica compartida se extrajeron aquí: se elimina el import circular `main` ↔ `creador_eventos`.
- `defer(ephemeral=True)` + `followup` en todas las interacciones lentas (adiós al "la aplicación no responde").
- Se respetan `allow_waitlist` y `multiple_registrations` (antes se guardaban y se ignoraban).
- `BEGIN IMMEDIATE` al ocupar plaza: dos personas ya no pueden coger la última a la vez.
- Se aplican las restricciones de rol (`evento_restricciones`: permitidos y bloqueados).
- Un evento se marca FINALIZADO en `inicio + duración`, no al empezar.
- Embeds recortados a los límites de Discord y menciones troceadas en bloques < 2000 caracteres.

## Bot (`main.py`)
- `setup_hook` en lugar de `on_ready` (no se reinicializa en cada reconexión).
- El loop de tareas ya no muere: cada evento/recordatorio se aísla y hay `@tareas_eventos.error` que lo reinicia.
- Voz temporal: el canal se crea al empezar y se borra al terminar.
- Recurrencia funcional: al terminar se clona el evento (opciones, menciones, restricciones y recordatorios) y se publica la siguiente ocurrencia.
- `/exportar_evento` filtra por servidor; `/marcar_asistencia` nuevo.

## Asistente (`creador_eventos.py`)
- El paso de ubicación acepta cancelar y pagina los canales (antes solo mostraba 15 y se colgaba).
- Nuevo paso "¿quién puede inscribirse?" que persiste en `evento_restricciones` junto con los roles bloqueados globales.
- Errores del asistente capturados y avisados al usuario (corre en una tarea aparte; antes se perdían).
- Inserción de evento en una sola transacción y en UTC.

## Otros
- Calendario filtrado por servidor, con meses en español y fechas UTC.
- `FeedbackModal` duplicado eliminado; ahora actualiza la valoración existente (`ON CONFLICT`) y hay `/valoraciones`.
- `smoke_test.py`: prueba sin Discord de lista de espera, promoción, inscripción única/múltiple, restricciones de rol y evento finalizado.

## Verificado
- `python -m compileall`, `pyflakes` limpio.
- Migración ejecutada dos veces sobre una copia de tu `eventos.db`: 3 eventos, 7 opciones, 2 inscripciones, 11 recordatorios y 2 valoraciones intactos, `PRAGMA foreign_key_check` sin errores.
- `python smoke_test.py` pasa.
- No se probó contra Discord (haría falta el token y un servidor de pruebas).

## Servidor web opcional (`webserver.py`)
- `keep_alive()` arranca Flask en un hilo daemon y escucha en `PORT` (8000 por defecto), para hosts que exigen un puerto abierto.
- Solo se arranca si `KEEP_ALIVE=1` en el `.env`; `main.py` lo llama al final. `flask` añadido a `requirements.txt`.
