"""Embeds y vistas persistentes de un evento.

Vive en su propio módulo para que `main` y `creador_eventos` puedan compartirlo
sin importarse mutuamente.
"""
import asyncio
import logging
from datetime import timedelta

import discord

from database import conectar_db
from formatters import (
    COLOR_MONOCHROME,
    ICON_ALERT,
    ICON_BULLET,
    ICON_CERRADO,
    ICON_CHECK,
    ICON_CROSS,
    ICON_FINALIZADO,
    ICON_NOTE,
    a_utc_iso,
    ahora,
    desde_iso,
    format_inscritos_opcion,
    format_retro_embed_description,
    format_retro_header,
    formatear_duracion,
    timestamp_discord,
)
from modulo_valoraciones import FeedbackModal

log = logging.getLogger(__name__)

MAX_CAMPOS_OPCIONES = 20
MAX_LONGITUD_CAMPO = 1024

_bot: discord.Client | None = None


def inicializar_vistas(bot: discord.Client):
    global _bot
    _bot = bot


def inicio_evento(evento):
    return desde_iso(evento["start_time"])


def fin_evento(evento):
    inicio = inicio_evento(evento)
    if inicio is None:
        return None
    return inicio + timedelta(minutes=evento["duration_minutes"] or 0)


def evento_finalizado(evento) -> bool:
    fin = fin_evento(evento)
    return bool(fin and ahora() >= fin)


def inscripciones_cerradas_por_tiempo(evento) -> bool:
    """Comprueba si las inscripciones se han cerrado debido a close_before_minutes."""
    inicio = inicio_evento(evento)
    if not inicio:
        return False
    minutos_cierre = evento["close_before_minutes"] or 0
    if minutos_cierre <= 0:
        return False
    momento_cierre = inicio - timedelta(minutes=minutos_cierre)
    return ahora() >= momento_cierre


def _recortar(texto: str, limite: int = MAX_LONGITUD_CAMPO) -> str:
    if len(texto) <= limite:
        return texto
    return texto[: limite - 20].rstrip() + "\n… (lista recortada)"


def trocear_menciones(menciones: list, limite: int = 1900) -> list:
    """Discord rechaza mensajes de más de 2000 caracteres."""
    bloques, actual = [], ""
    for mencion in menciones:
        if len(actual) + len(mencion) + 1 > limite:
            bloques.append(actual.strip())
            actual = ""
        actual += mencion + " "
    if actual.strip():
        bloques.append(actual.strip())
    return bloques


async def obtener_o_crear_hilo(guild: discord.Guild | None, evento) -> discord.Thread | None:
    if not guild:
        return None
    if evento["thread_id"]:
        hilo = guild.get_thread(evento["thread_id"])
        if hilo:
            return hilo
        try:
            hilo = await guild.fetch_channel(evento["thread_id"])
            if isinstance(hilo, discord.Thread):
                return hilo
        except discord.HTTPException as e:
            log.warning("No se pudo recuperar el hilo %s: %s", evento["thread_id"], e)

    canal = guild.get_channel(evento["channel_id"])
    if canal and evento["message_id"]:
        try:
            mensaje = await canal.fetch_message(evento["message_id"])
            hilo = await mensaje.create_thread(name=evento["title"][:100])
        except discord.HTTPException as e:
            log.error("No se pudo crear el hilo del evento %s: %s", evento["id"], e)
            return None

        conn = conectar_db()
        try:
            conn.execute("UPDATE eventos SET thread_id = ? WHERE id = ?", (hilo.id, evento["id"]))
            conn.commit()
        finally:
            conn.close()
        return hilo
    return None


def crear_embed_publicado(evento_id: int) -> discord.Embed:
    conn = conectar_db()
    try:
        evento = conn.execute("SELECT * FROM eventos WHERE id = ?", (evento_id,)).fetchone()
        if not evento:
            return discord.Embed(title="EVENTO NO ENCONTRADO", color=COLOR_MONOCHROME)

        opciones = conn.execute(
            "SELECT * FROM opciones_inscripcion WHERE event_id = ? ORDER BY id", (evento_id,)
        ).fetchall()
        inscripciones = conn.execute(
            "SELECT user_id, option_id, status, position FROM inscripciones WHERE event_id = ? ORDER BY id ASC",
            (evento_id,),
        ).fetchall()
    finally:
        conn.close()

    inicio = inicio_evento(evento)
    finalizado = evento_finalizado(evento)
    cerrado_tiempo = inscripciones_cerradas_por_tiempo(evento)

    guild = _bot.get_guild(evento["guild_id"]) if _bot else None
    canal_voz_str = None

    if evento["location_channel_id"]:
        if guild and (canal := guild.get_channel(evento["location_channel_id"])):
            canal_voz_str = canal.mention
    elif evento["auto_voice"]:
        canal_voz = guild.get_channel(evento["auto_voice_channel_id"]) if guild and evento["auto_voice_channel_id"] else None
        canal_voz_str = canal_voz.mention if canal_voz else f"{ICON_NOTE} Canal de voz temporal (se crea al empezar)"

    creador_mention = f"<@{evento['creator_id']}>"
    descripcion_formatted = format_retro_embed_description(
        desc=evento["description"],
        creador_mention=creador_mention,
        start_time_iso=evento["start_time"],
        duracion=evento["duration_minutes"],
        canal_voz=canal_voz_str,
        close_before_minutes=evento["close_before_minutes"] or 0
    )

    prefix = f"{ICON_FINALIZADO} [FINALIZADO]" if finalizado else (f"{ICON_CERRADO} [INSCRIPCIONES CERRADAS]" if cerrado_tiempo else "►")
    embed = discord.Embed(
        title=f"{prefix} EVENTO: {evento['title'].upper()}",
        description=descripcion_formatted,
        color=discord.Color.dark_gray() if (finalizado or cerrado_tiempo) else COLOR_MONOCHROME,
    )

    for op in opciones[:MAX_CAMPOS_OPCIONES]:
        confirmados = [f"<@{i['user_id']}>" for i in inscripciones
                       if i["option_id"] == op["id"] and i["status"] == "confirmado"]
        reservas = [f"<@{i['user_id']}>" for i in inscripciones
                    if i["option_id"] == op["id"] and i["status"] == "espera"]

        campo_texto = format_inscritos_opcion(
            nombre_opcion=op["name"],
            confirmados=confirmados,
            reserva=reservas,
            max_slots=op["max_slots"]
        )
        embed.add_field(name="\u200b", value=_recortar(campo_texto), inline=False)

    if len(opciones) > MAX_CAMPOS_OPCIONES:
        embed.add_field(
            name="► …",
            value=f"*Hay {len(opciones) - MAX_CAMPOS_OPCIONES} opciones más. Usa `/exportar_evento` para verlas todas.*",
            inline=False,
        )

    if evento["image_url"]:
        embed.set_image(url=evento["image_url"])

    estado_footer = "Finalizado" if finalizado else ("Inscripciones cerradas" if cerrado_tiempo else "Inscripciones abiertas")
    embed.set_footer(text=f"ID del evento: #{evento_id} | {estado_footer}")
    return embed


def _roles_restringidos(conn, event_id: int):
    filas = conn.execute("SELECT role_id, tipo FROM evento_restricciones WHERE event_id = ?", (event_id,)).fetchall()
    permitidos = {f["role_id"] for f in filas if f["tipo"] == "permitido"}
    bloqueados = {f["role_id"] for f in filas if f["tipo"] == "bloqueado"}
    return permitidos, bloqueados


def _motivo_restriccion(conn, evento, usuario) -> str | None:
    permitidos, bloqueados = _roles_restringidos(conn, evento["id"])
    if not permitidos and not bloqueados:
        return None
    roles_usuario = {r.id for r in getattr(usuario, "roles", [])}
    if bloqueados & roles_usuario:
        return f"{ICON_ALERT} Tu rol tiene bloqueada la inscripción a este evento."
    if permitidos and not (permitidos & roles_usuario):
        return f"{ICON_ALERT} Este evento está reservado para roles específicos."
    return None


async def inscribirse(interaction: discord.Interaction, event_id: int, option_id: int):
    await interaction.response.defer(ephemeral=True)

    conn = conectar_db()
    try:
        evento = conn.execute("SELECT * FROM eventos WHERE id = ?", (event_id,)).fetchone()
        if not evento:
            msg = await interaction.followup.send(f"{ICON_ALERT} Este evento ya no existe.", ephemeral=True)
            await msg.delete(delay=3)
            return
        if evento_finalizado(evento):
            msg = await interaction.followup.send(f"{ICON_FINALIZADO} Este evento ya ha finalizado.", ephemeral=True)
            await msg.delete(delay=3)
            return
        if inscripciones_cerradas_por_tiempo(evento):
            msg = await interaction.followup.send(
                f"{ICON_ALERT} Las inscripciones para este evento se cerraron {evento['close_before_minutes']} minutos antes del inicio.",
                ephemeral=True
            )
            await msg.delete(delay=3)
            return

        if (motivo := _motivo_restriccion(conn, evento, interaction.user)):
            msg = await interaction.followup.send(motivo, ephemeral=True)
            await msg.delete(delay=3)
            return

        opcion = conn.execute(
            "SELECT * FROM opciones_inscripcion WHERE id = ? AND event_id = ?", (option_id, event_id)
        ).fetchone()
        if not opcion:
            msg = await interaction.followup.send(f"{ICON_ALERT} Esta opción ya no existe.", ephemeral=True)
            await msg.delete(delay=3)
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            if not evento["multiple_registrations"]:
                otra = conn.execute(
                    "SELECT option_id FROM inscripciones WHERE event_id = ? AND user_id = ?",
                    (event_id, interaction.user.id),
                ).fetchone()
                if otra:
                    conn.rollback()
                    mensaje = (f"{ICON_ALERT} Ya estás registrado en esta opción." if otra["option_id"] == option_id
                               else f"{ICON_ALERT} Este evento no permite inscripción múltiple. Cancela tu registro previo primero.")
                    msg = await interaction.followup.send(mensaje, ephemeral=True)
                    await msg.delete(delay=3)
                    return
            elif conn.execute(
                "SELECT 1 FROM inscripciones WHERE event_id = ? AND option_id = ? AND user_id = ?",
                (event_id, option_id, interaction.user.id),
            ).fetchone():
                conn.rollback()
                msg = await interaction.followup.send(f"{ICON_ALERT} Ya estás registrado en esta opción.", ephemeral=True)
                await msg.delete(delay=3)
                return

            confirmados = conn.execute(
                "SELECT COUNT(*) FROM inscripciones WHERE option_id = ? AND status = 'confirmado'", (option_id,)
            ).fetchone()[0]

            status, pos = "confirmado", 0
            if opcion["max_slots"] and confirmados >= opcion["max_slots"]:
                if not evento["allow_waitlist"]:
                    conn.rollback()
                    msg = await interaction.followup.send(
                        f"{ICON_ALERT} **{opcion['name']}** está completa y no se admite lista de espera.",
                        ephemeral=True,
                    )
                    await msg.delete(delay=3)
                    return
                status = "espera"
                pos = conn.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM inscripciones WHERE option_id = ? AND status = 'espera'",
                    (option_id,),
                ).fetchone()[0] + 1

            conn.execute(
                "INSERT INTO inscripciones (event_id, option_id, user_id, status, position, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, option_id, interaction.user.id, status, pos, a_utc_iso(ahora())),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        datos_evento = dict(evento)
        nombre_opcion = opcion["name"]
    finally:
        conn.close()

    msg = await interaction.followup.send(f"{ICON_BULLET} Inscripción actualizada: **{nombre_opcion}**.", ephemeral=True)
    await msg.delete(delay=3)

    try:
        hilo = await obtener_o_crear_hilo(interaction.guild, datos_evento)
        if hilo:
            texto = (f"[{ICON_CHECK}] {interaction.user.mention} se ha inscrito en **{nombre_opcion}**." if status == "confirmado"
                     else f"[{ICON_ALERT}] {interaction.user.mention} entró en reserva (#{pos}) para **{nombre_opcion}**.")
            msg_hilo = await hilo.send(texto)
            await msg_hilo.delete(delay=3)
    except discord.HTTPException as e:
        log.warning("No se pudo avisar en el hilo del evento %s: %s", event_id, e)

    await actualizar_evento_publicado(event_id)


async def cancelar_inscripcion(interaction: discord.Interaction, event_id: int):
    await interaction.response.defer(ephemeral=True)

    conn = conectar_db()
    try:
        evento = conn.execute("SELECT * FROM eventos WHERE id = ?", (event_id,)).fetchone()
        if evento and evento_finalizado(evento):
            msg = await interaction.followup.send(f"{ICON_FINALIZADO} El evento ya ha finalizado.", ephemeral=True)
            await msg.delete(delay=3)
            return

        registros = conn.execute(
            "SELECT option_id, status FROM inscripciones WHERE event_id = ? AND user_id = ?",
            (event_id, interaction.user.id),
        ).fetchall()
        if not registros:
            msg = await interaction.followup.send(f"{ICON_ALERT} No estás inscrito en ninguna opción.", ephemeral=True)
            await msg.delete(delay=3)
            return

        conn.execute("DELETE FROM inscripciones WHERE event_id = ? AND user_id = ?", (event_id, interaction.user.id))
        conn.commit()

        liberadas = [r["option_id"] for r in registros if r["status"] == "confirmado"]
        datos_evento = dict(evento) if evento else None
    finally:
        conn.close()

    msg = await interaction.followup.send(f"{ICON_BULLET} Has cancelado tu inscripción.", ephemeral=True)
    await msg.delete(delay=3)

    for option_id in liberadas:
        await promover_lista_espera(_bot, event_id, option_id)

    if datos_evento:
        try:
            hilo = await obtener_o_crear_hilo(interaction.guild, datos_evento)
            if hilo:
                msg_hilo = await hilo.send(f"[{ICON_CROSS}] {interaction.user.mention} canceló su inscripción.")
                await msg_hilo.delete(delay=3)
        except discord.HTTPException as e:
            log.warning("No se pudo avisar en el hilo del evento %s: %s", event_id, e)

    await actualizar_evento_publicado(event_id)


class BotonInscripcionDinamico(discord.ui.Button):
    def __init__(self, option_id: int, label: str, event_id: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"evento_{event_id}_opt_{option_id}"
        )
        self.evento_id = event_id
        self.option_id = option_id

    async def callback(self, interaction: discord.Interaction):
        await inscribirse(interaction, self.evento_id, self.option_id)


class EventoView(discord.ui.View):
    def __init__(self, evento_id: int):
        super().__init__(timeout=None)
        self.evento_id = evento_id

        conn = conectar_db()
        try:
            opciones = conn.execute(
                "SELECT * FROM opciones_inscripcion WHERE event_id = ? ORDER BY id LIMIT 20", 
                (evento_id,)
            ).fetchall()
        finally:
            conn.close()

        for op in opciones:
            self.add_item(BotonInscripcionDinamico(
                option_id=op["id"], 
                label=op["name"][:80], 
                event_id=evento_id
            ))

        btn_cancelar = discord.ui.Button(
            label="X Cancelar", 
            style=discord.ButtonStyle.secondary,
            custom_id=f"evento_{evento_id}_cancelar",
            row=4
        )

        async def canc_cb(interaction: discord.Interaction):
            await cancelar_inscripcion(interaction, self.evento_id)

        btn_cancelar.callback = canc_cb
        self.add_item(btn_cancelar)

        btn_feedback = discord.ui.Button(
            label="★ Valorar", 
            style=discord.ButtonStyle.secondary,
            custom_id=f"evento_{evento_id}_feedback",
            row=4
        )

        async def fb_cb(interaction: discord.Interaction):
            await interaction.response.send_modal(FeedbackModal(self.evento_id))

        btn_feedback.callback = fb_cb
        self.add_item(btn_feedback)

        btn_editar = discord.ui.Button(
            label="Editar", 
            style=discord.ButtonStyle.secondary,
            custom_id=f"evento_{evento_id}_editar",
            row=4
        )

        async def edit_cb(interaction: discord.Interaction):
            """Punto 6: solo el creador del evento puede abrir el asistente de edición."""
            conn = conectar_db()
            try:
                ev = conn.execute(
                    "SELECT creator_id FROM eventos WHERE id = ?", (self.evento_id,)
                ).fetchone()
            finally:
                conn.close()
            if not ev:
                return await interaction.response.send_message(
                    f"{ICON_ALERT} Este evento ya no existe.", ephemeral=True
                )
            if interaction.user.id != ev["creator_id"]:
                return await interaction.response.send_message(
                    f"{ICON_ALERT} Solo el organizador del evento puede editarlo.", ephemeral=True
                )
            from modulo_edicion import iniciar_edicion_evento
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("► Asistente de edición enviado por DM.", ephemeral=True)
            asyncio.create_task(iniciar_edicion_evento(_bot, self.evento_id, interaction))

        btn_editar.callback = edit_cb
        self.add_item(btn_editar)


async def publicar_evento(evento_id: int, canal: discord.abc.Messageable, menciones: str | None = None):
    """Publica el anuncio del evento, abre su hilo y registra la vista persistente."""
    mensaje = await canal.send(
        content=menciones,
        embed=crear_embed_publicado(evento_id),
        view=EventoView(evento_id),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )

    conn = conectar_db()
    try:
        fila = conn.execute("SELECT title FROM eventos WHERE id = ?", (evento_id,)).fetchone()
    finally:
        conn.close()

    hilo = None
    try:
        hilo = await mensaje.create_thread(name=(fila["title"] if fila else f"Evento {evento_id}")[:100])
    except discord.HTTPException as e:
        log.warning("No se pudo crear el hilo del evento %s: %s", evento_id, e)

    conn = conectar_db()
    try:
        conn.execute(
            "UPDATE eventos SET message_id = ?, thread_id = ? WHERE id = ?",
            (mensaje.id, hilo.id if hilo else None, evento_id),
        )
        conn.commit()
    finally:
        conn.close()

    if _bot:
        _bot.add_view(EventoView(evento_id))
    return mensaje, hilo


async def actualizar_evento_publicado(event_id: int):
    conn = conectar_db()
    try:
        evento = conn.execute("SELECT * FROM eventos WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()

    if not evento or not evento["message_id"] or not evento["channel_id"] or not _bot:
        return
    guild = _bot.get_guild(evento["guild_id"])
    if not guild or not (canal := guild.get_channel(evento["channel_id"])):
        return

    try:
        mensaje = await canal.fetch_message(evento["message_id"])
        await mensaje.edit(embed=crear_embed_publicado(event_id), view=EventoView(event_id))
    except discord.HTTPException as e:
        log.warning("No se pudo actualizar el mensaje del evento %s: %s", event_id, e)


async def promover_lista_espera(bot, event_id: int, option_id: int):
    """Sube al primero de la reserva y reordena el resto."""
    conn = conectar_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        siguiente = conn.execute(
            "SELECT * FROM inscripciones WHERE event_id = ? AND option_id = ? AND status = 'espera'"
            " ORDER BY position ASC LIMIT 1",
            (event_id, option_id),
        ).fetchone()
        if not siguiente:
            conn.rollback()
            return

        conn.execute("UPDATE inscripciones SET status = 'confirmado', position = 0 WHERE id = ?", (siguiente["id"],))
        restantes = conn.execute(
            "SELECT id FROM inscripciones WHERE event_id = ? AND option_id = ? AND status = 'espera'"
            " ORDER BY position ASC",
            (event_id, option_id),
        ).fetchall()
        for idx, r in enumerate(restantes, start=1):
            conn.execute("UPDATE inscripciones SET position = ? WHERE id = ?", (idx, r["id"]))
        conn.commit()

        evento = conn.execute("SELECT * FROM eventos WHERE id = ?", (event_id,)).fetchone()
        opcion = conn.execute("SELECT name FROM opciones_inscripcion WHERE id = ?", (option_id,)).fetchone()
        promovido = siguiente["user_id"]
        datos_evento = dict(evento) if evento else None
    finally:
        conn.close()

    if not datos_evento or not opcion or not bot:
        return

    guild = bot.get_guild(datos_evento["guild_id"])
    if guild:
        try:
            hilo = await obtener_o_crear_hilo(guild, datos_evento)
            if hilo:
                await hilo.send(
                    f"[{ICON_CHECK}] <@{promovido}> ha sido promovido/a de la reserva a **CONFIRMADO** "
                    f"para **{opcion['name']}**."
                )
        except discord.HTTPException as e:
            log.warning("No se pudo avisar de la promoción en el evento %s: %s", event_id, e)

    try:
        usuario = await bot.fetch_user(promovido)
        desc = (
            f"► Has sido promovido de la lista de espera a **CONFIRMADO**.\n\n"
            f"├─ Evento: **{datos_evento['title']}**\n"
            f"└─ Opción: **{opcion['name']}**"
        )
        embed = discord.Embed(
            title="PLAZA CONFIRMADA EN EVENTO",
            description=desc,
            color=COLOR_MONOCHROME,
        )
        await usuario.send(embed=embed)
    except discord.HTTPException:
        pass