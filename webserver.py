import os
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
@app.route('/status')
def home():
    return "Bot de Discord activo", 200

def run():
    # Render asigna el puerto mediante la variable de entorno PORT (por defecto 10000)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.daemon = True  # Permite que el hilo finalice limpiamente si el bot se apaga
    t.start()