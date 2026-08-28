"""Prueba de humo de la logica de inscripcion (sin conexion a Discord)."""
import asyncio
import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "smoke.db")

import database  # noqa: E402
import formatters as f  # noqa: E402
import vistas_eventos as v  # noqa: E402


def crear_evento(**kwargs):
    conn = database.conectar_db()
    campos = {
        "guild_id": 1, "channel_id": 2, "creator_id": 3, "title": "Test", "description": "d",
        "start_time": f.a_utc_iso(f.ahora() + timedelta(hours=2)),
        "duration_minutes": 60, "frequency": "Una vez", "color": 0xFFFFFF,
        "multiple_registrations": 0, "allow_waitlist": 1, "created_at": f.a_utc_iso(f.ahora()),
    }
    campos.update(kwargs)
    cols = ", ".join(campos)
    cur = conn.execute(f"INSERT INTO eventos ({cols}) VALUES ({', '.join('?' * len(campos))})", tuple(campos.values()))
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    return eid


def crear_opcion(event_id, max_slots):
    conn = database.conectar_db()
    cur = conn.execute(
        "INSERT INTO opciones_inscripcion (event_id, name, emoji, max_slots) VALUES (?, ?, '', ?)",
        (event_id, "Escuadra A", max_slots),
    )
    conn.commit()
    oid = cur.lastrowid
    conn.close()
    return oid


def interaccion(user_id, roles=()):
    inter = MagicMock()
    inter.user = SimpleNamespace(id=user_id, mention=f"<@{user_id}>", roles=[SimpleNamespace(id=r) for r in roles])
    inter.guild = None
    inter.response.defer = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


def respuesta(inter):
    return inter.followup.send.await_args.args[0]


def inscripciones(event_id):
    conn = database.conectar_db()
    filas = conn.execute(
        "SELECT user_id, status, position FROM inscripciones WHERE event_id = ? ORDER BY id", (event_id,)
    ).fetchall()
    conn.close()
    return [tuple(r) for r in filas]


async def main():
    database.inicializar_db()
    v.inicializar_vistas(None)

    inicio = f.a_utc_iso(f.ahora() + timedelta(hours=2))

    # 1. Lista de espera deshabilitada -> se rechaza al llenarse
    ev = crear_evento(start_time=inicio, allow_waitlist=0)
    op = crear_opcion(ev, 1)
    await v.inscribirse(interaccion(10), ev, op)
    i = interaccion(11)
    await v.inscribirse(i, ev, op)
    assert "lista de espera" in respuesta(i).lower(), respuesta(i)
    assert inscripciones(ev) == [(10, "confirmado", 0)], inscripciones(ev)

    # 2. Lista de espera habilitada -> reserva y promocion al cancelar
    ev = crear_evento(start_time=inicio)
    op = crear_opcion(ev, 1)
    await v.inscribirse(interaccion(10), ev, op)
    await v.inscribirse(interaccion(11), ev, op)
    assert inscripciones(ev) == [(10, "confirmado", 0), (11, "espera", 1)], inscripciones(ev)
    await v.cancelar_inscripcion(interaccion(10), ev)
    assert inscripciones(ev) == [(11, "confirmado", 0)], inscripciones(ev)

    # 3. Una sola inscripcion por persona
    ev = crear_evento(start_time=inicio)
    op1, op2 = crear_opcion(ev, 5), crear_opcion(ev, 5)
    await v.inscribirse(interaccion(10), ev, op1)
    i = interaccion(10)
    await v.inscribirse(i, ev, op2)
    assert "inscripción múltiple" in respuesta(i).lower() or "cancelar" in respuesta(i).lower(), respuesta(i)

    # 4. Multiples inscripciones permitidas, sin duplicar la misma opcion
    ev = crear_evento(start_time=inicio, multiple_registrations=1)
    op1, op2 = crear_opcion(ev, 5), crear_opcion(ev, 5)
    await v.inscribirse(interaccion(10), ev, op1)
    await v.inscribirse(interaccion(10), ev, op2)
    i = interaccion(10)
    await v.inscribirse(i, ev, op2)
    assert "registrado" in respuesta(i).lower(), respuesta(i)
    assert len(inscripciones(ev)) == 2, inscripciones(ev)

    # 5. Restricciones de rol
    ev = crear_evento(start_time=inicio)
    op = crear_opcion(ev, 5)
    conn = database.conectar_db()
    conn.execute("INSERT INTO evento_restricciones (event_id, role_id, tipo) VALUES (?, 99, 'permitido')", (ev,))
    conn.execute("INSERT INTO evento_restricciones (event_id, role_id, tipo) VALUES (?, 66, 'bloqueado')", (ev,))
    conn.commit()
    conn.close()

    i = interaccion(10, roles=[1])
    await v.inscribirse(i, ev, op)
    assert "reservado" in respuesta(i).lower(), respuesta(i)

    i = interaccion(11, roles=[99, 66])
    await v.inscribirse(i, ev, op)
    assert "bloqueada" in respuesta(i).lower() or "no puede" in respuesta(i).lower(), respuesta(i)

    await v.inscribirse(interaccion(12, roles=[99]), ev, op)
    assert inscripciones(ev) == [(12, "confirmado", 0)], inscripciones(ev)

    # 6. Evento ya terminado
    ev = crear_evento(start_time=f.a_utc_iso(f.ahora() - timedelta(hours=3)))
    op = crear_opcion(ev, 5)
    i = interaccion(10)
    await v.inscribirse(i, ev, op)
    assert "finalizado" in respuesta(i).lower(), respuesta(i)

    print("smoke test OK")

asyncio.run(main())