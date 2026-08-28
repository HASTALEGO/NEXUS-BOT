import time
import aiohttp
import discord
from discord.ext import commands

# URL obtenida de tus registros de Render
RENDER_URL = "https://nexus-bot-0i5m.onrender.com"

class StatusChecker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(
        name="status", 
        description="Comprueba si el servidor web en Render está activo."
    )
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=10) as response:
                    latency = round((time.time() - start_time) * 1000)
                    
                    if response.status == 200:
                        embed = discord.Embed(
                            title="🟢 Servidor Online",
                            description=f"El servidor respondió correctamente desde Render.",
                            color=discord.Color.green()
                        )
                    else:
                        embed = discord.Embed(
                            title="⚠️ Servidor con problemas",
                            description=f"El servidor respondió con código HTTP: `{response.status}`.",
                            color=discord.Color.gold()
                        )
                        
                    embed.add_field(name="URL", value=RENDER_URL, inline=False)
                    embed.add_field(name="Latencia Web", value=f"{latency} ms", inline=True)
                    embed.add_field(name="Latencia Bot", value=f"{round(self.bot.latency * 1000)} ms", inline=True)
                    
                    await interaction.followup.send(embed=embed)
                    
        except Exception as e:
            embed = discord.Embed(
                title="🔴 Servidor Offline",
                description=f"No se pudo conectar al enlace web.\n**Error:** `{str(e)}`",
                color=discord.Color.red()
            )
            embed.add_field(name="URL", value=RENDER_URL, inline=False)
            await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(StatusChecker(bot))