"""
GuardianAV — Servidor de ventas
MercadoPago + envío automático de email con código de activación
© 2025 Jorge D. — Río Segundo
"""

import os, json, hmac, hashlib, smtplib, logging
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory
import mercadopago

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN — Completá estos datos
# ══════════════════════════════════════════════════════════
MP_ACCESS_TOKEN = "APP_USR-8307892353327077-060516-75e642ffb57b802fc99455d2a86f2ee4-3452014381"   # ← pegá tu token acá
PRECIO_ARS      = 7999                                # precio en pesos

# Email desde donde se envía (usá Gmail)
EMAIL_REMITENTE  = "dimeojorgeoscar@gmail.com"               # ← tu Gmail
EMAIL_PASSWORD   = "dpklvzzwogzevjfl"             # ← contraseña de app Gmail
EMAIL_NOMBRE     = "GuardianAV"

# URL de tu servidor (cuando lo subas a internet)
BASE_URL = "https://guardianav-web-production.up.railway.app"

# Clave para generar códigos (tiene que ser igual a la del antivirus)
_SECRET = b"GuardianAV-JorgeD-RioSegundo-2025"
# ══════════════════════════════════════════════════════════

app = Flask(__name__, static_folder=".", static_url_path="")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# Base de datos simple en JSON
DB_FILE    = Path("ventas.json")
CODES_USED = Path("codigos_usados.json")


def load_db() -> list:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text())
    return []

def save_db(data: list):
    DB_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def load_used() -> list:
    if CODES_USED.exists():
        return json.loads(CODES_USED.read_text())
    return []

def save_used(data: list):
    CODES_USED.write_text(json.dumps(data, indent=2))


def generar_codigo(serial: str) -> str:
    mac = hmac.new(_SECRET, serial.upper().encode(), hashlib.sha256).hexdigest()
    raw = mac[:20].upper()
    return f"{raw[0:5]}-{raw[5:10]}-{raw[10:15]}-{raw[15:20]}"


def asignar_codigo() -> str:
    """Asigna el próximo código disponible en orden."""
    used = load_used()
    i    = len(used) + 1
    serial = f"CLIENTE{i:03d}"
    code   = generar_codigo(serial)
    used.append({"serial": serial, "code": code, "asignado": datetime.now().isoformat()})
    save_used(used)
    return code


def enviar_email(nombre: str, email: str, codigo: str):
    """Envía el email con el código de activación y el link de descarga."""
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="background:#060d14; color:#e0f0ff; font-family:Arial,sans-serif; padding:40px;">
  <div style="max-width:560px; margin:0 auto;">
    <div style="text-align:center; margin-bottom:32px;">
      <h1 style="color:#00d4ff; font-size:32px; letter-spacing:4px;">GUARDIAN<span style="color:#e0f0ff">AV</span></h1>
      <p style="color:#3a6080; font-size:13px;">Sistema de Protección Avanzada</p>
    </div>

    <div style="background:#0b1929; border:1px solid #0a2d4a; border-radius:12px; padding:28px; margin-bottom:24px;">
      <p style="font-size:16px; margin-bottom:8px;">Hola <strong style="color:#00d4ff">{nombre}</strong>,</p>
      <p style="color:#3a6080; line-height:1.6;">
        Tu compra fue procesada correctamente. Acá tenés todo lo que necesitás para activar GuardianAV.
      </p>
    </div>

    <div style="background:#0b1929; border:2px solid #00d4ff; border-radius:12px; padding:28px; margin-bottom:24px; text-align:center;">
      <p style="color:#3a6080; font-size:13px; margin-bottom:8px;">TU CÓDIGO DE ACTIVACIÓN</p>
      <div style="background:#060d14; border-radius:8px; padding:16px; font-family:Courier New,monospace; font-size:22px; font-weight:bold; color:#ffcc00; letter-spacing:2px;">
        {codigo}
      </div>
      <p style="color:#3a6080; font-size:12px; margin-top:8px;">Guardá este código — lo vas a necesitar para activar el programa</p>
    </div>

    <div style="background:#0b1929; border:1px solid #0a2d4a; border-radius:12px; padding:28px; margin-bottom:24px;">
      <p style="font-size:15px; font-weight:bold; margin-bottom:16px; color:#00ff88;">Pasos para instalar:</p>
      <ol style="color:#3a6080; line-height:2; padding-left:20px;">
        <li>Descargá GuardianAV desde este link: <a href="https://drive.google.com/file/d/1uyrxHWQO9LJHqHDfLX6MgP_H-K0tM4JY/view?usp=sharing" style="color:#00d4ff;">DESCARGAR GUARDIANAV</a></li>
        <li>Ejecutá el archivo como <strong style="color:#e0f0ff">Administrador</strong> (clic derecho → Ejecutar como administrador)</li>
        <li>Ingresá el código de activación cuando lo pida</li>
        <li>¡Listo! Tu PC ya está protegida</li>
      </ol>
    </div>

    <div style="background:#1a0a00; border:2px solid #ff9900; border-radius:12px; padding:22px; margin-bottom:24px;">
      <p style="color:#ff9900; font-size:14px; font-weight:bold; margin-bottom:10px;">⚠️ AVISO IMPORTANTE — Windows SmartScreen</p>
      <p style="color:#cc7700; font-size:13px; line-height:1.7; margin:0;">
        Es posible que Windows muestre una pantalla azul diciendo <strong style="color:#ffbb44">"Windows protegió su PC"</strong>.
        Esto es normal para programas nuevos y <strong style="color:#ffbb44">NO significa que sea un virus</strong>.<br><br>
        Para continuar: hacé clic en <strong style="color:#ffbb44">"Más información"</strong> y después en <strong style="color:#ffbb44">"Ejecutar de todas formas"</strong>.
        El programa fue verificado y es 100% seguro. Ante cualquier duda, contactanos por WhatsApp.
      </p>
    </div>

    <div style="text-align:center; margin-bottom:24px;">
      <a href="https://wa.me/543518634434"
         style="background:#00ff88; color:#060d14; padding:14px 32px; border-radius:8px; font-weight:bold; text-decoration:none; font-size:15px;">
        CONTACTAR SOPORTE POR WHATSAPP
      </a>
    </div>

    <div style="text-align:center; border-top:1px solid #0a2d4a; padding-top:20px;">
      <p style="color:#3a6080; font-size:11px;">© 2025 Todos los derechos reservados</p>
    </div>
  </div>
</body>
</html>
"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "GuardianAV — Tu código de activación"
    msg["From"]    = f"{EMAIL_NOMBRE} <{EMAIL_REMITENTE}>"
    msg["To"]      = email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_REMITENTE, EMAIL_PASSWORD)
        s.sendmail(EMAIL_REMITENTE, email, msg.as_string())

    logging.info(f"Email enviado a {email}")


# ── Rutas ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/crear-pago", methods=["POST"])
def crear_pago():
    """Crea una preferencia de pago en MercadoPago."""
    data   = request.json
    nombre = data.get("nombre", "").strip()
    email  = data.get("email",  "").strip()

    if not nombre or not email:
        return jsonify({"error": "Datos incompletos"}), 400

    sdk  = mercadopago.SDK(MP_ACCESS_TOKEN)
    pref = {
        "items": [{
            "title":      "GuardianAV — Licencia completa",
            "quantity":   1,
            "unit_price": PRECIO_ARS,
            "currency_id": "ARS",
        }],
        "payer": {
            "name":  nombre,
            "email": email,
        },
        "back_urls": {
            "success": f"{BASE_URL}/pago-exitoso",
            "failure": f"{BASE_URL}/pago-fallido",
            "pending": f"{BASE_URL}/pago-pendiente",
        },
        "auto_return":    "approved",
        "notification_url": f"{BASE_URL}/webhook",
        "external_reference": json.dumps({"nombre": nombre, "email": email}),
        "statement_descriptor": "GUARDIANAV",
    }

    result = sdk.preference().create(pref)
    if result["status"] != 201:
        logging.error(f"Error MP: {result}")
        return jsonify({"error": "Error creando pago"}), 500

    init_point = result["response"]["init_point"]
    logging.info(f"Pago creado para {email} — {init_point}")
    return jsonify({"init_point": init_point})


@app.route("/webhook", methods=["POST"])
def webhook():
    """Recibe notificación de MercadoPago cuando el pago se aprueba."""
    data    = request.json or {}
    tipo    = data.get("type", "")
    pago_id = data.get("data", {}).get("id")

    if tipo != "payment" or not pago_id:
        return "", 200

    sdk    = mercadopago.SDK(MP_ACCESS_TOKEN)
    result = sdk.payment().get(pago_id)

    if result["status"] != 200:
        return "", 200

    pago   = result["response"]
    estado = pago.get("status")

    if estado != "approved":
        logging.info(f"Pago {pago_id} en estado: {estado}")
        return "", 200

    # Pago aprobado — procesar
    try:
        ref    = json.loads(pago.get("external_reference", "{}"))
        nombre = ref.get("nombre", pago.get("payer", {}).get("first_name", "Cliente"))
        email  = ref.get("email",  pago.get("payer", {}).get("email", ""))

        # Verificar que no se procese dos veces
        db = load_db()
        if any(v.get("pago_id") == str(pago_id) for v in db):
            logging.info(f"Pago {pago_id} ya procesado")
            return "", 200

        # Asignar código
        codigo = asignar_codigo()

        # Guardar venta
        db.append({
            "pago_id":  str(pago_id),
            "nombre":   nombre,
            "email":    email,
            "codigo":   codigo,
            "fecha":    datetime.now().isoformat(),
            "monto":    pago.get("transaction_amount"),
        })
        save_db(db)

        # Enviar email
        enviar_email(nombre, email, codigo)
        logging.info(f"VENTA PROCESADA — {nombre} ({email}) — Código: {codigo}")

    except Exception as e:
        logging.error(f"Error procesando webhook: {e}")

    return "", 200


@app.route("/pago-exitoso")
def pago_exitoso():
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Pago exitoso — GuardianAV</title>
    <meta http-equiv="refresh" content="5;url=/">
    </head>
    <body style="background:#060d14;color:#e0f0ff;font-family:Arial;text-align:center;padding:80px 20px;">
      <svg width="80" height="80" viewBox="0 0 80 80" style="margin:0 auto 20px;">
        <circle cx="40" cy="40" r="38" fill="none" stroke="#00ff88" stroke-width="3"/>
        <path d="M22 40 L35 53 L58 27" fill="none" stroke="#00ff88" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <h1 style="color:#00ff88;font-size:32px;margin-bottom:12px;">¡Pago aprobado!</h1>
      <p style="color:#3a6080;font-size:16px;margin-bottom:8px;">Revisá tu email — te enviamos el código de activación.</p>
      <p style="color:#3a6080;font-size:13px;">Redirigiendo en 5 segundos...</p>
    </body>
    </html>"""


@app.route("/pago-fallido")
def pago_fallido():
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Pago fallido — GuardianAV</title>
    <meta http-equiv="refresh" content="5;url=/">
    </head>
    <body style="background:#060d14;color:#e0f0ff;font-family:Arial;text-align:center;padding:80px 20px;">
      <h1 style="color:#ff3366;font-size:32px;margin-bottom:12px;">El pago no se completó</h1>
      <p style="color:#3a6080;">Podés intentarlo de nuevo. Redirigiendo...</p>
    </body>
    </html>"""


@app.route("/pago-pendiente")
def pago_pendiente():
    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Pago pendiente — GuardianAV</title></head>
    <body style="background:#060d14;color:#e0f0ff;font-family:Arial;text-align:center;padding:80px 20px;">
      <h1 style="color:#ffcc00;font-size:32px;margin-bottom:12px;">Pago pendiente</h1>
      <p style="color:#3a6080;">Cuando se acredite te enviamos el código por email automáticamente.</p>
    </body>
    </html>"""


@app.route("/activar", methods=["POST"])
def activar_licencia():
    """
    El antivirus llama a esta ruta para validar un código.
    Verifica que el código exista en los generados y no esté ya usado por otro dispositivo.
    """
    data       = request.json or {}
    codigo     = data.get("codigo", "").strip().upper()
    dispositivo = data.get("dispositivo", "desconocido")  # identificador del equipo

    if not codigo:
        return jsonify({"ok": False, "msg": "Código vacío"}), 400

    # Cargar lista de códigos asignados (generados por vos)
    usados = load_used()
    entrada = next((u for u in usados if u["code"].upper() == codigo), None)

    if not entrada:
        return jsonify({"ok": False, "msg": "Código inválido"}), 200

    # Si ya fue activado en otro dispositivo, rechazar
    if entrada.get("activado") and entrada.get("dispositivo") != dispositivo:
        return jsonify({"ok": False, "msg": "Código ya en uso en otro equipo"}), 200

    # Primera activación o mismo dispositivo — marcar y guardar
    entrada["activado"]    = True
    entrada["dispositivo"] = dispositivo
    entrada["fecha_activacion"] = datetime.now().isoformat()
    save_used(usados)

    logging.info(f"ACTIVACION OK — {codigo} — dispositivo: {dispositivo}")
    return jsonify({"ok": True, "msg": "Licencia activada correctamente"}), 200


@app.route("/ventas", methods=["GET"])
def ver_ventas():
    """Panel simple para ver las ventas (solo para vos)."""
    db    = load_db()
    total = sum(v.get("monto", 0) for v in db)
    usados = load_used()
    activados = sum(1 for u in usados if u.get("activado"))
    rows  = "".join(
        f"<tr><td>{v['fecha'][:16]}</td><td>{v['nombre']}</td><td>{v['email']}</td>"
        f"<td style='color:#ffcc00'>{v['codigo']}</td><td style='color:#00ff88'>${v.get('monto',0)}</td></tr>"
        for v in reversed(db)
    )
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Ventas GuardianAV</title></head>
    <body style="background:#060d14;color:#e0f0ff;font-family:monospace;padding:32px;">
      <h1 style="color:#00d4ff">GuardianAV — Panel de Ventas</h1>
      <p style="color:#3a6080">Ventas: {len(db)} | Recaudado: ${total:,.0f} | Activaciones: {activados}</p>
      <table style="width:100%;border-collapse:collapse;margin-top:20px;">
        <tr style="color:#3a6080;border-bottom:1px solid #0a2d4a;">
          <th style="text-align:left;padding:8px">Fecha</th>
          <th style="text-align:left;padding:8px">Nombre</th>
          <th style="text-align:left;padding:8px">Email</th>
          <th style="text-align:left;padding:8px">Código</th>
          <th style="text-align:left;padding:8px">Monto</th>
        </tr>
        {rows if rows else '<tr><td colspan="5" style="padding:20px;color:#3a6080">Sin ventas aún</td></tr>'}
      </table>
    </body>
    </html>"""


if __name__ == "__main__":
    print("=" * 50)
    print("  GuardianAV — Servidor de ventas")
    print("  http://localhost:5000")
    print("  Panel ventas: http://localhost:5000/ventas")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000)
