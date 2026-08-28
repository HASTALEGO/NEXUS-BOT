"""Perfil de asistencia, reseñas del organizador y preferencias de feedback (puntos 3, 4 y 8)."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    conectar_db,
    configurar_rol_valoracion,
    listar_roles_valoracion,
    obtener_detalles_feedback_evento,
    obtener_perfil_usuario,
    obtener_resumen_valoraciones_creador,
    obtener_zona_horaria,
    remover_rol_valoracion,
    setear_preferencia_feedback,
    setear_zona_horaria,
)
from formatters import COLOR_BLANCO, format_perfil_asistencia, generar_estrellas_ascii

log = logging.getLogger(__name__)

ZONAS_HORARIAS = [
    "Europe/Madrid", "Europe/London", "Atlantic/Canary",
    "America/Caracas", "America/Bogota", "America/Mexico_City",
    "America/Argentina/Buenos_Aires", "America/Lima", "America/Santiago",
    "America/Sao_Paulo", "America/Panama", "America/New_York",
    "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Havana", "America/Honduras", "America/Guatemala",
    "America/El_Salvador", "America/Costa_Rica", "America/La_Paz",
    "America/Guayaquil", "America/Asuncion", "America/Montevideo",
    "America/Managua",
]


def configurar_modulo_perfil(bot: commands.Bot):
    @bot.tree.command(name="mi_perfil", description="Muestra tus estadísticas de asistencia.")
    @app_commands.guild_only()
    async def cmd_mi_perfil(interaction: discord.Interaction):
        stats = obtener_perfil_usuario(interaction.user.id)
        if stats["total"] == 0:
            return await interaction.response.send_message(
                "◄ ◄ ◄ NOTA: no tienes ninguna asistencia registrada todavía. "
                "Cuando completes una misión y el organizador marque tu asistencia, aquí aparecerá tu fichaje. ► ► ►",
                ephemeral=True,
            )
        embed = discord.Embed(
            title="§ PERFIL DE ASISTENCIA §",
            description=format_perfil_asistencia(interaction.user.mention, stats),
            color=COLOR_BLANCO,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="mis_valoraciones", description="Muestra las valoraciones de tus eventos.")
    @app_commands.describe(evento_id="ID del evento (opcional): reseñas individuales de ese evento")
    @app_commands.guild_only()
    async def cmd_mis_valoraciones(interaction: discord.Interaction, evento_id: int = 0):
        await interaction.response.defer(ephemeral=True)

        conn = conectar_db()
        try:
            if evento_id:
                evento = conn.execute(
                    "SELECT id, title FROM eventos WHERE id = ? AND creator_id = ? AND guild_id = ?",
                    (evento_id, interaction.user.id, interaction.guild_id),
                ).fetchone()
                if not evento:
                    return await interaction.followup.send(
                        "‼ Ese evento no existe o no es tuyo.", ephemeral=True
                    )
                detalles = obtener_detalles_feedback_evento(evento_id)
                if not detalles:
                    return await interaction.followup.send(
                        f"► El evento **{evento['title']}** no tiene valoraciones todavía.", ephemeral=True
                    )
                embed = discord.Embed(
                    title=f"§ VALORACIONES: {evento['title']} §",
                    color=COLOR_BLANCO,
                )
                for d in detalles[:25]:
                    embed.add_field(
                        name=generar_estrellas_ascii(d["rating"]),
                        value=f"<@{d['user_id']}> — {(d['comment'] or 'Sin comentario')[:1024]}",
                        inline=False,
                    )
                return await interaction.followup.send(embed=embed, ephemeral=True)

            eventos = obtener_resumen_valoraciones_creador(interaction.user.id)
            if not eventos:
                return await interaction.followup.send(
                    "► Aún no tienes eventos con valoraciones.", ephemeral=True
                )
            embed = discord.Embed(
                title="§ MIS VALORACIONES §",
                description="Valoraciones acumuladas por cada uno de tus eventos.",
                color=COLOR_BLANCO,
            )
            for e in eventos:
                media = e["media"] or 0
                embed.add_field(
                    name=f"#{e['id']} {e['title'][:60]}",
                    value=f"{generar_estrellas_ascii(media)} **{media:.2f}/5**  ({e['total_reviews']} valoraciones)\nUsa `/mis_valoraciones evento_id:{e['id']}` para el detalle.",
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        finally:
            conn.close()

    @bot.tree.command(
        name="preferencias",
        description="Elige si quieres recibir DMs de valoraciones tras un evento.",
    )
    @app_commands.describe(recibir="✓ = recibir DMs de valoración, X = no recibir")
    @app_commands.choices(recibir=[
        app_commands.Choice(name="[√] Sí, quiero recibir DMs de valoración", value=1),
        app_commands.Choice(name="[X] No, no recibir DMs de valoración", value=0),
    ])
    async def cmd_preferencias(interaction: discord.Interaction, recibir: app_commands.Choice[int]):
        setear_preferencia_feedback(interaction.user.id, recibir.value == 1)
        estado = "activados" if recibir.value == 1 else "desactivados"
        await interaction.response.send_message(
            f"► DMs de valoraciones **{estado}** para <@{interaction.user.id}>.", ephemeral=True
        )

    @bot.tree.command(
        name="zona_horaria",
        description="Muestra o cambia tu zona horaria (para interpretar las fechas de los eventos).",
    )
    @app_commands.describe(zona="Zona horaria (opcional: déjalo vacío para ver la actual)")
    @app_commands.choices(zona=[app_commands.Choice(name=z, value=z) for z in ZONAS_HORARIAS])
    async def cmd_zona_horaria(interaction: discord.Interaction, zona: str = None):
        if zona:
            if setear_zona_horaria(interaction.user.id, zona):
                await interaction.response.send_message(
                    f"► Tu zona horaria ahora es **{zona}**. Las fechas que escribas se interpretarán en esa zona.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "‼ Zona horaria inválida. Usa un nombre IANA (p.ej. America/Caracas).", ephemeral=True
                )
        else:
            actual = obtener_zona_horaria(interaction.user.id)
            await interaction.response.send_message(
                f"► Tu zona horaria actual es **{actual}**.\n"
                f"► Úsala con un valor para cambiarla, p.ej. `/zona_horaria zona:America/Caracas`.",
                ephemeral=True,
            )

    @bot.tree.command(
        name="autorrol_valoracion",
        description="Configura el rol/autorrol que puede recibir DMs de valoraciones (Admin).",
    )
    @app_commands.describe(
        rol="Rol de valoraciones a configurar",
        activar="True = asignar rol, False = quitarlo",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def cmd_autorrol(interaction: discord.Interaction, rol: discord.Role, activar: bool):
        if activar:
            configurar_rol_valoracion(rol.id)
            mensaje = f"► Rol **@{rol.name}** añadido al autorrol de valoraciones."
        else:
            remover_rol_valoracion(rol.id)
            mensaje = f"► Rol **@{rol.name}** eliminado del autorrol de valoraciones."

        ids = listar_roles_valoracion()
        if ids:
            mensaje += "\n► Autorroles actuales: " + ", ".join(f"<@&{i}>" for i in ids)
        await interaction.response.send_message(mensaje, ephemeral=True)