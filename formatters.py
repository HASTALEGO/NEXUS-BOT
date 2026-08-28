import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Madrid"))
UTC = timezone.utc
COLOR_BLANCO = 0xFFFFFF

MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

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

def generar_estrellas_ascii(puntuacion: float, max_estrellas: int = 5) -> str:
    estrellas_llenas = int(round(puntuacion))
    estrellas_vacias = max_estrellas - estrellas_llenas
    return "★" * estrellas_llenas + "☆" * estrellas_vacias

def generar_enlace_google_calendar(titulo: str, descripcion: str, fecha_inicio: datetime, duracion_minutos: int) -> str:
    if fecha_inicio.tzinfo is None:
        fecha_inicio = fecha_inicio.replace(tzinfo=TIMEZONE)
    
    fecha_fin = fecha_inicio + timedelta(minutes=duracion_minutos or 60)
    fmt = "%Y%m%dT%H%M%SZ"
    
    dt_start_utc = fecha_inicio.astimezone(ZoneInfo("UTC")).strftime(fmt)
    dt_end_utc = fecha_fin.astimezone(ZoneInfo("UTC")).strftime(fmt)
    
    params = {
        "action": "TEMPLATE",
        "text": titulo,
        "details": descripcion or "",
        "dates": f"{dt_start_utc}/{dt_end_utc}"
    }
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"