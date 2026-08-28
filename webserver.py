import os
import threading
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
@app.route('/status')
def home():
    return jsonify({"status": "ok", "message": "Bot de Discord activo"}), 200

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.daemon = True
    t.start()