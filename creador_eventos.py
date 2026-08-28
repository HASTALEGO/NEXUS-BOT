import asyncio
import logging
import re

import discord
from discord.ext import commands

from database import conectar_db
from formatters import (
    COLOR_BLANCO, a_utc_iso, ahora, formatear_duracion, formatear_recordatorios,
    parsear_fecha, timestamp_discord,
)
from vistas_eventos import publicar_evento

log = logging.getLogger(__name__)

TIMEOUT_PASO = 600
COLORES = {
    "Azul": 0x5865F2, "Morado": 0x9B59B6, "Verde": 0x57F287, "Amarillo": 0xFEE75C,
    "Rojo": 0xED4245, "Naranja": 0xE67E22, "Rosa": 0xEB459E, "Cian": 0x00FFFF,
    "Blanco": 0xFFFFFF, "Negro": 0x000000
}
SESIONES_ACTIVAS = set()

async def ejecutar_creador_lineal(bot: commands.Bot, interaction: discord.Interaction):
    usuario, guild = interaction.user, interaction.guild
    if not guild: return
    SESIONES_ACTIVAS.add(usuario.id)

    try:
        datos = {
            "publish_channel": None, "title": None, "description": None, "start_time": None,
            "duration_minutes": None, "frequency": "Una vez", "signup_options": [],
            "multiple_registrations": False, "allow_waitlist": True, "mention_roles": [],
            "restricted_roles": [], "color_name": "Blanco", "color": 0xFFFFFF,
            "image_url": None, "location": None, "auto_voice": False, "reminders": []
        }

        async def enviar_paso(titulo: str, descripcion: str, aviso_error: str = None, mostrar_cancelar: bool = True):
            cuerpo = f"‼ AVISO: {aviso_error} ‼\n\n" + descripcion if aviso_error else descripcion
            embed = discord.Embed(title=titulo, description=cuerpo, color=COLOR_BLANCO)
            if mostrar_cancelar: embed.set_footer(text="► Escribe 'cancel' en cualquier momento para cancelar ◄")
            return await usuario.send(embed=embed)

        async def esperar_respuesta():
            def check(msg): return msg.author.id == usuario.id and isinstance(msg.channel, discord.DMChannel)
            try:
                msg = await bot.wait_for("message", check=check, timeout=TIMEOUT_PASO)
                c = msg.content.strip()
                return "CANCEL" if c.lower() == "cancel" else c
            except asyncio.TimeoutError: return "TIMEOUT"

        error_actual = None

        # PASO 0: Confirmacion
        try:
            while True:
                desc = f"► Servidor: **{guild.name}**\n\n► [Y] Si, iniciar creacion\n► [N] No, cancelar\n\n§ Introduce 'Y' o 'N':"
                await enviar_paso("¿DESEAS INICIAR LA CREACION DEL EVENTO?", desc, error_actual)
                error_actual, resp = None, await esperar_respuesta()
                if resp == "TIMEOUT": return await enviar_paso("CREACION CANCELADA", "‼ Se ha cancelado por inactividad.", mostrar_cancelar=False)
                if resp in ("CANCEL", "N", "NO", "n", "no"): return await enviar_paso("CREACION CANCELADA", "‼ Evento cancelado.", mostrar_cancelar=False)
                if resp.lower() in ("y", "s", "si", "yes"): break
                error_actual = "Introduce 'Y' o 'N'."
        except discord.Forbidden: return

        # PASO 1: Canal Destino
        canales = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
        if not canales: return await enviar_paso("SIN CANALES", "‼ No hay canales disponibles.", mostrar_cancelar=False)

        pagina, por_pagina = 0, 15
        total_paginas = max(1, (len(canales) + por_pagina - 1) // por_pagina)
        while True:
            canales_pag = canales[pagina * por_pagina : (pagina + 1) * por_pagina]
            lineas = [f"► [{i + 1}] {c.mention}" for i, c in enumerate(canales_pag)]
            desc = "Selecciona el canal de publicacion:\n\n" + "\n".join(lineas) + f"\n\n─── Pagina {pagina + 1} de {total_paginas} ───"
            if pagina < total_paginas - 1: desc += "\n► [S] Siguiente"
            if pagina > 0: desc += "\n◄ [A] Anterior"
            desc += "\n\n§ Introduce el numero, 'S' o 'A':"

            await enviar_paso("¿EN QUE CANAL DESEAS PUBLICAR EL EVENTO?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp == "TIMEOUT" or resp == "CANCEL": return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            r_up = resp.upper()
            if r_up == "S" and pagina < total_paginas - 1: pagina += 1
            elif r_up == "A" and pagina > 0: pagina -= 1
            elif resp.isdigit() and 1 <= int(resp) <= len(canales_pag):
                datos["publish_channel"] = canales_pag[int(resp) - 1]
                break
            else: error_actual = "Opcion invalida."

        # PASO 2: Titulo
        while True:
            await enviar_paso("¿CUAL ES EL TITULO DEL EVENTO?", "► Escribe el nombre o titulo.\n§ Maximo 100 caracteres.", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if 1 <= len(resp) <= 100:
                datos["title"] = resp
                break
            error_actual = "Titulo invalido (1-100 caracteres)."

        # PASO 3: Descripcion
        while True:
            await enviar_paso("¿CUAL ES LA DESCRIPCION?", "► Escribe la descripcion o 'ninguna' (Max 2000 caracteres).", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.lower() == "ninguna": datos["description"] = ""; break
            elif len(resp) <= 2000: datos["description"] = resp; break
            error_actual = "Descripcion demasiado larga."

        # PASO 4: Fecha y Hora
        while True:
            await enviar_paso("¿FECHA Y HORA DE INICIO?", "► Formato: DD/MM/YYYY HH:MM\n§ Ejemplo: 30/08/2026 20:30", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            fecha = parsear_fecha(resp)
            if not fecha: error_actual = "Formato incorrecto. Usa DD/MM/YYYY HH:MM"; continue
            if fecha <= ahora(): error_actual = "La fecha debe ser en el futuro."; continue
            datos["start_time"] = fecha
            break

        # PASO 5: Duracion
        while True:
            await enviar_paso("¿DURACION ESTIMADA?", "► Ejemplos: 2h, 90m, 2h 30m", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            cnt = resp.lower()
            h, m = re.search(r"(\d+)\s*h", cnt), re.search(r"(\d+)\s*m", cnt)
            tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
            if tot > 0: datos["duration_minutes"] = tot; break
            error_actual = "Duracion no valida."

        # PASO 6: Frecuencia
        frecuencias = ["Una vez", "Diariamente", "Semanalmente", "Mensualmente"]
        lista_frec = "\n".join(f"► [{i+1}] {f}" for i, f in enumerate(frecuencias))
        while True:
            await enviar_paso("¿FRECUENCIA DE REPETICION?", f"{lista_frec}\n\n§ Introduce un numero:", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.isdigit() and 1 <= int(resp) <= len(frecuencias):
                datos["frequency"] = frecuencias[int(resp) - 1]
                break
            error_actual = "Selecciona un numero entre 1 y 4."

        # PASO 7: Inscripciones
        while True:
            desc = "► [1] Opcion unica ilimitada\n► [2] Crear roles/opciones con o sin limite de plazas\n► [3] Sin inscripciones (anuncio informativo)\n\n§ Introduce 1, 2 o 3:"
            await enviar_paso("¿TIPO DE INSCRIPCIONES?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp == "1":
                datos["signup_options"] = [{"name": "Participantes", "max_slots": None}]
                break
            elif resp == "3":
                datos["signup_options"] = []
                break
            elif resp == "2":
                while True:
                    await enviar_paso("¿QUE OPCIONES DESEAS ANADIR?", "► Separa por comas. Ej: Tanque(2), DPS(5), Sanador(ilimitado)", error_actual)
                    error_actual, resp_sub = None, await esperar_respuesta()
                    if resp_sub in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
                    opciones = []
                    for parte in resp_sub.split(","):
                        p = parte.strip()
                        if not p: continue
                        m = re.match(r"^(.*?)\s*\(\s*(\d+|ilimitado)\s*\)$", p, re.IGNORECASE)
                        if m:
                            nom, plz = m.group(1).strip(), m.group(2).lower()
                            max_s = None if plz == "ilimitado" else int(plz)
                        else: nom, max_s = p, None
                        if nom: opciones.append({"name": nom[:100], "max_slots": max_s})
                    if opciones:
                        datos["signup_options"] = opciones
                        break
                    error_actual = "No se interpretaron opciones validas."
                break
            else: error_actual = "Introduce 1, 2 o 3."

        # PASO 8: Lista de Espera
        if datos["signup_options"]:
            while True:
                desc = "► [1] Si (Habilitar lista de espera cuando se llenen las plazas)\n► [2] No (Rechazar inscripciones cuando se llene)\n\n§ Introduce 1 o 2:"
                await enviar_paso("¿PERMITIR LISTA DE ESPERA (RESERVA)?", desc, error_actual)
                error_actual, resp = None, await esperar_respuesta()
                if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
                if resp in ("1", "2"):
                    datos["allow_waitlist"] = (resp == "1")
                    break
                error_actual = "Introduce 1 o 2."

        # PASO 9: Inscripciones Multiples
        if datos["signup_options"]:
            while True:
                desc = "► [1] No (Una sola inscripcion por usuario)\n► [2] Si (Inscripcion multiple)\n\n§ Introduce 1 o 2:"
                await enviar_paso("¿PERMITIR INSCRIPCIONES MULTIPLES?", desc, error_actual)
                error_actual, resp = None, await esperar_respuesta()
                if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
                if resp in ("1", "2"):
                    datos["multiple_registrations"] = (resp == "2")
                    break
                error_actual = "Introduce 1 o 2."

        # PASO 10: Menciones
        conn = conectar_db()
        filas_m = conn.execute("SELECT role_id FROM roles_mencionables").fetchall()
        conn.close()
        ids_permitidos = {int(f["role_id"]) for f in filas_m}
        roles = [guild.get_role(rid) for rid in ids_permitidos if guild.get_role(rid)] if ids_permitidos else guild.roles
        roles = [r for r in roles if r and not r.is_default() and not r.managed]

        pagina_r, total_pag_r = 0, max(1, (len(roles) + por_pagina - 1) // por_pagina)
        while True:
            roles_pag = roles[pagina_r * por_pagina : (pagina_r + 1) * por_pagina]
            lineas = ["► [0] Ninguna mencion"] + [f"► [{i}] @{r.name}" for i, r in enumerate(roles_pag, start=1)]
            desc = "Selecciona roles a mencionar:\n\n" + "\n".join(lineas)
            if total_pag_r > 1:
                desc += f"\n\n─── Pagina {pagina_r + 1} de {total_pag_r} ───"
                if pagina_r < total_pag_r - 1: desc += "\n► [S] Siguiente"
                if pagina_r > 0: desc += "\n◄ [A] Anterior"
            desc += "\n\n§ Introduce '0' o numeros separados por comas:"

            await enviar_paso("¿ROLES A MENCIONAR?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            r_up = resp.upper()
            if r_up == "S" and pagina_r < total_pag_r - 1: pagina_r += 1
            elif r_up == "A" and pagina_r > 0: pagina_r -= 1
            elif resp == "0": datos["mention_roles"] = []; break
            else:
                partes = [p.strip() for p in resp.split(",") if p.strip().isdigit()]
                seleccionados, valido = [], True
                for p in partes:
                    idx = int(p) - 1
                    if 0 <= idx < len(roles_pag): seleccionados.append(roles_pag[idx])
                    else: valido = False; break
                if valido and seleccionados:
                    datos["mention_roles"] = seleccionados
                    break
                error_actual = "Opcion no valida."

        # PASO 10B: Restriccion de acceso
        roles_acceso = [r for r in guild.roles if not r.is_default() and not r.managed]
        pagina_a, total_pag_a = 0, max(1, (len(roles_acceso) + por_pagina - 1) // por_pagina)
        while datos["signup_options"]:  # solo tiene sentido si el evento admite inscripciones
            roles_a_pag = roles_acceso[pagina_a * por_pagina : (pagina_a + 1) * por_pagina]
            lineas = ["► [0] Abierto a todo el servidor"] + [f"► [{i}] @{r.name}" for i, r in enumerate(roles_a_pag, start=1)]
            desc = "Solo estos roles podran inscribirse:\n\n" + "\n".join(lineas)
            if total_pag_a > 1:
                desc += f"\n\n─── Pagina {pagina_a + 1} de {total_pag_a} ───"
                if pagina_a < total_pag_a - 1: desc += "\n► [S] Siguiente"
                if pagina_a > 0: desc += "\n◄ [A] Anterior"
            desc += "\n\n§ Introduce '0' o numeros separados por comas:"

            await enviar_paso("¿QUIEN PUEDE INSCRIBIRSE?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

            r_up = resp.upper()
            if r_up == "S" and pagina_a < total_pag_a - 1: pagina_a += 1
            elif r_up == "A" and pagina_a > 0: pagina_a -= 1
            elif resp == "0": datos["restricted_roles"] = []; break
            else:
                partes = [p.strip() for p in resp.split(",") if p.strip().isdigit()]
                seleccionados, valido = [], True
                for p in partes:
                    idx = int(p) - 1
                    if 0 <= idx < len(roles_a_pag): seleccionados.append(roles_a_pag[idx])
                    else: valido = False; break
                if valido and seleccionados:
                    datos["restricted_roles"] = seleccionados
                    break
                error_actual = "Opcion no valida."

        # PASO 11: Color
        colores_lista = list(COLORES.keys())
        texto_colores = "\n".join(f"► [{i+1}] {c}" for i, c in enumerate(colores_lista))
        while True:
            await enviar_paso("¿COLOR DEL ANUNCIO?", f"{texto_colores}\n\n§ Introduce el numero:", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.isdigit() and 1 <= int(resp) <= len(colores_lista):
                datos["color_name"] = colores_lista[int(resp) - 1]
                datos["color"] = COLORES[datos["color_name"]]
                break
            error_actual = "Numero invalido."

        # PASO 12: Imagen
        while True:
            await enviar_paso("¿IMAGEN O BANNER?", "► Introduce la URL directa (http://... o https://...) o 'ninguna'.", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.lower() == "ninguna": datos["image_url"] = None; break
            elif resp.startswith(("http://", "https://")): datos["image_url"] = resp; break
            error_actual = "URL invalida."

        # PASO 13: Ubicacion
        while True:
            desc = "► [1] Seleccionar canal existente del servidor\n► [2] Crear canal de voz automatico al iniciar el evento\n► [3] Sin ubicacion especifica\n\n§ Introduce 1, 2 o 3:"
            await enviar_paso("¿UBICACION O SALA DEL EVENTO?", desc, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp == "3":
                datos["location"] = None; datos["auto_voice"] = False; break
            elif resp == "2":
                datos["location"] = None; datos["auto_voice"] = True; break
            elif resp == "1":
                canales_ub = [c for c in guild.channels if isinstance(c, (discord.TextChannel, discord.VoiceChannel))]
                pagina_u, total_pag_u = 0, max(1, (len(canales_ub) + por_pagina - 1) // por_pagina)
                seleccionado = None
                while seleccionado is None:
                    canales_u_pag = canales_ub[pagina_u * por_pagina : (pagina_u + 1) * por_pagina]
                    desc_u = "\n".join(f"► [{i+1}] {c.mention}" for i, c in enumerate(canales_u_pag))
                    desc_u += f"\n\n─── Pagina {pagina_u + 1} de {total_pag_u} ───"
                    if pagina_u < total_pag_u - 1: desc_u += "\n► [S] Siguiente"
                    if pagina_u > 0: desc_u += "\n◄ [A] Anterior"
                    desc_u += "\n► [V] Volver"

                    await enviar_paso("SELECCIONA CANAL DE UBICACION", desc_u, error_actual)
                    error_actual, r_chan = None, await esperar_respuesta()
                    if r_chan in ("TIMEOUT", "CANCEL"):
                        return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)

                    r_up_chan = r_chan.upper()
                    if r_up_chan == "V": break
                    elif r_up_chan == "S" and pagina_u < total_pag_u - 1: pagina_u += 1
                    elif r_up_chan == "A" and pagina_u > 0: pagina_u -= 1
                    elif r_chan.isdigit() and 1 <= int(r_chan) <= len(canales_u_pag):
                        seleccionado = canales_u_pag[int(r_chan) - 1]
                    else: error_actual = "Canal no valido."

                if seleccionado:
                    datos["location"] = seleccionado
                    datos["auto_voice"] = False
                    break
            else: error_actual = "Introduce 1, 2 o 3."

        # PASO 14: Recordatorios
        while True:
            await enviar_paso("¿RECORDATORIOS PREVIOS?", "► Ejemplos: 24h, 1h, 30m o 'ninguno'.", error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("TIMEOUT", "CANCEL"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp.lower() == "ninguno": datos["reminders"] = []; break
            recs = []
            for parte in resp.split(","):
                m = re.match(r"(\d+)\s*(m|min|h|hora|horas|d|dia|dias)", parte.strip().lower())
                if m:
                    cant, uni = int(m.group(1)), m.group(2)
                    mins = cant if uni.startswith("m") else (cant * 60 if uni.startswith("h") else cant * 1440)
                    if mins > 0: recs.append(mins)
            if recs: datos["reminders"] = sorted(set(recs), reverse=True); break
            error_actual = "Formato de recordatorios invalido."

        # PASO 15: Resumen y Publicacion
        resumen = [
            f"► Organizador: {usuario.mention}",
            f"► Titulo: **{datos['title']}**",
            f"► Inicio: <t:{timestamp_discord(datos['start_time'])}:F>",
            f"► Duracion: {formatear_duracion(datos['duration_minutes'])}",
            f"► Frecuencia: {datos['frequency']}",
            f"► Canal: {datos['publish_channel'].mention}",
            f"► Lista de Espera: {'Habilitada' if datos['allow_waitlist'] else 'Deshabilitada'}",
            f"► Acceso: {', '.join('@' + r.name for r in datos['restricted_roles']) or 'Todo el servidor'}",
            f"► Voz Automatica: {'Si' if datos['auto_voice'] else 'No'}",
            f"► Recordatorios: {formatear_recordatorios(datos['reminders'])}"
        ]
        desc_final = "╔════════════════════════════════════════╗\n          RESUMEN DEL EVENTO\n╚════════════════════════════════════════╝\n\n" + "\n".join(resumen) + "\n\n► [1] Publicar Evento\n► [2] Cancelar\n\n§ Introduce 1 o 2:"

        while True:
            await enviar_paso("¿DESEAS PUBLICAR EL EVENTO?", desc_final, error_actual)
            error_actual, resp = None, await esperar_respuesta()
            if resp in ("2", "CANCEL", "TIMEOUT"): return await enviar_paso("CREACION CANCELADA", "‼ Cancelado.", mostrar_cancelar=False)
            if resp == "1": break
            error_actual = "Introduce 1 o 2."

        # Publicar en la BD
        canal = datos["publish_channel"]
        loc_id = datos["location"].id if datos["location"] else None

        conn = conectar_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("""
                INSERT INTO eventos (guild_id, channel_id, creator_id, title, description, start_time,
                                     duration_minutes, frequency, color, location_channel_id, auto_voice,
                                     image_url, multiple_registrations, allow_waitlist, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                guild.id, canal.id, usuario.id, datos["title"], datos["description"],
                a_utc_iso(datos["start_time"]), datos["duration_minutes"], datos["frequency"],
                datos["color"], loc_id, 1 if datos["auto_voice"] else 0, datos["image_url"],
                1 if datos["multiple_registrations"] else 0, 1 if datos["allow_waitlist"] else 0,
                a_utc_iso(ahora()),
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

            bloqueados = conn.execute("SELECT role_id FROM roles_bloqueados").fetchall()
            conn.executemany(
                "INSERT INTO evento_restricciones (event_id, role_id, tipo) VALUES (?, ?, ?)",
                [(evento_id, r.id, "permitido") for r in datos["restricted_roles"]]
                + [(evento_id, f["role_id"], "bloqueado") for f in bloqueados],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        menciones_str = " ".join(r.mention for r in datos["mention_roles"]) or None
        _, hilo = await publicar_evento(evento_id, canal, menciones_str)
        if hilo:
            await hilo.send(
                f"📌 **Hilo del evento iniciado.** Canal oficial para recordatorios y avisos "
                f"del evento de {usuario.mention}."
            )

        destino = f" con su respectivo hilo en {hilo.mention}" if hilo else ""
        await enviar_paso(
            "EVENTO PUBLICADO CON EXITO",
            f"► Evento #{evento_id} creado en {canal.mention}{destino}.",
            mostrar_cancelar=False,
        )

    except discord.Forbidden:
        log.warning("No se pudo continuar el asistente con %s: DMs cerrados", usuario.id)
        await avisar_fallo(interaction, "‼ No puedo escribirte por privado. Activa los MD del servidor y reintenta.")
    except Exception:
        log.exception("Fallo el asistente de creacion de eventos de %s", usuario.id)
        await avisar_fallo(interaction, "‼ El asistente fallo por un error interno. Vuelve a intentarlo.")
    finally:
        SESIONES_ACTIVAS.discard(usuario.id)


async def avisar_fallo(interaction: discord.Interaction, mensaje: str):
    """El asistente corre en una tarea aparte: sin esto los errores se perderian en silencio."""
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
                "‼ Ya tienes una sesion activa en tus mensajes directos.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("► Asistente enviado por mensaje privado (DM).", ephemeral=True)
        asyncio.create_task(ejecutar_creador_lineal(bot, interaction))