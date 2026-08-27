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
# ENVIAR NUEVO PANEL
# ============================================================

async def actualizar_panel(user_id):

    datos = obtener_datos(user_id)

    try:

        canal = bot.get_channel(
            datos["panel_channel_id"]
        )

        if canal is None:

            canal = await bot.fetch_channel(
                datos["panel_channel_id"]
            )

        if datos["panel_message_id"]:

            try:

                mensaje_anterior = (
                    await canal.fetch_message(
                        datos["panel_message_id"]
                    )
                )

                await mensaje_anterior.edit(
                    view=None
                )

            except Exception:

                pass

        nuevo_mensaje = await canal.send(
            embed=crear_panel_embed(user_id),
            view=CrearEventoView(user_id)
        )

        datos["panel_message_id"] = nuevo_mensaje.id
        datos["panel_channel_id"] = canal.id

    except Exception as e:

        print(
            "ERROR ACTUALIZANDO PANEL:",
            repr(e)
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
# OPCIONES DE INSCRIPCIÓN
# ============================================================

# ============================================================
# OPCIONES DE INSCRIPCIÓN
# ============================================================

class OpcionesButton(discord.ui.Button):

    def __init__(self, user_id):

        self.user_id = user_id

        super().__init__(
            label="6. Opciones de inscripción",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        await interaction.response.send_message(
            "Escribe las opciones de inscripción separadas "
            "por comas.\n\n"
            "Formato:\n"
            "`Nombre(plazas máximas), Nombre(plazas máximas)`\n\n"
            "Ejemplo:\n"
            "`Tanque(2), DPS(5), Sanador(3)`\n\n"
            "Para plazas ilimitadas puedes escribir solamente "
            "el nombre:\n"
            "`Jugador, Espectador(10), Reserva`\n\n"
            "También puedes escribir explícitamente:\n"
            "`Jugador(ilimitado)`"
        )

        contenido = await esperar_mensaje(
            interaction.user
        )

        if not contenido:

            return

        contenido = contenido.strip()

        # --------------------------------------------------------
        # NINGUNA OPCIÓN
        # --------------------------------------------------------

        if contenido.lower() == "ninguna":

            datos = obtener_datos(
                self.user_id
            )

            datos["options"] = []

            await actualizar_panel(
                self.user_id
            )

            return

        # --------------------------------------------------------
        # SEPARAR OPCIONES
        # --------------------------------------------------------

        partes = contenido.split(",")

        opciones = []

        errores = []

        for parte in partes:

            parte = parte.strip()

            if not parte:

                continue

            # ----------------------------------------------------
            # FORMATO
            #
            # Nombre
            # Nombre(5)
            # Nombre(ilimitado)
            # ----------------------------------------------------

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

            nombre = match.group(1).strip()

            plazas_raw = match.group(2)

            # ----------------------------------------------------
            # COMPROBAR NOMBRE
            # ----------------------------------------------------

            if not nombre:

                errores.append(
                    parte
                )

                continue

            if len(nombre) > 100:

                errores.append(
                    f"{nombre[:30]}... "
                    "(nombre demasiado largo)"
                )

                continue

            # ----------------------------------------------------
            # PLAZAS
            # ----------------------------------------------------

            if plazas_raw is None:

                max_slots = None

            elif plazas_raw.lower() == "ilimitado":

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

            # ----------------------------------------------------
            # GUARDAR OPCIÓN
            # ----------------------------------------------------

            opciones.append(
                {
                    "name": nombre,
                    "emoji": "",
                    "max_slots": max_slots
                }
            )

        # --------------------------------------------------------
        # COMPROBAR ERRORES
        # --------------------------------------------------------

        if errores:

            mensaje = (
                "No pude interpretar algunas opciones.\n\n"
                "Formato correcto:\n"
                "`Tanque(2), DPS(5), Sanador(3)`\n\n"
                "Para plazas ilimitadas:\n"
                "`Jugador, Reserva`\n\n"
                "También puedes usar:\n"
                "`Jugador(ilimitado)`\n\n"
                "Opciones con formato incorrecto:\n"
            )

            mensaje += "\n".join(
                f"- `{error}`"
                for error in errores
            )

            await interaction.followup.send(
                mensaje
            )

            return

        # --------------------------------------------------------
        # COMPROBAR QUE EXISTAN OPCIONES
        # --------------------------------------------------------

        if not opciones:

            await interaction.followup.send(
                "No se encontró ninguna opción válida."
            )

            return

        # --------------------------------------------------------
        # GUARDAR
        # --------------------------------------------------------

        datos = obtener_datos(
            self.user_id
        )

        datos["options"] = opciones

        await actualizar_panel(
            self.user_id
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
# PANEL PRINCIPAL
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
                InicioButton(user_id)
            )

        # ----------------------------------------------------
        # 4. DURACIÓN
        # ----------------------------------------------------

        if datos["duration"] is None:

            self.add_item(
                DuracionButton(user_id)
            )

        # ----------------------------------------------------
        # 5. FRECUENCIA
        # ----------------------------------------------------

        if datos["frequency"] is None:

            self.add_item(
                FrecuenciaButton(user_id)
            )

        # ----------------------------------------------------
        # 6. OPCIONES
        # ----------------------------------------------------

        if not datos["options"]:

            self.add_item(
                OpcionesButton(user_id)
            )

        # ----------------------------------------------------
        # 7. MENCIONES
        # ----------------------------------------------------

        if datos["mentions"] is None:

            self.add_item(
                MencionesButton(user_id)
            )

        # ----------------------------------------------------
        # 8. COLOR
        # ----------------------------------------------------

        if datos["color"] is None:

            self.add_item(
                ColorButton(user_id)
            )

        # ----------------------------------------------------
        # 9. MÚLTIPLES
        # ----------------------------------------------------

        if datos["multiple"] is None:

            self.add_item(
                MultiplesButton(user_id)
            )

        # ----------------------------------------------------
        # 10. RECORDATORIOS
        # ----------------------------------------------------

        if datos["reminders"] is None:

            self.add_item(
                RecordatoriosButton(user_id)
            )

        # ----------------------------------------------------
        # 11. UBICACIÓN
        # ----------------------------------------------------

        if datos["location"] is None:

            self.add_item(
                UbicacionButton(user_id)
            )

        # ----------------------------------------------------
        # 12. IMAGEN
        # ----------------------------------------------------

        if datos["image"] is None:

            self.add_item(
                ImagenButton(user_id)
            )

        # ----------------------------------------------------
        # 14. CANAL DE PUBLICACIÓN
        # ----------------------------------------------------

        if datos["publish_channel"] is None:

            self.add_item(
                PublicarCanalButton(user_id)
            )

        # ----------------------------------------------------
        # BOTONES FINALES
        # ----------------------------------------------------

        self.add_item(
            PreviewButton(user_id)
        )

        self.add_item(
            PublicarButton(user_id)
        )

        self.add_item(
            CancelarButton(user_id)
        )


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


@bot.tree.command(
    guild=GUILD_OBJECT,
    name="roles_bloqueados",
    description="Configura los roles que no pueden participar en misiones"
)
async def roles_bloqueados(
    interaction: discord.Interaction
):

    if not interaction.user.guild_permissions.administrator:

        await interaction.response.send_message(
            "No tienes permisos de administrador.",
            ephemeral=True
        )

        return

    await interaction.response.send_message(
        "Selecciona los roles que NO podrán "
        "participar en las misiones:",
        view=RolesBloqueadosView(
            interaction.guild
        ),
        ephemeral=True
    )


# ============================================================
# /CREAR_EVENTO
# ============================================================

@bot.tree.command(
    guild=GUILD_OBJECT,
    name="crear_evento",
    description="Crea un nuevo evento"
)
async def crear_evento(
    interaction: discord.Interaction
):

    # ========================================================
    # COMPROBAR SERVIDOR
    # ========================================================

    if interaction.guild is None:

        await interaction.response.send_message(
            "Este comando solamente puede utilizarse "
            "dentro del servidor.",
            ephemeral=True
        )

        return

    # ========================================================
    # COMPROBAR ROL DM
    # ========================================================

    miembro = interaction.guild.get_member(
        interaction.user.id
    )

    if miembro is None:

        try:

            miembro = await interaction.guild.fetch_member(
                interaction.user.id
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):

            await interaction.response.send_message(
                "No pude comprobar tus roles.",
                ephemeral=True
            )

            return

    tiene_rol_dm = any(
        role.id == ROL_DM
        for role in miembro.roles
    )

    if not tiene_rol_dm:

        await interaction.response.send_message(
            "No tienes permiso para crear eventos.\n\n"
            "Solo los usuarios con el rol <@&1542487680389091328> "
            "pueden utilizar este comando.",
            ephemeral=True
        )

        return

    # ========================================================
    # CREAR SESIÓN
    # ========================================================

    user_id = interaction.user.id

    creaciones.pop(
        user_id,
        None
    )

    datos = obtener_datos(
        user_id
    )

    try:

        # ----------------------------------------------------
        # BUSCAR CANAL DE PUBLICACIÓN POR DEFECTO
        # ----------------------------------------------------

        guild = bot.get_guild(
            GUILD_ID
        )

        if guild:

            canal_defecto = discord.utils.get(
                guild.text_channels,
                name="eventos"
            )

            if canal_defecto is None:

                canal_defecto = discord.utils.get(
                    guild.text_channels,
                    name="general"
                )

            if canal_defecto:

                datos["publish_channel"] = (
                    canal_defecto
                )

        # ----------------------------------------------------
        # RESPONDER AL SLASH COMMAND
        # ----------------------------------------------------

        await interaction.response.send_message(
            "Te he enviado el panel de creación "
            "por mensaje privado.",
            ephemeral=True
        )

        # ----------------------------------------------------
        # ENVIAR PANEL AL DM
        # ----------------------------------------------------

        mensaje = await interaction.user.send(
            embed=crear_panel_embed(
                user_id
            ),
            view=CrearEventoView(
                user_id
            )
        )

        datos["panel_message_id"] = mensaje.id
        datos["panel_channel_id"] = mensaje.channel.id

    except discord.Forbidden:

        if not interaction.response.is_done():

            await interaction.response.send_message(
                "No puedo enviarte mensajes privados. "
                "Activa los mensajes directos "
                "del servidor.",
                ephemeral=True
            )

        else:

            await interaction.followup.send(
                "No puedo enviarte mensajes privados. "
                "Activa los mensajes directos "
                "del servidor.",
                ephemeral=True
            )

    except Exception as e:

        print()
        print(
            "=========================================="
        )
        print(
            "ERROR EN /CREAR_EVENTO"
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
                    "al crear el evento.\n\n"
                    f"Error: `{e}`",
                    ephemeral=True
                )

            else:

                await interaction.response.send_message(
                    "Ha ocurrido un error "
                    "al crear el evento.\n\n"
                    f"Error: `{e}`",
                    ephemeral=True
                )

        except Exception:

            pass


# ============================================================
# EJECUTAR BOT
# ============================================================    
bot.run(TOKEN)
