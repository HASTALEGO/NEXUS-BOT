import logging
import os
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from supabase import create_client
from formatters import TIMEZONE

log = logging.getLogger(__name__)

DATABASE = os.getenv("DATABASE_PATH", "eventos.db")
ESQUEMA_VERSION = 4  # v4: zonas horarias por usuario (fechas interpretadas en la zona de cada uno)

# Configuración y limpieza de URL de Supabase
RAW_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_URL = RAW_SUPABASE_URL.replace("/rest/v1", "").rstrip("/") if RAW_SUPABASE_URL else None
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = "bot-backups"

def crear_opciones_predeterminadas(event_id: int):
    """Inserta las 3 opciones fijas usando solo símbolos de texto retro."""
    opciones = [
        ("► Aceptar", None),
        ("X Rechazar", None),
        ("? Indeciso", None)
    ]
    conn = conectar_db()
    try:
        for nombre, max_slots in opciones:
            conn.execute(
                "INSERT INTO opciones_inscripcion (event_id, name, max_slots) VALUES (?, ?, ?)",
                (event_id, nombre, max_slots)
            )
        conn.commit()
    finally:
        conn.close()

def descargar_db_remota():
    """Descarga eventos.db desde Supabase si existe."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Sin credenciales de Supabase. Usando DB local.")
        return

    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        data = supabase.storage.from_(BUCKET_NAME).download(DATABASE)
        with open(DATABASE, "wb") as f:
            f.write(data)
        log.info("Base de datos restaurada desde la nube con éxito.")
    except Exception as e:
        log.info("No se encontró respaldo remoto previo (inicio limpio o primer despliegue): %s", str(e))

def guardar_db_remota():
    """Sube el archivo eventos.db a la nube."""
    if not SUPABASE_URL or not SUPABASE_KEY or not os.path.exists(DATABASE):
        return

    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        with open(DATABASE, "rb") as f:
            supabase.storage.from_(BUCKET_NAME).upload(
                path=DATABASE,
                file=f,
                file_options={"x-upsert": "true"}
            )
        log.info("Copia de seguridad guardada en la nube.")
    except Exception as e:
        log.error("Error guardando la DB en la nube: %s", str(e))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    thread_id INTEGER,
    creator_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    start_time TEXT,
    duration_minutes INTEGER,
    frequency TEXT,
    color INTEGER,
    location_channel_id INTEGER,
    auto_voice INTEGER DEFAULT 0,
    auto_voice_channel_id INTEGER,
    image_url TEXT,
    multiple_registrations INTEGER DEFAULT 0,
    allow_waitlist INTEGER DEFAULT 1,
    next_created INTEGER DEFAULT 0,
    parent_event_id INTEGER DEFAULT NULL REFERENCES eventos(id) ON DELETE SET NULL,
    close_before_minutes INTEGER DEFAULT 0,
    attendance_checked INTEGER DEFAULT 0,
    dm_reminders INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opciones_inscripcion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    emoji TEXT,
    max_slots INTEGER
);

CREATE TABLE IF NOT EXISTS inscripciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES opciones_inscripcion(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    status TEXT DEFAULT 'confirmado',
    position INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evento_menciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS evento_restricciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL,
    tipo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recordatorios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    minutes_before INTEGER NOT NULL,
    sent INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS asistencia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    attended INTEGER NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles_bloqueados (role_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS roles_mencionables (role_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS roles_valoracion (role_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS preferencias_usuario (
    user_id INTEGER PRIMARY KEY,
    recibir_feedback INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS zonas_horarias (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'Europe/Madrid'
);
"""

INDICES = """
CREATE INDEX IF NOT EXISTS idx_eventos_guild ON eventos(guild_id, start_time);
CREATE INDEX IF NOT EXISTS idx_inscripciones_event ON inscripciones(event_id);
CREATE INDEX IF NOT EXISTS idx_inscripciones_opcion ON inscripciones(option_id, status);
CREATE INDEX IF NOT EXISTS idx_asistencia_event ON asistencia(event_id);
CREATE INDEX IF NOT EXISTS idx_feedback_event ON feedback(event_id);
CREATE INDEX IF NOT EXISTS idx_eventos_parent ON eventos(parent_event_id);
"""

INDICES_UNICOS = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_inscripciones ON inscripciones(event_id, option_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_asistencia ON asistencia(event_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback ON feedback(event_id, user_id);
"""

COLUMNAS_ESPERADAS = {
    "eventos": {
        "channel_id": "INTEGER", "message_id": "INTEGER", "thread_id": "INTEGER",
        "description": "TEXT", "start_time": "TEXT", "duration_minutes": "INTEGER",
        "frequency": "TEXT", "color": "INTEGER", "location_channel_id": "INTEGER",
        "auto_voice": "INTEGER DEFAULT 0", "auto_voice_channel_id": "INTEGER",
        "image_url": "TEXT", "multiple_registrations": "INTEGER DEFAULT 0",
        "allow_waitlist": "INTEGER DEFAULT 1", "next_created": "INTEGER DEFAULT 0",
        "parent_event_id": "INTEGER DEFAULT NULL REFERENCES eventos(id) ON DELETE SET NULL",
        "close_before_minutes": "INTEGER DEFAULT 0",
        "attendance_checked": "INTEGER DEFAULT 0",
        "dm_reminders": "INTEGER DEFAULT 1",
        "created_at": "TEXT",
    },
    "opciones_inscripcion": {"emoji": "TEXT", "max_slots": "INTEGER"},
    "inscripciones": {"status": "TEXT DEFAULT 'confirmado'", "position": "INTEGER DEFAULT 0", "created_at": "TEXT"},
    "evento_restricciones": {"tipo": "TEXT"},
    "recordatorios": {"sent": "INTEGER DEFAULT 0"},
    "asistencia": {"attended": "INTEGER", "registered_at": "TEXT"},
    "feedback": {"rating": "INTEGER", "comment": "TEXT", "created_at": "TEXT"},
}

TABLAS_HIJAS = [
    "opciones_inscripcion", "inscripciones", "evento_menciones",
    "evento_restricciones", "recordatorios", "asistencia", "feedback",
]

def conectar_db():
    conn = sqlite3.connect(DATABASE, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _aplicar_sql(conn, guion):
    for sentencia in guion.split(";"):
        if sentencia.strip():
            conn.execute(sentencia)

def _aplicar_esquema(conn):
    _aplicar_sql(conn, ESQUEMA)

def _columnas(conn, tabla):
    return {f["name"] for f in conn.execute(f"PRAGMA table_info({tabla})")}

def _asegurar_columnas(conn):
    for tabla, columnas in COLUMNAS_ESPERADAS.items():
        existentes = _columnas(conn, tabla)
        if not existentes:
            continue
        for columna, tipo in columnas.items():
            if columna not in existentes:
                log.info("Añadiendo la columna %s.%s que faltaba", tabla, columna)
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")

def _rellenar_nulos(conn):
    ahora_utc = datetime.now(timezone.utc).isoformat()
    for tabla, columna, valor in (
        ("eventos", "created_at", ahora_utc),
        ("eventos", "close_before_minutes", 0),
        ("eventos", "attendance_checked", 0),
        ("eventos", "dm_reminders", 1),
        ("inscripciones", "created_at", ahora_utc),
        ("inscripciones", "status", "confirmado"),
        ("asistencia", "registered_at", ahora_utc),
        ("asistencia", "attended", 0),
        ("feedback", "created_at", ahora_utc),
        ("evento_restricciones", "tipo", "permitido"),
        ("recordatorios", "sent", 0),
    ):
        if columna in _columnas(conn, tabla):
            conn.execute(f"UPDATE {tabla} SET {columna} = ? WHERE {columna} IS NULL", (valor,))

def _a_utc_iso(texto):
    if not texto:
        return texto
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return texto
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TIMEZONE)
    return dt.astimezone(timezone.utc).isoformat()

def _migrar_a_utc(conn):
    for tabla, columnas in (
        ("eventos", ("start_time", "created_at")),
        ("inscripciones", ("created_at",)),
        ("asistencia", ("registered_at",)),
        ("feedback", ("created_at",)),
    ):
        existentes = _columnas(conn, tabla)
        for columna in columnas:
            if columna not in existentes:
                continue
            filas = conn.execute(f"SELECT id, {columna} AS valor FROM {tabla}").fetchall()
            for fila in filas:
                nuevo = _a_utc_iso(fila["valor"])
                if nuevo != fila["valor"]:
                    conn.execute(f"UPDATE {tabla} SET {columna} = ? WHERE id = ?", (nuevo, fila["id"]))

def _eliminar_duplicados(conn):
    conn.execute("""
        DELETE FROM inscripciones WHERE id NOT IN (
            SELECT MIN(id) FROM inscripciones GROUP BY event_id, option_id, user_id
        )
    """)
    conn.execute("""
        DELETE FROM feedback WHERE id NOT IN (
            SELECT MAX(id) FROM feedback GROUP BY event_id, user_id
        )
    """)
    conn.execute("""
        DELETE FROM asistencia WHERE id NOT IN (
            SELECT MAX(id) FROM asistencia GROUP BY event_id, user_id
        )
    """)

def _eliminar_huerfanos(conn):
    for tabla in TABLAS_HIJAS:
        conn.execute(f"DELETE FROM {tabla} WHERE event_id NOT IN (SELECT id FROM eventos)")
    conn.execute("DELETE FROM inscripciones WHERE option_id NOT IN (SELECT id FROM opciones_inscripcion)")

def _reconstruir_con_claves_foraneas(conn):
    for tabla in TABLAS_HIJAS:
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (tabla,)).fetchone()
        if not ddl or "REFERENCES" in ddl["sql"].upper():
            continue
        antiguas = _columnas(conn, tabla)
        conn.execute(f"ALTER TABLE {tabla} RENAME TO {tabla}_old")
        _aplicar_esquema(conn)
        columnas = ", ".join(sorted(antiguas & _columnas(conn, tabla)))
        conn.execute(f"INSERT INTO {tabla} ({columnas}) SELECT {columnas} FROM {tabla}_old")
        conn.execute(f"DROP TABLE {tabla}_old")

def inicializar_db():
    descargar_db_remota()
    conn = conectar_db()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        _aplicar_esquema(conn)
        _asegurar_columnas(conn)
        _aplicar_sql(conn, INDICES)
        _rellenar_nulos(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < ESQUEMA_VERSION:
            log.info("Migrando la base de datos de la version %s a la %s", version, ESQUEMA_VERSION)
            _eliminar_huerfanos(conn)
            _eliminar_duplicados(conn)
            _migrar_a_utc(conn)
            _reconstruir_con_claves_foraneas(conn)
            _aplicar_esquema(conn)
            _asegurar_columnas(conn)
            _aplicar_sql(conn, INDICES)
            conn.execute(f"PRAGMA user_version = {ESQUEMA_VERSION}")

        _aplicar_sql(conn, INDICES_UNICOS)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()

# --- CONSULTAS ESPECÍFICAS DE ASISTENCIA, VALORACIONES Y ESTADÍSTICAS ---

def registrar_asistencia(event_id: int, user_id: int, attended: bool):
    """Registra o actualiza la asistencia real de un usuario en un evento."""
    ahora_utc = datetime.now(timezone.utc).isoformat()
    conn = conectar_db()
    try:
        conn.execute("""
            INSERT INTO asistencia (event_id, user_id, attended, registered_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_id, user_id) DO UPDATE SET 
                attended = excluded.attended,
                registered_at = excluded.registered_at;
        """, (event_id, user_id, 1 if attended else 0, ahora_utc))
        conn.commit()
    finally:
        conn.close()

def marcar_asistencia_revisada(event_id: int):
    """Marca un evento indicando que el creador ya ha procesado la asistencia."""
    conn = conectar_db()
    try:
        conn.execute("UPDATE eventos SET attendance_checked = 1 WHERE id = ?;", (event_id,))
        conn.commit()
    finally:
        conn.close()

def obtener_perfil_usuario(user_id: int) -> dict:
    """Calcula las estadísticas del perfil del jugador (asistencias vs inasistencias)."""
    conn = conectar_db()
    try:
        row = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN attended = 1 THEN 1 ELSE 0 END) as asistidos,
                SUM(CASE WHEN attended = 0 THEN 1 ELSE 0 END) as faltas
            FROM asistencia
            WHERE user_id = ?;
        """, (user_id,)).fetchone()
        
        total = row["total"] or 0
        asistidos = row["asistidos"] or 0
        faltas = row["faltas"] or 0
        ratio = (asistidos / total * 100) if total > 0 else 0.0
        return {"total": total, "asistidos": asistidos, "faltas": faltas, "ratio": ratio}
    finally:
        conn.close()

def guardar_feedback(event_id: int, user_id: int, rating: int, comment: str):
    """Guarda o actualiza la reseña de un usuario para un evento."""
    ahora_utc = datetime.now(timezone.utc).isoformat()
    conn = conectar_db()
    try:
        conn.execute("""
            INSERT INTO feedback (event_id, user_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(event_id, user_id) DO UPDATE SET
                rating = excluded.rating,
                comment = excluded.comment,
                created_at = excluded.created_at;
        """, (event_id, user_id, rating, comment, ahora_utc))
        conn.commit()
    finally:
        conn.close()

def obtener_resumen_valoraciones_creador(creator_id: int) -> list:
    """Obtiene los eventos propios de un creador que tienen valoraciones acumuladas."""
    conn = conectar_db()
    try:
        cursor = conn.execute("""
            SELECT e.id, e.title, AVG(f.rating) as media, COUNT(f.id) as total_reviews
            FROM eventos e
            JOIN feedback f ON e.id = f.event_id OR f.event_id IN (SELECT id FROM eventos WHERE parent_event_id = e.id)
            WHERE e.creator_id = ?
            GROUP BY e.id;
        """, (creator_id,))
        return cursor.fetchall()
    finally:
        conn.close()

def obtener_detalles_feedback_evento(event_id: int) -> list:
    """Obtiene el listado individual de reseñas de un evento específico o su saga."""
    conn = conectar_db()
    try:
        cursor = conn.execute("""
            SELECT f.user_id, f.rating, f.comment, f.created_at
            FROM feedback f
            WHERE f.event_id = ? 
               OR f.event_id IN (SELECT id FROM eventos WHERE parent_event_id = ?);
        """, (event_id, event_id))
        return cursor.fetchall()
    finally:
        conn.close()

# --- PREFERENCIAS DE FEEDBACK POR DM Y AUTORROL DE VALORACIONES ---

def obtener_preferencia_feedback(user_id: int) -> bool:
    """True si el usuario quiere recibir DMs de valoración (opt-out: default True)."""
    conn = conectar_db()
    try:
        fila = conn.execute(
            "SELECT recibir_feedback FROM preferencias_usuario WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(fila["recibir_feedback"]) if fila else True
    finally:
        conn.close()

def setear_preferencia_feedback(user_id: int, recibir: bool):
    conn = conectar_db()
    try:
        conn.execute("""
            INSERT INTO preferencias_usuario (user_id, recibir_feedback)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET recibir_feedback = excluded.recibir_feedback
        """, (user_id, 1 if recibir else 0))
        conn.commit()
    finally:
        conn.close()

def listar_roles_valoracion() -> list:
    conn = conectar_db()
    try:
        filas = conn.execute("SELECT role_id FROM roles_valoracion").fetchall()
        return [f["role_id"] for f in filas]
    finally:
        conn.close()

def configurar_rol_valoracion(role_id: int):
    conn = conectar_db()
    try:
        conn.execute("INSERT INTO roles_valoracion (role_id) VALUES (?)", (role_id,))
        conn.commit()
    finally:
        conn.close()

def remover_rol_valoracion(role_id: int):
    conn = conectar_db()
    try:
        conn.execute("DELETE FROM roles_valoracion WHERE role_id = ?", (role_id,))
        conn.commit()
    finally:
        conn.close()

def zona_horaria_defecto() -> str:
    return TIMEZONE.key

def obtener_zona_horaria(user_id: int) -> str:
    """Zona horaria del usuario (IATA name, p.ej. 'America/Caracas'); si no tiene, la del bot."""
    conn = conectar_db()
    try:
        fila = conn.execute(
            "SELECT timezone FROM zonas_horarias WHERE user_id = ?", (user_id,)
        ).fetchone()
        if fila and fila["timezone"]:
            try:
                ZoneInfo(fila["timezone"])
                return fila["timezone"]
            except Exception:
                pass
        return zona_horaria_defecto()
    finally:
        conn.close()

def setear_zona_horaria(user_id: int, timezone_nombre: str) -> bool:
    """Guarda la zona horaria del usuario. Devuelve True solo si el nombre es válido."""
    try:
        zona = ZoneInfo(timezone_nombre.strip())
    except Exception:
        return False
    conn = conectar_db()
    try:
        conn.execute("""
            INSERT INTO zonas_horarias (user_id, timezone)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET timezone = excluded.timezone
        """, (user_id, zona.key))
        conn.commit()
        return True
    finally:
        conn.close()

def debe_enviar_feedback(user_id: int, roles_usuario: set) -> bool:
    """Regla del punto 3: envia por DM si la preferencia individual lo permite
    (opt-out) y, si hay autorrol configurado, el usuario tiene ese rol."""
    if not obtener_preferencia_feedback(user_id):
        return False
    roles_configurados = set(listar_roles_valoracion())
    if not roles_configurados:
        return True
    return bool(roles_configurados & set(roles_usuario))

# --- CONSULTAS PARA FLUJOS DM, EDICION Y REPETICION ---

def obtener_inscritos_evento(event_id: int) -> list:
    """Inscritos confirmados o en espera de un evento (para DM y asistencia)."""
    conn = conectar_db()
    try:
        cursor = conn.execute("""
            SELECT i.user_id, i.status, i.position, o.name AS opcion
            FROM inscripciones i
            JOIN opciones_inscripcion o ON o.id = i.option_id
            WHERE i.event_id = ? AND i.status = 'confirmado'
            ORDER BY o.id, i.id
        """, (event_id,))
        return cursor.fetchall()
    finally:
        conn.close()

def obtener_eventos_creador(creator_id: int, guild_id: int) -> list:
    """Eventos del creador (ordenados por inicio descendente) para /mis_valoraciones y /repetir_evento."""
    conn = conectar_db()
    try:
        cursor = conn.execute("""
            SELECT e.id, e.title, e.start_time, e.frequency, e.duration_minutes,
                   (SELECT COUNT(*) FROM inscripciones i WHERE i.event_id = e.id) AS inscritos
            FROM eventos e
            WHERE e.creator_id = ? AND e.guild_id = ?
            ORDER BY e.start_time DESC
        """, (creator_id, guild_id))
        return cursor.fetchall()
    finally:
        conn.close()

def obtener_evento_pendiente_asistencia(guild_id: int) -> list:
    """Eventos finalizados aun no procesados por su creador (punto 2)."""
    conn = conectar_db()
    try:
        cursor = conn.execute("""
            SELECT * FROM eventos
            WHERE guild_id = ? AND attendance_checked = 0
              AND start_time IS NOT NULL AND duration_minutes > 0
        """, (guild_id,))
        return cursor.fetchall()
    finally:
        conn.close()

def actualizar_campos_evento(event_id: int, **campos):
    """Actualiza los campos dados de un evento de forma segura."""
    if not campos:
        return
    conn = conectar_db()
    try:
        columnas = ", ".join(f"{c} = ?" for c in campos)
        conn.execute(f"UPDATE eventos SET {columnas} WHERE id = ?", (*campos.values(), event_id))
        conn.commit()
    finally:
        conn.close()

def actualizar_opcion(option_id: int, nombre: str = None, max_slots: int = None):
    conn = conectar_db()
    try:
        if nombre is not None:
            conn.execute("UPDATE opciones_inscripcion SET name = ? WHERE id = ?", (nombre, option_id))
        if max_slots is not None:
            conn.execute("UPDATE opciones_inscripcion SET max_slots = ? WHERE id = ?", (max_slots, option_id))
        conn.commit()
    finally:
        conn.close()