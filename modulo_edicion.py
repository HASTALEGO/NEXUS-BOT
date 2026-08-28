"""Puntos 5 y 6: edición de eventos activos y repetición/reutilización por DM."""
import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from creador_eventos import TIMEOUT_PASO
from database import (
    actualizar_campos_evento,
    actualizar_opcion,
    conectar_db,
    obtener_eventos_creador,
)
from formatters import (
    COLOR_BLANCO,
    a_utc_iso,
    ahora,
    parsear_fecha,
    timestamp_discord,
)
from vistas_eventos import actualizar_evento_publicado, obtener_o_crear_hilo, publicar_evento

log = logging.getLogger(__name__)

POR_PAGINA = 15
SESIONES_ACTIVAS = set()


class _Asistente:
    """Mini asistente por DM reutilizable por los wizards de edición y repetición."""

    def __init__(self, bot, interaction: discord.Interaction):
        self.bot = bot
        self.usuario = interaction.user
        self.guild = interaction.guild
        self.msg = None

    async def enviar(self, titulo: str, descripcion: str, mostrar_cancelar: bool = True):
        embed = discord.Embed(title=titulo, description=descripcion, color=COLOR_BLANCO)
        if mostrar_cancelar:
            embed.set_footer(text="► Escribe 'cancel' en cualquier momento para cancelar ◄")
        if self.msg:
            try:
                await self.msg.edit(embed=embed)
                return self.msg
            except discord.HTTPException:
                self.msg = None
        self.msg = await self.usuario.send(embed=embed)
        return self.msg

    async def esperar(self) -> str:
        def check(m):
            return m.author.id == self.usuario.id and isinstance(m.channel, discord.DMChannel)
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=TIMEOUT_PASO)
        except asyncio.TimeoutError:
            return "TIMEOUT"
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        contenido = msg.content.strip()
        return "CANCEL" if contenido.lower() == "cancel" else contenido


async def _paginador_eventos(flujo: _Asistente, eventos: list):
    """Deja elegir un evento de la lista del creador (con paginación)."""
    total_paginas = max(1, (len(eventos) + POR_PAGINA - 1) // POR_PAGINA)
    pagina = 0
    while True:
        bloque = eventos[pagina * POR_PAGINA:(pagina + 1) * POR_PAGINA]
        lineas = []
        for i, ev in enumerate(bloque, start=1):
            ts = timestamp_discord(ev["start_time"]) if ev["start_time"] else 0
            cuando = f"<t:{ts}:D>" if ts else "sin fecha"
            lineas.append(f"► [{i}] **#{ev['id']} {ev['title'][:40]}** — {cuando} ({ev['inscritos']} inscritos)")
        desc = "Selecciona un evento:\n\n" + "\n".join(lineas)
        desc += f"\n\n─── Página {pagina + 1} de {total_paginas} ───"
        if pagina < total_paginas - 1:
            desc += "\n► [S] Siguiente"
        if pagina > 0:
            desc += "\n◄ [A] Anterior"
        desc += "\n► [X] Cancelar\n\n§ Introduce un número:"

        await flujo.enviar("SELECCIÓN DE EVENTO", desc)
        resp = await flujo.esperar()
        if resp in ("CANCEL", "TIMEOUT", "X", "x"):
            return None
        r_up = resp.upper()
        if r_up == "S" and pagina < total_paginas - 1:
            pagina += 1
        elif r_up == "A" and pagina > 0:
            pagina -= 1
        elif resp.isdigit() and 1 <= int(resp) <= len(bloque):
            return bloque[int(resp) - 1]


async def _seleccionar_canal(flujo: _Asistente):
    """Deja elegir un canal de texto del servidor (con paginación)."""
    canales = [c for c in flujo.guild.text_channels
               if c.permissions_for(flujo.guild.me).send_messages]
    if not canales:
        return None
    total_paginas = max(1, (len(canales) + POR_PAGINA - 1) // POR_PAGINA)
    pagina = 0
    while True:
        bloque = canales[pagina * POR_PAGINA:(pagina + 1) * POR_PAGINA]
        lineas = [f"► [{i}] {c.mention}" for i, c in enumerate(bloque, start=1)]
        desc = "Canal de publicación:\n\n" + "\n".join(lineas)
        desc += f"\n\n─── Página {pagina + 1} de {total_paginas} ───"
        if pagina < total_paginas - 1:
            desc += "\n► [S] Siguiente"
        if pagina > 0:
            desc += "\n◄ [A] Anterior"
        desc += "\n§ Introduce un número:"

        await flujo.enviar("SELECCIÓN DE CANAL", desc)
        resp = await flujo.esperar()
        if resp in ("CANCEL", "TIMEOUT"):
            return None
        r_up = resp.upper()
        if r_up == "S" and pagina < total_paginas - 1:
            pagina += 1
        elif r_up == "A" and pagina > 0:
            pagina -= 1
        elif resp.isdigit() and 1 <= int(resp) <= len(bloque):
            return bloque[int(resp) - 1]


def _crear_edicion(elegido, nuevo_inicio, canal_id=None, duracion=None, recordatorios=None) -> int:
    conn = conectar_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        parent = elegido["parent_event_id"] or elegido["id"]
        cursor = conn.execute("""
            INSERT INTO eventos (guild_id, channel_id, creator_id, title, description, start_time,
                                 duration_minutes, frequency, color, location_channel_id, auto_voice,
                                 image_url, multiple_registrations, allow_waitlist, created_at,
                                 parent_event_id, close_before_minutes, dm_reminders)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            elegido["guild_id"],
            canal_id if canal_id is not None else elegido["channel_id"],
            elegido["creator_id"], elegido["title"], elegido["description"],
            a_utc_iso(nuevo_inicio),
            duracion if duracion is not None else elegido["duration_minutes"],
            elegido["frequency"], elegido["color"], elegido["location_channel_id"],
            elegido["auto_voice"], elegido["image_url"], elegido["multiple_registrations"],
            elegido["allow_waitlist"], a_utc_iso(ahora()), parent,
            elegido["close_before_minutes"] or 0,
            elegido["dm_reminders"] if elegido["dm_reminders"] is not None else 1,
        ))
        nuevo_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO opciones_inscripcion (event_id, name, emoji, max_slots)"
            " SELECT ?, name, emoji, max_slots FROM opciones_inscripcion WHERE event_id = ?",
            (nuevo_id, elegido["id"]),
        )
        conn.execute(
            "INSERT INTO evento_menciones (event_id, role_id) SELECT ?, role_id FROM evento_menciones WHERE event_id = ?",
            (nuevo_id, elegido["id"]),
        )
        conn.execute(
            "INSERT INTO evento_restricciones (event_id, role_id, tipo) SELECT ?, role_id, tipo FROM evento_restricciones WHERE event_id = ?",
            (nuevo_id, elegido["id"]),
        )
        if recordatorios is not None:
            conn.executemany(
                "INSERT INTO recordatorios (event_id, minutes_before, sent) VALUES (?, ?, 0)",
                [(nuevo_id, m) for m in recordatorios],
            )
        else:
            conn.execute(
                "INSERT INTO recordatorios (event_id, minutes_before, sent) SELECT ?, minutes_before, 0 FROM recordatorios WHERE event_id = ?",
                (nuevo_id, elegido["id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return nuevo_id


async def iniciar_edicion_evento(bot: commands.Bot, event_id: int, interaction: discord.Interaction):
    """Punto 6: asistente de edición de un evento por DM."""
    usuario = interaction.user
    SESIONES_ACTIVAS.add(usuario.id)
    flujo = _Asistente(bot, interaction)
    try:
        conn = conectar_db()
        try:
            ev = conn.execute(
                "SELECT * FROM eventos WHERE id = ? AND guild_id = ?",
                (event_id, interaction.guild_id or 0),
            ).fetchone()
        finally:
            conn.close()
        if not ev:
            return await flujo.enviar("ERROR", f"{'‼'} El evento #{event_id} no existe en este servidor.", False)

        while True:
            menu = "\n".join([
                "§ MENÚ DE EDICIÓN §",
                "► [1] Título",
                "► [2] Descripción",
                "► [3] Fecha y hora de inicio",
                "► [4] Duración",
                "► [5] Cupos por opción",
                "► [6] Imagen / Banner",
                "► [7] Cierre automático de inscripciones",
                "► [8] Recordatorios por DM",
                "► [0] Salir",
            ])
            await flujo.enviar(f"EDITANDO EVENTO #{event_id}", menu)
            resp = await flujo.esperar()
            if resp in ("CANCEL", "TIMEOUT", "0"):
                return await flujo.enviar(
                    "EDICIÓN FINALIZADA",
                    f"► Se han aplicado los cambios y el mensaje público está actualizado.",
                    False,
                )

            cambio = False
            if resp == "1":
                await flujo.enviar("NUEVO TÍTULO", "► Escribe el nuevo título (máx 100):")
                r = await flujo.esperar()
                if r not in ("CANCEL", "TIMEOUT") and 1 <= len(r) <= 100:
                    actualizar_campos_evento(event_id, title=r)
                    await _renombrar_hilo(bot, ev)
                    cambio = True
            elif resp == "2":
                await flujo.enviar("NUEVA DESCRIPCIÓN", "► Escribe la descripción o 'ninguna' (máx 2000):")
                r = await flujo.esperar()
                if r not in ("CANCEL", "TIMEOUT"):
                    desc = "" if r.lower() == "ninguna" else r
                    if len(desc) <= 2000:
                        actualizar_campos_evento(event_id, description=desc)
                        cambio = True
            elif resp == "3":
                await flujo.enviar("NUEVA FECHA DE INICIO", "► Formato: DD/MM/YYYY HH:MM\n§ Ejemplo: 30/09/2026 20:30")
                r = await flujo.esperar()
                if r not in ("CANCEL", "TIMEOUT"):
                    fecha = parsear_fecha(r)
                    if fecha and fecha > ahora():
                        actualizar_campos_evento(event_id, start_time=a_utc_iso(fecha))
                        cambio = True
                    else:
                        await flujo.enviar("ERROR", "‼ Formato incorrecto o fecha en el pasado.")
            elif resp == "4":
                await flujo.enviar("NUEVA DURACIÓN", "► Ejemplos: 2h, 90m, 2h 30m")
                r = await flujo.esperar()
                if r not in ("CANCEL", "TIMEOUT"):
                    cnt = r.lower()
                    h = re.search(r"(\d+)\s*h", cnt)
                    m = re.search(r"(\d+)\s*m", cnt)
                    tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
                    if tot > 0:
                        actualizar_campos_evento(event_id, duration_minutes=tot)
                        cambio = True
            elif resp == "5":
                opciones = await _editar_cupos(bot, interaction, flujo, event_id)
                cambio = opciones
            elif resp == "6":
                await flujo.enviar("NUEVA IMAGEN", "► URL directa (jpg/png/gif) o 'ninguna':")
                r = await flujo.esperar()
                if r not in ("CANCEL", "TIMEOUT"):
                    if r.lower() == "ninguna":
                        actualizar_campos_evento(event_id, image_url=None)
                        cambio = True
                    elif r.startswith(("http://", "https://")):
                        actualizar_campos_evento(event_id, image_url=r)
                        cambio = True
            elif resp == "7":
                await flujo.enviar(
                    "CIERRE DE INSCRIPCIONES",
                    "► Ejemplos: 30m, 1h, 2h 30m\n► 'ninguno' = no cerrar.\n► La lista de espera sigue promoviendo aunque esté cerrado.",
                )
                r = await flujo.esperar()
                if r not in ("CANCEL", "TIMEOUT"):
                    if r.lower() in ("ninguno", "no", "0"):
                        actualizar_campos_evento(event_id, close_before_minutes=0)
                        cambio = True
                    else:
                        cnt = r.lower()
                        h = re.search(r"(\d+)\s*h", cnt)
                        m = re.search(r"(\d+)\s*m", cnt)
                        tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
                        if tot > 0:
                            actualizar_campos_evento(event_id, close_before_minutes=tot)
                            cambio = True
            elif resp == "8":
                await flujo.enviar(
                    "RECORDATORIOS POR DM",
                    "► [1] Sí, enviar DM a los confirmados\n► [2] No, solo avisos en el hilo",
                )
                r = await flujo.esperar()
                if r in ("1", "2"):
                    actualizar_campos_evento(event_id, dm_reminders=1 if r == "1" else 0)
                    cambio = True
            else:
                await flujo.enviar("ERROR", "‼ Opción inválida. Introduce 1-8 o 0 para salir.")
                continue

            if cambio:
                await actualizar_evento_publicado(event_id)
                await flujo.enviar("CAMBIOS GUARDADOS", f"► Evento #{event_id} actualizado.")
    except discord.Forbidden:
        await _avisar_fallo(interaction, "‼ No puedo escribirte por privado. Activa los DM del servidor.")
    except Exception:
        log.exception("Falló la edición del evento %s para %s", event_id, usuario.id)
        await _avisar_fallo(interaction, "‼ La edición falló por un error interno.")
    finally:
        SESIONES_ACTIVAS.discard(usuario.id)


async def _renombrar_hilo(bot: commands.Bot, evento):
    if not bot or not evento["thread_id"]:
        return
    guild = bot.get_guild(evento["guild_id"])
    if not guild:
        return
    try:
        hilo = await obtener_o_crear_hilo(guild, evento)
        if hilo:
            await hilo.edit(name=evento["title"][:100])
    except discord.HTTPException as e:
        log.warning("No se pudo renombrar el hilo del evento %s: %s", evento["id"], e)


async def _editar_cupos(bot, interaction, flujo: _Asistente, event_id: int) -> bool:
    conn = conectar_db()
    try:
        opciones = conn.execute(
            "SELECT id, name, max_slots FROM opciones_inscripcion WHERE event_id = ? ORDER BY id",
            (event_id,),
        ).fetchall()
    finally:
        conn.close()
    if not opciones:
        await flujo.enviar("ERROR", "‼ Este evento no tiene opciones de inscripción.")
        return False

    lineas = []
    for i, op in enumerate(opciones, start=1):
        cupo = f"[{op['max_slots']}]" if op["max_slots"] else "[∞]"
        lineas.append(f"► [{i}] {op['name']} {cupo}")
    await flujo.enviar("CUPOS POR OPCIÓN", "\n".join(lineas) + "\n\n§ Introduce el número de la opción:")
    resp = await flujo.esperar()
    if not (resp.isdigit() and 1 <= int(resp) <= len(opciones)):
        return False
    op = opciones[int(resp) - 1]

    await flujo.enviar(
        f"NUEVO CUPO — {op['name']}",
        "► Escribe el nuevo cupo máximo (número) o '∞'/'sin' para sin límite:",
    )
    r = await flujo.esperar()
    if r in ("CANCEL", "TIMEOUT"):
        return False
    if r.lower() in ("∞", "inf", "infinito", "sin", "none", "ninguno", "0"):
        actualizar_opcion(op["id"], max_slots=None)
    elif r.isdigit() and int(r) >= 0:
        actualizar_opcion(op["id"], max_slots=int(r))
    else:
        return False
    return True


async def iniciar_edicion_select(bot: commands.Bot, interaction: discord.Interaction):
    """Elige un evento propio y lanza el editor (para /editar_evento sin ID)."""
    usuario = interaction.user
    SESIONES_ACTIVAS.add(usuario.id)
    flujo = _Asistente(bot, interaction)
    try:
        eventos = obtener_eventos_creador(usuario.id, interaction.guild_id or 0)
        if not eventos:
            return await flujo.enviar("SIN EVENTOS", "‼ No tienes eventos propios para editar.", False)
        elegido = await _paginador_eventos(flujo, eventos)
        if elegido is None:
            return await flujo.enviar("CANCELADO", "‼ Edición cancelada.", False)
    finally:
        SESIONES_ACTIVAS.discard(usuario.id)
    await iniciar_edicion_evento(bot, elegido["id"], interaction)


async def iniciar_repetir_evento(bot: commands.Bot, interaction: discord.Interaction):
    """Punto 5: reutilización de un evento pasado para crear una nueva edición."""
    usuario, guild = interaction.user, interaction.guild
    SESIONES_ACTIVAS.add(usuario.id)
    flujo = _Asistente(bot, interaction)
    try:
        eventos = obtener_eventos_creador(usuario.id, interaction.guild_id or 0)
        if not eventos:
            return await flujo.enviar("SIN EVENTOS", "‼ No tienes eventos propios para repetir.", False)
        elegido = await _paginador_eventos(flujo, eventos)
        if elegido is None:
            return await flujo.enviar("CANCELADO", "‼ Reutilización cancelada.", False)

        # Nueva fecha y hora (obligatorio)
        while True:
            await flujo.enviar(
                "NUEVA FECHA DE LA EDICIÓN",
                "► Formato: DD/MM/YYYY HH:MM\n§ Ejemplo: 30/09/2026 20:30",
            )
            r = await flujo.esperar()
            if r in ("CANCEL", "TIMEOUT"):
                return await flujo.enviar("CANCELADO", "‼ Reutilización cancelada.", False)
            fecha = parsear_fecha(r)
            if fecha and fecha > ahora():
                break
            await flujo.enviar("ERROR", "‼ Fecha inválida. Usa DD/MM/YYYY HH:MM y debe ser futura.")

        # Canal de publicación (opcional: Enter mantiene el mismo)
        await flujo.enviar(
            "CANAL DE PUBLICACIÓN",
            "► [1] Seleccionar otro canal\n► [2] Usar el mismo canal que la edición anterior",
        )
        r_canal = await flujo.esperar()
        canal_id = None
        if r_canal == "1":
            canal = await _seleccionar_canal(flujo)
            if canal is None:
                return await flujo.enviar("CANCELADO", "‼ Reutilización cancelada.", False)
            canal_id = canal.id
        elif r_canal in ("CANCEL", "TIMEOUT"):
            return await flujo.enviar("CANCELADO", "‼ Reutilización cancelada.", False)

        # Duración (opcional)
        await flujo.enviar(
            "DURACIÓN",
            "► Escribe la nueva duración (Ej: 2h, 90m) o 'mantener' para conservar la anterior:",
        )
        r_dur = await flujo.esperar()
        duracion = None
        if r_dur not in ("CANCEL", "TIMEOUT", "mantener", "conservar", "no"):
            cnt = r_dur.lower()
            h = re.search(r"(\d+)\s*h", cnt)
            m = re.search(r"(\d+)\s*m", cnt)
            tot = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
            if tot > 0:
                duracion = tot

        # Recordatorios (opcional)
        await flujo.enviar(
            "RECORDATORIOS",
            "► Ejemplos: 24h, 1h, 30m o 'mantener' para conservar los anteriores.\n► Escribe '0' para sin recordatorios.",
        )
        r_recs = await flujo.esperar()
        recordatorios = None
        if r_recs in ("0", "ninguno", "none", "sin"):
            recordatorios = []
        elif r_recs not in ("CANCEL", "TIMEOUT", "mantener", "conservar", "no"):
            recs = []
            for parte in r_recs.split(","):
                m = re.match(r"(\d+)\s*(m|min|h|hora|horas|d|dia|dias)", parte.strip().lower())
                if m:
                    cant, uni = int(m.group(1)), m.group(2)
                    mins = cant if uni.startswith("m") else (cant * 60 if uni.startswith("h") else cant * 1440)
                    if mins > 0:
                        recs.append(mins)
            if recs:
                recordatorios = sorted(set(recs), reverse=True)
        elif r_recs in ("CANCEL", "TIMEOUT"):
            return await flujo.enviar("CANCELADO", "‼ Reutilización cancelada.", False)

        nuevo_id = _crear_edicion(elegido, fecha, canal_id=canal_id, duracion=duracion, recordatorios=recordatorios)

        canal_pub = guild.get_channel(elegido["channel_id"]) if canal_id is None else guild.get_channel(canal_id)
        if not canal_pub:
            return await flujo.enviar("ERROR", "‼ El canal de publicación no existe.", False)
        _, hilo = await publicar_evento(nuevo_id, canal_pub, None)
        destino = f" con su hilo en {hilo.mention}" if hilo else ""
        await flujo.enviar(
            "EDICIÓN PUBLICADA",
            f"► Nueva edición **#{nuevo_id}** creada a partir del evento #{elegido['id']}{destino}.\n"
            f"► Las valoraciones de esta edición se acumulan con la saga original.",
            False,
        )
        try:
            await interaction.followup.send(f"► Nueva edición #{nuevo_id} publicada.", ephemeral=True)
        except discord.HTTPException:
            pass
    except discord.Forbidden:
        await _avisar_fallo(interaction, "‼ No puedo escribirte por privado. Activa los DM del servidor.")
    except Exception:
        log.exception("Falló la reutilización del evento para %s", usuario.id)
        await _avisar_fallo(interaction, "‼ La reutilización falló por un error interno.")
    finally:
        SESIONES_ACTIVAS.discard(usuario.id)


async def _avisar_fallo(interaction: discord.Interaction, mensaje: str):
    try:
        await interaction.followup.send(mensaje, ephemeral=True)
    except discord.HTTPException:
        log.warning("Tampoco se pudo avisar del fallo a %s", getattr(interaction.user, "id", None))


def configurar_modulo_edicion(bot: commands.Bot):
    @bot.tree.command(name="editar_evento", description="Edita un evento existente.")
    @app_commands.describe(evento_id="ID del evento (opcional): déjalo vacío para elegir")
    @app_commands.guild_only()
    async def cmd_editar_evento(interaction: discord.Interaction, evento_id: int = 0):
        if interaction.user.id in SESIONES_ACTIVAS:
            return await interaction.response.send_message(
                "‼ Ya tienes una sesión de edición activa.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("► Asistente de edición enviado por DM.", ephemeral=True)
        if evento_id > 0:
            asyncio.create_task(iniciar_edicion_evento(bot, evento_id, interaction))
        else:
            asyncio.create_task(iniciar_edicion_select(bot, interaction))

    @bot.tree.command(name="repetir_evento", description="Reutiliza un evento pasado para crear una nueva edición.")
    @app_commands.guild_only()
    async def cmd_repetir_evento(interaction: discord.Interaction):
        if interaction.user.id in SESIONES_ACTIVAS:
            return await interaction.response.send_message(
                "‼ Ya tienes una sesión activa.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("► Asistente de repetición enviado por DM.", ephemeral=True)
        asyncio.create_task(iniciar_repetir_evento(bot, interaction))