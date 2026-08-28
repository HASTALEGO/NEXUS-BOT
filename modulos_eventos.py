import csv
import io
import logging

import discord

from database import conectar_db

log = logging.getLogger(__name__)


def generar_csv_evento(event_id: int) -> discord.File:
    conn = conectar_db()
    try:
        registros = conn.execute("""
            SELECT i.user_id, o.name AS opcion, i.status, i.position, i.created_at,
                   COALESCE(a.attended, -1) AS asistencia,
                   f.rating, f.comment
            FROM inscripciones i
            JOIN opciones_inscripcion o ON o.id = i.option_id
            LEFT JOIN asistencia a ON a.event_id = i.event_id AND a.user_id = i.user_id
            LEFT JOIN feedback f ON f.event_id = i.event_id AND f.user_id = i.user_id
            WHERE i.event_id = ?
            ORDER BY o.id, i.status, i.position
        """, (event_id,)).fetchall()
    finally:
        conn.close()

    salida = io.StringIO()
    writer = csv.writer(salida)
    writer.writerow([
        "ID_Usuario", "Opcion", "Estado", "Posicion_Reserva",
        "Fecha_Inscripcion", "Asistencia", "Valoracion", "Comentario",
    ])
    for r in registros:
        asistencia = "No marcado" if r["asistencia"] == -1 else ("Asistio" if r["asistencia"] == 1 else "Falto")
        writer.writerow([
            r["user_id"], r["opcion"], r["status"], r["position"], r["created_at"],
            asistencia, r["rating"] or "", r["comment"] or "",
        ])

    datos = io.BytesIO(salida.getvalue().encode("utf-8"))
    return discord.File(fp=datos, filename=f"evento_{event_id}_export.csv")


async def gestionar_voz_temporal(bot, evento):
    """Crea el canal de voz temporal cuando arranca un evento con voz automatica."""
    if not evento["auto_voice"] or evento["auto_voice_channel_id"]:
        return None

    guild = bot.get_guild(evento["guild_id"])
    if not guild:
        return None

    try:
        canal_voz = await guild.create_voice_channel(
            name=f"♪ Evento: {evento['title'][:25]} ♪",
            reason=f"Canal temporal para evento #{evento['id']}",
        )
    except discord.HTTPException as e:
        log.error("No se pudo crear el canal de voz del evento %s: %s", evento["id"], e)
        return None

    conn = conectar_db()
    try:
        conn.execute("UPDATE eventos SET auto_voice_channel_id = ? WHERE id = ?", (canal_voz.id, evento["id"]))
        conn.commit()
    finally:
        conn.close()
    return canal_voz


async def eliminar_voz_temporal(bot, evento):
    """Borra el canal temporal cuando el evento ya termino."""
    if not evento["auto_voice_channel_id"]:
        return

    guild = bot.get_guild(evento["guild_id"])
    canal = guild.get_channel(evento["auto_voice_channel_id"]) if guild else None
    if canal:
        try:
            await canal.delete(reason=f"Fin del evento #{evento['id']}")
        except discord.HTTPException as e:
            log.warning("No se pudo borrar el canal de voz del evento %s: %s", evento["id"], e)
            return

    conn = conectar_db()
    try:
        conn.execute("UPDATE eventos SET auto_voice_channel_id = NULL WHERE id = ?", (evento["id"],))
        conn.commit()
    finally:
        conn.close()
