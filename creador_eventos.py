import asyncio
import logging
import re
from typing import List, Optional, Any

import discord
from discord.ext import commands

from database import conectar_db, zona_horaria_defecto, zona_horaria_guardada
from formatters import (
    COLOR_BLANCO, a_utc_iso, ahora, canal_predeterminado_id, formatear_duracion,
    formatear_recordatorios, interpretar_fecha, timestamp_discord, zona_desde_locale,
)
from vistas_eventos import publicar_evento

log = logging.getLogger(__name__)

TIMEOUT_PASO = 180  # Tiempo límite en segundos por cada paso (3 min)
COLORES = {
    "Azul": 0x5865F2, "Morado": 0x9B59B6, "Verde": 0x57F287, "Amarillo": 0xFEE75C,
    "Rojo": 0xED4245, "Naranja": 0xE67E22, "Rosa": 0xEB459E, "Cian": 0x00FFFF,
    "Blanco": 0xFFFFFF, "Negro": 0x000000
}
SESIONES_ACTIVAS = set()


async def ejecutar_creador_lineal(bot: commands.Bot, interaction: discord.Interaction):
    usuario, guild = interaction.user, interaction.guild
    if not guild:
        return
    
    SESIONES_ACTIVAS.add(usuario.id)
    msg_asistente: Optional[discord.Message] = None

    try:
        datos = {
            "publish_channel": None, "title": None, "description": None, "start_time": None,
            "duration_minutes": None, "frequency": "Una vez", "signup_options": [],
            "multiple_registrations": False, "allow_waitlist": True, "mention_roles": [],
            "restricted_roles": [], "color_name": "Blanco", "color": 0xFFFFFF,
            "image_url": None, "location": None, "auto_voice": False, "reminders": [],
            "close_before_minutes": 0, "dm_reminders": True, "max_personas": 0
        }

        async def enviar_paso(titulo: str, descripcion: str, aviso_error: str = None, mostrar_cancelar: bool = True):
            nonlocal msg_asistente
            cuerpo = f"‼ AVISO: {aviso_error} ‼\n\n" + descripcion if aviso_error else descripcion
            embed = discord.Embed(title=titulo, description=cuerpo, color=COLOR_BLANCO)
            if mostrar_cancelar:
                embed.set_footer(text="► Escribe 'cancel' en cualquier momento para cancelar ◄")
            
            # Cada paso envía un embed NUEVO en vez de sustituir el anterior.
            msg_asistente = await usuario.send(embed=embed)
            log.info("Paso '%s' enviado a %s", titulo, usuario.id)
            return msg_asistente

        async def esperar_respuesta():
            def check(m):
                return m.author.id == usuario.id and m.guild is None
            try:
                msg = await bot.wait_for("message", check=check, timeout=TIMEOUT_PASO)
                c = msg.content.strip()
                log.info("DM de %s recibido (%d caracteres): %r", usuario.id, len(c), c[:80])
                return "CANCEL" if c.lower() == "cancel" else c
            except asyncio.TimeoutError:
                log.warning("Sin respuesta de %s tras %ss (timeout)", usuario.id, TIMEOUT_PASO)
                return "TIMEOUT"

        error_actual = None

        # PASO 0: Confirmación
        while True:
            desc = f"► Servidor: **{guild.name}**\n\n► [Y] Sí, iniciar creación\n► [N] No, cancelar\n\n§ Introduce 'Y' o 'N':"
            await enviar_paso("¿DESEAS INICIAR LA CREACIÓN DEL EVENTO?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp == "TIMEOUT": 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Se ha cancelado por inactividad.", mostrar_cancelar=False)
            if resp in ("CANCEL", "N", "NO", "n", "no"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Evento cancelado.", mostrar_cancelar=False)
            if resp.lower() in ("y", "s", "si", "yes"): 
                break
            error_actual = "Introduce 'Y' o 'N'."

        # PASO 1: Canal Destino
        canales = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
        if not canales: 
            return await enviar_paso("SIN CANALES", "‼ No hay canales disponibles donde el bot tenga permisos de envío.", mostrar_cancelar=False)

        canal_defecto = None
        if (cid := canal_predeterminado_id()) and (c := guild.get_channel(cid)):
            if isinstance(c, discord.TextChannel) and c.permissions_for(guild.me).send_messages:
                canal_defecto = c

        pagina, por_pagina = 0, 15
        total_paginas = max(1, (len(canales) + por_pagina - 1) // por_pagina)
        while True:
            canales_pag = canales[pagina * por_pagina : (pagina + 1) * por_pagina]
            lineas = []
            if canal_defecto:
                lineas.append(f"► [0] CANAL PREDETERMINADO → {canal_defecto.mention}")
            lineas += [f"► [{i + 1}] {c.mention}" for i, c in enumerate(canales_pag)]
            desc = "Selecciona el canal de publicación:\n\n" + "\n".join(lineas) + f"\n\n─── Página {pagina + 1} de {total_paginas} ───"
            if pagina < total_paginas - 1: desc += "\n► [S] Siguiente"
            if pagina > 0: desc += "\n◄ [A] Anterior"
            desc += "\n\n§ Introduce el número, 'S' o 'A':"

            await enviar_paso("¿EN QUÉ CANAL DESEAS PUBLICAR EL EVENTO?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            r_up = resp.upper()
            if r_up == "S" and pagina < total_paginas - 1: pagina += 1
            elif r_up == "A" and pagina > 0: pagina -= 1
            elif resp == "0" and canal_defecto:
                datos["publish_channel"] = canal_defecto
                break
            elif resp.isdigit() and 1 <= int(resp) <= len(canales_pag):
                datos["publish_channel"] = canales_pag[int(resp) - 1]
                break
            else: 
                error_actual = "Opción inválida."

        # PASO 2: Título
        while True:
            await enviar_paso("¿CUÁL ES EL TÍTULO DEL EVENTO?", "► Escribe el nombre o título.\n§ Máximo 100 caracteres.", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if 1 <= len(resp) <= 100:
                datos["title"] = resp
                break
            error_actual = "Título inválido (debe tener entre 1 y 100 caracteres)."

        # PASO 3: Descripción
        while True:
            await enviar_paso("¿CUÁL ES LA DESCRIPCIÓN?", "► Escribe la descripción o 'ninguna' (Máx 2000 caracteres).", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            texto = resp.strip()
            if texto.lower() == "ninguna":
                datos["description"] = ""
                break
            if len(texto) > 2000:
                texto = texto[:2000]
            datos["description"] = texto
            break

# PASO 4: Fecha y Hora (lenguaje natural, en la zona horaria del usuario)
        try:
            zona_usuario = (
                zona_horaria_guardada(usuario.id)
                or zona_desde_locale(interaction.locale)
                or zona_horaria_defecto()
            )
        except Exception as e:
            print(f"[ERROR ZONA HORARIA]: {e}")
            zona_usuario = zona_horaria_defecto()

        while True:
            try:
                await enviar_paso(
                    "¿FECHA Y HORA DE INICIO?",
                    f"► Responda en lenguaje natural:\n"
                    f"► 'en 30 min' · 'en 1 hora' · 'en 927 minutos'\n"
                    f"► 'mañana a las 4:00 PM' · 'mañana 17:30'\n"
                    f"► 'viernes a las 17:00' · 'viernes 5:00 pm'\n"
                    f"► '17:30' (hoy; si ya pasó, mañana)\n"
                    f"► '20/08/2026 12:30' (o solo la fecha → a las 20:00)\n"
                    f"§ Tu zona horaria (detectada): **{zona_usuario}**. Usa /zona_horaria si quieres corregirla.",
                    error_actual,
                )
            except Exception as e:
                print(f"[ERROR AL ENVIAR PASO FECHA]: {e}")

            error_actual, resp = None, await esperar_respuesta()

            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            try:
                fecha = interpretar_fecha(resp, zona_usuario)
            except Exception as e:
                print(f"[ERROR EN INTERPRETAR_FECHA]: {e}")
                fecha = None

            if not fecha:
                error_actual = "No he entendido la fecha. Prueba: 'en 1 hora', 'mañana a las 18:00', 'viernes a las 17:00' o 'DD/MM/YYYY HH:MM'."
                continue

            if fecha <= ahora(): 
                error_actual = "La fecha debe ser en el futuro."
                continue

            datos["start_time"] = fecha
            break
        
        # PASO 5: Duración
        while True:
            await enviar_paso("¿DURACIÓN ESTIMADA?", "► Ejemplos: 2h, 90m, 2h 30m", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            cnt = resp.lower()
            h, m = re.search(r"(\d+)\s*h", cnt), re.search(r"(\d+)\s*m", cnt)
            tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
            if tot > 0: 
                datos["duration_minutes"] = tot
                break
            error_actual = "Duración no válida. Especifica horas (h) o minutos (m)."

        # PASO 6: Frecuencia
        frecuencias = ["Una vez", "Diariamente", "Semanalmente", "Mensualmente"]
        lista_frec = "\n".join(f"► [{i+1}] {f}" for i, f in enumerate(frecuencias))
        while True:
            await enviar_paso("¿FRECUENCIA DE REPETICIÓN?", f"{lista_frec}\n\n§ Introduce un número:", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.isdigit() and 1 <= int(resp) <= len(frecuencias):
                datos["frequency"] = frecuencias[int(resp) - 1]
                break
            error_actual = "Selecciona un número entre 1 y 4."

        # PASO 7: Opciones de inscripción predeterminadas
        datos["signup_options"] = [
            {"name": "[√] Acepto", "max_slots": None},
            {"name": "[X] Rechazo", "max_slots": None},
            {"name": "[?] Indeciso", "max_slots": None}
        ]

        # PASO 8: Lista de Espera
        while True:
            desc = "► [1] Sí (Habilitar lista de espera cuando se me llenen las plazas)\n► [2] No (Rechazar inscripciones cuando se llene)\n\n§ Introduce 1, 2, 'sí' o 'no':"
            await enviar_paso("¿PERMITIR LISTA DE ESPERA (RESERVA)?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            r = resp.strip().lower()
            if r in ("1", "sí", "si", "s", "y", "yes"):
                datos["allow_waitlist"] = True
                break
            if r in ("2", "no", "n"):
                datos["allow_waitlist"] = False
                break
            error_actual = "Introduce 1 o 2."

        # PASO 9: Inscripciones Múltiples (1 = Sí, 2 = No)
        while True:
            desc = "► [1] Sí (Permitir inscribirse varias veces)\n► [2] No (Una sola inscripción por persona)\n\n§ Introduce 1, 2, 'sí' o 'no':"
            await enviar_paso("¿PERMITIR INSCRIPCIONES MÚLTIPLES?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            r = resp.strip().lower()
            if r in ("1", "sí", "si", "s", "y", "yes"):
                datos["multiple_registrations"] = True
                break
            if r in ("2", "no", "n"):
                datos["multiple_registrations"] = False
                break
            error_actual = "Introduce 1 o 2."

        # PASO 9B: Límite de personas (cupo máximo que acepta la misión)
        while True:
            desc = "► Máximo de personas que pueden **ACEPTAR** la misión.\n► Escribe 'ninguno' para sin límite.\n\n§ Introduce un número:"
            await enviar_paso("¿CUÁL ES EL LÍMITE DE PERSONAS?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.strip().lower() in ("ninguno", "no", "none", "infinito", "∞", "0"):
                datos["signup_options"][0]["max_slots"] = None
                datos["max_personas"] = 0
                break
            if resp.strip().isdigit() and int(resp.strip()) > 0:
                cupo = int(resp.strip())
                datos["signup_options"][0]["max_slots"] = cupo
                datos["max_personas"] = cupo
                break
            error_actual = "Introduce un número mayor que 0 o 'ninguno'."

        # PASO 10: Menciones
        conn = conectar_db()
        filas_m = conn.execute("SELECT role_id FROM roles_mencionables WHERE guild_id = ?", (guild.id,)).fetchall()
        conn.close()
        
        ids_permitidos = {int(f["role_id"]) for f in filas_m} if filas_m else set()
        roles = [guild.get_role(rid) for rid in ids_permitidos if guild.get_role(rid)] if ids_permitidos else guild.roles
        roles = [r for r in roles if r and not r.is_default() and not r.managed]

        pagina_r, total_pag_r = 0, max(1, (len(roles) + por_pagina - 1) // por_pagina)
        while True:
            roles_pag = roles[pagina_r * por_pagina : (pagina_r + 1) * por_pagina]
            lineas = ["► [0] Ninguna mención"] + [f"► [{i}] @{r.name}" for i, r in enumerate(roles_pag, start=1)]
            desc = "Selecciona roles a mencionar:\n\n" + "\n".join(lineas)
            if total_pag_r > 1:
                desc += f"\n\n─── Página {pagina_r + 1} de {total_pag_r} ───"
                if pagina_r < total_pag_r - 1: desc += "\n► [S] Siguiente"
                if pagina_r > 0: desc += "\n◄ [A] Anterior"
            desc += "\n\n§ Introduce '0' o números separados por comas (ej: 1,3):"

            await enviar_paso("¿ROLES A MENCIONAR?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            r_up = resp.upper()
            if r_up == "S" and pagina_r < total_pag_r - 1: pagina_r += 1
            elif r_up == "A" and pagina_r > 0: pagina_r -= 1
            elif resp == "0": 
                datos["mention_roles"] = []
                break
            else:
                partes = [p.strip() for p in resp.split(",") if p.strip().isdigit()]
                seleccionados, valido = [], True
                for p in partes:
                    idx = int(p) - 1
                    if 0 <= idx < len(roles_pag): 
                        seleccionados.append(roles_pag[idx])
                    else: 
                        valido = False
                        break
                if valido and seleccionados:
                    datos["mention_roles"] = seleccionados
                    break
                error_actual = "Opción no válida."

        # PASO 10B: Restricción de acceso
        roles_acceso = [r for r in guild.roles if not r.is_default() and not r.managed]
        pagina_a, total_pag_a = 0, max(1, (len(roles_acceso) + por_pagina - 1) // por_pagina)
        while True:
            roles_a_pag = roles_acceso[pagina_a * por_pagina : (pagina_a + 1) * por_pagina]
            lineas = ["► [0] Abierto a todo el servidor"] + [f"► [{i}] @{r.name}" for i, r in enumerate(roles_a_pag, start=1)]
            desc = "Solo estos roles podrán inscribirse:\n\n" + "\n".join(lineas)
            if total_pag_a > 1:
                desc += f"\n\n─── Página {pagina_a + 1} de {total_pag_a} ───"
                if pagina_a < total_pag_a - 1: desc += "\n► [S] Siguiente"
                if pagina_a > 0: desc += "\n◄ [A] Anterior"
            desc += "\n\n§ Introduce '0' o números separados por comas:"

            await enviar_paso("¿QUIÉN PUEDE INSCRIBIRSE?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            r_up = resp.upper()
            if r_up == "S" and pagina_a < total_pag_a - 1: pagina_a += 1
            elif r_up == "A" and pagina_a > 0: pagina_a -= 1
            elif resp == "0": 
                datos["restricted_roles"] = []
                break
            else:
                partes = [p.strip() for p in resp.split(",") if p.strip().isdigit()]
                seleccionados, valido = [], True
                for p in partes:
                    idx = int(p) - 1
                    if 0 <= idx < len(roles_a_pag): 
                        seleccionados.append(roles_a_pag[idx])
                    else: 
                        valido = False
                        break
                if valido and seleccionados:
                    datos["restricted_roles"] = seleccionados
                    break
                error_actual = "Opción no válida."

        # PASO 11: Color
        colores_lista = list(COLORES.keys())
        texto_colores = "\n".join(f"► [{i+1}] {c}" for i, c in enumerate(colores_lista))
        while True:
            await enviar_paso("¿COLOR DEL ANUNCIO?", f"{texto_colores}\n\n§ Introduce el número:", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.isdigit() and 1 <= int(resp) <= len(colores_lista):
                datos["color_name"] = colores_lista[int(resp) - 1]
                datos["color"] = COLORES[datos["color_name"]]
                break
            error_actual = "Número inválido."

        # PASO 12: Imagen
        regex_url = re.compile(r"^https?://\S+\.(?:png|jpg|jpeg|gif|webp)(?:\?\S*)?$", re.IGNORECASE)
        while True:
            await enviar_paso("¿IMAGEN O BANNER?", "► Introduce la URL directa a una imagen (jpg, png, gif) o escribe 'ninguna'.", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.lower() == "ninguna": 
                datos["image_url"] = None
                break
            elif regex_url.match(resp) or resp.startswith(("http://", "https://")):
                datos["image_url"] = resp
                break
            error_actual = "URL no válida. Asegúrate de que empiece por http:// o https://"

        # PASO 13: Ubicación
        while True:
            desc = "► [1] Seleccionar canal existente del servidor\n► [2] Crear canal de voz automático al iniciar el evento\n► [3] Sin ubicación específica\n\n§ Introduce 1, 2 o 3:"
            await enviar_paso("¿UBICACIÓN O SALA DEL EVENTO?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp == "3":
                datos["location"], datos["auto_voice"] = None, False
                break
            elif resp == "2":
                datos["location"], datos["auto_voice"] = None, True
                break
            elif resp == "1":
                canales_ub = [c for c in guild.channels if isinstance(c, (discord.TextChannel, discord.VoiceChannel))]
                pagina_u, total_pag_u = 0, max(1, (len(canales_ub) + por_pagina - 1) // por_pagina)
                seleccionado = None
                while seleccionado is None:
                    canales_u_pag = canales_ub[pagina_u * por_pagina : (pagina_u + 1) * por_pagina]
                    desc_u = "\n".join(f"► [{i+1}] {c.mention}" for i, c in enumerate(canales_u_pag))
                    desc_u += f"\n\n─── Página {pagina_u + 1} de {total_pag_u} ───"
                    if pagina_u < total_pag_u - 1: desc_u += "\n► [S] Siguiente"
                    if pagina_u > 0: desc_u += "\n◄ [A] Anterior"
                    desc_u += "\n► [V] Volver"

                    await enviar_paso("SELECCIONA CANAL DE UBICACIÓN", desc_u, error_actual)
                    error_actual, r_chan = None, await esperar_respuesta()
                    if r_chan in ("TIMEOUT", "CANCEL"):
                        return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

                    r_up_chan = r_chan.upper()
                    if r_up_chan == "V": 
                        break
                    elif r_up_chan == "S" and pagina_u < total_pag_u - 1: pagina_u += 1
                    elif r_up_chan == "A" and pagina_u > 0: pagina_u -= 1
                    elif r_chan.isdigit() and 1 <= int(r_chan) <= len(canales_u_pag):
                        seleccionado = canales_u_pag[int(r_chan) - 1]
                    else: 
                        error_actual = "Canal no válido."

                if seleccionado:
                    datos["location"] = seleccionado
                    datos["auto_voice"] = False
                    break
            else: 
                error_actual = "Introduce 1, 2 o 3."

        # PASO 14: Recordatorios
        while True:
            await enviar_paso("¿RECORDATORIOS PREVIOS?", "► Ejemplos: 24h, 1h, 30m o 'ninguno'. Separa por comas si son varios.", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.lower() == "ninguno": 
                datos["reminders"] = []
                break
            recs = []
            for parte in resp.split(","):
                m = re.match(r"(\d+)\s*(m|min|h|hora|horas|d|dia|dias)", parte.strip().lower())
                if m:
                    cant, uni = int(m.group(1)), m.group(2)
                    mins = cant if uni.startswith("m") else (cant * 60 if uni.startswith("h") else cant * 1440)
                    if mins > 0: recs.append(mins)
            if recs: 
                datos["reminders"] = sorted(set(recs), reverse=True)
                break
            error_actual = "Formato de recordatorios inválido. Ejemplo: '1h, 30m'"

        # PASO 14B: Cierre automático de inscripciones directas
        while True:
            desc = ("► Cuánto antes de la hora de inicio se cierran las inscripciones DIRECTAS.\n"
                    "► La lista de espera sigue activa y promueve aunque esté cerrado.\n\n"
                    "► Ejemplos: 30m, 1h, 2h 30m\n"
                    "► Escribe 'ninguno' para no cerrar inscripciones (todas abiertas).")
            await enviar_paso("¿CIERRE AUTOMÁTICO DE INSCRIPCIONES?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.lower() in ("ninguno", "no", "none", "0"):
                datos["close_before_minutes"] = 0
                break
            cnt = resp.lower()
            h = re.search(r"(\d+)\s*h", cnt)
            m = re.search(r"(\d+)\s*m", cnt)
            tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
            if tot > 0:
                datos["close_before_minutes"] = tot
                break
            error_actual = "Formato inválido. Ejemplo: '30m', '1h', '1h 30m'"

        # PASO 14C: Recordatorios privados por DM
        while True:
            desc = "► [1] Sí (Enviar DM a cada confirmado antes del inicio)\n► [2] No (Solo avisos en el hilo)\n\n§ Introduce 1 o 2:"
            await enviar_paso("¿RECORDATORIOS PRIVADOS POR DM?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp in ("1", "2"):
                datos["dm_reminders"] = (resp == "1")
                break
            error_actual = "Introduce 1 o 2."

        # PASO 15: Resumen y Publicación
        resumen = [
            f"► Organizador: {usuario.mention}",
            f"► Título: **{datos['title']}**",
            f"► Inicio: <t:{timestamp_discord(datos['start_time'])}:F>",
            f"► Duración: {formatear_duracion(datos['duration_minutes'])}",
            f"► Frecuencia: {datos['frequency']}",
            f"► Canal de Anuncio: {datos['publish_channel'].mention}",
            f"► Lista de Espera: {'Habilitada' if datos['allow_waitlist'] else 'Deshabilitada'}",
            f"► Límite de Personas: {datos['max_personas'] if datos['max_personas'] else 'Sin límite'}",
            f"► Acceso Restringido: {', '.join('@' + r.name for r in datos['restricted_roles']) or 'Todo el servidor'}",
            f"► Voz Automática: {'Sí' if datos['auto_voice'] else 'No'}",
            f"► Recordatorios: {formatear_recordatorios(datos['reminders'])}",
            f"► Cierre de Inscripciones: {formatear_duracion(datos['close_before_minutes']) if datos['close_before_minutes'] else 'Ninguno'} antes del inicio",
            f"► Recordatorios por DM: {'Sí' if datos['dm_reminders'] else 'No'}"
        ]
        desc_final = "╔════════════════════════════════════════╗\n          RESUMEN DEL EVENTO\n╚════════════════════════════════════════╝\n\n" + "\n".join(resumen) + "\n\n► [1] Publicar Evento\n► [2] Cancelar\n\n§ Introduce 1 o 2:"

        while True:
            await enviar_paso("¿DESEAS PUBLICAR EL EVENTO?", desc_final, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("2", "CANCEL", "TIMEOUT"): 
                return await enviar_paso("CREACIÓN CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp == "1": 
                break
            error_actual = "Introduce 1 o 2."

        # Verificación final de que el canal de destino aún existe en Discord
        canal = datos["publish_channel"]
        if not canal or not guild.get_channel(canal.id):
            return await enviar_paso("ERROR AL PUBLICAR", "‼ El canal seleccionado ya no existe en el servidor.", mostrar_cancelar=False)

        loc_id = datos["location"].id if datos["location"] else None

# Publicación en Base de Datos
        conn = conectar_db()
        try:
            cursor = conn.execute("""
                INSERT INTO eventos (guild_id, channel_id, creator_id, title, description, start_time,
                                     duration_minutes, frequency, color, location_channel_id, auto_voice,
                                     image_url, multiple_registrations, allow_waitlist, created_at,
                                     close_before_minutes, dm_reminders)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                guild.id, canal.id, usuario.id, datos["title"], datos["description"],
                a_utc_iso(datos["start_time"]), datos["duration_minutes"], datos["frequency"],
                datos["color"], loc_id, 1 if datos["auto_voice"] else 0, datos["image_url"],
                1 if datos["multiple_registrations"] else 0, 1 if datos["allow_waitlist"] else 0,
                a_utc_iso(ahora()), datos["close_before_minutes"], 1 if datos["dm_reminders"] else 0,
            ))
            evento_id = cursor.lastrowid

            conn.executemany(
                "INSERT INTO opciones_inscripcion (event_id, name, emoji, max_slots) VALUES (?, ?, ?, ?)",
                [(evento_id, o["name"], "", o["max_slots"]) for o in datos["signup_options"]],
            )
            conn.executemany(
                "INSERT INTO evento_menciones (event_id, role_id) VALUES (?, ?)",
                [(evento_id, r.id) for r in datos["mention_roles"]],
            )
            conn.executemany(
                "INSERT INTO recordatorios (event_id, minutes_before) VALUES (?, ?)",
                [(evento_id, m) for m in datos["reminders"]],
            )

            bloqueados = conn.execute("SELECT role_id FROM roles_bloqueados WHERE guild_id = ?", (guild.id,)).fetchall()
            conn.executemany(
                "INSERT INTO evento_restricciones (event_id, role_id, tipo) VALUES (?, ?, ?)",
                [(evento_id, r.id, "permitido") for r in datos["restricted_roles"]]
                + [(evento_id, f["role_id"], "bloqueado") for f in bloqueados],
            )
            conn.commit()
        finally:
            conn.close()

        # Publicar evento visualmente en el canal
        menciones_str = " ".join(r.mention for r in datos["mention_roles"]) or None
        _, hilo = await publicar_evento(evento_id, canal, menciones_str)
        if hilo:
            await hilo.send(
                f"{ICON_BULLET} **Hilo del evento iniciado.** Canal oficial para recordatorios y avisos "
                f"del evento de {usuario.mention}."
            )

        destino = f" con su respectivo hilo en {hilo.mention}" if hilo else ""
        await enviar_paso(
            "EVENTO PUBLICADO CON ÉXITO",
            f"► Evento #{evento_id} creado en {canal.mention}{destino}.",
            mostrar_cancelar=False,
        )

    except discord.Forbidden:
        log.warning("No se pudo continuar el asistente con %s: DMs cerrados", usuario.id)
        await avisar_fallo(interaction, "‼ No puedo escribirte por privado. Activa los MD del servidor y reintenta.")
    except Exception:
        log.exception("Falló el asistente de creación de eventos de %s", usuario.id)
        await avisar_fallo(interaction, "‼ El asistente falló por un error interno. Vuelve a intentarlo.")
    finally:
        SESIONES_ACTIVAS.discard(usuario.id)


async def avisar_fallo(interaction: discord.Interaction, mensaje: str):
    """Avisa en la interacción si el asistente falla o no se pueden mandar DMs."""
    try:
        await interaction.followup.send(mensaje, ephemeral=True)
    except discord.HTTPException:
        log.warning("Tampoco se pudo avisar del fallo a %s", interaction.user.id)


def configurar_creador_eventos(bot: commands.Bot):
    @bot.tree.command(name="crear_evento", description="Inicia el asistente para crear un nuevo evento.")
    @discord.app_commands.guild_only()
    async def cmd_crear_evento(interaction: discord.Interaction):
        if interaction.user.id in SESIONES_ACTIVAS:
            return await interaction.response.send_message(
                "‼ Ya tienes una sesión activa en tus mensajes directos.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("► Asistente enviado por mensaje privado (DM).", ephemeral=True)
        asyncio.create_task(ejecutar_creador_lineal(bot, interaction))