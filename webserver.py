"""Servidor web minimo para hosts (Render, Replit...) que exigen un puerto abierto
y para que un pinger externo mantenga el proceso despierto."""
import logging
import os
from threading import Thread

from flask import Flask

log = logging.getLogger("bot_eventos.webserver")

app = Flask(__name__)


@app.route("/")
def index():
    return "NEXUS BOT activo", 200


def run():
    # El host asigna el puerto por la variable PORT; 8000 solo como valor local.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False, use_reloader=False)


def keep_alive():
    """Arranca el servidor en segundo plano. Daemon para que no impida cerrar el bot."""
    hilo = Thread(target=run, daemon=True, name="webserver")
    hilo.start()
    log.info("Servidor web escuchando en el puerto %s", os.getenv("PORT", "8000"))
    return hilo
