# Desplegar NEXUS-BOT 24/7 gratis en Oracle Cloud Always Free

Registro: https://www.oracle.com/es/cloud/free/ · Detalle del tier gratuito: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

Necesitas una tarjeta solo para verificar identidad (cobro temporal de ~1 € que se devuelve). Mientras uses recursos "Always Free" no se cobra nada.

## 1. Crear la máquina
1. Entra en la consola → **Compute → Instances → Create instance**.
2. **Image**: Canonical Ubuntu 24.04. **Shape**: `VM.Standard.A1.Flex` (marcado *Always Free eligible*), 1-4 OCPU y 6-24 GB RAM.
   - Si sale "Out of capacity", prueba otro *Availability Domain* o repite más tarde; también sirve `VM.Standard.E2.1.Micro` (x86, también gratis).
3. En **Add SSH keys**, deja que genere el par y **descarga la clave privada**.
4. Crea la instancia y apunta la **IP pública**.

No hace falta abrir puertos: el bot solo hace conexiones salientes a Discord.

## 2. Conectarte
Desde PowerShell en tu PC:

```powershell
icacls .\ssh-key.key /inheritance:r /grant:r "$env:USERNAME:R"
ssh -i .\ssh-key.key ubuntu@LA_IP_PUBLICA
```

## 3. Instalar el bot

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
git clone https://github.com/TU_USUARIO/NEXUS-BOT.git ~/nexus-bot
cd ~/nexus-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Crea el `.env` en el servidor (nunca lo subas al repo):

```bash
nano ~/nexus-bot/.env
```

```
DISCORD_TOKEN=tu_token
GUILD_ID=tu_guild_id
```

```bash
chmod 600 ~/nexus-bot/.env
```

Prueba manual: `~/nexus-bot/.venv/bin/python main.py` → debe decir "Bot listo". Corta con Ctrl+C.

## 4. Servicio systemd (arranca solo y se reinicia si falla)

```bash
sudo nano /etc/systemd/system/nexus-bot.service
```

```ini
[Unit]
Description=NEXUS BOT (Discord)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/nexus-bot
ExecStart=/home/ubuntu/nexus-bot/.venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nexus-bot
systemctl status nexus-bot
journalctl -u nexus-bot -f      # ver logs en vivo
```

Para actualizar el código:

```bash
cd ~/nexus-bot && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart nexus-bot
```

## 5. Copia de seguridad de la base de datos

```bash
crontab -e
```

```
0 4 * * * sqlite3 /home/ubuntu/nexus-bot/eventos.db ".backup '/home/ubuntu/backup-eventos.db'"
```

(`sudo apt install -y sqlite3` si hace falta.)

## Avisos
- Oracle **borra las instancias Always Free inactivas** si la CPU baja del 10 % durante 7 días seguidos; con una cuenta *Upgrade to Pay As You Go* no pasa, y también puedes crear una alarma de actividad. En la práctica un bot pequeño consume poco: si te la reclaman, vuelve a crearla o mantén la instancia con algo de carga.
- Zona horaria: `sudo timedatectl set-timezone Europe/Madrid` (el código ya usa `Europe/Madrid` para mostrar y guarda en UTC).
- Voz: si quieres soporte de audio, `sudo apt install -y libffi-dev libnacl-dev` y `pip install PyNaCl`.
