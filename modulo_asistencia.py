"""Control automatizado de asistencia y feedback por DM (punto 2).

Flujo: al terminar la duración de un evento, el bot pregunta al creador
(Y/N) por DM si la misión concluyó. Si es afirmativo, se toma la asistencia
uno a uno de los confirmados, se marca quién asistió y después solo los
asistentes reciben la invitación por DM a valorar el evento.
"""
import asyncio
import logging

import discord

from database import (
    debe_enviar_feedback,
    guardar_feedback,
    marcar_asistencia_revisada,
    obtener_inscritos_evento,
    registrar_asistencia,
)
from formatters import (
    COLOR_MONOCHROME,
    ICON_ALERT,
    ICON_BULLET,
    ICON_CHECK,
    generar_estrellas_ascii,
)

log = logging.getLogger(__name__)

TIMEOUT_PASO = 180
CONTROLES_ACTIVOS = set()  # Creadores con un control de asistencia en curso


async def _enviar_embed(usuario: discord.User, titulo: str, descripcion: str):
    embed = discord.Embed(title=titulo, description=descripcion, color=COLOR_MONOCHROME)
    try:
        await usuario.send(embed=embed)
    except discord.HTTPException:
        raise


def _chequeo_dm(author_id: int):
    def check(m):
        return m.author.id == author_id and m.guild is None
    return check


def _si_respuesta(texto: str) -> bool:
    return texto.strip().lower() in ("y", "s", "si", "yes")


async def desencadenar_asistencia(bot: discord.Client, evento):
    """Pregunta al creador por DM si el evento concluyó (se lanza desde main)."""
    if evento["attendance_checked"] or evento["creator_id"] in CONTROLES_ACTIVOS:
        return

    marcar_asistencia_revisada(evento["id"])
    creator_id = evento["creator_id"]
    CONTROLES_ACTIVOS.add(creator_id)

    try:
        creador = await bot.fetch_user(creator_id)
        await _enviar_embed(
            creador,
            "§ CONTROL DE ASISTENCIA §",
            (
                f"{ICON_BULLET} Evento: **{evento['title']}**\n\n"
                f"{ICON_BULLET} La duración programada ha terminado.\n"
                f"{ICON_BULLET} ¿Ha concluido la misión?\n\n"
                f"{ICON_BULLET} [Y] Sí, tomar asistencia\n"
                f"{ICON_BULLET} [N] No, aún en curso"
            ),
        )
    except discord.HTTPException:
        log.warning("No se pudo contactar al creador %s para el control de asistencia", creator_id)
        CONTROLES_ACTIVOS.discard(creator_id)
        return

    try:
        msg = await bot.wait_for("message", check=_chequeo_dm(creator_id), timeout=TIMEOUT_PASO)
    except asyncio.TimeoutError:
        CONTROLES_ACTIVOS.discard(creator_id)
        return

    if not _si_respuesta(msg.content):
        try:
            await creador.send(f"{ICON_BULLET} Entendido. Se omite el control de asistencia de este evento.")
        except discord.HTTPException:
            pass
        CONTROLES_ACTIVOS.discard(creator_id)
        return

    try:
        await tomar_asistencia(bot, evento)
    finally:
        CONTROLES_ACTIVOS.discard(creator_id)


async def tomar_asistencia(bot: discord.Client, evento):
    """Toma la asistencia uno a uno de los confirmados por DM y lanza las valoraciones."""
    creator_id = evento["creator_id"]
    try:
        creador = await bot.fetch_user(creator_id)
    except discord.HTTPException:
        log.warning("No se pudo contactar al creador %s para tomar asistencia", creator_id)
        return

    inscritos = obtener_inscritos_evento(evento["id"])
    if not inscritos:
        try:
            await creador.send(f"{ICON_BULLET} No hay confirmados en este evento. Control cerrado.")
        except discord.HTTPException:
            pass
        return

    total = len(inscritos)
    asistieron = []
    abortado = False

    for idx, fila in enumerate(inscritos, start=1):
        while True:
            try:
                await _enviar_embed(
                    creador,
                    f"§ ASISTENCIA: {evento['title'][:60]} § ({idx}/{total})",
                    (
                        f"{ICON_BULLET} ¿Asistió <@{fila['user_id']}>?\n"
                        f"{ICON_BULLET} Opción: **{fila['opcion']}**\n\n"
                        f"{ICON_BULLET} [Y] Sí — [N] No — [X] Finalizar"
                    ),
                )
                msg = await bot.wait_for("message", check=_chequeo_dm(creator_id), timeout=TIMEOUT_PASO)
            except asyncio.TimeoutError:
                abortado = True
                break
            except discord.HTTPException:
                log.warning("No se pudo enviar la consulta de asistencia al creador %s", creator_id)
                abortado = True
                break

            r = msg.content.strip().lower()
            if r in ("x", "fin", "finalizar", "exit", "salir"):
                abortado = True
                break
            if r in ("y", "s", "si", "yes"):
                registrar_asistencia(evento["id"], fila["user_id"], True)
                asistieron.append(fila["user_id"])
                break
            if r in ("n", "no", "none"):
                registrar_asistencia(evento["id"], fila["user_id"], False)
                break

        if abortado:
            break

    resumen = (
        f"{ICON_BULLET} **RESUMEN DE ASISTENCIA**\n"
        f"{ICON_BULLET} Asistieron: **{len(asistieron)}/{total}**\n"
    )
    for uid in asistieron:
        resumen += f"{ICON_BULLET} {ICON_CHECK} <@{uid}>\n"
    try:
        await creador.send(resumen)
    except discord.HTTPException:
        pass

    for uid in asistieron:
        asyncio.create_task(invitar_valoracion(bot, evento, uid))


async def invitar_valoracion(bot: discord.Client, evento, user_id: int):
    """Invita por DM a un asistente a valorar el evento (punto 2 y 3)."""
    if user_id in CONTROLES_ACTIVOS:
        return

    guild = bot.get_guild(evento["guild_id"])
    roles_usuario = set()
    if guild:
        miembro = guild.get_member(user_id)
        if miembro:
            roles_usuario = {r.id for r in miembro.roles}

    if not debe_enviar_feedback(user_id, roles_usuario):
        return

    try:
        usuario = await bot.fetch_user(user_id)
    except discord.HTTPException:
        return

    try:
        await _enviar_embed(
            usuario,
            "§ VALORACIÓN DEL EVENTO §",
            (
                f"{ICON_BULLET} Has asistido a **{evento['title']}**.\n\n"
                f"{ICON_BULLET} ¿Deseas valorar la misión?\n"
                f"{ICON_BULLET} [Y] Sí — [N] No"
            ),
        )
    except discord.HTTPException:
        return

    try:
        msg = await bot.wait_for("message", check=_chequeo_dm(user_id), timeout=TIMEOUT_PASO)
        if not _si_respuesta(msg.content):
            return
        await usuario.send(f"{ICON_BULLET} Introduce la puntuación (1 a 5):")
        punt = await bot.wait_for("message", check=_chequeo_dm(user_id), timeout=TIMEOUT_PASO)
        rating = punt.content.strip()
        if not rating.isdigit() or not 1 <= int(rating) <= 5:
            await usuario.send(f"{ICON_ALERT} Puntuación no válida. Se cancela la valoración.")
            return
        await usuario.send(
            f"{ICON_BULLET} Introduce un comentario o reseña (opcional).\n"
            f"{ICON_BULLET} Escribe 'ninguno' para omitirlo:"
        )
        com = await bot.wait_for("message", check=_chequeo_dm(user_id), timeout=TIMEOUT_PASO)
        comentario = com.content.strip()
        if comentario.lower() in ("ninguno", "no", "none", "sin comentario"):
            comentario = ""
        guardar_feedback(evento["id"], user_id, int(rating), comentario)
        await usuario.send(
            f"{ICON_CHECK} ¡Gracias por tu valoración! {generar_estrellas_ascii(int(rating))}"
        )
    except asyncio.TimeoutError:
        return
    except discord.HTTPException:
        return