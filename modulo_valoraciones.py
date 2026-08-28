import discord
from discord import app_commands
from discord.ext import commands

from database import conectar_db
from formatters import COLOR_BLANCO, a_utc_iso, ahora, generar_estrellas_ascii


class FeedbackModal(discord.ui.Modal, title="§ VALORACION DEL EVENTO §"):
    puntuacion = discord.ui.TextInput(
        label="► Puntuacion (1 al 5)",
        placeholder="Introduce un numero del 1 al 5",
        min_length=1,
        max_length=1,
    )
    comentario = discord.ui.TextInput(
        label="► Comentario / Feedback (Opcional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, event_id: int):
        super().__init__()
        self.event_id = event_id

    async def on_submit(self, interaction: discord.Interaction):
        val = self.puntuacion.value.strip()
        if not val.isdigit() or not (1 <= int(val) <= 5):
            return await interaction.response.send_message(
                "‼ Debes introducir una puntuacion valida entre 1 y 5.", ephemeral=True
            )

        rating = int(val)
        conn = conectar_db()
        try:
            # Una valoracion por persona: si ya existe, se actualiza.
            conn.execute("""
                INSERT INTO feedback (event_id, user_id, rating, comment, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id, user_id) DO UPDATE SET
                    rating = excluded.rating,
                    comment = excluded.comment,
                    created_at = excluded.created_at
            """, (self.event_id, interaction.user.id, rating, self.comentario.value.strip(), a_utc_iso(ahora())))
            conn.commit()
        finally:
            conn.close()

        await interaction.response.send_message(
            f"► ¡Gracias por tu valoracion! ({generar_estrellas_ascii(rating)})", ephemeral=True
        )


def registrar_comandos_valoraciones(bot: commands.Bot):
    @bot.tree.command(name="valoraciones", description="Muestra el resumen de valoraciones de un evento.")
    @app_commands.describe(evento_id="ID del evento (aparece en el pie del anuncio)")
    async def cmd_valoraciones(interaction: discord.Interaction, evento_id: int):
        conn = conectar_db()
        try:
            evento = conn.execute(
                "SELECT title FROM eventos WHERE id = ? AND guild_id = ?",
                (evento_id, interaction.guild_id),
            ).fetchone()
            if not evento:
                return await interaction.response.send_message("‼ Ese evento no existe en este servidor.", ephemeral=True)

            resumen = conn.execute(
                "SELECT COUNT(*) AS total, AVG(rating) AS media FROM feedback WHERE event_id = ?", (evento_id,)
            ).fetchone()
            comentarios = conn.execute(
                "SELECT rating, comment FROM feedback WHERE event_id = ? AND comment != '' ORDER BY id DESC LIMIT 10",
                (evento_id,),
            ).fetchall()
        finally:
            conn.close()

        if not resumen["total"]:
            return await interaction.response.send_message(
                f"► El evento **{evento['title']}** todavia no tiene valoraciones.", ephemeral=True
            )

        media = resumen["media"]
        embed = discord.Embed(
            title=f"§ VALORACIONES: {evento['title']} §",
            description=f"{generar_estrellas_ascii(media)}  **{media:.2f}/5** ({resumen['total']} valoraciones)",
            color=COLOR_BLANCO,
        )
        for c in comentarios:
            embed.add_field(name=generar_estrellas_ascii(c["rating"]), value=c["comment"][:1024], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)
