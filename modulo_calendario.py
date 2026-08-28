from datetime import datetime
import calendar
import discord
from discord import app_commands
from discord.ext import commands

from database import conectar_db
from formatters import TIMEZONE, COLOR_BLANCO, a_utc_iso, ahora, desde_iso, nombre_mes, timestamp_discord

class CalendarioView(discord.ui.View):
    def __init__(self, anio: int, mes: int, user_id: int, guild_id: int):
        super().__init__(timeout=180)
        self.anio = anio
        self.mes = mes
        self.user_id = user_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("‼ Solo la persona que abrió el calendario puede cambiar de página.", ephemeral=True)
            return False
        return True

    def obtener_embed(self) -> discord.Embed:
        primer_dia_mes = datetime(self.anio, self.mes, 1, tzinfo=TIMEZONE)
        dias_en_mes = calendar.monthrange(self.anio, self.mes)[1]
        fin_dia_mes = datetime(self.anio, self.mes, dias_en_mes, 23, 59, 59, tzinfo=TIMEZONE)

        conn = conectar_db()
        try:
            eventos = conn.execute("""
                SELECT id, title, start_time, duration_minutes, location_channel_id, auto_voice
                FROM eventos
                WHERE guild_id = ? AND start_time >= ? AND start_time <= ?
                ORDER BY start_time ASC
            """, (self.guild_id, a_utc_iso(primer_dia_mes), a_utc_iso(fin_dia_mes))).fetchall()
        finally:
            conn.close()

        embed = discord.Embed(
            title=f"📅 CALENDARIO DE EVENTOS Y MISIONES — {nombre_mes(self.mes)} {self.anio}",
            color=COLOR_BLANCO
        )

        if not eventos:
            embed.description = "*No hay misiones o eventos programados para este mes.*"
            return embed

        lineas = []
        for ev in eventos:
            inicio = desde_iso(ev["start_time"])
            if not inicio:
                continue
            lineas.append(
                f"► **#{ev['id']} {ev['title']}**\n"
                f"  └ 🕒 <t:{timestamp_discord(inicio)}:f> (<t:{timestamp_discord(inicio)}:R>)"
            )

        embed.description = "\n\n".join(lineas)[:4096]
        embed.set_footer(text=f"Total de eventos este mes: {len(eventos)}")
        return embed

    @discord.ui.button(label="◄ Mes Anterior", style=discord.ButtonStyle.secondary, custom_id="cal_prev")
    async def anterior_mes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mes == 1:
            self.mes = 12
            self.anio -= 1
        else:
            self.mes -= 1

        embed = self.obtener_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Hoy 📌", style=discord.ButtonStyle.primary, custom_id="cal_today")
    async def mes_actual(self, interaction: discord.Interaction, button: discord.ui.Button):
        ahora_dt = ahora()
        self.anio = ahora_dt.year
        self.mes = ahora_dt.month

        embed = self.obtener_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Siguiente Mes ►", style=discord.ButtonStyle.secondary, custom_id="cal_next")
    async def siguiente_mes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mes == 12:
            self.mes = 1
            self.anio += 1
        else:
            self.mes += 1

        embed = self.obtener_embed()
        await interaction.response.edit_message(embed=embed, view=self)


def configurar_modulo_calendario(bot: commands.Bot):
    @bot.tree.command(name="calendario", description="Muestra el calendario interactivo de misiones y eventos.")
    @app_commands.guild_only()
    async def cmd_calendario(interaction: discord.Interaction):
        ahora_dt = ahora()
        view = CalendarioView(
            anio=ahora_dt.year, mes=ahora_dt.month,
            user_id=interaction.user.id, guild_id=interaction.guild_id,
        )
        embed = view.obtener_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)