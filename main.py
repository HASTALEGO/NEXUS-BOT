import os
import re
import asyncio
import sqlite3

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ============================================================
# GUÍA DE CONFIGURACIÓN DEL BOT
# ============================================================
# Esta guía sirve para trasladar el bot a otro servidor de Discord sin tener que buscar entre todo main.py.
# La idea es que todas las variables que dependan del servidor estén juntas al principio del archivo.
# https://docs.google.com/document/d/1IZcinxnR_TyUkFRJEPqLAsG_y-sA7AmV6ATA8vgCstk/edit?usp=sharing

ROL_NUEVO = 1542471137517379594
ROL_VERIFICADO = 1542351631683690549
ROL_DM = 1542487680389091328

# ============================================================
# ROLES DE INSCRIPCIÓN
# ============================================================

ROLES_INSCRIPCION = {
    ROL_NUEVO: "nuevo",
    ROL_VERIFICADO: "verificado"
}


# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No se ha encontrado DISCORD_TOKEN en el archivo .env"
    )

try:
    GUILD_ID = int(os.getenv("GUILD_ID", "0"))
except ValueError:
    raise RuntimeError(
        "GUILD_ID debe ser un número entero."
    )

if not GUILD_ID:
    raise RuntimeError(
        "No se ha encontrado GUILD_ID válido en el archivo .env"
    )

DATABASE = "eventos.db"

# Zona horaria de España peninsular.
TIMEZONE = ZoneInfo("Europe/Madrid")

GUILD_OBJECT = discord.Object(id=GUILD_ID)

# ============================================================
# ROLES DE INSCRIPCIÓN
# ============================================================

ROL_NUEVO_ID = 1542471137517379594
ROL_VERIFICADO_ID = 1542351631683690549

# Roles que impiden participar en misiones.
# Se almacenan en memoria y también en la base de datos.
ROLES_BLOQUEADOS = set()

# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# BASE DE DATOS
# ============================================================

def conectar_db():
    conn = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def inicializar_db():

    conn = conectar_db()

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            creator_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            start_time TEXT,
            duration_minutes INTEGER,
            frequency TEXT,
            color INTEGER,
            location_channel_id INTEGER,
            image_url TEXT,
            multiple_registrations INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS opciones_inscripcion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            emoji TEXT,
            max_slots INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inscripciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            option_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evento_menciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evento_restricciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recordatorios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            minutes_before INTEGER NOT NULL,
            sent INTEGER DEFAULT 0
        )
    """)

    # ========================================================
    # ROLES BLOQUEADOS PARA LAS MISIONES
    # ========================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles_bloqueados (
            role_id INTEGER PRIMARY KEY
        )
    """)

    # ========================================================
    # ÍNDICES
    # ========================================================

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_inscripcion_unica
        ON inscripciones(event_id, option_id, user_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_inscripciones_event
        ON inscripciones(event_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_recordatorios_event
        ON recordatorios(event_id)
    """)

    conn.commit()
    conn.close()


# ============================================================
# ROLES BLOQUEADOS
# ============================================================

def cargar_roles_bloqueados():

    global ROLES_BLOQUEADOS

    conn = conectar_db()

    try:

        filas = conn.execute(
            """
            SELECT role_id
            FROM roles_bloqueados
            """
        ).fetchall()

        ROLES_BLOQUEADOS = {
            int(fila["role_id"])
            for fila in filas
        }

    finally:

        conn.close()


def guardar_rol_bloqueado(role_id):

    role_id = int(role_id)

    conn = conectar_db()

    try:

        conn.execute(
            """
            INSERT OR IGNORE INTO roles_bloqueados (
                role_id
            )
            VALUES (?)
            """,
            (role_id,)
        )

        conn.commit()

    finally:

        conn.close()

    ROLES_BLOQUEADOS.add(role_id)


def eliminar_rol_bloqueado(role_id):

    role_id = int(role_id)

    conn = conectar_db()

    try:

        conn.execute(
            """
            DELETE FROM roles_bloqueados
            WHERE role_id = ?
            """,
            (role_id,)
        )

        conn.commit()

    finally:

        conn.close()

    ROLES_BLOQUEADOS.discard(role_id)

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
            "duration": None,
            "frequency": None,

            "options": [],

            "mentions": None,

            "color": None,
            "color_name": None,

            "multiple": None,

            "reminders": None,

            "location": None,

            "image": None,

            "restrictions": None,

            "publish_channel": None,

            "panel_message_id": None,
            "panel_channel_id": None,

            "created_at": datetime.now(TIMEZONE)
        }

    return creaciones[user_id]


# ============================================================
# COLORES
# ============================================================

COLORES = {

    "Azul": 0x5865F2,
    "Morado": 0x9B59B6,
    "Verde": 0x57F287,
    "Amarillo": 0xFEE75C,
    "Rojo": 0xED4245,
    "Naranja": 0xE67E22,
    "Rosa": 0xEB459E,
    "Cian": 0x00FFFF,
    "Blanco": 0xFFFFFF,
    "Negro": 0x000000
}


# ============================================================
# UTILIDADES
# ============================================================

def ahora():

    return datetime.now(TIMEZONE)


def formatear_duracion(minutos):

    if not minutos:
        return "Sin configurar"

    horas = minutos // 60
    mins = minutos % 60

    resultado = []

    if horas:
        resultado.append(f"{horas} h")

    if mins:
        resultado.append(f"{mins} min")

    return " ".join(resultado)


def formatear_recordatorios(recordatorios):

    if not recordatorios:
        return "Ninguno"

    resultado = []

    for minutos in recordatorios:

        if minutos >= 1440:

            resultado.append(
                f"{minutos // 1440} día(s)"
            )

        elif minutos >= 60:

            resultado.append(
                f"{minutos // 60} hora(s)"
            )

        else:

            resultado.append(
                f"{minutos} min"
            )

    return ", ".join(resultado)


def timestamp_discord(fecha):

    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=TIMEZONE)

    return int(fecha.timestamp())


def parsear_fecha(contenido):

    try:

        fecha = datetime.strptime(
            contenido,
            "%d/%m/%Y %H:%M"
        )

        return fecha.replace(
            tzinfo=TIMEZONE
        )

    except ValueError:

        return None


def calcular_siguiente_ocurrencia(fecha, frecuencia):

    if frecuencia == "Diariamente":

        return fecha + timedelta(days=1)

    if frecuencia == "Semanalmente":

        return fecha + timedelta(days=7)

    if frecuencia == "Mensualmente":

        # Suma de meses sin dependencias externas.
        year = fecha.year
        month = fecha.month + 1

        if month > 12:
            month = 1
            year += 1

        # Último día válido del mes.
        import calendar

        ultimo_dia = calendar.monthrange(
            year,
            month
        )[1]

        dia = min(
            fecha.day,
            ultimo_dia
        )

        return fecha.replace(
            year=year,
            month=month,
            day=dia
        )

    return None


async def esperar_mensaje(
    usuario,
    timeout=300
):

    def check(message):

        return (
            message.author.id == usuario.id
            and isinstance(
                message.channel,
                discord.DMChannel
            )
        )

    try:

        mensaje = await bot.wait_for(
            "message",
            check=check,
            timeout=timeout
        )

        return mensaje.content.strip()

    except asyncio.TimeoutError:

        return None

# ============================================================
# NUEVOS MIEMBROS
# ============================================================

@bot.event
async def on_member_join(member):

    ROL_NUEVO = 1542471137517379594

    rol_nuevo = member.guild.get_role(
        ROL_NUEVO
    )

    if rol_nuevo is None:

        print(
            f"No encuentro el rol nuevo "
            f"en el servidor {member.guild.id}"
        )

        return

    try:

        await member.add_roles(
            rol_nuevo,
            reason="Nuevo miembro del servidor"
        )

        print(
            f"Rol nuevo asignado a "
            f"{member} ({member.id})"
        )

    except discord.Forbidden:

        print(
            f"No puedo asignar el rol nuevo a "
            f"{member}. Comprueba la posición "
            f"del rol del bot."
        )

    except discord.HTTPException as e:

        print(
            f"Error asignando el rol nuevo a "
            f"{member}: {e}"
        )

# ============================================================
# CREAR EMBED DEL PANEL
# ============================================================

def crear_panel_embed(user_id):

    datos = obtener_datos(user_id)

    color = (
        datos["color"]
        if datos["color"] is not None
        else 0x5865F2
    )

    embed = discord.Embed(
        title="Crear evento",
        description=(
            "Configura tu evento seleccionando "
            "los apartados disponibles.\n\n"
            "Cada apartado que completes "
            "desaparecerá del siguiente panel."
        ),
        color=discord.Color(color)
    )

    configuracion = []

    if datos["title"] is not None:

        configuracion.append(
            f"**Título:** {datos['title']}"
        )

    if datos["description"] is not None:

        descripcion = datos["description"]

        if len(descripcion) > 300:
            descripcion = (
                descripcion[:297]
                + "..."
            )

        configuracion.append(
            f"**Descripción:** {descripcion}"
        )

    if datos["start_time"] is not None:

        configuracion.append(
            "**Hora de inicio:** "
            f"<t:{timestamp_discord(datos['start_time'])}:F>"
        )

    if datos["duration"] is not None:

        configuracion.append(
            "**Duración:** "
            + formatear_duracion(
                datos["duration"]
            )
        )

    if datos["frequency"] is not None:

        configuracion.append(
            "**Frecuencia:** "
            + datos["frequency"]
        )

    if datos["options"]:

        nombres = []

        for opcion in datos["options"]:

            nombre = opcion["name"]

            if opcion["max_slots"]:

                nombre += (
                    f" ({opcion['max_slots']} plazas)"
                )

            nombres.append(nombre)

        texto = ", ".join(nombres)

        if len(texto) > 500:
            texto = texto[:497] + "..."

        configuracion.append(
            "**Opciones de inscripción:** "
            + texto
        )

    if datos["mentions"] is not None:

        if datos["mentions"]:

            menciones = " ".join(
                role.mention
                for role in datos["mentions"]
            )

        else:

            menciones = "Ninguna"

        configuracion.append(
            "**Menciones:** "
            + menciones
        )

    if datos["color"] is not None:

        configuracion.append(
            "**Color:** "
            + str(datos["color_name"])
        )

    if datos["multiple"] is not None:

        configuracion.append(
            "**Inscripciones múltiples:** "
            + (
                "Sí"
                if datos["multiple"]
                else "No"
            )
        )

    if datos["reminders"] is not None:

        configuracion.append(
            "**Recordatorios:** "
            + formatear_recordatorios(
                datos["reminders"]
            )
        )

    if datos["location"] is not None:

        configuracion.append(
            "**Ubicación:** "
            + datos["location"].mention
        )

    if datos["image"] is not None:

        configuracion.append(
            "**Imagen:** "
            + (
                "Configurada"
                if datos["image"]
                else "Sin imagen"
            )
        )

    if datos["restrictions"] is not None:

        if datos["restrictions"]:

            restricciones = " ".join(
                role.mention
                for role in datos["restrictions"]
            )

        else:

            restricciones = "Ninguna"

        configuracion.append(
            "**Restricciones:** "
            + restricciones
        )

    if datos["publish_channel"] is not None:

        configuracion.append(
            "**Canal de publicación:** "
            + datos["publish_channel"].mention
        )

    if configuracion:

        texto = "\n".join(configuracion)

        embed.add_field(
            name="Configuración actual",
            value=texto[:1024],
            inline=False
        )

    return embed


# ============================================================
# ACTUALIZAR PANEL DE CREACIÓN
# ============================================================

async def actualizar_panel(user_id):

    datos = obtener_datos(
        user_id
    )

    # --------------------------------------------------------
    # Comprobar que existe la sesión
    # --------------------------------------------------------

    if not datos:
        return

    # --------------------------------------------------------
    # Obtener el canal donde está el panel
    # --------------------------------------------------------

    canal_id = datos.get(
        "panel_channel_id"
    )

    mensaje_id = datos.get(
        "panel_message_id"
    )

    if not canal_id or not mensaje_id:
        return

    try:

        canal = bot.get_channel(
            canal_id
        )

        if canal is None:

            canal = await bot.fetch_channel(
                canal_id
            )

        if canal is None:
            return

        # ----------------------------------------------------
        # Obtener el mensaje existente
        # ----------------------------------------------------

        try:

            mensaje = await canal.fetch_message(
                mensaje_id
            )

        except discord.NotFound:

            mensaje = None

        # ----------------------------------------------------
        # Si el mensaje ya no existe,
        # crear uno nuevo
        # ----------------------------------------------------

        if mensaje is None:

            nuevo_mensaje = await canal.send(
                embed=crear_panel_embed(
                    user_id
                ),
                view=CrearEventoView(
                    user_id
                )
            )

            datos["panel_message_id"] = (
                nuevo_mensaje.id
            )

            datos["panel_channel_id"] = (
                canal.id
            )

            return

        # ----------------------------------------------------
        # Actualizar el panel existente
        # ----------------------------------------------------

        await mensaje.edit(
            embed=crear_panel_embed(
                user_id
            ),
            view=CrearEventoView(
                user_id
            )
        )

    except discord.Forbidden:

        print(
            "ERROR ACTUALIZANDO PANEL: "
            "Discord no permite acceder o editar "
            "el mensaje."
        )

    except discord.NotFound:

        print(
            "ERROR ACTUALIZANDO PANEL: "
            "El canal o mensaje ya no existe."
        )

    except discord.HTTPException as e:

        print(
            "ERROR HTTP ACTUALIZANDO PANEL:",
            repr(e)
        )

    except Exception as e:

        print(
            "ERROR ACTUALIZANDO PANEL:",
            repr(e)
        )


# ============================================================
# OPCIONES DE INSCRIPCIÓN
# ============================================================

class OpcionesView(discord.ui.View):

    def __init__(self, user_id):

        super().__init__(
            timeout=300
        )

        self.user_id = user_id

        self.add_item(
            discord.ui.Button(
                label="Crear opciones",
                style=discord.ButtonStyle.primary,
                custom_id=f"crear_opciones_{user_id}"
            )
        )

        self.add_item(
            discord.ui.Button(
                label="Opción única",
                style=discord.ButtonStyle.secondary,
                custom_id=f"opcion_unica_{user_id}"
            )
        )

        # ----------------------------------------------------
        # CALLBACK CREAR OPCIONES
        # ----------------------------------------------------

        self.children[0].callback = (
            self.crear_opciones
        )

        # ----------------------------------------------------
        # CALLBACK OPCIÓN ÚNICA
        # ----------------------------------------------------

        self.children[1].callback = (
            self.opcion_unica
        )

    async def crear_opciones(
        self,
        interaction
    ):

        await interaction.response.send_message(
            "Escribe las opciones de inscripción "
            "separadas por comas.\n\n"
            "Ejemplo:\n"
            "`Tanque(2), DPS(5), Sanador(3)`\n\n"
            "Para plazas ilimitadas escribe simplemente:\n"
            "`Jugador`\n\n"
            "También puedes indicar explícitamente:\n"
            "`Jugador(ilimitado)`"
        )

        contenido = await esperar_mensaje(
            interaction.user
        )

        if not contenido:

            return

        opciones = []

        for parte in contenido.split(","):

            parte = parte.strip()

            if not parte:
                continue

            match = re.match(
                r"^(.*?)\s*"
                r"\(\s*(\d+|ilimitado)\s*\)$",
                parte,
                re.IGNORECASE
            )

            if match:

                nombre = match.group(1).strip()
                plazas = match.group(2).lower()

                if not nombre:
                    continue

                if plazas == "ilimitado":

                    max_slots = None

                else:

                    max_slots = int(
                        plazas
                    )

                    if max_slots <= 0:
                        continue

            else:

                nombre = parte
                max_slots = None

            opciones.append(
                {
                    "name": nombre[:100],
                    "max_slots": max_slots
                }
            )

        if not opciones:

            await interaction.followup.send(
                "No pude interpretar ninguna opción."
            )

            return

        datos = obtener_datos(
            self.user_id
        )

        datos["options"] = opciones

        await interaction.followup.send(
            "Opciones de inscripción configuradas."
        )

        await actualizar_panel(
            self.user_id
        )

    async def opcion_unica(
        self,
        interaction
    ):

        datos = obtener_datos(
            self.user_id
        )

        datos["options"] = [
            {
                "name": "Participantes",
                "max_slots": None
            }
        ]

        await interaction.response.send_message(
            "Se ha creado una única opción: "
            "**Participantes**."
        )

        await actualizar_panel(
            self.user_id
        )


class OpcionesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="6. Opciones de inscripción",
            style=discord.ButtonStyle.secondary
        )

    async def callback(
        self,
        interaction
    ):

        await interaction.response.send_message(
            "Configura las opciones de inscripción:",
            view=OpcionesView(
                self.user_id
            )
        )

# ============================================================
# VISTA PRINCIPAL DE CREACIÓN DE EVENTOS
# ============================================================

class CrearEventoView(discord.ui.View):

    def __init__(self, user_id):

        super().__init__(
            timeout=1800
        )

        datos = obtener_datos(
            user_id
        )

        # ----------------------------------------------------
        # 1. TÍTULO
        # ----------------------------------------------------

        if datos["title"] is None:

            self.add_item(
                CampoTextoButton(
                    user_id,
                    "title",
                    1,
                    "Título",
                    "Escribe el título del evento.",
                    100
                )
            )

        # ----------------------------------------------------
        # 2. DESCRIPCIÓN
        # ----------------------------------------------------

        if datos["description"] is None:

            self.add_item(
                CampoTextoButton(
                    user_id,
                    "description",
                    2,
                    "Descripción",
                    "Escribe la descripción del evento.",
                    3000
                )
            )

        # ----------------------------------------------------
        # 3. HORA
        # ----------------------------------------------------

        if datos["start_time"] is None:

            self.add_item(
                InicioButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 4. DURACIÓN
        # ----------------------------------------------------

        if datos["duration"] is None:

            self.add_item(
                DuracionButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 5. FRECUENCIA
        # ----------------------------------------------------

        if datos["frequency"] is None:

            self.add_item(
                FrecuenciaButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 6. OPCIONES DE INSCRIPCIÓN
        # ----------------------------------------------------

        if not datos["options"]:

            self.add_item(
                OpcionesButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 7. MENCIONES
        # ----------------------------------------------------

        if datos["mentions"] is None:

            self.add_item(
                MencionesButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 8. COLOR
        # ----------------------------------------------------

        if datos["color"] is None:

            self.add_item(
                ColorButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 9. INSCRIPCIONES MÚLTIPLES
        # ----------------------------------------------------

        if datos["multiple"] is None:

            self.add_item(
                MultiplesButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 10. RECORDATORIOS
        # ----------------------------------------------------

        if datos["reminders"] is None:

            self.add_item(
                RecordatoriosButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 11. UBICACIÓN
        # ----------------------------------------------------

        if datos["location"] is None:

            self.add_item(
                UbicacionButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 12. IMAGEN
        # ----------------------------------------------------

        if datos["image"] is None:

            self.add_item(
                ImagenButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 13. ROLES DE INSCRIPCIÓN
        # ----------------------------------------------------

        if datos["restrictions"] is None:

            self.add_item(
                RestriccionesButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # 14. CANAL DE PUBLICACIÓN
        # ----------------------------------------------------

        if datos["publish_channel"] is None:

            self.add_item(
                PublicarCanalButton(
                    user_id
                )
            )

        # ----------------------------------------------------
        # BOTONES FINALES
        # ----------------------------------------------------

        self.add_item(
            PreviewButton(
                user_id
            )
        )

        self.add_item(
            PublicarButton(
                user_id
            )
        )

        self.add_item(
            CancelarButton(
                user_id
            )
        )


# ============================================================
# CAMPO DE TEXTO
# ============================================================

class CampoTextoButton(discord.ui.Button):

    def __init__(
        self,
        user_id,
        campo,
        numero,
        label,
        pregunta,
        max_length
    ):

        self.user_id = user_id
        self.campo = campo
        self.numero = numero
        self.pregunta = pregunta
        self.max_length = max_length

        super().__init__(
            label=f"{numero}. {label}",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        try:

            await interaction.response.send_message(
                self.pregunta
                + "\n\n"
                + f"Límite de caracteres: {self.max_length}\n"
                + "Escribe el contenido "
                "en este mismo mensaje privado."
            )

            while True:

                contenido = await esperar_mensaje(
                    interaction.user
                )

                if contenido is None:

                    await interaction.followup.send(
                        "Se agotó el tiempo de espera."
                    )

                    return

                longitud = len(contenido)

                if longitud > self.max_length:

                    await interaction.followup.send(
                        "El texto supera el límite "
                        "de caracteres.\n\n"
                        f"Caracteres introducidos: {longitud}\n"
                        f"Límite de caracteres: {self.max_length}\n\n"
                        "Inténtalo de nuevo."
                    )

                    continue

                break

            datos = obtener_datos(
                self.user_id
            )

            datos[self.campo] = contenido

            await actualizar_panel(
                self.user_id
            )

        except Exception as e:

            print(
                "ERROR EN CAMPO DE TEXTO:",
                repr(e)
            )

            try:

                if interaction.response.is_done():

                    await interaction.followup.send(
                        "Ha ocurrido un error "
                        "al procesar este apartado."
                    )

                else:

                    await interaction.response.send_message(
                        "Ha ocurrido un error "
                        "al procesar este apartado."
                    )

            except Exception:

                pass


# ============================================================
# HORA DE INICIO
# ============================================================

class InicioButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="3. Hora de inicio",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        try:

            await interaction.response.send_message(
                "Escribe la fecha y hora de inicio.\n\n"
                "Formato:\n"
                "DD/MM/YYYY HH:MM\n\n"
                "Ejemplo:\n"
                "30/08/2026 20:00\n\n"
                "La hora se interpretará como hora de España "
                "(Europe/Madrid)."
            )

            contenido = await esperar_mensaje(
                interaction.user
            )

            if not contenido:
                return

            fecha = parsear_fecha(
                contenido
            )

            if fecha is None:

                await interaction.followup.send(
                    "Formato incorrecto.\n\n"
                    "Usa:\n"
                    "DD/MM/YYYY HH:MM\n\n"
                    "Ejemplo:\n"
                    "30/08/2026 20:00"
                )

                return

            if fecha <= ahora():

                await interaction.followup.send(
                    "La fecha debe estar en el futuro."
                )

                return

            datos = obtener_datos(
                self.user_id
            )

            datos["start_time"] = fecha

            await actualizar_panel(
                self.user_id
            )

        except Exception as e:

            print(
                "ERROR EN HORA:",
                repr(e)
            )


# ============================================================
# DURACIÓN
# ============================================================

class DuracionButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="4. Duración",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        try:

            await interaction.response.send_message(
                "Escribe la duración.\n\n"
                "Ejemplos:\n"
                "2h\n"
                "90m\n"
                "2h 30m"
            )

            contenido = await esperar_mensaje(
                interaction.user
            )

            if not contenido:
                return

            contenido = contenido.lower()

            horas = re.search(
                r"(\d+)\s*h",
                contenido
            )

            minutos = re.search(
                r"(\d+)\s*m",
                contenido
            )

            total = 0

            if horas:

                total += (
                    int(horas.group(1))
                    * 60
                )

            if minutos:

                total += int(
                    minutos.group(1)
                )

            if total <= 0:

                await interaction.followup.send(
                    "Duración incorrecta.\n"
                    "Ejemplo: `2h 30m`"
                )

                return

            datos = obtener_datos(
                self.user_id
            )

            datos["duration"] = total

            await actualizar_panel(
                self.user_id
            )

        except Exception as e:

            print(
                "ERROR EN DURACIÓN:",
                repr(e)
            )


# ============================================================
# FRECUENCIA
# ============================================================

class FrecuenciaSelect(discord.ui.Select):

    def __init__(self, user_id):

        self.user_id = user_id

        opciones = [

            discord.SelectOption(
                label="Una vez",
                value="Una vez"
            ),

            discord.SelectOption(
                label="Diariamente",
                value="Diariamente"
            ),

            discord.SelectOption(
                label="Semanalmente",
                value="Semanalmente"
            ),

            discord.SelectOption(
                label="Mensualmente",
                value="Mensualmente"
            )
        ]

        super().__init__(
            placeholder="Selecciona la frecuencia",
            options=opciones
        )

    async def callback(self, interaction):

        datos = obtener_datos(
            self.user_id
        )

        datos["frequency"] = self.values[0]

        await interaction.response.send_message(
            "Frecuencia configurada: "
            + self.values[0]
        )

        await actualizar_panel(
            self.user_id
        )


class FrecuenciaView(discord.ui.View):

    def __init__(self, user_id):

        super().__init__(
            timeout=300
        )

        self.add_item(
            FrecuenciaSelect(user_id)
        )


class FrecuenciaButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="5. Frecuencia",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        await interaction.response.send_message(
            "Selecciona la frecuencia:",
            view=FrecuenciaView(self.user_id)
        )


# ============================================================
# ROLES
# ============================================================

class RolesSelect(discord.ui.Select):

    def __init__(
        self,
        user_id,
        guild,
        tipo
    ):

        self.user_id = user_id
        self.guild_id = guild.id
        self.tipo = tipo

        roles = [
            role
            for role in guild.roles
            if not role.is_default()
            and not role.managed
        ]

        roles = roles[:25]

        opciones = []

        for role in roles:

            opciones.append(
                discord.SelectOption(
                    label=role.name[:100],
                    value=str(role.id)
                )
            )

        if not opciones:

            opciones.append(
                discord.SelectOption(
                    label="No hay roles disponibles",
                    value="none"
                )
            )

        super().__init__(
            placeholder="Selecciona los roles",
            options=opciones,
            min_values=1,
            max_values=min(
                len(opciones),
                25
            )
        )

    async def callback(self, interaction):

        guild = bot.get_guild(
            self.guild_id
        )

        if guild is None:

            await interaction.response.send_message(
                "No encuentro el servidor."
            )

            return

        datos = obtener_datos(
            self.user_id
        )

        roles = []

        for role_id in self.values:

            if role_id == "none":
                continue

            role = guild.get_role(
                int(role_id)
            )

            if role:
                roles.append(role)

        if self.tipo == "mentions":

            datos["mentions"] = roles

        else:

            datos["restrictions"] = roles

        await interaction.response.send_message(
            "Roles seleccionados correctamente."
        )

        await actualizar_panel(
            self.user_id
        )


# ============================================================
# MENCIONES
# ============================================================

class MencionesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="7. Menciones",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        guild = bot.get_guild(
            GUILD_ID
        )

        await interaction.response.send_message(
            "Selecciona los roles que "
            "quieres mencionar:",
            view=MencionesView(
                self.user_id,
                guild
            )
        )


class SinMencionesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="Ninguna",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        datos = obtener_datos(
            self.user_id
        )

        datos["mentions"] = []

        await interaction.response.send_message(
            "No se utilizarán menciones."
        )

        await actualizar_panel(
            self.user_id
        )


class MencionesView(discord.ui.View):

    def __init__(
        self,
        user_id,
        guild
    ):

        super().__init__(
            timeout=300
        )

        self.add_item(
            RolesSelect(
                user_id,
                guild,
                "mentions"
            )
        )

        self.add_item(
            SinMencionesButton(
                user_id
            )
        )


# ============================================================
# COLOR
# ============================================================

class ColorSelect(discord.ui.Select):

    def __init__(self, user_id):

        self.user_id = user_id

        opciones = []

        for nombre, valor in COLORES.items():

            opciones.append(
                discord.SelectOption(
                    label=nombre,
                    value=str(valor)
                )
            )

        super().__init__(
            placeholder="Selecciona un color",
            options=opciones
        )

    async def callback(self, interaction):

        datos = obtener_datos(
            self.user_id
        )

        valor = int(
            self.values[0]
        )

        datos["color"] = valor

        for nombre, color in COLORES.items():

            if color == valor:

                datos["color_name"] = nombre
                break

        await interaction.response.send_message(
            "Color seleccionado: "
            + datos["color_name"]
        )

        await actualizar_panel(
            self.user_id
        )


class ColorView(discord.ui.View):

    def __init__(self, user_id):

        super().__init__(
            timeout=300
        )

        self.add_item(
            ColorSelect(user_id)
        )


class ColorButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="8. Color",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        await interaction.response.send_message(
            "Selecciona el color:",
            view=ColorView(self.user_id)
        )


# ============================================================
# INSCRIPCIONES MÚLTIPLES
# ============================================================

class MultipleSelect(discord.ui.Select):

    def __init__(self, user_id):

        self.user_id = user_id

        opciones = [

            discord.SelectOption(
                label="No",
                value="false",
                description="Una inscripción por usuario"
            ),

            discord.SelectOption(
                label="Sí",
                value="true",
                description="Varias inscripciones por usuario"
            )
        ]

        super().__init__(
            placeholder="Selecciona una opción",
            options=opciones
        )

    async def callback(self, interaction):

        datos = obtener_datos(
            self.user_id
        )

        datos["multiple"] = (
            self.values[0] == "true"
        )

        await interaction.response.send_message(
            "Configuración guardada."
        )

        await actualizar_panel(
            self.user_id
        )


class MultipleView(discord.ui.View):

    def __init__(self, user_id):

        super().__init__(
            timeout=300
        )

        self.add_item(
            MultipleSelect(user_id)
        )


class MultiplesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="9. Inscripciones múltiples",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        await interaction.response.send_message(
            "Selecciona una opción:",
            view=MultipleView(self.user_id)
        )


# ============================================================
# RECORDATORIOS
# ============================================================

class RecordatoriosButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="10. Recordatorios",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        await interaction.response.send_message(
            "Escribe los recordatorios separados "
            "por comas.\n\n"
            "Ejemplo:\n"
            "7d, 24h, 1h, 30m\n\n"
            "Unidades disponibles:\n"
            "m = minutos\n"
            "h = horas\n"
            "d = días\n\n"
            "Para ninguno escribe:\n"
            "ninguno"
        )

        contenido = await esperar_mensaje(
            interaction.user
        )

        if not contenido:
            return

        datos = obtener_datos(
            self.user_id
        )

        if contenido.lower() == "ninguno":

            datos["reminders"] = []

            await actualizar_panel(
                self.user_id
            )

            return

        recordatorios = []

        for parte in contenido.split(","):

            parte = parte.strip().lower()

            match = re.match(
                r"(\d+)\s*"
                r"(m|min|h|hora|horas|d|dia|dias|día|días)",
                parte
            )

            if not match:
                continue

            cantidad = int(
                match.group(1)
            )

            unidad = match.group(2)

            if unidad.startswith("m"):

                minutos = cantidad

            elif unidad.startswith("h"):

                minutos = cantidad * 60

            else:

                minutos = cantidad * 1440

            if minutos > 0:

                recordatorios.append(
                    minutos
                )

        if not recordatorios:

            await interaction.followup.send(
                "No pude interpretar "
                "ningún recordatorio."
            )

            return

        datos["reminders"] = sorted(
            set(recordatorios),
            reverse=True
        )

        await actualizar_panel(
            self.user_id
        )


# ============================================================
# CANALES
# ============================================================

class CanalSelect(discord.ui.Select):

    def __init__(
        self,
        user_id,
        guild,
        tipo="location"
    ):

        self.user_id = user_id
        self.guild_id = guild.id
        self.tipo = tipo

        canales = guild.text_channels

        # Discord permite 25 opciones por Select.
        canales = canales[:25]

        opciones = []

        for canal in canales:

            opciones.append(
                discord.SelectOption(
                    label=f"#{canal.name}"[:100],
                    value=str(canal.id)
                )
            )

        if not opciones:

            opciones.append(
                discord.SelectOption(
                    label="No hay canales disponibles",
                    value="none"
                )
            )

        super().__init__(
            placeholder="Selecciona un canal",
            options=opciones
        )

    async def callback(self, interaction):

        if self.values[0] == "none":

            await interaction.response.send_message(
                "No hay canales disponibles."
            )

            return

        guild = bot.get_guild(
            self.guild_id
        )

        if guild is None:

            await interaction.response.send_message(
                "No encuentro el servidor."
            )

            return

        canal = guild.get_channel(
            int(self.values[0])
        )

        if not canal:

            await interaction.response.send_message(
                "No pude encontrar ese canal."
            )

            return

        datos = obtener_datos(
            self.user_id
        )

        if self.tipo == "publish":

            datos["publish_channel"] = canal

        else:

            datos["location"] = canal

        await interaction.response.send_message(
            "Canal seleccionado: "
            + canal.mention
        )

        await actualizar_panel(
            self.user_id
        )


class CanalView(discord.ui.View):

    def __init__(
        self,
        user_id,
        guild,
        tipo="location"
    ):

        super().__init__(
            timeout=300
        )

        self.add_item(
            CanalSelect(
                user_id,
                guild,
                tipo
            )
        )


class UbicacionButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="11. Ubicación",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        guild = bot.get_guild(
            GUILD_ID
        )

        await interaction.response.send_message(
            "Selecciona el canal que será "
            "la ubicación del evento:",
            view=CanalView(
                self.user_id,
                guild,
                "location"
            )
        )


# ============================================================
# IMAGEN
# ============================================================

class ImagenButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="12. Imagen",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        await interaction.response.send_message(
            "Envía la URL de la imagen.\n\n"
            "Límite de caracteres: 2048\n\n"
            "Si no quieres imagen escribe:\n"
            "ninguna"
        )

        contenido = await esperar_mensaje(
            interaction.user
        )

        if not contenido:
            return

        datos = obtener_datos(
            self.user_id
        )

        if contenido.lower() == "ninguna":

            datos["image"] = ""

            await actualizar_panel(
                self.user_id
            )

            return

        if len(contenido) > 2048:

            await interaction.followup.send(
                "La URL supera el límite.\n\n"
                f"Caracteres introducidos: {len(contenido)}\n"
                "Límite: 2048\n\n"
                "Inténtalo de nuevo."
            )

            return

        if not (
            contenido.startswith("http://")
            or contenido.startswith("https://")
        ):

            await interaction.followup.send(
                "La URL no parece válida.\n\n"
                "Debe comenzar por "
                "`http://` o `https://`."
            )

            return

        datos["image"] = contenido

        await actualizar_panel(
            self.user_id
        )


# ============================================================
# RESTRICCIONES
# ============================================================

class RestriccionesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="13. Roles de inscripción",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        # El panel está en DM, por lo que
        # interaction.guild es None.
        guild = bot.get_guild(
            GUILD_ID
        )

        if guild is None:

            await interaction.response.send_message(
                "No encuentro el servidor.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            "Selecciona qué roles podrán "
            "inscribirse en esta misión:",
            view=RestriccionesView(
                self.user_id,
                guild
            ),
            ephemeral=True
        )
class SinRestriccionesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="Sin restricciones",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        datos = obtener_datos(
            self.user_id
        )

        datos["restrictions"] = []

        await interaction.response.send_message(
            "La misión no tiene restricciones "
            "de roles.",
            ephemeral=True
        )

        await actualizar_panel(
            self.user_id
        )

class RestriccionesView(discord.ui.View):

    def __init__(
        self,
        user_id,
        guild
    ):

        super().__init__(
            timeout=300
        )

        self.user_id = user_id

        opciones = []

        # ----------------------------------------------------
        # ROL NUEVO
        # ----------------------------------------------------

        rol_nuevo = guild.get_role(
            ROL_NUEVO
        )

        if rol_nuevo:

            opciones.append(
                discord.SelectOption(
                    label=rol_nuevo.name[:100],
                    value=str(ROL_NUEVO),
                    description=(
                        "Permite inscribirse a usuarios nuevos."
                    )[:100]
                )
            )

        # ----------------------------------------------------
        # ROL VERIFICADO
        # ----------------------------------------------------

        rol_verificado = guild.get_role(
            ROL_VERIFICADO
        )

        if rol_verificado:

            opciones.append(
                discord.SelectOption(
                    label=rol_verificado.name[:100],
                    value=str(ROL_VERIFICADO),
                    description=(
                        "Permite inscribirse a usuarios verificados."
                    )[:100]
                )
            )

        # ----------------------------------------------------
        # SI NO EXISTEN LOS ROLES
        # ----------------------------------------------------

        if opciones:

            selector = discord.ui.Select(
                placeholder="Selecciona los roles permitidos",
                min_values=1,
                max_values=len(opciones),
                options=opciones
            )

            async def selector_callback(
                interaction
            ):

                datos = obtener_datos(
                    self.user_id
                )

                datos["restrictions"] = []

                for valor in selector.values:

                    datos["restrictions"].append(
                        guild.get_role(
                            int(valor)
                        )
                    )

                datos["restrictions"] = [
                    role
                    for role in datos["restrictions"]
                    if role is not None
                ]

                nombres = ", ".join(
                    role.mention
                    for role in datos["restrictions"]
                )

                await interaction.response.send_message(
                    "Roles permitidos:\n"
                    + nombres,
                    ephemeral=True
                )

                await actualizar_panel(
                    self.user_id
                )

            selector.callback = selector_callback

            self.add_item(
                selector
            )

        else:

            self.add_item(
                discord.ui.Button(
                    label="No se encontraron los roles",
                    style=discord.ButtonStyle.danger,
                    disabled=True
                )
            )

        # ----------------------------------------------------
        # SIN RESTRICCIONES
        # ----------------------------------------------------

        self.add_item(
            SinRestriccionesButton(
                self.user_id
            )
        )

# ============================================================
# CANAL DE PUBLICACIÓN
# ============================================================

class PublicarCanalButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="14. Canal de publicación",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        guild = bot.get_guild(
            GUILD_ID
        )

        await interaction.response.send_message(
            "Selecciona el canal donde se publicará "
            "el evento:",
            view=CanalView(
                self.user_id,
                guild,
                "publish"
            )
        )


# ============================================================
# VISTA PREVIA
# ============================================================

class PreviewButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="Vista previa",
            style=discord.ButtonStyle.primary,
            row=4
        )

    async def callback(self, interaction):

        datos = obtener_datos(
            self.user_id
        )

        embed = discord.Embed(
            title=(
                datos["title"]
                or "Evento sin título"
            ),
            description=(
                datos["description"]
                or "Sin descripción"
            ),
            color=discord.Color(
                datos["color"]
                or 0x5865F2
            )
        )

        if datos["start_time"]:

            embed.add_field(
                name="Hora de inicio",
                value=(
                    f"<t:{timestamp_discord(datos['start_time'])}:F>"
                ),
                inline=False
            )

        if datos["duration"]:

            embed.add_field(
                name="Duración",
                value=formatear_duracion(
                    datos["duration"]
                )
            )

        if datos["frequency"]:

            embed.add_field(
                name="Frecuencia",
                value=datos["frequency"]
            )

        if datos["location"]:

            embed.add_field(
                name="Ubicación",
                value=datos["location"].mention
            )

        if datos["publish_channel"]:

            embed.add_field(
                name="Canal de publicación",
                value=datos["publish_channel"].mention
            )

        if datos["options"]:

            opciones_texto = []

            for opcion in datos["options"]:

                if opcion["max_slots"]:

                    texto = (
                        f"{opcion['name']} "
                        f"({opcion['max_slots']} plazas)"
                    )

                else:

                    texto = (
                        f"{opcion['name']} "
                        "(ilimitado)"
                    )

                opciones_texto.append(
                    texto
                )

            texto_opciones = "\n".join(
                opciones_texto
            )

            if len(texto_opciones) > 1024:
                texto_opciones = (
                    texto_opciones[:1021]
                    + "..."
                )

            embed.add_field(
                name="Inscripciones",
                value=texto_opciones,
                inline=False
            )

        if datos["image"]:

            embed.set_image(
                url=datos["image"]
            )

        await interaction.response.send_message(
            "Vista previa:",
            embed=embed
        )


# ============================================================
# CANCELAR
# ============================================================

class CancelarButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="Cancelar",
            style=discord.ButtonStyle.danger,
            row=4
        )

    async def callback(self, interaction):

        creaciones.pop(
            self.user_id,
            None
        )

        await interaction.response.edit_message(
            content=(
                "Creación cancelada.\n"
                "No se ha publicado ningún evento."
            ),
            embed=None,
            view=None
        )


# ============================================================
# PUBLICAR
# ============================================================

class PublicarButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="Publicar evento",
            style=discord.ButtonStyle.success,
            row=4
        )

    async def callback(self, interaction):

        try:

            await interaction.response.defer()

            datos = obtener_datos(
                self.user_id
            )

            faltan = []

            if not datos["title"]:
                faltan.append("1. Título")

            if not datos["description"]:
                faltan.append("2. Descripción")

            if not datos["start_time"]:
                faltan.append("3. Hora de inicio")

            if not datos["duration"]:
                faltan.append("4. Duración")

            if not datos["frequency"]:
                faltan.append("5. Frecuencia")

            if not datos["options"]:
                faltan.append(
                    "6. Opciones de inscripción"
                )

            if datos["mentions"] is None:
                faltan.append("7. Menciones")

            if datos["color"] is None:
                faltan.append("8. Color")

            if datos["multiple"] is None:
                faltan.append(
                    "9. Inscripciones múltiples"
                )

            if datos["reminders"] is None:
                faltan.append(
                    "10. Recordatorios"
                )

            if not datos["location"]:
                faltan.append("11. Ubicación")

            if not datos["publish_channel"]:
                faltan.append(
                    "14. Canal de publicación"
                )

            if faltan:

                await interaction.followup.send(
                    "Todavía faltan estos apartados:\n\n"
                    + "\n".join(
                        f"- {campo}"
                        for campo in faltan
                    )
                )

                return

            guild = bot.get_guild(
                GUILD_ID
            )

            if not guild:

                await interaction.followup.send(
                    "No encuentro el servidor."
                )

                return

            canal = datos["publish_channel"]

            # Comprobamos que el canal sigue existiendo.
            try:

                canal = guild.get_channel(
                    canal.id
                )

            except Exception:

                canal = None

            if canal is None:

                await interaction.followup.send(
                    "El canal de publicación "
                    "ya no existe."
                )

                return

            conn = conectar_db()

            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO eventos (
                    guild_id,
                    channel_id,
                    creator_id,
                    title,
                    description,
                    start_time,
                    duration_minutes,
                    frequency,
                    color,
                    location_channel_id,
                    image_url,
                    multiple_registrations,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild.id,
                    canal.id,
                    self.user_id,
                    datos["title"],
                    datos["description"],
                    datos["start_time"].isoformat(),
                    datos["duration"],
                    datos["frequency"],
                    datos["color"],
                    datos["location"].id,
                    datos["image"],
                    (
                        1
                        if datos["multiple"]
                        else 0
                    ),
                    ahora().isoformat()
                )
            )

            evento_id = cursor.lastrowid

            for opcion in datos["options"]:

                cursor.execute(
                    """
                    INSERT INTO opciones_inscripcion (
                        event_id,
                        name,
                        emoji,
                        max_slots
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        evento_id,
                        opcion["name"],
                        opcion.get("emoji", ""),
                        opcion["max_slots"]
                    )
                )

            for role in datos["mentions"]:

                cursor.execute(
                    """
                    INSERT INTO evento_menciones (
                        event_id,
                        role_id
                    )
                    VALUES (?, ?)
                    """,
                    (
                        evento_id,
                        role.id
                    )
                )

            for minutos in datos["reminders"]:

                cursor.execute(
                    """
                    INSERT INTO recordatorios (
                        event_id,
                        minutes_before
                    )
                    VALUES (?, ?)
                    """,
                    (
                        evento_id,
                        minutos
                    )
                )

            conn.commit()
            conn.close()

            # ====================================================
            # DEBUG
            # ====================================================

            print()
            print(
                "========== DEBUG PUBLICAR =========="
            )
            print(
                "evento_id:",
                evento_id,
                type(evento_id)
            )
            print(
                "self.user_id:",
                self.user_id,
                type(self.user_id)
            )
            print(
                "guild.id:",
                guild.id,
                type(guild.id)
            )
            print(
                "canal.id:",
                canal.id,
                type(canal.id)
            )
            print(
                "location.id:",
                datos["location"].id,
                type(datos["location"].id)
            )
            print(
                "===================================="
            )
            print()

            # ====================================================
            # CREAR EMBED
            # ====================================================

            print(
                "DEBUG: creando embed..."
            )

            embed = crear_embed_publicado(
                evento_id
            )

            print(
                "DEBUG: embed creado."
            )

            # ====================================================
            # MENCIONES
            # ====================================================

            menciones = ""

            if datos["mentions"]:

                menciones = " ".join(
                    role.mention
                    for role in datos["mentions"]
                )

            # ====================================================
            # CREAR VIEW
            # ====================================================

            print(
                "DEBUG: creando EventoView..."
            )

            view = EventoView(
                evento_id
            )

            print(
                "DEBUG: EventoView creada."
            )

            # ====================================================
            # ENVIAR MENSAJE
            # ====================================================

            print(
                "DEBUG: enviando mensaje..."
            )

            mensaje = await canal.send(
                content=(
                    menciones
                    if menciones
                    else None
                ),
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    roles=True
                )
            )

            print(
                "DEBUG: mensaje enviado."
            )

            # ====================================================
            # GUARDAR MESSAGE ID
            # ====================================================

            conn = conectar_db()

            conn.execute(
                """
                UPDATE eventos
                SET message_id = ?
                WHERE id = ?
                """,
                (
                    mensaje.id,
                    evento_id
                )
            )

            conn.commit()
            conn.close()

            # ====================================================
            # REGISTRAR VIEW PERSISTENTE
            # ====================================================

            print(
                "DEBUG: registrando view persistente..."
            )

            bot.add_view(
                EventoView(
                    evento_id
                )
            )

            print(
                "DEBUG: view registrada."
            )

            # ====================================================
            # FINALIZAR
            # ====================================================

            creaciones.pop(
                self.user_id,
                None
            )

            await interaction.followup.send(
                "Evento publicado correctamente "
                f"en {canal.mention}."
            )

        except Exception as e:

            import traceback

            print()
            print(
                "=================================================="
            )
            print(
                "ERROR PUBLICANDO EVENTO"
            )
            print(
                repr(e)
            )

            traceback.print_exc()

            print(
                "=================================================="
            )
            print()

            try:

                await interaction.followup.send(
                    "Ha ocurrido un error "
                    "al publicar el evento.\n\n"
                    f"Error: `{e}`"
                )

            except Exception:

                pass


# ============================================================
# EMBED DEL EVENTO PUBLICADO
# ============================================================

def crear_embed_publicado(evento_id):

    conn = conectar_db()

    evento = conn.execute(
        """
        SELECT *
        FROM eventos
        WHERE id = ?
        """,
        (evento_id,)
    ).fetchone()

    if not evento:

        conn.close()

        return discord.Embed(
            title="Evento no encontrado"
        )

    opciones = conn.execute(
        """
        SELECT
            opciones_inscripcion.*,
            COUNT(inscripciones.id) AS inscritos
        FROM opciones_inscripcion
        LEFT JOIN inscripciones
            ON inscripciones.option_id =
               opciones_inscripcion.id
        WHERE opciones_inscripcion.event_id = ?
        GROUP BY opciones_inscripcion.id
        ORDER BY opciones_inscripcion.id
        """,
        (evento_id,)
    ).fetchall()

    conn.close()

    inicio = datetime.fromisoformat(
        evento["start_time"]
    )

    if inicio.tzinfo is None:

        inicio = inicio.replace(
            tzinfo=TIMEZONE
        )

    # ========================================================
    # COLOR
    # ========================================================

    color_raw = evento["color"]

    try:

        if color_raw is None:

            color_int = 0x5865F2

        elif isinstance(color_raw, int):

            color_int = color_raw

        else:

            color_str = str(
                color_raw
            ).strip()

            if color_str.startswith("#"):

                color_int = int(
                    color_str[1:],
                    16
                )

            elif color_str.lower().startswith("0x"):

                color_int = int(
                    color_str[2:],
                    16
                )

            else:

                color_int = int(
                    color_str
                )

        if not 0 <= color_int <= 0xFFFFFF:

            print(
                f"COLOR INVALIDO: "
                f"{color_raw!r} -> {color_int}"
            )

            color_int = 0x5865F2

    except (ValueError, TypeError):

        print(
            f"NO SE PUDO CONVERTIR EL COLOR: "
            f"{color_raw!r}"
        )

        color_int = 0x5865F2

    # ========================================================
    # EMBED
    # ========================================================

    embed = discord.Embed(
        title=evento["title"],
        description=(
            evento["description"]
            or ""
        ),
        color=discord.Color(
            color_int
        )
    )

    # ========================================================
    # HORA DE INICIO
    # ========================================================

    embed.add_field(
        name="Hora de inicio",
        value=(
            f"<t:{timestamp_discord(inicio)}:F>"
        ),
        inline=False
    )

    # ========================================================
    # DURACIÓN
    # ========================================================

    embed.add_field(
        name="Duración",
        value=formatear_duracion(
            evento["duration_minutes"]
        )
    )

    # ========================================================
    # FRECUENCIA
    # ========================================================

    embed.add_field(
        name="Frecuencia",
        value=evento["frequency"]
    )

    # ========================================================
    # UBICACIÓN
    # ========================================================

    guild = bot.get_guild(
        evento["guild_id"]
    )

    if guild:

        canal = guild.get_channel(
            evento["location_channel_id"]
        )

        if canal:

            embed.add_field(
                name="Ubicación",
                value=canal.mention
            )

    # ========================================================
    # OPCIONES DE INSCRIPCIÓN
    # ========================================================

    for opcion in opciones:

        cantidad = opcion["inscritos"]

        if opcion["max_slots"]:

            plazas = (
                f"{cantidad}/"
                f"{opcion['max_slots']}"
            )

        else:

            plazas = str(
                cantidad
            )

        embed.add_field(
            name=opcion["name"],
            value=(
                f"{plazas} inscritos"
            ),
            inline=True
        )

    # ========================================================
    # IMAGEN
    # ========================================================

    if evento["image_url"]:

        embed.set_image(
            url=evento["image_url"]
        )

    # ========================================================
    # PIE
    # ========================================================

    embed.set_footer(
        text=f"Evento #{evento_id}"
    )

    return embed


# ============================================================
# OBTENER OPCIÓN
# ============================================================

def obtener_opcion(option_id):

    conn = conectar_db()

    opcion = conn.execute(
        """
        SELECT *
        FROM opciones_inscripcion
        WHERE id = ?
        """,
        (option_id,)
    ).fetchone()

    conn.close()

    return opcion


# ============================================================
# INSCRIBIRSE
# ============================================================

async def inscribirse(
    interaction,
    event_id,
    option_id
):

    conn = conectar_db()

    try:

        # ====================================================
        # OBTENER EVENTO
        # ====================================================

        evento = conn.execute(
            """
            SELECT *
            FROM eventos
            WHERE id = ?
            """,
            (event_id,)
        ).fetchone()

        if not evento:

            await interaction.response.send_message(
                "Este evento ya no existe.",
                ephemeral=True
            )

            return

        # ====================================================
        # COMPROBAR FECHA
        # ====================================================

        inicio = datetime.fromisoformat(
            evento["start_time"]
        )

        if inicio.tzinfo is None:

            inicio = inicio.replace(
                tzinfo=TIMEZONE
            )

        if ahora() >= inicio:

            await interaction.response.send_message(
                "Las inscripciones para este "
                "evento están cerradas.",
                ephemeral=True
            )

            return

        # ====================================================
        # ROLES BLOQUEADOS
        # ====================================================

        if ROLES_BLOQUEADOS:

            miembro = None

            # ------------------------------------------------
            # INTENTAR OBTENER MIEMBRO DE LA CACHÉ
            # ------------------------------------------------

            if interaction.guild:

                miembro = interaction.guild.get_member(
                    interaction.user.id
                )

            # ------------------------------------------------
            # SI NO ESTÁ EN CACHÉ, CONSULTAR DISCORD
            # ------------------------------------------------

            if miembro is None and interaction.guild:

                try:

                    miembro = await interaction.guild.fetch_member(
                        interaction.user.id
                    )

                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException
                ):

                    miembro = None

            # ------------------------------------------------
            # NO SE PUDO OBTENER EL MIEMBRO
            # ------------------------------------------------

            if miembro is None:

                await interaction.response.send_message(
                    "No pude comprobar tus roles.",
                    ephemeral=True
                )

                return

            # ------------------------------------------------
            # ROLES DEL USUARIO
            # ------------------------------------------------

            roles_usuario = {
                int(role.id)
                for role in miembro.roles
            }

            # ------------------------------------------------
            # COMPROBAR SI TIENE ALGÚN ROL BLOQUEADO
            # ------------------------------------------------

            roles_bloqueados_usuario = (
                roles_usuario
                & ROLES_BLOQUEADOS
            )

            if roles_bloqueados_usuario:

                nombres_roles = []

                for role_id in roles_bloqueados_usuario:

                    role = interaction.guild.get_role(
                        role_id
                    )

                    if role:

                        nombres_roles.append(
                            role.name
                        )

                if nombres_roles:

                    roles_texto = ", ".join(
                        f"`{nombre}`"
                        for nombre in nombres_roles
                    )

                    mensaje = (
                        "No puedes inscribirte en esta misión "
                        "porque tienes un rol que impide participar.\n\n"
                        "Rol bloqueado: "
                        f"{roles_texto}"
                    )

                else:

                    mensaje = (
                        "No puedes inscribirte en esta misión "
                        "porque tienes un rol bloqueado."
                    )

                await interaction.response.send_message(
                    mensaje,
                    ephemeral=True
                )

                return

        # ====================================================
        # OPCIÓN
        # ====================================================

        opcion = conn.execute(
            """
            SELECT *
            FROM opciones_inscripcion
            WHERE id = ?
            AND event_id = ?
            """,
            (
                option_id,
                event_id
            )
        ).fetchone()

        if not opcion:

            await interaction.response.send_message(
                "Esta opción ya no existe.",
                ephemeral=True
            )

            return

        # ====================================================
        # INSCRIPCIÓN EXISTENTE EN LA MISMA OPCIÓN
        # ====================================================

        existente = conn.execute(
            """
            SELECT *
            FROM inscripciones
            WHERE event_id = ?
            AND option_id = ?
            AND user_id = ?
            """,
            (
                event_id,
                option_id,
                interaction.user.id
            )
        ).fetchone()

        if existente:

            await interaction.response.send_message(
                "Ya estás inscrito en esta opción.",
                ephemeral=True
            )

            return

        # ====================================================
        # MÚLTIPLES
        # ====================================================

        if not evento["multiple_registrations"]:

            existente = conn.execute(
                """
                SELECT *
                FROM inscripciones
                WHERE event_id = ?
                AND user_id = ?
                """,
                (
                    event_id,
                    interaction.user.id
                )
            ).fetchone()

            if existente:

                await interaction.response.send_message(
                    "Este evento no permite "
                    "inscripciones múltiples.",
                    ephemeral=True
                )

                return

        # ====================================================
        # PLAZAS
        # ====================================================

        if opcion["max_slots"]:

            cantidad = conn.execute(
                """
                SELECT COUNT(*)
                FROM inscripciones
                WHERE option_id = ?
                """,
                (option_id,)
            ).fetchone()[0]

            if cantidad >= opcion["max_slots"]:

                await interaction.response.send_message(
                    "Esta opción está completa.",
                    ephemeral=True
                )

                return

        # ====================================================
        # GUARDAR
        # ====================================================

        conn.execute(
            """
            INSERT INTO inscripciones (
                event_id,
                option_id,
                user_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                event_id,
                option_id,
                interaction.user.id,
                ahora().isoformat()
            )
        )

        conn.commit()

        await interaction.response.send_message(
            "Te has inscrito en "
            f"{opcion['name']}.",
            ephemeral=True
        )

    except sqlite3.IntegrityError:

        try:

            if interaction.response.is_done():

                await interaction.followup.send(
                    "Ya estabas inscrito en esta opción.",
                    ephemeral=True
                )

            else:

                await interaction.response.send_message(
                    "Ya estabas inscrito en esta opción.",
                    ephemeral=True
                )

        except Exception:

            pass

    except Exception as e:

        print()
        print(
            "=========================================="
        )

        print(
            "ERROR EN INSCRIPCIÓN"
        )

        print(
            repr(e)
        )

        print(
            "=========================================="
        )

        try:

            if interaction.response.is_done():

                await interaction.followup.send(
                    "Ha ocurrido un error "
                    "al realizar la inscripción.",
                    ephemeral=True
                )

            else:

                await interaction.response.send_message(
                    "Ha ocurrido un error "
                    "al realizar la inscripción.",
                    ephemeral=True
                )

        except Exception:

            pass

    finally:

        conn.close()

    # ========================================================
    # ACTUALIZAR EVENTO PUBLICADO
    # ========================================================

    await actualizar_evento_publicado(
        event_id
    )


# ============================================================
# CANCELAR INSCRIPCIÓN
# ============================================================

async def cancelar_inscripcion(
    interaction,
    event_id
):

    conn = conectar_db()

    try:

        resultado = conn.execute(
            """
            DELETE FROM inscripciones
            WHERE event_id = ?
            AND user_id = ?
            """,
            (
                event_id,
                interaction.user.id
            )
        )

        conn.commit()

        if resultado.rowcount == 0:

            await interaction.response.send_message(
                "No estás inscrito en este evento.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            "Has cancelado tu inscripción.",
            ephemeral=True
        )

    finally:

        conn.close()

    await actualizar_evento_publicado(
        event_id
    )


# ============================================================
# EVENTO PUBLICADO
# ============================================================

class EventoView(discord.ui.View):

    def __init__(self, evento_id):

        super().__init__(
            timeout=None
        )

        self.evento_id = evento_id

        conn = conectar_db()

        opciones = conn.execute(
            """
            SELECT *
            FROM opciones_inscripcion
            WHERE event_id = ?
            ORDER BY id
            """,
            (evento_id,)
        ).fetchall()

        conn.close()

        if opciones:

            select_options = []

            for opcion in opciones:

                if opcion["max_slots"]:

                    descripcion = (
                        f"Máximo: "
                        f"{opcion['max_slots']} plazas"
                    )

                else:

                    descripcion = (
                        "Plazas ilimitadas"
                    )

                select_options.append(
                    discord.SelectOption(
                        label=opcion["name"][:100],
                        value=str(opcion["id"]),
                        description=descripcion[:100]
                    )
                )

            selector = discord.ui.Select(
                placeholder="Selecciona una opción",
                options=select_options,
                custom_id=(
                    f"evento_{evento_id}_opciones"
                )
            )

            async def selector_callback(
                interaction
            ):

                option_id = int(
                    selector.values[0]
                )

                await inscribirse(
                    interaction,
                    self.evento_id,
                    option_id
                )

            selector.callback = selector_callback

            self.add_item(
                selector
            )

        # ====================================================
        # VER INSCRITOS
        # ====================================================

        participantes = discord.ui.Button(
            label="Ver inscritos",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                f"evento_{evento_id}_"
                "participantes"
            )
        )

        participantes.callback = (
            self.ver_participantes
        )

        self.add_item(
            participantes
        )

        # ====================================================
        # CANCELAR INSCRIPCIÓN
        # ====================================================

        cancelar = discord.ui.Button(
            label="Cancelar inscripción",
            style=discord.ButtonStyle.danger,
            custom_id=(
                f"evento_{evento_id}_"
                "cancelar"
            )
        )

        async def cancelar_callback(
            interaction
        ):

            await cancelar_inscripcion(
                interaction,
                self.evento_id
            )

        cancelar.callback = (
            cancelar_callback
        )

        self.add_item(
            cancelar
        )

    # ========================================================
    # VER PARTICIPANTES
    # ========================================================

    async def ver_participantes(
        self,
        interaction
    ):

        conn = conectar_db()

        registros = conn.execute(
            """
            SELECT
                inscripciones.user_id,
                opciones_inscripcion.name
            FROM inscripciones
            JOIN opciones_inscripcion
                ON opciones_inscripcion.id =
                   inscripciones.option_id
            WHERE inscripciones.event_id = ?
            ORDER BY opciones_inscripcion.id,
                     inscripciones.id
            """,
            (self.evento_id,)
        ).fetchall()

        conn.close()

        if not registros:

            await interaction.response.send_message(
                "No hay inscritos todavía.",
                ephemeral=True
            )

            return

        grupos = {}

        for registro in registros:

            nombre = registro["name"]

            if nombre not in grupos:

                grupos[nombre] = []

            miembro = interaction.guild.get_member(
                registro["user_id"]
            )

            if miembro:

                grupos[nombre].append(
                    miembro.mention
                )

            else:

                grupos[nombre].append(
                    f"<@{registro['user_id']}>"
                )

        partes = []

        for nombre, usuarios in grupos.items():

            partes.append(
                f"**{nombre}**\n"
                + "\n".join(
                    f"- {usuario}"
                    for usuario in usuarios
                )
            )

        texto = "\n\n".join(
            partes
        )

        # ====================================================
        # MENSAJE ÚNICO
        # ====================================================

        if len(texto) <= 2000:

            await interaction.response.send_message(
                texto,
                allowed_mentions=discord.AllowedMentions(
                    users=False
                ),
                ephemeral=True
            )

            return

        # ====================================================
        # MENSAJE DIVIDIDO
        # ====================================================

        await interaction.response.send_message(
            "La lista de inscritos es demasiado larga. "
            "La enviaré dividida.",
            ephemeral=True
        )

        for i in range(
            0,
            len(texto),
            1900
        ):

            await interaction.followup.send(
                texto[i:i + 1900],
                allowed_mentions=discord.AllowedMentions(
                    users=False
                ),
                ephemeral=True
            )


# ============================================================
# ACTUALIZAR EVENTO PUBLICADO
# ============================================================

async def actualizar_evento_publicado(
    event_id
):

    conn = conectar_db()

    evento = conn.execute(
        """
        SELECT *
        FROM eventos
        WHERE id = ?
        """,
        (event_id,)
    ).fetchone()

    conn.close()

    if not evento:
        return

    guild = bot.get_guild(
        evento["guild_id"]
    )

    if not guild:
        return

    canal = guild.get_channel(
        evento["channel_id"]
    )

    if not canal:
        return

    if not evento["message_id"]:
        return

    try:

        mensaje = await canal.fetch_message(
            evento["message_id"]
        )

        await mensaje.edit(
            embed=crear_embed_publicado(
                event_id
            ),
            view=EventoView(
                event_id
            )
        )

    except discord.NotFound:

        print(
            f"El mensaje del evento #{event_id} "
            "ya no existe."
        )

    except Exception as e:

        print(
            "Error actualizando evento publicado:",
            repr(e)
        )


# ============================================================
# PROCESAR EVENTOS RECURRENTES
# ============================================================

async def comprobar_eventos_recurrentes():

    ahora_actual = ahora()

    conn = conectar_db()

    eventos = conn.execute(
        """
        SELECT *
        FROM eventos
        WHERE frequency != 'Una vez'
        AND start_time IS NOT NULL
        """
    ).fetchall()

    for evento in eventos:

        try:

            inicio = datetime.fromisoformat(
                evento["start_time"]
            )

            if inicio.tzinfo is None:

                inicio = inicio.replace(
                    tzinfo=TIMEZONE
                )

            duracion = evento["duration_minutes"] or 0

            fin = inicio + timedelta(
                minutes=duracion
            )

            # El evento solamente pasa a la siguiente
            # ocurrencia después de haber terminado.
            if ahora_actual < fin:
                continue

            siguiente = calcular_siguiente_ocurrencia(
                inicio,
                evento["frequency"]
            )

            if siguiente is None:
                continue

            # Si el bot estuvo apagado durante varias
            # ocurrencias, avanzamos hasta encontrar una futura.
            while siguiente + timedelta(
                minutes=duracion
            ) <= ahora_actual:

                siguiente = calcular_siguiente_ocurrencia(
                    siguiente,
                    evento["frequency"]
                )

                if siguiente is None:
                    break

            if siguiente is None:
                continue

            conn.execute(
                """
                UPDATE eventos
                SET start_time = ?
                WHERE id = ?
                """,
                (
                    siguiente.isoformat(),
                    evento["id"]
                )
            )

            # Una nueva ocurrencia comienza sin inscripciones
            # de la anterior.
            conn.execute(
                """
                DELETE FROM inscripciones
                WHERE event_id = ?
                """,
                (evento["id"],)
            )

            # Los recordatorios vuelven a estar disponibles.
            conn.execute(
                """
                UPDATE recordatorios
                SET sent = 0
                WHERE event_id = ?
                """,
                (evento["id"],)
            )

            conn.commit()

            await actualizar_evento_publicado(
                evento["id"]
            )

        except Exception as e:

            print(
                "Error procesando evento recurrente:",
                repr(e)
            )

    conn.close()


# ============================================================
# RECORDATORIOS
# ============================================================

async def procesar_recordatorios():

    ahora_actual = ahora()

    conn = conectar_db()

    recordatorios = conn.execute(
        """
        SELECT
            recordatorios.id AS reminder_id,
            recordatorios.minutes_before,
            recordatorios.event_id,
            eventos.*
        FROM recordatorios
        JOIN eventos
            ON recordatorios.event_id = eventos.id
        WHERE recordatorios.sent = 0
        """
    ).fetchall()

    for recordatorio in recordatorios:

        try:

            inicio = datetime.fromisoformat(
                recordatorio["start_time"]
            )

            if inicio.tzinfo is None:

                inicio = inicio.replace(
                    tzinfo=TIMEZONE
                )

            momento = (
                inicio
                - timedelta(
                    minutes=recordatorio[
                        "minutes_before"
                    ]
                )
            )

            # Todavía no toca.
            if ahora_actual < momento:
                continue

            # Si el evento ya empezó, este recordatorio
            # se considera perdido y no se manda tarde.
            if ahora_actual >= inicio:

                conn.execute(
                    """
                    UPDATE recordatorios
                    SET sent = 1
                    WHERE id = ?
                    """,
                    (
                        recordatorio[
                            "reminder_id"
                        ],
                    )
                )

                conn.commit()

                continue

            guild = bot.get_guild(
                recordatorio["guild_id"]
            )

            if not guild:
                continue

            canal = guild.get_channel(
                recordatorio["channel_id"]
            )

            if not canal:
                continue

            usuarios = conn.execute(
                """
                SELECT DISTINCT user_id
                FROM inscripciones
                WHERE event_id = ?
                """,
                (
                    recordatorio["event_id"],
                )
            ).fetchall()

            menciones = " ".join(
                f"<@{usuario['user_id']}>"
                for usuario in usuarios
            )

            contenido = (
                "Recordatorio del evento\n\n"
                f"**{recordatorio['title']}**\n"
                f"Comienza <t:{timestamp_discord(inicio)}:R>"
            )

            if menciones:

                contenido += (
                    "\n\n"
                    + menciones
                )

            await canal.send(
                contenido,
                allowed_mentions=discord.AllowedMentions(
                    users=True
                )
            )

            conn.execute(
                """
                UPDATE recordatorios
                SET sent = 1
                WHERE id = ?
                """,
                (
                    recordatorio[
                        "reminder_id"
                    ],
                )
            )

            conn.commit()

        except Exception as e:

            print(
                "Error procesando recordatorio:",
                repr(e)
            )

    conn.close()


# ============================================================
# TAREA GENERAL
# ============================================================

@tasks.loop(seconds=30)
async def tareas_eventos():

    try:

        await comprobar_eventos_recurrentes()

        await procesar_recordatorios()

        # Limpiar sesiones abandonadas.
        limite = ahora() - timedelta(
            minutes=30
        )

        usuarios_eliminar = []

        for user_id, datos in creaciones.items():

            creado = datos.get(
                "created_at",
                ahora()
            )

            if creado < limite:

                usuarios_eliminar.append(
                    user_id
                )

        for user_id in usuarios_eliminar:

            creaciones.pop(
                user_id,
                None
            )

    except Exception as e:

        print(
            "ERROR EN TAREA DE EVENTOS:",
            repr(e)
        )


@tareas_eventos.before_loop
async def antes_de_tareas():

    await bot.wait_until_ready()


# ============================================================
# CARGAR VISTAS PERSISTENTES
# ============================================================

async def cargar_vistas_persistentes():

    conn = conectar_db()

    eventos = conn.execute(
        """
        SELECT id
        FROM eventos
        WHERE message_id IS NOT NULL
        """
    ).fetchall()

    conn.close()

    cargados = 0

    for evento in eventos:

        try:

            bot.add_view(
                EventoView(
                    evento["id"]
                )
            )

            cargados += 1

        except Exception as e:

            print(
                f"No se pudo cargar la vista "
                f"del evento #{evento['id']}:",
                repr(e)
            )

    print(
        f"Vistas persistentes cargadas: {cargados}"
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print()
    print(
        "=========================================="
    )

    print(
        f"{bot.user} está conectado."
    )

    print(
        "=========================================="
    )

    inicializar_db()

    # Las vistas persistentes solamente deben cargarse
    # una vez por proceso.
    if not getattr(
        bot,
        "_vistas_cargadas",
        False
    ):

        await cargar_vistas_persistentes()

        bot._vistas_cargadas = True

    try:

        synced = await bot.tree.sync(
            guild=GUILD_OBJECT
        )

        print(
            f"Comandos sincronizados: {len(synced)}"
        )

    except Exception as e:

        print(
            "Error al sincronizar comandos:",
            repr(e)
        )

    if not tareas_eventos.is_running():

        tareas_eventos.start()


# ============================================================
# ADMINISTRACIÓN DE ROLES BLOQUEADOS
# ============================================================

class RolesBloqueadosSelect(discord.ui.Select):

    def __init__(self, guild):

        opciones = []

        for role in guild.roles:

            # No mostramos @everyone.
            if role.is_default():
                continue

            opciones.append(
                discord.SelectOption(
                    label=role.name[:100],
                    value=str(role.id),
                    default=role.id in ROLES_BLOQUEADOS
                )
            )

            # Discord permite como máximo 25 opciones.
            if len(opciones) >= 25:
                break

        if not opciones:

            opciones.append(
                discord.SelectOption(
                    label="No hay roles disponibles",
                    value="none"
                )
            )

        super().__init__(
            placeholder="Selecciona los roles bloqueados",
            min_values=0,
            max_values=len(opciones),
            options=opciones
        )

    async def callback(self, interaction):

        if not interaction.user.guild_permissions.administrator:

            await interaction.response.send_message(
                "No tienes permisos de administrador.",
                ephemeral=True
            )

            return

        if self.values == ["none"]:

            await interaction.response.send_message(
                "No hay roles disponibles.",
                ephemeral=True
            )

            return

        nuevos = set()

        for role_id in self.values:

            try:

                role_id = int(role_id)

            except ValueError:

                continue

            nuevos.add(role_id)

        actuales = set(ROLES_BLOQUEADOS)

        # Eliminar los que ya no están seleccionados.
        for role_id in actuales - nuevos:

            eliminar_rol_bloqueado(
                role_id
            )

        # Añadir los nuevos.
        for role_id in nuevos - actuales:

            guardar_rol_bloqueado(
                role_id
            )

        guild = interaction.guild

        nombres = []

        for role_id in sorted(ROLES_BLOQUEADOS):

            role = guild.get_role(
                role_id
            )

            if role:

                nombres.append(
                    role.mention
                )

        if nombres:

            texto = (
                "Roles que no pueden participar "
                "en las misiones:\n\n"
                + "\n".join(
                    f"- {rol}"
                    for rol in nombres
                )
            )

        else:

            texto = (
                "No hay ningún rol bloqueado. "
                "Todos pueden participar."
            )

        await interaction.response.send_message(
            texto,
            ephemeral=True
        )


class RolesBloqueadosView(discord.ui.View):

    def __init__(self, guild):

        super().__init__(
            timeout=300
        )

        self.add_item(
            RolesBloqueadosSelect(
                guild
            )
        )


# ============================================================
# CREACIÓN DE EVENTOS - SISTEMA CONVERSACIONAL
# ============================================================

CREACION_TIMEOUT = 1800


def mensaje_creacion(texto):
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "      CREACIÓN DE EVENTO\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{texto}\n\n"
        "Para cancelar la creación escribe `cancel`."
    )


async def esperar_mensaje_creacion(
    bot,
    user_id,
    channel_id,
    pregunta,
    validar=None
):
    while True:

        await bot.get_channel(channel_id).send(
            mensaje_creacion(pregunta)
        )

        def check(message):

            return (
                message.author.id == user_id
                and message.channel.id == channel_id
            )

        try:

            message = await bot.wait_for(
                "message",
                timeout=CREACION_TIMEOUT,
                check=check
            )

        except asyncio.TimeoutError:

            return None

        contenido = message.content.strip()

        if contenido.lower() == "cancel":

            return "CANCEL"

        if validar is not None:

            valido, resultado = validar(contenido)

            if not valido:

                await message.channel.send(
                    f"❌ {resultado}\n\n"
                    "Vuelve a introducirlo correctamente."
                )

                continue

            return resultado

        return contenido


# ============================================================
# VALIDADORES
# ============================================================

def validar_numero(contenido, minimo=1, maximo=None):

    try:

        numero = int(contenido)

    except ValueError:

        return (
            False,
            "Debes introducir un número."
        )

    if numero < minimo:

        return (
            False,
            f"El número debe ser como mínimo {minimo}."
        )

    if maximo is not None and numero > maximo:

        return (
            False,
            f"El número máximo permitido es {maximo}."
        )

    return True, numero


def validar_opciones_inscripcion(contenido):

    partes = [
        parte.strip()
        for parte in contenido.split(",")
        if parte.strip()
    ]

    if not partes:

        return (
            False,
            "Debes introducir al menos una opción."
        )

    opciones = []

    for parte in partes:

        if "(" in parte:

            if not parte.endswith(")"):

                return (
                    False,
                    f"Formato incorrecto en `{parte}`."
                )

            nombre, plazas = parte.rsplit("(", 1)

            nombre = nombre.strip()
            plazas = plazas[:-1].strip()

            if not nombre:

                return (
                    False,
                    "Una opción no puede tener el nombre vacío."
                )

            try:

                plazas = int(plazas)

            except ValueError:

                return (
                    False,
                    f"Las plazas de `{nombre}` deben ser un número."
                )

            if plazas < 1:

                return (
                    False,
                    "El número de plazas debe ser mayor que 0."
                )

        else:

            nombre = parte
            plazas = None

        if len(nombre) > 100:

            return (
                False,
                f"`{nombre}` supera los 100 caracteres."
            )

        opciones.append(
            {
                "name": nombre,
                "max_slots": plazas
            }
        )

    return True, opciones


# ============================================================
# MENÚ PRINCIPAL
# ============================================================

async def menu_creacion_evento(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        opciones = (
            "1. Información básica\n"
            "2. Fecha y duración\n"
            "3. Inscripciones\n"
            "4. Menciones\n"
            "5. Apariencia\n"
            "6. Ubicación\n"
            "7. Recordatorios\n"
            "8. Canal de publicación\n"
            "9. Vista previa\n"
            "10. Publicar evento\n"
            "11. Cancelar creación"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            opciones
            + "\n\nIntroduce el número de una sección.",
            validar=lambda x: validar_numero(
                x,
                1,
                11
            )
        )

        if respuesta is None:

            return False

        if respuesta == "CANCEL":

            return False

        if respuesta == 1:

            resultado = await menu_informacion_basica(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 2:

            resultado = await menu_fecha(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 3:

            resultado = await menu_inscripciones(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 4:

            resultado = await menu_menciones(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 5:

            resultado = await menu_apariencia(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 6:

            resultado = await menu_ubicacion(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 7:

            resultado = await menu_recordatorios(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 8:

            resultado = await menu_publicacion(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                return False

        elif respuesta == 9:

            await mostrar_vista_previa(
                bot,
                user_id,
                channel_id,
                datos
            )

        elif respuesta == 10:

            resultado = await finalizar_creacion_evento(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado:

                return True

        elif respuesta == 11:

            await bot.get_channel(channel_id).send(
                "❌ Creación de evento cancelada."
            )

            return False


# ============================================================
# INFORMACIÓN BÁSICA
# ============================================================

async def menu_informacion_basica(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        texto = (
            "INFORMACIÓN BÁSICA\n\n"
            "1. Cambiar título\n"
            "2. Cambiar descripción\n"
            "3. Volver"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                3
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 3:

            return

        if respuesta == 1:

            titulo = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "¿Cómo se llama tu evento?\n"
                "Se permiten 100 caracteres.",
            )

            if titulo == "CANCEL":

                continue

            if len(titulo) > 100:

                await bot.get_channel(channel_id).send(
                    "❌ El título supera los 100 caracteres.\n"
                    "Vuelve a introducirlo."
                )

                continue

            datos["title"] = titulo

            await bot.get_channel(channel_id).send(
                "✓ Título actualizado."
            )

        elif respuesta == 2:

            descripcion = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "Inserta la descripción del evento.\n"
                "Escribe `None` si no quieres descripción.\n"
                "Se permiten 2000 caracteres.",
            )

            if descripcion == "CANCEL":

                continue

            if descripcion.lower() == "none":

                descripcion = None

            elif len(descripcion) > 2000:

                await bot.get_channel(channel_id).send(
                    "❌ La descripción supera los 2000 caracteres.\n"
                    "Vuelve a introducirla."
                )

                continue

            datos["description"] = descripcion

            await bot.get_channel(channel_id).send(
                "✓ Descripción actualizada."
            )


# ============================================================
# FECHA Y DURACIÓN
# ============================================================

async def menu_fecha(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        texto = (
            "FECHA Y DURACIÓN\n\n"
            "1. Cambiar fecha y hora\n"
            "2. Cambiar duración\n"
            "3. Cambiar frecuencia\n"
            "4. Volver"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                4
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 4:

            return

        if respuesta == 1:

            fecha = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "¿Qué día y hora será el evento?\n\n"
                "Formato:\n"
                "`DD/MM/YYYY HH:MM`"
            )

            if fecha == "CANCEL":

                continue

            try:

                fecha_obj = datetime.strptime(
                    fecha,
                    "%d/%m/%Y %H:%M"
                )

                fecha_obj = fecha_obj.replace(
                    tzinfo=TIMEZONE
                )

            except ValueError:

                await bot.get_channel(channel_id).send(
                    "❌ Formato incorrecto.\n"
                    "Usa `DD/MM/YYYY HH:MM`.\n\n"
                    "Vuelve a introducir la fecha."
                )

                continue

            datos["start_time"] = fecha_obj.isoformat()

            await bot.get_channel(channel_id).send(
                "✓ Fecha actualizada."
            )

        elif respuesta == 2:

            duracion = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "¿Cuántos minutos durará el evento?"
                "\nEjemplo: `120`",
                validar=lambda x: validar_numero(
                    x,
                    1
                )
            )

            if duracion == "CANCEL":

                continue

            datos["duration_minutes"] = duracion

            await bot.get_channel(channel_id).send(
                "✓ Duración actualizada."
            )

        elif respuesta == 3:

            frecuencia = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "Selecciona la frecuencia:\n\n"
                "1. Una vez\n"
                "2. Diariamente\n"
                "3. Semanalmente\n"
                "4. Mensualmente",
                validar=lambda x: validar_numero(
                    x,
                    1,
                    4
                )
            )

            if frecuencia == "CANCEL":

                continue

            frecuencias = {
                1: "Una vez",
                2: "Diariamente",
                3: "Semanalmente",
                4: "Mensualmente"
            }

            datos["frequency"] = frecuencias[
                frecuencia
            ]

            await bot.get_channel(channel_id).send(
                "✓ Frecuencia actualizada."
            )


# ============================================================
# INSCRIPCIONES
# ============================================================

async def menu_inscripciones(
    bot,
    user_id,
    channel_id,
    datos
):

    if "signup_options" not in datos:

        datos["signup_options"] = []

    while True:

        texto = (
            "OPCIONES DE INSCRIPCIÓN\n\n"
            "1. Usar opciones predeterminadas\n"
            "2. Crear opciones personalizadas\n"
            "3. Sin opciones de inscripción\n"
            "4. Inscripciones múltiples\n"
            "5. Volver"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                5
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 5:

            return

        if respuesta == 1:

            datos["signup_options"] = [
                {
                    "name": "Aceptado",
                    "max_slots": None
                },
                {
                    "name": "Rechazado",
                    "max_slots": None
                },
                {
                    "name": "Provisional",
                    "max_slots": None
                }
            ]

            await bot.get_channel(channel_id).send(
                "✓ Se han establecido las opciones "
                "predeterminadas."
            )

        elif respuesta == 2:

            resultado = await configurar_opciones_personalizadas(
                bot,
                user_id,
                channel_id,
                datos
            )

            if resultado == "CANCEL":

                continue

        elif respuesta == 3:

            datos["signup_options"] = []

            await bot.get_channel(channel_id).send(
                "✓ Se han eliminado las opciones de inscripción."
            )

        elif respuesta == 4:

            datos["multiple_registrations"] = not datos.get(
                "multiple_registrations",
                False
            )

            estado = (
                "activadas"
                if datos["multiple_registrations"]
                else "desactivadas"
            )

            await bot.get_channel(channel_id).send(
                f"✓ Inscripciones múltiples {estado}."
            )


# ============================================================
# OPCIONES PERSONALIZADAS
# ============================================================

async def configurar_opciones_personalizadas(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        opciones = datos["signup_options"]

        if opciones:

            actuales = "\n".join(
                f"{i + 1}. "
                f"{opcion['name']} "
                f"({'ilimitadas' if opcion['max_slots'] is None else str(opcion['max_slots']) + ' plazas'})"
                for i, opcion in enumerate(opciones)
            )

        else:

            actuales = "No se han especificado opciones."

        texto = (
            "CONFIGURAR OPCIONES DE INSCRIPCIÓN\n\n"
            f"{actuales}\n\n"
            "1. Añadir opción\n"
            "2. Eliminar opción\n"
            "3. Terminar"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                3
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 3:

            return

        if respuesta == 1:

            entrada = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "¿Qué opción de inscripción quieres añadir?\n\n"
                "Formato:\n"
                "`Nombre(plazas máximas)`\n\n"
                "Ejemplos:\n"
                "`Tanque(2)`\n"
                "`Sanador(3)`\n"
                "`Daño`\n\n"
                "Si no pones número, las plazas serán ilimitadas."
            )

            if entrada == "CANCEL":

                continue

            valido, resultado = validar_opciones_inscripcion(
                entrada
            )

            if not valido:

                await bot.get_channel(channel_id).send(
                    f"❌ {resultado}\n\n"
                    "Vuelve a introducir la opción."
                )

                continue

            datos["signup_options"].extend(
                resultado
            )

            await bot.get_channel(channel_id).send(
                "✓ Opción añadida correctamente."
            )

        elif respuesta == 2:

            if not opciones:

                await bot.get_channel(channel_id).send(
                    "No hay opciones que eliminar."
                )

                continue

            numero = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "Introduce el número de la opción "
                "que quieres eliminar.",
                validar=lambda x: validar_numero(
                    x,
                    1,
                    len(opciones)
                )
            )

            if numero == "CANCEL":

                continue

            eliminada = datos["signup_options"].pop(
                numero - 1
            )

            await bot.get_channel(channel_id).send(
                f"✓ Se ha eliminado `{eliminada['name']}`."
            )


# ============================================================
# MENCIONES
# ============================================================

async def menu_menciones(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        texto = (
            "MENCIONES\n\n"
            "1. Añadir rol para mencionar\n"
            "2. Eliminar rol\n"
            "3. Ver roles\n"
            "4. Volver"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                4
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 4:

            return

        if "mention_roles" not in datos:

            datos["mention_roles"] = []

        if respuesta == 1:

            rol_id = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "Introduce la ID del rol que quieres mencionar.",
                validar=lambda x: validar_numero(
                    x,
                    1
                )
            )

            if rol_id == "CANCEL":

                continue

            if interaction_guild := bot.get_guild(
                GUILD_ID
            ):

                rol = interaction_guild.get_role(
                    rol_id
                )

                if rol is None:

                    await bot.get_channel(channel_id).send(
                        "❌ No encuentro ese rol."
                    )

                    continue

            if rol_id not in datos["mention_roles"]:

                datos["mention_roles"].append(
                    rol_id
                )

            await bot.get_channel(channel_id).send(
                "✓ Rol añadido."
            )

        elif respuesta == 2:

            if not datos["mention_roles"]:

                await bot.get_channel(channel_id).send(
                    "No hay roles configurados."
                )

                continue

            datos["mention_roles"].pop()

            await bot.get_channel(channel_id).send(
                "✓ Último rol eliminado."
            )

        elif respuesta == 3:

            if not datos["mention_roles"]:

                texto_roles = "No hay roles."

            else:

                guild = bot.get_guild(
                    GUILD_ID
                )

                nombres = []

                for role_id in datos["mention_roles"]:

                    role = guild.get_role(
                        role_id
                    )

                    nombres.append(
                        role.name
                        if role
                        else f"ID {role_id}"
                    )

                texto_roles = "\n".join(
                    f"- {nombre}"
                    for nombre in nombres
                )

            await bot.get_channel(channel_id).send(
                texto_roles
            )


# ============================================================
# APARIENCIA
# ============================================================

async def menu_apariencia(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        texto = (
            "APARIENCIA\n\n"
            "1. Cambiar color\n"
            "2. Cambiar imagen\n"
            "3. Volver"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                3
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 3:

            return

        if respuesta == 1:

            color = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "Introduce el color hexadecimal.\n"
                "Ejemplo: `5865F2`"
            )

            if color == "CANCEL":

                continue

            color = color.replace(
                "#",
                ""
            )

            if len(color) != 6:

                await bot.get_channel(channel_id).send(
                    "❌ El color debe tener 6 caracteres."
                )

                continue

            try:

                datos["color"] = int(
                    color,
                    16
                )

            except ValueError:

                await bot.get_channel(channel_id).send(
                    "❌ Color hexadecimal incorrecto."
                )

                continue

            await bot.get_channel(channel_id).send(
                "✓ Color actualizado."
            )

        elif respuesta == 2:

            imagen = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "Introduce la URL de la imagen.\n"
                "Escribe `None` para eliminarla."
            )

            if imagen == "CANCEL":

                continue

            if imagen.lower() == "none":

                datos["image_url"] = None

            else:

                datos["image_url"] = imagen

            await bot.get_channel(channel_id).send(
                "✓ Imagen actualizada."
            )


# ============================================================
# UBICACIÓN
# ============================================================

async def menu_ubicacion(
    bot,
    user_id,
    channel_id,
    datos
):

    ubicacion = await esperar_mensaje_creacion(
        bot,
        user_id,
        channel_id,
        "¿Dónde será el evento?\n"
        "Escribe `None` si no quieres especificar ubicación."
    )

    if ubicacion == "CANCEL":

        return "CANCEL"

    if ubicacion.lower() == "none":

        datos["location"] = None

    else:

        datos["location"] = ubicacion


# ============================================================
# RECORDATORIOS
# ============================================================

async def menu_recordatorios(
    bot,
    user_id,
    channel_id,
    datos
):

    while True:

        texto = (
            "RECORDATORIOS\n\n"
            "1. Añadir recordatorio\n"
            "2. Eliminar recordatorios\n"
            "3. Volver"
        )

        respuesta = await esperar_mensaje_creacion(
            bot,
            user_id,
            channel_id,
            texto,
            validar=lambda x: validar_numero(
                x,
                1,
                3
            )
        )

        if respuesta == "CANCEL":

            return "CANCEL"

        if respuesta == 3:

            return

        if respuesta == 1:

            minutos = await esperar_mensaje_creacion(
                bot,
                user_id,
                channel_id,
                "¿Cuántos minutos antes quieres "
                "enviar el recordatorio?",
                validar=lambda x: validar_numero(
                    x,
                    1
                )
            )

            if minutos == "CANCEL":

                continue

            if "reminders" not in datos:

                datos["reminders"] = []

            datos["reminders"].append(
                minutos
            )

            await bot.get_channel(channel_id).send(
                f"✓ Recordatorio añadido: {minutos} minutos antes."
            )

        elif respuesta == 2:

            datos["reminders"] = []

            await bot.get_channel(channel_id).send(
                "✓ Recordatorios eliminados."
            )


# ============================================================
# PUBLICACIÓN
# ============================================================

async def menu_publicacion(
    bot,
    user_id,
    channel_id,
    datos
):

    guild = bot.get_guild(
        GUILD_ID
    )

    if guild is None:

        await bot.get_channel(channel_id).send(
            "❌ No puedo encontrar el servidor."
        )

        return

    canales = [
        canal
        for canal in guild.text_channels
    ]

    texto = "CANALES DE PUBLICACIÓN\n\n"

    for i, canal in enumerate(
        canales,
        start=1
    ):

        texto += (
            f"{i}. {canal.mention}\n"
        )

    texto += "\n0. Volver"

    respuesta = await esperar_mensaje_creacion(
        bot,
        user_id,
        channel_id,
        texto,
        validar=lambda x: validar_numero(
            x,
            0,
            len(canales)
        )
    )

    if respuesta == "CANCEL":

        return "CANCEL"

    if respuesta == 0:

        return

    datos["publish_channel"] = canales[
        respuesta - 1
    ]


# ============================================================
# VISTA PREVIA
# ============================================================

async def mostrar_vista_previa(
    bot,
    user_id,
    channel_id,
    datos
):

    inicio = datos.get(
        "start_time"
    )

    if inicio:

        try:

            fecha = datetime.fromisoformat(
                inicio
            )

            fecha_texto = fecha.strftime(
                "%d/%m/%Y %H:%M"
            )

        except Exception:

            fecha_texto = inicio

    else:

        fecha_texto = "No configurada"

    opciones = datos.get(
        "signup_options",
        []
    )

    if opciones:

        opciones_texto = "\n".join(
            f"- {o['name']} "
            f"({'ilimitadas' if o['max_slots'] is None else str(o['max_slots']) + ' plazas'})"
            for o in opciones
        )

    else:

        opciones_texto = "Sin opciones"

    texto = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "          VISTA PREVIA\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**{datos.get('title', 'Sin título')}**\n\n"
        f"{datos.get('description') or 'Sin descripción'}\n\n"
        f"Fecha: {fecha_texto}\n"
        f"Duración: {datos.get('duration_minutes', 'No configurada')} minutos\n"
        f"Frecuencia: {datos.get('frequency', 'Una vez')}\n"
        f"Ubicación: {datos.get('location') or 'No especificada'}\n\n"
        "**Inscripciones:**\n"
        f"{opciones_texto}"
    )

    await bot.get_channel(channel_id).send(
        texto
    )


# ============================================================
# FINALIZAR
# ============================================================

async def finalizar_creacion_evento(
    bot,
    user_id,
    channel_id,
    datos
):

    obligatorios = [
        "title",
        "start_time"
    ]

    faltan = [
        campo
        for campo in obligatorios
        if not datos.get(campo)
    ]

    if faltan:

        nombres = {
            "title": "título",
            "start_time": "fecha"
        }

        texto = ", ".join(
            nombres[campo]
            for campo in faltan
        )

        await bot.get_channel(channel_id).send(
            "❌ No puedes publicar todavía.\n\n"
            f"Falta: {texto}.\n\n"
            "Configura esos datos antes de publicar."
        )

        return False

    confirmacion = await esperar_mensaje_creacion(
        bot,
        user_id,
        channel_id,
        "¿Quieres publicar el evento?\n\n"
        "1. Publicar\n"
        "2. Volver",
        validar=lambda x: validar_numero(
            x,
            1,
            2
        )
    )

    if confirmacion == "CANCEL":

        return False

    if confirmacion == 2:

        return False

    conn = conectar_db()

    try:

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO eventos (
                guild_id,
                creator_id,
                title,
                description,
                start_time,
                duration_minutes,
                frequency,
                color,
                image_url,
                multiple_registrations,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                GUILD_ID,
                user_id,
                datos["title"],
                datos.get("description"),
                datos["start_time"],
                datos.get(
                    "duration_minutes",
                    0
                ),
                datos.get(
                    "frequency",
                    "Una vez"
                ),
                datos.get(
                    "color",
                    0x5865F2
                ),
                datos.get(
                    "image_url"
                ),
                int(
                    datos.get(
                        "multiple_registrations",
                        False
                    )
                ),
                ahora().isoformat()
            )
        )

        event_id = cursor.lastrowid

        # ----------------------------------------------------
        # OPCIONES DE INSCRIPCIÓN
        # ----------------------------------------------------

        for opcion in datos.get(
            "signup_options",
            []
        ):

            cursor.execute(
                """
                INSERT INTO opciones_inscripcion (
                    event_id,
                    name,
                    emoji,
                    max_slots
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    event_id,
                    opcion["name"],
                    opcion.get("emoji"),
                    opcion.get("max_slots")
                )
            )

        # ----------------------------------------------------
        # MENCIONES
        # ----------------------------------------------------

        for role_id in datos.get(
            "mention_roles",
            []
        ):

            cursor.execute(
                """
                INSERT INTO evento_menciones (
                    event_id,
                    role_id
                )
                VALUES (?, ?)
                """,
                (
                    event_id,
                    role_id
                )
            )

        # ----------------------------------------------------
        # RECORDATORIOS
        # ----------------------------------------------------

        for minutos in datos.get(
            "reminders",
            []
        ):

            cursor.execute(
                """
                INSERT INTO recordatorios (
                    event_id,
                    minutes_before,
                    sent
                )
                VALUES (?, ?, 0)
                """,
                (
                    event_id,
                    minutos
                )
            )

        conn.commit()

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()

    # --------------------------------------------------------
    # PUBLICAR
    # --------------------------------------------------------

    canal = datos.get(
        "publish_channel"
    )

    if canal is None:

        guild = bot.get_guild(
            GUILD_ID
        )

        if guild:

            canal = discord.utils.get(
                guild.text_channels,
                name="eventos"
            )

            if canal is None:

                canal = discord.utils.get(
                    guild.text_channels,
                    name="general"
                )

    if canal is None:

        await bot.get_channel(channel_id).send(
            "❌ No encuentro un canal donde publicar."
        )

        return False

    mensaje = await canal.send(
        embed=crear_embed_publicado(
            event_id
        ),
        view=EventoView(
            event_id
        )
    )

    conn = conectar_db()

    try:

        conn.execute(
            """
            UPDATE eventos
            SET channel_id = ?,
                message_id = ?
            WHERE id = ?
            """,
            (
                canal.id,
                mensaje.id,
                event_id
            )
        )

        conn.commit()

    finally:

        conn.close()

    await bot.get_channel(channel_id).send(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "       EVENTO CREADO\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Tu evento ha sido publicado en {canal.mention}.\n"
        f"ID del evento: `{event_id}`"
    )

    return True


# ============================================================
# CREACIÓN DE EVENTOS - FLUJO CONVERSACIONAL
# ============================================================

async def flujo_pregunta(
    usuario,
    texto
):
    """
    Envía una pregunta al DM y espera la respuesta.

    Comandos globales:
    - cancel -> cancela toda la creación
    - reset   -> vuelve a la sección anterior
    """

    await usuario.send(texto)

    contenido = await esperar_mensaje(
        usuario,
        timeout=900
    )

    if contenido is None:
        return "cancel"

    contenido = contenido.strip()

    if contenido.lower() == "cancel":
        return "cancel"

    if contenido.lower() == "reset":
        return "reset"

    return contenido


async def flujo_opcion(
    usuario,
    texto,
    opciones,
    permitir_back=True
):
    """
    Muestra una lista numerada y espera un número válido.
    """

    while True:

        contenido = await flujo_pregunta(
            usuario,
            texto
        )

        if contenido in ("cancel", "reset"):
            return contenido

        try:
            numero = int(contenido)

        except ValueError:

            await usuario.send(
                "Respuesta no válida.\n\n"
                "Introduce únicamente el número "
                "de una de las opciones."
            )

            continue

        if numero < 1 or numero > len(opciones):

            await usuario.send(
                "Esa opción no existe.\n\n"
                f"Introduce un número entre 1 y {len(opciones)}."
            )

            continue

        return numero


async def seleccionar_canal_conversacional(
    usuario,
    guild,
    titulo
):

    canales = [
        canal
        for canal in guild.text_channels
        if not canal.is_news()
    ]

    if not canales:

        await usuario.send(
            "No hay canales de texto disponibles."
        )

        return None

    pagina = 0
    por_pagina = 15

    while True:

        inicio = pagina * por_pagina
        fin = inicio + por_pagina

        pagina_canales = canales[inicio:fin]

        texto = (
            f"**{titulo}**\n\n"
        )

        for numero, canal in enumerate(
            pagina_canales,
            start=1
        ):

            texto += (
                f"{numero}. {canal.mention}\n"
            )

        if fin < len(canales):

            texto += (
                "\n16. Ver siguientes canales"
            )

        texto += (
            "\n\n"
            "Introduce el número del canal.\n"
            "Escribe `reset` para volver a empezar a empezar.\n"
            "Escribe `cancel` para cancelar."
        )

        contenido = await flujo_pregunta(
            usuario,
            texto
        )

        if contenido == "cancel":
            return "cancel"

        if contenido == "reset":
            return "reset"

        try:
            numero = int(contenido)

        except ValueError:

            await usuario.send(
                "Introduce únicamente un número."
            )

            continue

        if (
            fin < len(canales)
            and numero == 16
        ):

            pagina += 1

            continue

        if (
            numero < 1
            or numero > len(pagina_canales)
        ):

            await usuario.send(
                "Ese número no corresponde "
                "a ningún canal."
            )

            continue

        return pagina_canales[
            numero - 1
        ]


async def seleccionar_roles_conversacional(
    usuario,
    guild,
    titulo,
    permitir_ninguno=True
):

    roles = [
        role
        for role in guild.roles
        if not role.is_default()
        and not role.managed
    ]

    if not roles:

        return []

    pagina = 0
    por_pagina = 15

    while True:

        inicio = pagina * por_pagina
        fin = inicio + por_pagina

        pagina_roles = roles[inicio:fin]

        texto = (
            f"**{titulo}**\n\n"
        )

        if permitir_ninguno:

            texto += (
                "1. Ninguno\n"
            )

            desplazamiento = 2

        else:

            desplazamiento = 1

        for indice, role in enumerate(
            pagina_roles,
            start=desplazamiento
        ):

            texto += (
                f"{indice}. {role.name}\n"
            )

        siguiente_numero = (
            desplazamiento
            + len(pagina_roles)
        )

        if fin < len(roles):

            texto += (
                f"{siguiente_numero}. "
                "Ver siguientes roles\n"
            )

        texto += (
            "\nIntroduce el número del rol.\n"
            "Puedes escribir varios números separados "
            "por comas.\n"
            "Ejemplo: `2, 4, 7`\n\n"
            "Escribe `reset` para volver a empezar.\n"
            "Escribe `cancel` para cancelar."
        )

        contenido = await flujo_pregunta(
            usuario,
            texto
        )

        if contenido == "cancel":
            return "cancel"

        if contenido == "reset":
            return "reset"

        try:

            numeros = [
                int(x.strip())
                for x in contenido.split(",")
            ]

        except ValueError:

            await usuario.send(
                "Formato incorrecto.\n\n"
                "Escribe los números separados "
                "por comas.\n\n"
                "Ejemplo: `2, 4, 7`"
            )

            continue

        if (
            permitir_ninguno
            and numeros == [1]
        ):

            return []

        if (
            fin < len(roles)
            and numeros == [siguiente_numero]
        ):

            pagina += 1

            continue

        roles_seleccionados = []

        valido = True

        for numero in numeros:

            indice = numero - desplazamiento

            if (
                indice < 0
                or indice >= len(pagina_roles)
            ):

                valido = False
                break

            role = pagina_roles[indice]

            if role not in roles_seleccionados:

                roles_seleccionados.append(
                    role
                )

        if not valido:

            await usuario.send(
                "Uno de los números indicados "
                "no corresponde a un rol válido."
            )

            continue

        return roles_seleccionados


async def preguntar_texto_conversacional(
    usuario,
    pregunta,
    max_length,
    permitir_none=False
):

    while True:

        contenido = await flujo_pregunta(
            usuario,
            pregunta
            + "\n\n"
            + f"Se permiten {max_length} caracteres."
            + "\n"
            + "Escribe `reset` para volver a empezar."
            + "\n"
            + "Escribe `cancel` para cancelar."
        )

        if contenido in (
            "cancel",
            "reset"
        ):

            return contenido

        if (
            permitir_none
            and contenido.lower() == "none"
        ):

            return None

        if len(contenido) > max_length:

            await usuario.send(
                "El texto supera el límite.\n\n"
                f"Caracteres introducidos: "
                f"{len(contenido)}\n"
                f"Límite: {max_length}\n\n"
                "Vuelve a introducirlo cumpliendo "
                "el límite de caracteres."
            )

            continue

        return contenido


async def preguntar_fecha_conversacional(
    usuario
):

    while True:

        contenido = await flujo_pregunta(
            usuario,
            "**¿Qué día y a qué hora será el evento?**\n\n"
            "Formato:\n"
            "`DD/MM/YYYY HH:MM`\n\n"
            "Ejemplo:\n"
            "`30/08/2026 20:00`\n\n"
            "La hora se interpreta como hora de España "
            "(Europe/Madrid).\n\n"
            "Escribe `reset` para volver a empezar.\n"
            "Escribe `cancel` para cancelar."
        )

        if contenido in (
            "cancel",
            "reset"
        ):

            return contenido

        fecha = parsear_fecha(
            contenido
        )

        if fecha is None:

            await usuario.send(
                "Formato incorrecto.\n\n"
                "Vuelve a introducir la fecha usando:\n"
                "`DD/MM/YYYY HH:MM`"
            )

            continue

        if fecha <= ahora():

            await usuario.send(
                "La fecha debe estar en el futuro.\n\n"
                "Vuelve a introducirla."
            )

            continue

        return fecha


async def preguntar_duracion_conversacional(
    usuario
):

    while True:

        contenido = await flujo_pregunta(
            usuario,
            "**¿Cuánto durará el evento?**\n\n"
            "Ejemplos:\n"
            "`2h`\n"
            "`90m`\n"
            "`2h 30m`\n\n"
            "Escribe `reset` para volver a empezar.\n"
            "Escribe `cancel` para cancelar."
        )

        if contenido in (
            "cancel",
            "reset"
        ):

            return contenido

        contenido = contenido.lower()

        horas = re.search(
            r"(\d+)\s*h",
            contenido
        )

        minutos = re.search(
            r"(\d+)\s*m",
            contenido
        )

        total = 0

        if horas:

            total += (
                int(horas.group(1))
                * 60
            )

        if minutos:

            total += int(
                minutos.group(1)
            )

        if total <= 0:

            await usuario.send(
                "Duración incorrecta.\n\n"
                "Ejemplo: `2h 30m`\n\n"
                "Vuelve a introducirla."
            )

            continue

        return total


async def preguntar_opciones_inscripcion(
    usuario
):

    while True:

        contenido = await flujo_pregunta(
            usuario,
            "**¿Cómo funcionarán las inscripciones?**\n\n"
            "1. Usar una opción predeterminada\n"
            "2. Configurar opciones de inscripción\n"
            "3. Sin opciones de inscripción\n\n"
            "Introduce el número de una opción.\n"
            "Escribe `reset` para volver a empezar.\n"
            "Escribe `cancel` para cancelar."
        )

        if contenido in (
            "cancel",
            "reset"
        ):

            return contenido

        if contenido == "1":

            return [
                {
                    "name": "Aceptado",
                    "emoji": "",
                    "max_slots": None
                }
            ]

        if contenido == "3":

            return []

        if contenido != "2":

            await usuario.send(
                "Introduce `1`, `2` o `3`."
            )

            continue

        while True:

            contenido = await flujo_pregunta(
                usuario,
                "**Configurar opciones de inscripción**\n\n"
                "Introduce las opciones con este formato:\n\n"
                "`Nombre(plazas máximas), Nombre(plazas máximas)`\n\n"
                "Ejemplo:\n"
                "`Tanque(2), DPS(5), Sanador(3)`\n\n"
                "Para plazas ilimitadas:\n"
                "`Tanque, DPS`\n\n"
                "También puedes usar:\n"
                "`Tanque(ilimitado)`\n\n"
                "Escribe `reset` para volver a empezar.\n"
                "Escribe `cancel` para cancelar."
            )

            if contenido in (
                "cancel",
                "reset"
            ):

                return contenido

            partes = contenido.split(",")

            opciones = []
            errores = []

            for parte in partes:

                parte = parte.strip()

                if not parte:
                    continue

                match = re.fullmatch(
                    r"\s*(.+?)\s*"
                    r"(?:\(\s*(\d+|ilimitado)\s*\))?"
                    r"\s*",
                    parte,
                    re.IGNORECASE
                )

                if not match:

                    errores.append(
                        parte
                    )

                    continue

                nombre = (
                    match.group(1)
                    .strip()
                )

                plazas_raw = (
                    match.group(2)
                )

                if not nombre:

                    errores.append(
                        parte
                    )

                    continue

                if len(nombre) > 100:

                    errores.append(
                        f"{nombre[:30]}..."
                        " (nombre demasiado largo)"
                    )

                    continue

                if plazas_raw is None:

                    max_slots = None

                elif (
                    plazas_raw.lower()
                    == "ilimitado"
                ):

                    max_slots = None

                else:

                    max_slots = int(
                        plazas_raw
                    )

                    if max_slots <= 0:

                        errores.append(
                            parte
                        )

                        continue

                opciones.append(
                    {
                        "name": nombre,
                        "emoji": "",
                        "max_slots": max_slots
                    }
                )

            if errores:

                await usuario.send(
                    "No pude interpretar algunas opciones.\n\n"
                    "Formato correcto:\n"
                    "`Tanque(2), DPS(5), Sanador(3)`\n\n"
                    "Para ilimitadas:\n"
                    "`Tanque, DPS`\n\n"
                    "Vuelve a introducir las opciones "
                    "cumpliendo el formato."
                )

                continue

            if not opciones:

                await usuario.send(
                    "No has introducido ninguna opción válida.\n\n"
                    "Vuelve a introducirlas."
                )

                continue

            return opciones


async def preguntar_recordatorios_conversacional(
    usuario
):

    while True:

        contenido = await flujo_pregunta(
            usuario,
            "**¿Quieres enviar recordatorios?**\n\n"
            "1. No enviar recordatorios\n"
            "2. Configurar recordatorios\n\n"
            "Introduce el número.\n"
            "Escribe `reset` para volver a empezar.\n"
            "Escribe `cancel` para cancelar."
        )

        if contenido in (
            "cancel",
            "reset"
        ):

            return contenido

        if contenido == "1":

            return []

        if contenido != "2":

            await usuario.send(
                "Introduce `1` o `2`."
            )

            continue

        while True:

            contenido = await flujo_pregunta(
                usuario,
                "**Introduce los recordatorios.**\n\n"
                "Puedes usar:\n"
                "`7d, 24h, 1h, 30m`\n\n"
                "Unidades:\n"
                "m = minutos\n"
                "h = horas\n"
                "d = días\n\n"
                "Escribe `reset` para volver a empezar.\n"
                "Escribe `cancel` para cancelar."
            )

            if contenido in (
                "cancel",
                "reset"
            ):

                return contenido

            recordatorios = []

            for parte in contenido.split(","):

                parte = parte.strip().lower()

                match = re.fullmatch(
                    r"(\d+)\s*"
                    r"(m|min|h|hora|horas|d|dia|dias|día|días)",
                    parte
                )

                if not match:
                    continue

                cantidad = int(
                    match.group(1)
                )

                unidad = match.group(2)

                if unidad.startswith("m"):

                    minutos = cantidad

                elif unidad.startswith("h"):

                    minutos = cantidad * 60

                else:

                    minutos = cantidad * 1440

                if minutos > 0:

                    recordatorios.append(
                        minutos
                    )

            if not recordatorios:

                await usuario.send(
                    "No pude interpretar ningún recordatorio.\n\n"
                    "Vuelve a introducirlos usando, por ejemplo:\n"
                    "`7d, 24h, 1h, 30m`"
                )

                continue

            return sorted(
                set(recordatorios),
                reverse=True
            )


async def ejecutar_flujo_creacion_evento(
    interaction
):

    usuario = interaction.user
    guild = interaction.guild

    if guild is None:

        return

    user_id = usuario.id

    creaciones.pop(
        user_id,
        None
    )

    datos = obtener_datos(
        user_id
    )

    # ========================================================
    # CANAL DE PUBLICACIÓN
    # ========================================================

    canal_actual = interaction.channel

    if isinstance(
        canal_actual,
        discord.TextChannel
    ):

        canal_defecto = canal_actual

    else:

        canal_defecto = discord.utils.get(
            guild.text_channels,
            name="eventos"
        )

        if canal_defecto is None:

            canal_defecto = discord.utils.get(
                guild.text_channels,
                name="general"
            )

    while True:

        texto = (
            "**¿Dónde quieres publicar el evento?**\n\n"
        )

        if canal_defecto:

            texto += (
                f"1. En el canal actual "
                f"{canal_defecto.mention}\n"
            )

        else:

            texto += (
                "1. En el canal predeterminado\n"
            )

        texto += (
            "2. En otro canal\n\n"
            "Introduce el número de una opción.\n"
            "Escribe `cancel` para cancelar."
        )

        resultado = await flujo_opcion(
            usuario,
            texto,
            [
                "actual",
                "otro"
            ]
        )

        if resultado == "cancel":

            creaciones.pop(
                user_id,
                None
            )

            await usuario.send(
                "Creación del evento cancelada."
            )

            return

        if resultado == 1:

            if canal_defecto is None:

                await usuario.send(
                    "No pude encontrar un canal "
                    "predeterminado."
                )

                continue

            datos["publish_channel"] = (
                canal_defecto
            )

            break

        if resultado == 2:

            canal = await seleccionar_canal_conversacional(
                usuario,
                guild,
                "¿En qué canal quieres publicar el evento?"
            )

            if canal == "cancel":

                creaciones.pop(
                    user_id,
                    None
                )

                await usuario.send(
                    "Creación del evento cancelada."
                )

                return

            if canal == "reset":

                continue

            datos["publish_channel"] = canal

            break

    # ========================================================
    # SECCIÓN 1 - INFORMACIÓN BÁSICA
    # ========================================================

    resultado = await preguntar_texto_conversacional(
        usuario,
        "**¿Cómo se llama tu evento?**",
        100
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["title"] = resultado

    resultado = await preguntar_texto_conversacional(
        usuario,
        "**Inserta la descripción del evento.**\n"
        "Escribe `None` si no quieres descripción.",
        3000,
        permitir_none=True
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["description"] = resultado

    # ========================================================
    # SECCIÓN 2 - FECHA Y DURACIÓN
    # ========================================================

    resultado = await preguntar_fecha_conversacional(
        usuario
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["start_time"] = resultado

    resultado = await preguntar_duracion_conversacional(
        usuario
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["duration"] = resultado

    resultado = await flujo_opcion(
        usuario,
        "**¿Será un evento recurrente?**\n\n"
        "1. Una vez\n"
        "2. Diariamente\n"
        "3. Semanalmente\n"
        "4. Mensualmente\n\n"
        "Introduce el número.\n"
        "Escribe `reset` para volver a empezar.\n"
        "Escribe `cancel` para cancelar.",
        [
            "Una vez",
            "Diariamente",
            "Semanalmente",
            "Mensualmente"
        ]
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["frequency"] = [
        "Una vez",
        "Diariamente",
        "Semanalmente",
        "Mensualmente"
    ][resultado - 1]

    # ========================================================
    # SECCIÓN 3 - INSCRIPCIONES
    # ========================================================

    resultado = await preguntar_opciones_inscripcion(
        usuario
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["options"] = resultado

    resultado = await flujo_opcion(
        usuario,
        "**¿Se permiten inscripciones múltiples?**\n\n"
        "1. No, una inscripción por usuario\n"
        "2. Sí, varias inscripciones por usuario\n\n"
        "Introduce el número.\n"
        "Escribe `reset` para volver a empezar.\n"
        "Escribe `cancel` para cancelar.",
        [
            "No",
            "Sí"
        ]
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["multiple"] = (
        resultado == 2
    )

    # ========================================================
    # SECCIÓN 4 - MENCIONES
    # ========================================================

    resultado = await seleccionar_roles_conversacional(
        usuario,
        guild,
        "¿Qué roles quieres mencionar?",
        permitir_ninguno=True
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["mentions"] = resultado

    # ========================================================
    # SECCIÓN 5 - APARIENCIA
    # ========================================================

    resultado = await flujo_opcion(
        usuario,
        "**¿Qué color quieres para el evento?**\n\n"
        + "\n".join(
            f"{numero}. {nombre}"
            for numero, nombre
            in enumerate(
                COLORES.keys(),
                start=1
            )
        )
        + "\n\nIntroduce el número.\n"
        "Escribe `reset` para volver a empezar a empezar.\n"
        "Escribe `cancel` para cancelar.",
        list(COLORES.keys())
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    color_nombre = list(
        COLORES.keys()
    )[resultado - 1]

    datos["color_name"] = color_nombre
    datos["color"] = COLORES[
        color_nombre
    ]

    resultado = await preguntar_texto_conversacional(
        usuario,
        "**¿Quieres añadir una imagen al evento?**\n\n"
        "Escribe la URL de la imagen.\n"
        "Escribe `None` si no quieres imagen.",
        2048,
        permitir_none=True
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    if resultado is None:

        datos["image"] = ""

    else:

        while not (
            resultado.startswith("http://")
            or resultado.startswith("https://")
        ):

            await usuario.send(
                "La URL no parece válida.\n\n"
                "Debe comenzar por `http://` "
                "o `https://`.\n\n"
                "Vuelve a introducirla."
            )

            resultado = await flujo_pregunta(
                usuario,
                "Introduce la URL de la imagen.\n"
                "Escribe `reset` para volver a empezar a empezar.\n"
                "Escribe `cancel` para cancelar."
            )

            if resultado in (
                "cancel",
                "reset"
            ):

                if resultado == "cancel":

                    creaciones.pop(
                        user_id,
                        None
                    )

                    await usuario.send(
                        "Creación del evento cancelada."
                    )

                    return

                return await ejecutar_flujo_creacion_evento(
                    interaction
                )

        datos["image"] = resultado

    # ========================================================
    # SECCIÓN 6 - UBICACIÓN
    # ========================================================

    resultado = await seleccionar_canal_conversacional(
        usuario,
        guild,
        "¿Dónde se realizará el evento?"
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["location"] = resultado

    # ========================================================
    # SECCIÓN 7 - RECORDATORIOS
    # ========================================================

    resultado = await preguntar_recordatorios_conversacional(
        usuario
    )

    if resultado == "cancel":

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Creación del evento cancelada."
        )

        return

    if resultado == "reset":

        return await ejecutar_flujo_creacion_evento(
            interaction
        )

    datos["reminders"] = resultado

    # ========================================================
    # VISTA PREVIA
    # ========================================================

    embed = discord.Embed(
        title=(
            datos["title"]
            or "Evento sin título"
        ),
        description=(
            datos["description"]
            or "Sin descripción"
        ),
        color=discord.Color(
            datos["color"]
            or 0x5865F2
        )
    )

    if datos["start_time"]:

        embed.add_field(
            name="Hora de inicio",
            value=(
                f"<t:{timestamp_discord(datos['start_time'])}:F>"
            ),
            inline=False
        )

    if datos["duration"]:

        embed.add_field(
            name="Duración",
            value=formatear_duracion(
                datos["duration"]
            )
        )

    embed.add_field(
        name="Frecuencia",
        value=datos["frequency"]
    )

    if datos["publish_channel"]:

        embed.add_field(
            name="Canal de publicación",
            value=datos["publish_channel"].mention
        )

    if datos["location"]:

        embed.add_field(
            name="Ubicación",
            value=datos["location"].mention
        )

    if datos["options"]:

        opciones_texto = []

        for opcion in datos["options"]:

            if opcion["max_slots"]:

                opciones_texto.append(
                    f"{opcion['name']} "
                    f"({opcion['max_slots']} plazas)"
                )

            else:

                opciones_texto.append(
                    f"{opcion['name']} "
                    "(ilimitado)"
                )

        embed.add_field(
            name="Inscripciones",
            value="\n".join(
                opciones_texto
            )[:1024],
            inline=False
        )

    if datos["mentions"]:

        embed.add_field(
            name="Menciones",
            value=" ".join(
                role.mention
                for role in datos["mentions"]
            ),
            inline=False
        )

    embed.add_field(
        name="Color",
        value=datos["color_name"]
    )

    embed.add_field(
        name="Inscripciones múltiples",
        value=(
            "Sí"
            if datos["multiple"]
            else "No"
        )
    )

    embed.add_field(
        name="Recordatorios",
        value=formatear_recordatorios(
            datos["reminders"]
        )
    )

    if datos["image"]:

        embed.set_image(
            url=datos["image"]
        )

    while True:

        await usuario.send(
            "**VISTA PREVIA DEL EVENTO**",
            embed=embed
        )

        resultado = await flujo_opcion(
            usuario,
            "¿Qué quieres hacer?\n\n"
            "1. Publicar evento\n"
            "2. Volver a recordatorios\n"
            "3. Cancelar creación\n\n"
            "Introduce el número.",
            [
                "publicar",
                "reset",
                "cancelar"
            ]
        )

        if resultado == 1:

            break

        if resultado == 3:

            creaciones.pop(
                user_id,
                None
            )

            await usuario.send(
                "Creación del evento cancelada."
            )

            return

        if resultado == 2:

            resultado = await preguntar_recordatorios_conversacional(
                usuario
            )

            if resultado == "cancel":

                creaciones.pop(
                    user_id,
                    None
                )

                await usuario.send(
                    "Creación del evento cancelada."
                )

                return

            if resultado != "reset":

                datos["reminders"] = resultado

            continue

    # ========================================================
    # PUBLICAR
    # ========================================================

    try:

        canal = datos["publish_channel"]

        if canal is None:

            await usuario.send(
                "No se ha configurado un canal de publicación."
            )

            return

        conn = conectar_db()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO eventos (
                guild_id,
                channel_id,
                creator_id,
                title,
                description,
                start_time,
                duration_minutes,
                frequency,
                color,
                location_channel_id,
                image_url,
                multiple_registrations,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild.id,
                canal.id,
                user_id,
                datos["title"],
                datos["description"],
                datos["start_time"].isoformat(),
                datos["duration"],
                datos["frequency"],
                datos["color"],
                datos["location"].id,
                datos["image"],
                (
                    1
                    if datos["multiple"]
                    else 0
                ),
                ahora().isoformat()
            )
        )

        evento_id = cursor.lastrowid

        for opcion in datos["options"]:

            cursor.execute(
                """
                INSERT INTO opciones_inscripcion (
                    event_id,
                    name,
                    emoji,
                    max_slots
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    evento_id,
                    opcion["name"],
                    opcion.get("emoji", ""),
                    opcion["max_slots"]
                )
            )

        for role in datos["mentions"]:

            cursor.execute(
                """
                INSERT INTO evento_menciones (
                    event_id,
                    role_id
                )
                VALUES (?, ?)
                """,
                (
                    evento_id,
                    role.id
                )
            )

        for minutos in datos["reminders"]:

            cursor.execute(
                """
                INSERT INTO recordatorios (
                    event_id,
                    minutes_before
                )
                VALUES (?, ?)
                """,
                (
                    evento_id,
                    minutos
                )
            )

        conn.commit()
        conn.close()

        # ----------------------------------------------------
        # CREAR EMBED
        # ----------------------------------------------------

        embed = crear_embed_publicado(
            evento_id
        )

        # ----------------------------------------------------
        # MENCIONES
        # ----------------------------------------------------

        menciones = ""

        if datos["mentions"]:

            menciones = " ".join(
                role.mention
                for role in datos["mentions"]
            )

        # ----------------------------------------------------
        # VIEW
        # ----------------------------------------------------

        view = EventoView(
            evento_id
        )

        mensaje = await canal.send(
            content=(
                menciones
                if menciones
                else None
            ),
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(
                roles=True
            )
        )

        # ----------------------------------------------------
        # GUARDAR MESSAGE ID
        # ----------------------------------------------------

        conn = conectar_db()

        conn.execute(
            """
            UPDATE eventos
            SET message_id = ?
            WHERE id = ?
            """,
            (
                mensaje.id,
                evento_id
            )
        )

        conn.commit()
        conn.close()

        # ----------------------------------------------------
        # VIEW PERSISTENTE
        # ----------------------------------------------------

        bot.add_view(
            EventoView(
                evento_id
            )
        )

        creaciones.pop(
            user_id,
            None
        )

        await usuario.send(
            "Evento publicado correctamente en "
            f"{canal.mention}."
        )

    except Exception as e:

        import traceback

        print()
        print(
            "=================================================="
        )

        print(
            "ERROR PUBLICANDO EVENTO"
        )

        print(
            repr(e)
        )

        traceback.print_exc()

        print(
            "=================================================="
        )

        await usuario.send(
            "Ha ocurrido un error al publicar "
            "el evento.\n\n"
            f"Error: `{e}`"
        )


# ============================================================
# /CREAR_EVENTO - FLUJO SECUENCIAL
# ============================================================

@bot.tree.command(
    guild=GUILD_OBJECT,
    name="crear_evento",
    description="Crea un nuevo evento"
)
async def crear_evento(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "Este comando solamente puede utilizarse "
            "dentro del servidor.",
            ephemeral=True
        )
        return

    miembro = interaction.guild.get_member(
        interaction.user.id
    )

    if miembro is None:

        try:
            miembro = await interaction.guild.fetch_member(
                interaction.user.id
            )
        except Exception:
            miembro = None

    if miembro is None:

        await interaction.response.send_message(
            "No pude comprobar tus roles.",
            ephemeral=True
        )
        return

    # ========================================================
    # COMPROBAR PERMISO
    # ========================================================

    rol_dm = interaction.guild.get_role(
        ROL_DM
    )

    if rol_dm is None:

        await interaction.response.send_message(
            "No encuentro el rol DM configurado.",
            ephemeral=True
        )
        return

    if rol_dm not in miembro.roles:

        await interaction.response.send_message(
            "No tienes permiso para crear eventos.",
            ephemeral=True
        )
        return

    # ========================================================
    # ABRIR DM
    # ========================================================

    try:

        dm = await interaction.user.create_dm()

    except discord.Forbidden:

        await interaction.response.send_message(
            "No puedo abrirte los mensajes privados. "
            "Activa los mensajes directos del servidor.",
            ephemeral=True
        )
        return

    except Exception as e:

        print(
            "ERROR CREANDO DM:",
            repr(e)
        )

        await interaction.response.send_message(
            "No pude abrir tus mensajes privados.",
            ephemeral=True
        )
        return

    # ========================================================
    # AVISAR EN EL SERVIDOR
    # ========================================================

    await interaction.response.send_message(
        "Te he enviado la creación del evento "
        "por mensaje privado.",
        ephemeral=True
    )

    # ========================================================
    # INICIAR FLUJO
    # ========================================================

    try:

        await ejecutar_flujo_creacion_evento(
            interaction
        )

    except discord.Forbidden:

        print(
            "ERROR: Discord ha rechazado el envío del DM."
        )

    except Exception as e:

        print(
            "ERROR EN EJECUTAR_FLUJO_CREACION_EVENTO:",
            repr(e)
        )

        try:

            await interaction.followup.send(
                "Ha ocurrido un error al iniciar "
                "la creación del evento.",
                ephemeral=True
            )

        except Exception:
            pass
        
# ============================================================
# EJECUTAR BOT
# ============================================================    
bot.run(TOKEN)
