import calendar
import logging
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from formatters import COLOR_BLANCO
from webserver import run  # o keep_alive() según la función que arranca Flask

# Inicia el servidor Flask en un hilo separado antes de correr el bot
threading.Thread(target=run, daemon=True).start()

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import dotenv_values, load_dotenv
from flask import Flask

# Integración del banner saturado estilizado
from creador_eventos import configurar_creador_eventos
from database import conectar_db, inicializar_db
from formatters import a_utc_iso, ahora, timestamp_discord
from modulo_calendario import configurar_modulo_calendario
from modulo_valoraciones import registrar_comandos_valoraciones
from modulos_eventos import eliminar_voz_temporal, generar_csv_evento, gestionar_voz_temporal
from vistas_eventos import (
    EventoView, actualizar_evento_publicado, fin_evento, inicializar_vistas,
    inicio_evento, obtener_o_crear_hilo, publicar_evento, trocear_menciones,
)

app_web = Flask('')

@app_web.route('/')
def home():
    return "NEXUS BOT está activo."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot_eventos")

RUTA_ENV = Path(__file__).resolve().with_name(".env")
load_dotenv(RUTA_ENV)

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip().strip("\"'")
if not TOKEN:
    if RUTA_ENV.exists():
        claves = sorted(dotenv_values(RUTA_ENV))
        log.error(
            "El archivo %s no define DISCORD_TOKEN. Variables encontradas: %s",
            RUTA_ENV, ", ".join(claves) or "(ninguna)",
        )
    else:
        log.error("No existe %s. Crealo con la linea: DISCORD_TOKEN=tu_token", RUTA_ENV)
    sys.exit(1)

GUILD_ID = int((os.getenv("GUILD_ID") or "0").strip() or 0)
GUILD_OBJECT = discord.Object(id=GUILD_ID) if GUILD_ID > 0 else None

FRECUENCIAS_REPETIBLES = ("Diariamente", "Semanalmente", "Mensualmente")


def calcular_siguiente_ocurrencia(fecha: datetime, frecuencia: str):
    if frecuencia == "Diariamente":
        return fecha + timedelta(days=1)
    if frecuencia == "Semanalmente":
        return fecha + timedelta(days=7)
    if frecuencia == "Mensualmente":
        y, m = fecha.year, fecha.month + 1
        if m > 12:
            m, y = 1, y + 1
        return fecha.replace(year=y, month=m, day=min(fecha.day, calendar.monthrange(y, m)[1]))
    return None


def clonar_evento(evento, nuevo_inicio: datetime) -> int:
    """Duplica un evento recurrente (opciones, menciones, restricciones y recordatorios)."""
    conn = conectar_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("""
            INSERT INTO eventos (guild_id, channel_id, creator_id, title, description, start_time,
                                 duration_minutes, frequency, color, location_channel_id, auto_voice,
                                 image_url, multiple_registrations, allow_waitlist, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            evento["guild_id"], evento["channel_id"], evento["creator_id"], evento["title"],
            evento["description"], a_utc_iso(nuevo_inicio), evento["duration_minutes"],
            evento["frequency"], evento["color"], evento["location_channel_id"], evento["auto_voice"],
            evento["image_url"], evento["multiple_registrations"], evento["allow_waitlist"],
            a_utc_iso(ahora()),
        ))
        nuevo_id = cursor.lastrowid

        conn.execute("""
            INSERT INTO opciones_inscripcion (event_id, name, emoji, max_slots)
            SELECT ?, name, emoji, max_slots FROM opciones_inscripcion WHERE event_id = ?
        """, (nuevo_id, evento["id"]))
        conn.execute("""
            INSERT INTO evento_menciones (event_id, role_id)
            SELECT ?, role_id FROM evento_menciones WHERE event_id = ?
        """, (nuevo_id, evento["id"]))
        conn.execute("""
            INSERT INTO evento_restricciones (event_id, role_id, tipo)
            SELECT ?, role_id, tipo FROM evento_restricciones WHERE event_id = ?
        """, (nuevo_id, evento["id"]))
        conn.execute("""
            INSERT INTO recordatorios (event_id, minutes_before, sent)
            SELECT ?, minutes_before, 0 FROM recordatorios WHERE event_id = ?
        """, (nuevo_id, evento["id"]))
        conn.execute("UPDATE eventos SET next_created = 1 WHERE id = ?", (evento["id"],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return nuevo_id


async def enviar_recordatorios(conn, ahora_actual):
    pendientes = conn.execute("""
        SELECT r.id AS reminder_id, r.minutes_before, e.id AS id, e.guild_id, e.channel_id,
               e.message_id, e.thread_id, e.title, e.start_time, e.duration_minutes
        FROM recordatorios r JOIN eventos e ON r.event_id = e.id
        WHERE r.sent = 0
    """).fetchall()

    for rec in pendientes:
        inicio = inicio_evento(rec)
        if not inicio or ahora_actual < inicio - timedelta(minutes=rec["minutes_before"]):
            continue
        try:
            guild = bot.get_guild(rec["guild_id"])
            if guild and ahora_actual < inicio:
                hilo = await obtener_o_crear_hilo(guild, rec)
                if hilo:
                    usuarios = conn.execute(
                        "SELECT DISTINCT user_id FROM inscripciones WHERE event_id = ? AND status = 'confirmado'",
                        (rec["id"],),
                    ).fetchall()
                    await hilo.send(
                        f"☼ **RECORDATORIO DE MISIÓN**\n"
                        f"► La misión **{rec['title']}** empieza <t:{timestamp_discord(inicio)}:R>"
                    )
                    for bloque in trocear_menciones([f"<@{u['user_id']}>" for u in usuarios]):
                        await hilo.send(bloque, allowed_mentions=discord.AllowedMentions(users=True))
        except Exception:
            log.exception("Fallo al enviar el recordatorio %s", rec["reminder_id"])
        finally:
            conn.execute("UPDATE recordatorios SET sent = 1 WHERE id = ?", (rec["reminder_id"],))
            conn.commit()


async def gestionar_ciclo_de_vida(conn, ahora_actual):
    eventos = conn.execute("""
        SELECT * FROM eventos
        WHERE start_time IS NOT NULL AND (next_created = 0 OR auto_voice_channel_id IS NOT NULL)
    """).fetchall()

    for evento in eventos:
        inicio, fin = inicio_evento(evento), fin_evento(evento)
        if not inicio:
            continue
        try:
            if inicio <= ahora_actual < fin and evento["auto_voice"] and not evento["auto_voice_channel_id"]:
                if await gestionar_voz_temporal(bot, evento):
                    await actualizar_evento_publicado(evento["id"])

            if ahora_actual >= fin:
                if evento["auto_voice_channel_id"]:
                    await eliminar_voz_temporal(bot, evento)
                if not evento["next_created"]:
                    await cerrar_evento(evento, ahora_actual)
        except Exception:
            log.exception("Fallo en el ciclo de vida del evento %s", evento["id"])


async def cerrar_evento(evento, ahora_actual):
    """Marca el anuncio como finalizado y, si es recurrente, publica la siguiente ocurrencia."""
    await actualizar_evento_publicado(evento["id"])

    if evento["frequency"] not in FRECUENCIAS_REPETIBLES:
        conn = conectar_db()
        try:
            conn.execute("UPDATE eventos SET next_created = 1 WHERE id = ?", (evento["id"],))
            conn.commit()
        finally:
            conn.close()
        return

    siguiente = calcular_siguiente_ocurrencia(inicio_evento(evento), evento["frequency"])
    while siguiente and siguiente <= ahora_actual:
        siguiente = calcular_siguiente_ocurrencia(siguiente, evento["frequency"])
    if not siguiente:
        return

    nuevo_id = clonar_evento(evento, siguiente)
    guild = bot.get_guild(evento["guild_id"])
    canal = guild.get_channel(evento["channel_id"]) if guild else None
    if not canal:
        log.warning("Evento recurrente %s clonado como %s pero sin canal donde publicarlo", evento["id"], nuevo_id)
        return

    conn = conectar_db()
    try:
        roles = conn.execute("SELECT role_id FROM evento_menciones WHERE event_id = ?", (nuevo_id,)).fetchall()
    finally:
        conn.close()
    menciones = " ".join(f"<@&{r['role_id']}>" for r in roles) or None

    await publicar_evento(nuevo_id, canal, menciones)
    log.info("Evento recurrente %s replicado como %s", evento["id"], nuevo_id)


@tasks.loop(seconds=30)
async def tareas_eventos():
    ahora_actual = ahora()
    conn = conectar_db()
    try:
        await enviar_recordatorios(conn, ahora_actual)
        await gestionar_ciclo_de_vida(conn, ahora_actual)
    finally:
        conn.close()


@tareas_eventos.error
async def error_tareas(error: Exception):
    log.exception("El loop de tareas fallo, se reinicia", exc_info=error)
    if not tareas_eventos.is_running():
        tareas_eventos.restart()


@tareas_eventos.before_loop
async def antes_de_tareas():
    await bot.wait_until_ready()


class BotEventos(commands.Bot):
    async def setup_hook(self):
        # 1. Imprimir banner ASCII masivo

        print("DATABASE", "Inicializando base de datos SQLite...", "INFO")
        inicializar_db()

        print("VIEWS", "Cargando vistas y persistencia de botones...", "INFO")
        inicializar_vistas(self)

        print("STATUS", "Cargando extensión status_checker...", "INFO")
       # await self.load_extension("status_checker")

        print("MODULES", "Registrando eventos, calendario y valoraciones...", "INFO")
        configurar_creador_eventos(self)
        configurar_modulo_calendario(self)
        registrar_comandos_valoraciones(self)

        conn = conectar_db()
        try:
            eventos = conn.execute("SELECT id FROM eventos WHERE message_id IS NOT NULL").fetchall()
        finally:
            conn.close()
        for ev in eventos:
            self.add_view(EventoView(ev["id"]))

        # Sincronización formateada
        if GUILD_OBJECT:
            self.tree.copy_global_to(guild=GUILD_OBJECT)
            comandos = await self.tree.sync(guild=GUILD_OBJECT)
            print(len(comandos), f"GUILD: {GUILD_ID}")
        else:
            comandos = await self.tree.sync()
            print(len(comandos), "GLOBAL (TODOS LOS SERVIDORES)")

        print("TASKS", "Desplegando loop de tareas en segundo plano...", "SYNC")
        tareas_eventos.start()


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = BotEventos(command_prefix="!", intents=intents)

from status_checker import obtener_estado_sistema

@bot.tree.command(name="status", description="Muestra el estado del sistema y métricas del servidor.")
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    datos = obtener_estado_sistema()

    if not datos:
        return await interaction.followup.send(
            "‼ No se pudo obtener el estado del servidor local en este momento.", 
            ephemeral=True
        )

    embed = discord.Embed(
        title="📊 ESTADO DEL SISTEMA — NEXUS BOT",
        color=COLOR_BLANCO
    )
    
    for clave, valor in datos.items():
        embed.add_field(
            name=f"► {clave.replace('_', ' ').capitalize()}", 
            value=f"`{valor}`", 
            inline=True
        )

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="exportar_evento", description="Exporta los datos de un evento a archivo CSV.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def cmd_exportar_evento(interaction: discord.Interaction, evento_id: int):
    conn = conectar_db()
    try:
        existe = conn.execute(
            "SELECT 1 FROM eventos WHERE id = ? AND guild_id = ?", (evento_id, interaction.guild_id)
        ).fetchone()
    finally:
        conn.close()
    if not existe:
        return await interaction.response.send_message("‼ Ese evento no existe en este servidor.", ephemeral=True)

    archivo = generar_csv_evento(evento_id)
    await interaction.response.send_message(f"► Exportación de evento #{evento_id}:", file=archivo, ephemeral=True)


@bot.tree.command(name="marcar_asistencia", description="Registra si un usuario asistio a un evento.")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def cmd_marcar_asistencia(interaction: discord.Interaction, evento_id: int, usuario: discord.Member, asistio: bool):
    conn = conectar_db()
    try:
        existe = conn.execute(
            "SELECT 1 FROM eventos WHERE id = ? AND guild_id = ?", (evento_id, interaction.guild_id)
        ).fetchone()
        if not existe:
            return await interaction.response.send_message("‼ Ese evento no existe en este servidor.", ephemeral=True)

        conn.execute("""
            INSERT INTO asistencia (event_id, user_id, attended, registered_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_id, user_id) DO UPDATE SET
                attended = excluded.attended, registered_at = excluded.registered_at
        """, (evento_id, usuario.id, 1 if asistio else 0, a_utc_iso(ahora())))
        conn.commit()
    finally:
        conn.close()

    estado = "asistió" if asistio else "faltó"
    await interaction.response.send_message(
        f"► Registrado: {usuario.mention} {estado} al evento #{evento_id}.", ephemeral=True
    )


@bot.event
async def on_ready():
    print("CORE_ONLINE", f"NEXUS BOT OPERATIVO COMO: {bot.user}", "OK")

keep_alive()

if __name__ == "__main__":
    bot.run(TOKEN)
