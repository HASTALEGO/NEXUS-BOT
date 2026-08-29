"""Test exacto del flujo: desencadenar_asistencia -> tomar_asistencia -> mi_perfil"""
import tempfile, os
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "prod_test.db")

import database
database.inicializar_db()
from formatters import a_utc_iso, ahora
from datetime import timedelta

# 1. Crear evento EXACTAMENTE como lo hace clonar_evento en main.py
conn = database.conectar_db()
conn.execute("BEGIN IMMEDIATE")
cur = conn.execute(
    """INSERT INTO eventos (guild_id, channel_id, creator_id, title, description, start_time,
         duration_minutes, frequency, color, location_channel_id, auto_voice,
         image_url, multiple_registrations, allow_waitlist, created_at,
         parent_event_id, close_before_minutes, dm_reminders)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (1, 2, 3, "LAS AVENTURAS DE JOSEARLOLO", "desc",
     a_utc_iso(ahora() + timedelta(hours=1)), 60, "Una vez", 0xFFFFFF,
     None, 0, None, 0, 1, a_utc_iso(ahora()), None, 0, 1),
)
eid = cur.lastrowid
conn.execute("INSERT INTO opciones_inscripcion (event_id, name, emoji, max_slots) VALUES (?, '[√] Acepto', '', NULL)", (eid,))
oid = conn.execute("SELECT id FROM opciones_inscripcion WHERE event_id = ?", (eid,)).fetchone()[0]
conn.execute(
    "INSERT INTO inscripciones (event_id, option_id, user_id, status, position, created_at) VALUES (?, ?, ?, 'confirmado', 0, ?)",
    (eid, oid, 999999, a_utc_iso(ahora())),
)
conn.commit()
conn.close()
print(f"Evento creado con id={eid}")

# 2. Simular desencadenar_asistencia: obtener evento de la DB (como main.py lo haria)
conn = database.conectar_db()
evento = conn.execute("SELECT * FROM eventos WHERE id = ?", (eid,)).fetchone()
conn.close()
print(f"Evento recuperado: id={evento['id']}, title={evento['title']}")

# 3. Simular lo que hace tomar_asistencia
inscritos = database.obtener_inscritos_evento(evento["id"])
print(f"Inscritos: {[dict(i) for i in inscritos]}")

for fila in inscritos:
    print(f"Registrando asistencia de user_id={fila['user_id']} en event_id={evento['id']}")
    database.registrar_asistencia(evento["id"], fila["user_id"], True)

# 4. Verificar DIRECTAMENTE en la DB
conn = database.conectar_db()
todas = conn.execute("SELECT * FROM asistencia").fetchall()
print(f"Filas en asistencia DESPUES de registrar: {[dict(r) for r in todas]}")
conn.close()

# 5. Simular /mi_perfil (exactamente como modulo_perfil.py lo hace)
perfil = database.obtener_perfil_usuario(999999)
print(f"Perfil de user 999999: {perfil}")

# 6. Verificar tambien con obtener_inscritos_evento
inscritos2 = database.obtener_inscritos_evento(eid)
print(f"Inscritos de nuevo: {[dict(i) for i in inscritos2]}")

print("\nDONE")
