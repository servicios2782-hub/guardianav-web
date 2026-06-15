"""
GuardianAV — Servidor de ventas
MercadoPago + envío automático de email con código de activación
© 2026 GuardianAV
"""

import os, json, hmac, hashlib, logging
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
import mercadopago
import psycopg2
from psycopg2.extras import RealDictCursor

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════
MP_ACCESS_TOKEN  = os.environ.get("MP_ACCESS_TOKEN", "")
PRECIO_ARS       = 4999   # Precio web. ML va aparte a $5.500. Ajustar a mano si cambia.
ACTIVADORES_POR_COMPRA = 5
EMAIL_REMITENTE  = os.environ.get("EMAIL_REMITENTE", "dimeojorgeoscar@gmail.com")
EMAIL_NOMBRE     = "GuardianAV"
# ── Resend (envío de emails por HTTPS — Railway bloquea SMTP) ──
RESEND_API_KEY   = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM       = f"{EMAIL_NOMBRE} <noreply@guardian-av.com>"  # dominio de prueba gratis de Resend
EMAIL_REPLY_TO   = EMAIL_REMITENTE
DOMINIO_PUBLICO  = "https://guardian-av.com"                          # lo que ve el cliente
BASE_URL         = "https://guardianav-web-production.up.railway.app"  # solo webhook (directo, sin Cloudflare)
_SECRET          = b"GuardianAV-JorgeD-RioSegundo-2025"
ML_ACCESS_TOKEN  = os.environ.get("ML_ACCESS_TOKEN", "")
DATABASE_URL     = os.environ.get("DATABASE_URL", "")

# Resend — API HTTP por puerto 443 (Railway bloquea SMTP saliente)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = f"{EMAIL_NOMBRE} <noreply@guardian-av.com>"
RESEND_TIMEOUT = 15
# ══════════════════════════════════════════════════════════

app = Flask(__name__, static_folder=".", static_url_path="")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


def _resend_send(destinatario: str, asunto: str, html: str):
    """Manda un email vía Resend API. Lanza excepción con el body si la API
    devuelve != 2xx."""
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY no configurada en el entorno")
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": RESEND_FROM,
            "to": [destinatario],
            "subject": asunto,
            "html": html,
            "reply_to": EMAIL_REMITENTE,
        },
        timeout=RESEND_TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(f"Resend {r.status_code}: {r.text}")


# ── PostgreSQL ─────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    """Crea las tablas si no existen."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ventas (
                    id SERIAL PRIMARY KEY,
                    pago_id TEXT UNIQUE,
                    nombre TEXT,
                    email TEXT,
                    codigo TEXT,
                    fecha TEXT,
                    monto REAL,
                    fuente TEXT DEFAULT 'web',
                    ref TEXT DEFAULT ''
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS codigos_usados (
                    id SERIAL PRIMARY KEY,
                    serial TEXT UNIQUE,
                    code TEXT UNIQUE,
                    asignado TEXT,
                    activado BOOLEAN DEFAULT FALSE,
                    dispositivo TEXT DEFAULT '',
                    fecha_activacion TEXT DEFAULT ''
                );
            """)
        conn.commit()
    logging.info("Base de datos PostgreSQL inicializada")

try:
    init_db()
except Exception as e:
    logging.error(f"Error iniciando DB: {e}")


def load_db() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM ventas ORDER BY id DESC")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"load_db error: {e}")
        return []

def save_venta(entrada: dict):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ventas (pago_id, nombre, email, codigo, fecha, monto, fuente, ref)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (pago_id) DO NOTHING
                """, (
                    entrada.get("pago_id"), entrada.get("nombre"), entrada.get("email"),
                    entrada.get("codigo"), entrada.get("fecha"), entrada.get("monto"),
                    entrada.get("fuente","web"), entrada.get("ref","")
                ))
            conn.commit()
    except Exception as e:
        logging.error(f"save_venta error: {e}")

def venta_existe(pago_id: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM ventas WHERE pago_id=%s", (str(pago_id),))
                return cur.fetchone() is not None
    except Exception as e:
        logging.error(f"venta_existe error: {e}")
        return False

def load_used() -> list:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM codigos_usados ORDER BY id")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"load_used error: {e}")
        return []

def save_codigo(entry: dict):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO codigos_usados (serial, code, asignado)
                    VALUES (%s,%s,%s) ON CONFLICT (serial) DO NOTHING
                """, (entry["serial"], entry["code"], entry["asignado"]))
            conn.commit()
    except Exception as e:
        logging.error(f"save_codigo error: {e}")

def activar_codigo(codigo: str, dispositivo: str) -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM codigos_usados WHERE UPPER(code)=%s", (codigo.upper(),))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "msg": "Código inválido"}
                if row["activado"] and row["dispositivo"] != dispositivo:
                    return {"ok": False, "msg": "Código ya en uso en otro equipo"}
                cur.execute("""
                    UPDATE codigos_usados SET activado=TRUE, dispositivo=%s, fecha_activacion=%s
                    WHERE UPPER(code)=%s
                """, (dispositivo, datetime.now().isoformat(), codigo.upper()))
            conn.commit()
            return {"ok": True, "msg": "Licencia activada correctamente"}
    except Exception as e:
        logging.error(f"activar_codigo error: {e}")
        return {"ok": False, "msg": "Error interno"}


def generar_codigo(serial: str) -> str:
    mac = hmac.new(_SECRET, serial.upper().encode(), hashlib.sha256).hexdigest()
    raw = mac[:20].upper()
    return f"{raw[0:5]}-{raw[5:10]}-{raw[10:15]}-{raw[15:20]}"


def asignar_codigo() -> str:
    """Asigna el próximo código disponible en orden (1 solo)."""
    return asignar_codigos(1)[0]


def asignar_codigos(n: int = 1) -> list:
    """Asigna los próximos n códigos disponibles en orden y devuelve la lista."""
    codes = []
    for _ in range(n):
        used   = load_used()
        i      = len(used) + 1
        serial = f"CLIENTE{i:03d}"
        code   = generar_codigo(serial)
        save_codigo({"serial": serial, "code": code, "asignado": datetime.now().isoformat()})
        codes.append(code)
    return codes


def _enviar_html(destinatario: str, asunto: str, html: str):
    """Envía un email por la API de Resend (HTTPS).
    Railway bloquea SMTP, por eso NO mandamos por Gmail directo sino por Resend.
    Necesita la variable de entorno RESEND_API_KEY configurada en Railway."""
    if not RESEND_API_KEY:
        logging.error("RESEND_API_KEY no configurada — el email NO se envió")
        raise RuntimeError("RESEND_API_KEY no configurada")
    payload = json.dumps({
        "from":     EMAIL_FROM,
        "to":       [destinatario],
        "reply_to": EMAIL_REPLY_TO,
        "subject":  asunto,
        "html":     html,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
            "User-Agent":    "GuardianAV/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "ignore")
        logging.error(f"Resend rechazó ({e.code}): {cuerpo}")
        raise RuntimeError(f"Resend {e.code}: {cuerpo}")
    logging.info(f"Email enviado vía Resend a {destinatario}")


def enviar_email(nombre: str, email: str, codigos):
    """Envía el email con el/los código(s) de activación y el link de descarga.
    `codigos` puede ser un string (1 código) o una lista de códigos."""
    if isinstance(codigos, str):
        codigos = [codigos]

    # Cajas de código (una por activador)
    cajas_codigos = "".join(
        f'''<div style="background:#060d14; border-radius:8px; padding:14px; font-family:Courier New,monospace; font-size:20px; font-weight:bold; color:#ffcc00; letter-spacing:2px; margin-bottom:8px;">
        <span style="color:#3a6080; font-size:12px; display:block; letter-spacing:1px;">Activador {idx}</span>{cod}
      </div>'''
        for idx, cod in enumerate(codigos, start=1)
    )
    plural = "tus códigos" if len(codigos) > 1 else "tu código"
    titulo_codigos = f"TUS {len(codigos)} CÓDIGOS DE ACTIVACIÓN" if len(codigos) > 1 else "TU CÓDIGO DE ACTIVACIÓN"
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
      <p style="color:#3a6080; font-size:13px; margin-bottom:8px;">{titulo_codigos}</p>
      {cajas_codigos}
      <p style="color:#3a6080; font-size:12px; margin-top:8px;">Cada código activa 1 PC. Guardá {plural} — los vas a necesitar para activar el programa.</p>
      <p style="color:#ffcc00; font-size:12px; margin-top:4px;">⚠️ Si no encontrabas este mail, revisá la carpeta de <strong>SPAM</strong> o <strong>No deseado</strong>.</p>
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
      <p style="color:#3a6080; font-size:11px;">© 2026 GuardianAV — Todos los derechos reservados</p>
    </div>
  </div>
</body>
</html>
"""
    _enviar_html(email, "GuardianAV — Tu código de activación", html)
    logging.info(f"Email enviado a {email}")


def enviar_email_admin(nombre: str, email: str, codigo: str, monto, fuente: str):
    """Notifica al admin (Jorge) de cada venta."""
    html = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#060d14;color:#e0f0ff;font-family:Arial,sans-serif;padding:30px;">
  <div style="max-width:500px;margin:0 auto;">
    <h2 style="color:#00ff88;text-align:center;">💰 NUEVA VENTA — GuardianAV</h2>
    <div style="background:#0b1929;border:2px solid #00ff88;border-radius:12px;padding:24px;margin:20px 0;">
      <p><strong style="color:#00d4ff">Cliente:</strong> {nombre}</p>
      <p><strong style="color:#00d4ff">Email:</strong> {email}</p>
      <p><strong style="color:#00d4ff">Código asignado:</strong> <span style="color:#ffcc00;font-family:monospace">{codigo}</span></p>
      <p><strong style="color:#00d4ff">Monto:</strong> <span style="color:#00ff88">${monto}</span></p>
      <p><strong style="color:#00d4ff">Plataforma:</strong> {fuente}</p>
    </div>
    <p style="color:#3a6080;font-size:12px;text-align:center;">Panel de ventas: {DOMINIO_PUBLICO}/ventas</p>
  </div>
</body></html>
"""
    _enviar_html(EMAIL_REMITENTE, f"💰 Nueva venta GuardianAV — {nombre}", html)
    logging.info(f"Email admin enviado — venta de {nombre}")



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
            "title":      "GuardianAV — Pack 5 activadores (licencia 1 año)",
            "quantity":   1,
            "unit_price": PRECIO_ARS,
            "currency_id": "ARS",
        }],
        "payer": {
            "name":  nombre,
            "email": email,
        },
        "back_urls": {
            "success": f"{DOMINIO_PUBLICO}/pago-exitoso",
            "failure": f"{DOMINIO_PUBLICO}/pago-fallido",
            "pending": f"{DOMINIO_PUBLICO}/pago-pendiente",
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
        if venta_existe(str(pago_id)):
            logging.info(f"Pago {pago_id} ya procesado")
            return "", 200

        # Asignar los 5 códigos del pack
        codigos = asignar_codigos(ACTIVADORES_POR_COMPRA)
        codigo_str = ", ".join(codigos)

        # Guardar venta
        entrada = {
            "pago_id":  str(pago_id),
            "nombre":   nombre,
            "email":    email,
            "codigo":   codigo_str,
            "fecha":    datetime.now().isoformat(),
            "monto":    pago.get("transaction_amount"),
            "fuente":   "web",
        }
        save_venta(entrada)

        # Enviar email con los 5 códigos
        try:
            enviar_email(nombre, email, codigos)
            logging.info(f"VENTA PROCESADA — {nombre} ({email}) — Códigos: {codigo_str}")
        except Exception as email_err:
            logging.error(f"ERROR ENVIANDO EMAIL a {email}: {email_err}")

        try:
            enviar_email_admin(nombre, email, codigo_str, pago.get("transaction_amount"), "web")
        except Exception as e:
            logging.error(f"Error email admin: {e}")

    except Exception as e:
        logging.error(f"Error procesando webhook: {e}")

    return "", 200


@app.route("/test-email")
def test_email():
    """Envia un email de prueba — sirve para confirmar que el fix de SMTP funciona.
    Uso: /test-email?to=email@destino.com (si no se pasa to, va al admin)."""
    destino = request.args.get("to", EMAIL_REMITENTE).strip()
    try:
        enviar_email("PRUEBA", destino, "TEST-CODIGO-1234")
        return jsonify({"ok": True, "mensaje": f"Email enviado a {destino}. Revisá tu bandeja y spam."}), 200
    except Exception as e:
        logging.error(f"TEST-EMAIL falló para {destino}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


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
      <p style="color:#ffcc00;font-size:14px;margin-bottom:8px;">⚠️ Si no lo encontrás, revisá la carpeta de <strong>SPAM</strong> o <strong>No deseado</strong>.</p>
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


@app.route("/webhook-hotmart", methods=["POST"])
def webhook_hotmart():
    """Recibe notificación de Hotmart cuando se aprueba una compra."""
    try:
        raw = request.get_data(as_text=True)
        logging.info(f"Webhook Hotmart RAW: {raw[:500]}")
        data = request.get_json(force=True, silent=True) or {}

        event = data.get("event", "")
        logging.info(f"Webhook Hotmart evento: {event}")

        if event not in ("PURCHASE_APPROVED", "PURCHASE_COMPLETE"):
            return jsonify({"ok": True}), 200

        inner = data.get("data", {})
        buyer = inner.get("buyer", {})
        nombre = buyer.get("name", "Cliente")
        email  = buyer.get("email", "")

        if not email:
            logging.warning("Webhook Hotmart sin email")
            return jsonify({"ok": True}), 200

        purchase = inner.get("purchase", {})
        order_id = str(purchase.get("transaction", data.get("id", datetime.now().isoformat())))

        if venta_existe(order_id):
            logging.info(f"Hotmart orden {order_id} ya procesada")
            return jsonify({"ok": True}), 200

        codigo = asignar_codigo()
        save_venta({
            "pago_id": order_id,
            "nombre":  nombre,
            "email":   email,
            "codigo":  codigo,
            "fecha":   datetime.now().isoformat(),
            "monto":   purchase.get("price", {}).get("value", 0),
            "fuente":  "hotmart",
            "ref":     "",
        })

        try:
            enviar_email(nombre, email, codigo)
            logging.info(f"VENTA HOTMART — {nombre} ({email}) — Codigo: {codigo}")
        except Exception as email_err:
            logging.error(f"ERROR EMAIL HOTMART a {email}: {email_err}")

        try:
            monto_hotmart = purchase.get("price", {}).get("value", 0)
            enviar_email_admin(nombre, email, codigo, monto_hotmart, "hotmart")
        except Exception as e:
            logging.error(f"Error email admin hotmart: {e}")

    except Exception as e:
        logging.error(f"Error procesando webhook Hotmart: {e}")

    return jsonify({"ok": True}), 200


@app.route("/webhook-ml", methods=["POST"])
def webhook_ml():
    """Recibe notificación de MercadoLibre cuando se concreta una orden."""
    data    = request.json or {}
    topic   = data.get("topic", "") or request.args.get("topic", "")
    resource = data.get("resource", "") or request.args.get("resource", "")

    logging.info(f"Webhook ML recibido: topic={topic} resource={resource}")

    if topic not in ("orders_v2", "orders"):
        return "", 200

    # Obtener ID de la orden
    order_id = resource.split("/")[-1] if resource else data.get("id")
    if not order_id:
        return "", 200

    try:
        import urllib.request as _req
        ml_url = f"https://api.mercadolibre.com/orders/{order_id}?access_token={ML_ACCESS_TOKEN}"
        with _req.urlopen(ml_url, timeout=10) as resp:
            orden = json.loads(resp.read().decode())

        estado = orden.get("status")
        if estado != "paid":
            logging.info(f"Orden ML {order_id} en estado: {estado}")
            return "", 200

        # Datos del comprador
        buyer  = orden.get("buyer", {})
        nombre = f"{buyer.get('first_name', '')} {buyer.get('last_name', '')}".strip() or "Cliente"
        email  = buyer.get("email", "")
        monto  = orden.get("total_amount", 0)

        if venta_existe(str(order_id)):
            logging.info(f"Orden ML {order_id} ya procesada")
            return "", 200

        codigos = asignar_codigos(ACTIVADORES_POR_COMPRA)
        save_venta({
            "pago_id": str(order_id),
            "nombre":  nombre,
            "email":   email,
            "codigo":  ", ".join(codigos),
            "fecha":   datetime.now().isoformat(),
            "monto":   monto,
            "fuente":  "mercadolibre",
            "ref":     "",
        })

        try:
            enviar_email(nombre, email, codigos)
            logging.info(f"VENTA ML PROCESADA — {nombre} ({email}) — Codigos: {', '.join(codigos)}")
        except Exception as email_err:
            logging.error(f"ERROR EMAIL ML a {email}: {email_err}")

        try:
            enviar_email_admin(nombre, email, ", ".join(codigos), monto, "mercadolibre")
        except Exception as e:
            logging.error(f"Error email admin ML: {e}")

    except Exception as e:
        logging.error(f"Error procesando webhook ML: {e}")

    return "", 200


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

    resultado = activar_codigo(codigo, dispositivo)
    if resultado["ok"]:
        logging.info(f"ACTIVACION OK — {codigo} — dispositivo: {dispositivo}")
    else:
        logging.warning(f"ACTIVACION FALLIDA — {codigo}: {resultado['msg']}")
    return jsonify(resultado), 200


@app.route("/ventas", methods=["GET"])
def ver_ventas():
    """Panel simple para ver las ventas (solo para vos)."""
    db    = load_db()
    total = sum(v.get("monto", 0) or 0 for v in db)
    usados = load_used()
    activados = sum(1 for u in usados if u.get("activado"))
    rows  = "".join(
        f"<tr><td>{v['fecha'][:16]}</td><td>{v['nombre']}</td><td>{v['email']}</td>"
        f"<td style='color:#ffcc00'>{v['codigo']}</td><td style='color:#00ff88'>${v.get('monto',0)}</td>"
        f"<td style='color:#3a6080'>{v.get('fuente','web')}</td></tr>"
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
          <th style="text-align:left;padding:8px">Fuente</th>
        </tr>
        {rows if rows else '<tr><td colspan="6" style="padding:20px;color:#3a6080">Sin ventas aún</td></tr>'}
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
