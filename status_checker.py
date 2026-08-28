import os
import urllib.request
import urllib.error
import json
import logging
import discord
from discord import app_commands
from discord.ext import commands
from formatters import COLOR_BLANCO

log = logging.getLogger(__name__)

def obtener_estado_sistema():
    """Consulta la ruta /status del servidor local Flask (127.0.0.1)."""
    port = os.getenv("PORT", "8000")
    local_url = f"http://127.0.0.1:{port}/status"

    try:
        req = urllib.request.Request(local_url, headers={'User-Agent': 'NexusBot-LocalCheck'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                return json.loads(response.read().decode('utf-8'))
            else:
                log.error(f"Error HTTP al consultar status local: {response.status}")
                return None
    except urllib.error.URLError as e:
        log.error(f"No se pudo conectar al endpoint local de status ({local_url}): {e}")
        return None
    except Exception as e:
        log.error(f"Excepción inesperada al obtener status: {e}")
        return None


async def setup(bot: commands.Bot):
    """Punto de entrada para load_extension."""
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