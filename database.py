import logging
import os
import sqlite3
from datetime import datetime, timezone

from formatters import TIMEZONE

log = logging.getLogger(__name__)

DATABASE = os.getenv("DATABASE_PATH", "eventos.db")
ESQUEMA_VERSION = 1

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
    rating INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles_bloqueados (role_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS roles_mencionables (role_id INTEGER PRIMARY KEY);

"""

# Van aparte del esquema: sobre una base antigua las columnas indexadas pueden
# no existir todavia cuando se crean las tablas.
INDICES = """
CREATE INDEX IF NOT EXISTS idx_eventos_guild ON eventos(guild_id, start_time);
CREATE INDEX IF NOT EXISTS idx_inscripciones_event ON inscripciones(event_id);
CREATE INDEX IF NOT EXISTS idx_inscripciones_opcion ON inscripciones(option_id, status);
CREATE INDEX IF NOT EXISTS idx_asistencia_event ON asistencia(event_id);
CREATE INDEX IF NOT EXISTS idx_feedback_event ON feedback(event_id);
"""

# Se aplican despues de deduplicar, porque fallarian sobre datos antiguos repetidos.
INDICES_UNICOS = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_inscripciones ON inscripciones(event_id, option_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_asistencia ON asistencia(event_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback ON feedback(event_id, user_id);
"""

# Columnas que pueden faltar en bases creadas con versiones antiguas del esquema.
# No pueden llevar NOT NULL sin valor por defecto: ALTER TABLE lo rechaza.
COLUMNAS_ESPERADAS = {
    "eventos": {
        "channel_id": "INTEGER", "message_id": "INTEGER", "thread_id": "INTEGER",
        "description": "TEXT", "start_time": "TEXT", "duration_minutes": "INTEGER",
        "frequency": "TEXT", "color": "INTEGER", "location_channel_id": "INTEGER",
        "auto_voice": "INTEGER DEFAULT 0", "auto_voice_channel_id": "INTEGER",
        "image_url": "TEXT", "multiple_registrations": "INTEGER DEFAULT 0",
        "allow_waitlist": "INTEGER DEFAULT 1", "next_created": "INTEGER DEFAULT 0",
        "created_at": "TEXT",
    },
    "opciones_inscripcion": {"emoji": "TEXT", "max_slots": "INTEGER"},
    "inscripciones": {"status": "TEXT DEFAULT 'confirmado'", "position": "INTEGER DEFAULT 0", "created_at": "TEXT"},
    "evento_restricciones": {"tipo": "TEXT"},
    "recordatorios": {"sent": "INTEGER DEFAULT 0"},
    "asistencia": {"attended": "INTEGER", "registered_at": "TEXT"},
    "feedback": {"comment": "TEXT", "created_at": "TEXT"},
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
    """executescript() haria commit de la transaccion abierta, asi que se ejecuta
    cada sentencia por separado."""
    for sentencia in guion.split(";"):
        if sentencia.strip():
            conn.execute(sentencia)


def _aplicar_esquema(conn):
    _aplicar_sql(conn, ESQUEMA)


def _columnas(conn, tabla):
    return {f["name"] for f in conn.execute(f"PRAGMA table_info({tabla})")}


def _asegurar_columnas(conn):
    """Anade las columnas que falten en bases creadas con esquemas antiguos."""
    for tabla, columnas in COLUMNAS_ESPERADAS.items():
        existentes = _columnas(conn, tabla)
        if not existentes:
            continue
        for columna, tipo in columnas.items():
            if columna not in existentes:
                log.info("Anadiendo la columna %s.%s que faltaba", tabla, columna)
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")


def _rellenar_nulos(conn):
    """Las columnas anadidas por ALTER TABLE quedan a NULL y el esquema nuevo las
    declara NOT NULL, asi que se les da un valor antes de reconstruir las tablas."""
    ahora_utc = datetime.now(timezone.utc).isoformat()
    for tabla, columna, valor in (
        ("eventos", "created_at", ahora_utc),
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
    """Normaliza una marca de tiempo almacenada a ISO-8601 en UTC."""
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
    """Las fechas se guardaban con el offset local, lo que rompe las comparaciones
    de texto al cambiar el horario de verano. Se reescriben en UTC."""
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
    """Las tablas antiguas se crearon sin REFERENCES; se recrean copiando los datos."""
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
