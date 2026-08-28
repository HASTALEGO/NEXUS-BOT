import os
from flask import Flask, jsonify
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "NexusBot está activo."

@app.route('/status')
def status():
    return jsonify({"status": "online", "message": "Servidor web operativo"}), 200

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()