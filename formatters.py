import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Madrid"))
UTC = timezone.utc

# ═════════════════════════════════════════════════════════════════
# CONSTANTES ESTÉTICAS RETRO (ESTILO Y2K / ALT-CODES ASCII 2000s)
# ═════════════════════════════════════════════════════════════════
ICON_BULLET = "►"
ICON_SUB = "├─"
ICON_STAR_FILLED = "★"
ICON_STAR_EMPTY = "☆"
ICON_ALERT = "‼"
ICON_WAITLIST = "↕"
ICON_USER = "☻"
ICON_CHECK = "√"
ICON_CROSS = "X"
ICON_NOTE = "♪"
ICON_ARROW_RIGHT = "→"
ICON_GENDER_MALE = "♂"
ICON_GENDER_FEMALE = "♀"
ICON_PARAGRAPH = "§"
ICON_PILCROW = "¶"
ICON_FINALIZADO = "▬"
ICON_CERRADO = "▓"

# Escala de grises estricta (Sin colores estrafalarios)
COLOR_MONOCHROME = 0x2B2D31  # Gris neutro oscuro para Embeds
COLOR_BLANCO = 0xFFFFFF

MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

# ═════════════════════════════════════════════════════════════════
# FUNCIONES DE MANEJO DE FECHA Y TIEMPO
# ═════════════════════════════════════════════════════════════════

def ahora():
    return datetime.now(TIMEZONE)

def nombre_mes(mes: int) -> str:
    return MESES_ES[mes - 1]

def a_utc_iso(fecha: datetime) -> str:
    """Formato de almacenamiento: ISO-8601 en UTC, comparable como texto."""
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=TIMEZONE)
    return fecha.astimezone(UTC).isoformat()

def desde_iso(texto: str):
    """Lee una fecha almacenada y la devuelve en la zona horaria local."""
    if not texto:
        return None
    try:
        fecha = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=TIMEZONE)
    return fecha.astimezone(TIMEZONE)

def parsear_fecha(contenido: str):
    """Convierte una cadena DD/MM/YYYY HH:MM a un objeto datetime en la zona horaria local."""
    try:
        dt = datetime.strptime(contenido.strip(), "%d/%m/%Y %H:%M")
        return dt.replace(tzinfo=TIMEZONE)
    except ValueError:
        return None

def timestamp_discord(fecha):
    if isinstance(fecha, str):
        try:
            fecha = datetime.fromisoformat(fecha)
        except ValueError:
            return 0
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=TIMEZONE)
    return int(fecha.timestamp())

def formatear_duracion(minutos: int) -> str:
    if not minutos:
        return "Sin configurar"
    h, m = divmod(minutos, 60)
    res = []
    if h:
        res.append(f"{h} h")
    if m:
        res.append(f"{m} min")
    return " ".join(res)

def formatear_recordatorios(recordatorios: list) -> str:
    if not recordatorios:
        return "Ninguno"
    res = []
    for m in recordatorios:
        if m >= 1440:
            res.append(f"{m // 1440} dia(s)")
        elif m >= 60:
            res.append(f"{m // 60} hora(s)")
        else:
            res.append(f"{m} min")
    return ", ".join(res)

# ═════════════════════════════════════════════════════════════════
# FORMATOS VISUALES RETRO Y COMPONENTES EN ESCALA DE GRISES
# ═════════════════════════════════════════════════════════════════

def generar_estrellas_ascii(puntuacion: float, max_estrellas: int = 5) -> str:
    """Genera barra de estrellas ASCII (ej: ★★★☆☆)"""
    estrellas_llenas = int(round(puntuacion))
    estrellas_llenas = max(0, min(max_estrellas, estrellas_llenas))
    estrellas_vacias = max_estrellas - estrellas_llenas
    return (ICON_STAR_FILLED * estrellas_llenas) + (ICON_STAR_EMPTY * estrellas_vacias)

def format_retro_header(title: str) -> str:
    """Formatea cabeceras al estilo texto de consola de los 2000"""
    clean_title = title.upper()
    sep = "═" * (len(clean_title) + 6)
    return f"```{sep}\n  {ICON_BULLET} {clean_title}  \n{sep}```"

def format_retro_embed_description(
    desc: str, 
    creador_mention: str, 
    start_time_iso: str, 
    duracion: int, 
    canal_voz: str = None, 
    close_before_minutes: int = 0
) -> str:
    """Genera el bloque principal de texto para el anuncio en consola monocromática retro."""
    ts = timestamp_discord(start_time_iso)
    fecha_formatted = f"<t:{ts}:F> (<t:{ts}:R>)" if ts else "Por determinar"
    
    lineas = [
        f"{ICON_BULLET} ORGANIZADOR: {creador_mention}",
        f"{ICON_BULLET} FECHA Y HORA: {fecha_formatted}",
        f"{ICON_BULLET} DURACIÓN: {formatear_duracion(duracion)}",
    ]
    
    if canal_voz:
        lineas.append(f"{ICON_BULLET} UBICACIÓN: {canal_voz}")
        
    if close_before_minutes > 0:
        lineas.append(f"{ICON_ALERT} CIERRE DIRECTO: {close_before_minutes} min antes del inicio")

    lineas.append(f"\n{ICON_NOTE} DETALLES:\n{desc or 'Sin descripción proporcionada.'}\n")
    lineas.append("═" * 38)
    return "\n".join(lineas)

def format_inscritos_opcion(nombre_opcion: str, confirmados: list, reserva: list, max_slots: int = None) -> str:
    """Formatea las listas de inscritos por opción utilizando únicamente caracteres ASCII."""
    cupo_str = f"[{len(confirmados)}/{max_slots}]" if max_slots else f"[{len(confirmados)}]"
    header = f"**{ICON_BULLET} {nombre_opcion.upper()} {cupo_str}**"
    
    filas = [header]
    if not confirmados:
        filas.append(f"  {ICON_SUB} *(Sin inscritos)*")
    else:
        for i, user_mention in enumerate(confirmados, 1):
            filas.append(f"  {ICON_SUB} {i}. {ICON_USER} {user_mention}")

    if reserva:
        filas.append(f"  **{ICON_WAITLIST} LISTA DE ESPERA:**")
        for i, user_mention in enumerate(reserva, 1):
            filas.append(f"    {ICON_SUB} W-{i}. {user_mention}")

    return "\n".join(filas)

def format_perfil_asistencia(user_mention: str, stats: dict) -> str:
    """Formatea las estadísticas del usuario al estilo terminal retro."""
    total = stats["total"]
    asistidos = stats["asistidos"]
    faltas = stats["faltas"]
    ratio = stats["ratio"]
    
    bar_length = 10
    filled = int(round((ratio / 100) * bar_length))
    bar = "█" * filled + "░" * (bar_length - filled)
    
    return (
        f"```{ICON_PARAGRAPH} PERFIL DE ASISTENCIA: {user_mention}\n"
        f"═" * 36 + "\n"
        f"► EVENTOS ASISTIDOS : {asistidos}\n"
        f"► EVENTOS FALTADOS  : {faltas}\n"
        f"► TOTAL REGISTROS   : {total}\n"
        f"► FIABILIDAD        : [{bar}] {ratio:.1f}%\n"
        f"═" * 36 + "```"
    )