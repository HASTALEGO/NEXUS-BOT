"""Servidor web minimo para hosts (Render, Replit...) que exigen un puerto abierto
y para que un pinger externo mantenga el proceso despierto."""
import logging
import os
from threading import Thread

from flask import Flask, jsonify

log = logging.getLogger("bot_eventos.webserver")

app = Flask(__name__)


@app.route('/')
def home():
    return "Bot en línea"

@app.route('/status')
def status():
    return jsonify({
        "estado": "Online",
        "bot": "Activo"
    })

def run():
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

def keep_alive():
    """Arranca el servidor en segundo plano. Daemon para que no impida cerrar el bot."""
    hilo = Thread(target=run, daemon=True, name="webserver")
    hilo.start()
    log.info("Servidor web escuchando en el puerto %s", os.getenv("PORT", "8000"))
    return hilo
