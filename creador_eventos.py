import os
import discord

from dotenv import load_dotenv

load_dotenv()

GUILD_ID = int(os.getenv("GUILD_ID"))

creaciones = {}


def obtener_datos(user_id):

    if user_id not in creaciones:

        creaciones[user_id] = {
            "title": None,
            "description": None,
            "start_time": None,
            "duration": None,
            "frequency": None,
            "options": [],
            "mentions": [],
            "color": None,
            "color_name": None,
            "multiple": False,
            "reminders": [],
            "location": None,
            "image": "",
            "restrictions": [],
            "publish_channel": None,
        }

    return creaciones[user_id]

async def flujo_opcion(
    usuario,
    texto,
    opciones,
    permitir_back=True
):

creaciones = {}


# ============================================================
# DATOS TEMPORALES DE CREACIÓN
# ============================================================

creaciones = {}


def obtener_datos(user_id):

    if user_id not in creaciones:

        creaciones[user_id] = {
            "title": None,
            "description": None,
            "start_time": None,
            "duration_minutes": None,
            "frequency": None,
            "signup_options": [],
            "mention_roles": [],
            "color": None,
            "color_name": None,
            "multiple_registrations": None,
            "reminders": [],
            "location": None,
            "image_url": None,
            "restrictions": [],
            "publish_channel": None,
        }

    return creaciones[user_id]


# ============================================================
# UTILIDADES
# ============================================================

async def enviar_dm(usuario, mensaje):

    try:
        return await usuario.send(mensaje)

    except discord.Forbidden:
        return None


async def preguntar(bot, usuario, pregunta):

    mensaje = await enviar_dm(usuario, pregunta)

    if mensaje is None:
        return None

    def comprobar(m):

        return (
            m.author.id == usuario.id
            and isinstance(m.channel, discord.DMChannel)
        )

    try:

        respuesta = await bot.wait_for(
            "message",
            timeout=300,
            check=comprobar
        )

        return respuesta.content.strip()

    except TimeoutError:

        await enviar_dm(
            usuario,
            "La creación del evento ha expirado por inactividad."
        )

        return None

# ============================================================
# CONVERSACIÓN
# ============================================================

async def iniciar_creacion_evento(bot, usuario):

    datos = obtener_datos(usuario.id)

    await enviar_dm(
        usuario,
        "Vamos a crear un evento.\n\n"
        "Escribe `cancelar` en cualquier momento para cancelar."
    )

    # --------------------------------------------------------
    # TÍTULO
    # --------------------------------------------------------

    respuesta = await preguntar(
        bot,
     usuario,
     "¿Cuál será el título del evento?"
)

    if respuesta is None:
        return

    if respuesta.lower() == "cancelar":
        return await cancelar_creacion(usuario.id, usuario)

    datos["title"] = respuesta

    # --------------------------------------------------------
    # DESCRIPCIÓN
    # --------------------------------------------------------

    respuesta = await preguntar(
        bot,
       usuario,
     "¿Cuál será la descripción del evento?"
)
    if respuesta is None:
        return

    if respuesta.lower() == "cancelar":
        return await cancelar_creacion(usuario.id, usuario)

    datos["description"] = respuesta

    # --------------------------------------------------------
    # FINAL TEMPORAL
    # --------------------------------------------------------

    await enviar_dm(
        usuario,
        "Datos recibidos correctamente.\n\n"
        f"**Título:** {datos['title']}\n"
        f"**Descripción:** {datos['description']}\n\n"
        "El sistema conversacional está funcionando."
    )


# ============================================================
# CANCELAR
# ============================================================

async def cancelar_creacion(user_id, usuario):

    creaciones.pop(user_id, None)

    await enviar_dm(
        usuario,
        "Creación del evento cancelada."
    )


# ============================================================
# REGISTRO DEL COMANDO
# ============================================================

def configurar_creador_eventos(bot):

    @bot.tree.command(
        name="crear_evento",
        description="Crear un evento mediante conversación.",
        guild=discord.Object(id=GUILD_ID)
    )
    async def crear_evento(interaction: discord.Interaction):

        usuario = interaction.user

        await interaction.response.send_message(
            "Te he enviado un mensaje privado para crear el evento.",
            ephemeral=True
        )

        await iniciar_creacion_evento(
            bot,
            usuario
        )