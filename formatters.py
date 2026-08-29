import os
import re
from datetime import datetime, timezone, timedelta
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

def canal_predeterminado_id() -> int:
    """ID del canal por defecto para publicar eventos (variable CANAL_EVENTOS_ID del .env)."""
    raw = (os.getenv("CANAL_EVENTOS_ID") or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0

LOCALES_A_ZONA = {
    # idioma-REGION de Discord → IANA zone
    "es-ES": "Europe/Madrid", "es-MX": "America/Mexico_City", "es-VE": "America/Caracas",
    "es-AR": "America/Argentina/Buenos_Aires", "es-CO": "America/Bogota",
    "es-CL": "America/Santiago", "es-PE": "America/Lima", "es-PA": "America/Panama",
    "es-CR": "America/Costa_Rica", "es-GT": "America/Guatemala", "es-HN": "America/Honduras",
    "es-SV": "America/El_Salvador", "es-NI": "America/Managua", "es-PY": "America/Asuncion",
    "es-UY": "America/Montevideo", "es-EC": "America/Guayaquil", "es-BO": "America/La_Paz",
    "es-DO": "America/Santo_Domingo", "es-CU": "America/Havana", "es-US": "America/New_York",
    "pt-BR": "America/Sao_Paulo", "en-US": "America/New_York", "en-GB": "Europe/London",
    "en-ES": "Europe/Madrid", "en-CA": "America/Toronto", "fr-FR": "Europe/Paris",
    "it-IT": "Europe/Rome", "de-DE": "Europe/Berlin", "pt-PT": "Europe/Lisbon",
}

LOCALES_A_ZONA_NORM = {k.lower(): v for k, v in LOCALES_A_ZONA.items()}

def zona_desde_locale(locale_str):
    """Deduce la zona horaria a partir del idioma/región del usuario en Discord."""
    if not locale_str:
        return None
    # Convertimos a string por si Discord pasa un Enum (discord.Locale)
    clave = str(locale_str.value if hasattr(locale_str, 'value') else locale_str).strip().lower()
    if clave in LOCALES_A_ZONA_NORM:
        return LOCALES_A_ZONA_NORM[clave]
    # fallback por idioma genérico: es-XX → Europe/Madrid
    if clave.startswith("es"):
        return "Europe/Madrid"
    return None

DIA_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
    "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
    "lun": 0, "mar": 1, "mie": 2, "mié": 2, "jue": 3, "vie": 4, "sab": 5, "sáb": 5, "dom": 6,
}

def _zona_aviable(tz):
    if isinstance(tz, str):
        try:
            return ZoneInfo(tz)
        except Exception:
            return TIMEZONE
    return tz if isinstance(tz, ZoneInfo) else TIMEZONE

def _hora_natural(texto: str):
    """Extrae hora:minuto de un texto. Admite 24h ('17:30'/'17h') y 12h ('4:00 pm')."""
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?|am|pm)?", texto.lower())
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    ampm = (m.group(3) or "")
    if "p" in ampm and h < 12:
        h += 12
    elif "a" in ampm and h == 12:
        h = 0
    return h, mi

def interpretar_fecha(contenido: str, tz=None):
    """Interpreta fechas/horas en español usando la zona horaria del usuario (tz).

    Formas aceptadas:
      - 'en 30 min', 'en 1 hora', 'en 927 minutos', 'en 2 dias'
      - 'hoy a las 21:00', 'mañana a las 4:00 PM', 'mañana 17:30'
      - 'viernes a las 17:00', 'viernes 5:00 pm', 'sábado 12:30'
      - '17:30' (solo hora: hoy; si ya pasó, mañana)
      - '20/08/2026 12:30' o '20/08/2026' (a las 20:00)
    Devuelve datetime en tz (o TIMEZONE si tz no es válido/omitido) o None si no entiende.
    """
    zona = _zona_aviable(tz)
    ahora_dt = datetime.now(zona)
    s = " ".join(contenido.lower().split())

    # 1) Relativos: "en X minutos / horas / minutos / días" y abreviaturas (1h, 20m, 2d)
    m = re.search(
        r"\ben\s+(\d+(?:[.,]\d+)?)\s*(horas?|minutos?|dias?|días?|min|h|m|d)\b",
        s,
    )
    if m:
        cant = float(m.group(1).replace(",", "."))
        uni = m.group(2).lower()
        if uni in ("horas", "hora", "h"):
            delta = timedelta(hours=cant)
        elif uni in ("dias", "día", "días", "dia", "d"):
            delta = timedelta(days=cant)
        else:
            delta = timedelta(minutes=cant)
        return ahora_dt + delta

    def con_hora(fecha_base, defensa_hora):
        h = _hora_natural(s)
        if h is None:
            if re.search(r"\bmediod", s.replace("í", "i")) or "mediodia" in s or "mediodía" in s:
                return fecha_base.replace(hour=12, minute=0, second=0, microsecond=0)
            if re.search(r"\bmedianoche\b", s):
                return fecha_base.replace(hour=0, minute=0, second=0, microsecond=0)
            return fecha_base.replace(hour=defensa_hora[0], minute=defensa_hora[1], second=0, microsecond=0)
        return fecha_base.replace(hour=h[0], minute=h[1], second=0, microsecond=0)

    # 2) Palabras clave: hoy / mañana
    if re.search(r"\bhoy\b", s):
        dt = con_hora(ahora_dt, (ahora_dt.hour, ahora_dt.minute))
        if dt <= ahora_dt:
            dt += timedelta(days=1)
        return dt
    if re.search(r"\bmañana\b", s) or re.search(r"\bmanana\b", s):
        base = ahora_dt + timedelta(days=1)
        return con_hora(base, (ahora_dt.hour, ahora_dt.minute))

    # 3) Día de la semana
    for nombre, dia in DIA_SEMANA.items():
        if re.search(rf"\b{re.escape(nombre)}\b", s):
            diferencia = (dia - ahora_dt.weekday()) % 7
            dt = con_hora(ahora_dt + timedelta(days=diferencia), (20, 0))
            if dt <= ahora_dt:
                dt += timedelta(days=7)
            return dt

    # 4) Fecha estricta DD/MM/YYYY [HH:MM] (se comprueba antes que la hora suelta)
    m = re.match(
        r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(?:a\s+las\s+)?(\d{1,2}):(\d{2})\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|am|pm)?)?$",
        s,
    )
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if m.group(4) is None:
                h_h, h_m = 20, 0
            else:
                h_h, h_m = int(m.group(4)), int(m.group(5))
            return datetime(y, mo, d, h_h, h_m, tzinfo=zona)
        except ValueError:
            return None

    # 5) Solo hora: "17:30", "4:00 pm" → hoy, o mañana si ya pasó
    if re.search(r"\d{1,2}:\d{2}", s) or re.search(r"\d{1,2}\s*(?:am|pm)\b", s):
        h = _hora_natural(s)
        if h:
            dt = ahora_dt.replace(hour=h[0], minute=h[1], second=0, microsecond=0)
            if dt <= ahora_dt:
                dt += timedelta(days=1)
            return dt

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
            v = m // 1440
            res.append(f"{v} día" if v == 1 else f"{v} días")
        elif m >= 60:
            v = m // 60
            res.append(f"{v} hora" if v == 1 else f"{v} horas")
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

    if total == 0:
        return (
            f"```{ICON_PARAGRAPH} PERFIL DE ASISTENCIA: {user_mention}\n"
            f"═" * 36 + "\n"
            f"► EVENTOS ASISTIDOS : 0\n"
            f"► EVENTOS FALTADOS  : 0\n"
            f"► TOTAL REGISTROS   : 0\n"
            f"► FIABILIDAD        : Sin datos aun\n"
            f"═" * 36 + "```"
        )

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