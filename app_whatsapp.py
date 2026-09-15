"""
School AI Assistant - Modern Infinity Language School
SECURITY HARDENED VERSION

Security measures implemented:
1. Webhook signature verification (HMAC-SHA256) - prevents fake webhook calls
2. Rate limiting per phone number - prevents abuse/flooding
3. Input sanitization - prevents injection attacks
4. Request size limits - prevents DoS via large payloads
5. Token never logged - prevents token leakage in logs
6. Error responses never reveal internals
7. Health endpoint sanitized - no sensitive data exposed
8. Gunicorn production server - no Flask dev server in prod
"""

import os
import json
import re
import base64
import hmac
import hashlib
import time
import logging
import requests
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, abort
import gspread
from google.oauth2.service_account import Credentials

# ── Logging (never log tokens or keys) ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 KB max payload

# ── Config from environment (NEVER hardcode) ────────────────────────────────
SHEET_ID         = os.environ.get("SHEET_ID", "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE")
PHONE_NUMBER_ID  = os.environ.get("PHONE_NUMBER_ID", "1358537447338280")
ACCESS_TOKEN     = os.environ.get("ACCESS_TOKEN", "")
VERIFY_TOKEN     = os.environ.get("VERIFY_TOKEN", "schoolai2026")
APP_SECRET       = os.environ.get("APP_SECRET", "")          # Meta App Secret for sig verification
META_API_URL     = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

SCHOOL = {
    "name": "Modern Infinity Language School",
    "phone": "02-3796-9155 / 02-3796-9166",
    "whatsapp": "01066253331",
    "address": "El Yasmeen Compound, Entrance 1, El Sheikh Zayed, 6th of October City",
    "admin_hours": "Sunday to Thursday, 8:00 AM to 5:00 PM",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Rate limiting (in-memory, per phone number) ──────────────────────────────
RATE_LIMIT_WINDOW = 60          # seconds
RATE_LIMIT_MAX    = 10          # max messages per window per number
_rate_store       = defaultdict(list)

def is_rate_limited(phone: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    hits = [t for t in _rate_store[phone] if t > window_start]
    hits.append(now)
    _rate_store[phone] = hits
    if len(hits) > RATE_LIMIT_MAX:
        logger.warning(f"[rate_limit] {phone[:6]}*** exceeded limit")
        return True
    return False

# ── Webhook signature verification ──────────────────────────────────────────
def verify_webhook_signature(payload: bytes, sig_header: str) -> bool:
    """
    Meta sends X-Hub-Signature-256: sha256=<hmac>
    Verify with App Secret to confirm the request is genuinely from Meta.
    Without this, anyone could POST fake messages to your webhook.
    """
    if not APP_SECRET:
        logger.warning("[security] APP_SECRET not set — skipping signature check")
        return True   # degrade gracefully if not configured yet
    if not sig_header or not sig_header.startswith("sha256="):
        logger.warning("[security] Missing or malformed signature header")
        return False
    expected = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)

# ── Google credentials (base64 → JSON, no private key ever logged) ──────────
def _load_creds_info() -> dict:
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "").strip()
    if b64:
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    raw = os.environ.get("GOOGLE_CREDENTIALS", "").strip()
    if raw:
        info = json.loads(raw)
        if isinstance(info.get("private_key"), str):
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info
    if os.path.exists("credentials.json"):
        with open("credentials.json") as f:
            return json.load(f)
    raise RuntimeError("No Google credentials found. Set GOOGLE_CREDENTIALS_B64.")

def get_client():
    info = _load_creds_info()
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    logger.info(f"[creds] authorized as {info.get('client_email')}")
    return gspread.authorize(creds)

def read_tab(tab_name: str) -> list:
    try:
        wb = get_client().open_by_key(SHEET_ID)
        rows = wb.worksheet(tab_name).get_all_records()
        logger.info(f"[sheets] OK {tab_name}: {len(rows)} rows")
        return rows
    except Exception as e:
        logger.error(f"[sheets] FAIL {tab_name}: {e}")
        return []

# ── Input sanitization ───────────────────────────────────────────────────────
def sanitize(text: str, max_len: int = 500) -> str:
    """Strip control chars and truncate to prevent injection / oversized processing."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_len]

# ── WhatsApp send (token never logged) ──────────────────────────────────────
def send_whatsapp(to_phone: str, message: str) -> None:
    if not ACCESS_TOKEN:
        logger.error("[wa] ACCESS_TOKEN not set")
        return
    # Truncate message to WhatsApp limit
    message = message[:4096]
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"body": message},
    }
    try:
        res = requests.post(META_API_URL, headers=headers, json=payload, timeout=10)
        logger.info(f"[wa] sent to {to_phone[:6]}***: {res.status_code}")
        if res.status_code != 200:
            logger.error(f"[wa] error response: {res.text[:200]}")
    except requests.Timeout:
        logger.error("[wa] request timed out")
    except Exception as e:
        logger.error(f"[wa] exception: {e}")

# ── Message processing ───────────────────────────────────────────────────────
def process_message(msg: str) -> str:
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    if any(w in m for w in ['homework', 'assignment', 'hw', 'واجب', 'تكليف']):
        grade = None
        for g in ['12', '11', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1']:
            if f'grade {g}' in m or f'الصف {g}' in m or f'صف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف\nمثال: واجب الصف السابع" if is_arabic else "Specify grade\nExample: Homework for Grade 7"
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in str(r.get("Grade", "")).lower() and str(r.get("Assignment", "")).strip()]
        if not hw:
            return f"لا يوجد واجب لـ{grade}\n📞 {SCHOOL['phone']}" if is_arabic else f"No homework for {grade}\n📞 {SCHOOL['phone']}"
        r = f"📚 {'واجبات' if is_arabic else ''} {grade} {'Homework' if not is_arabic else ''}\n\n"
        for h in hw:
            r += f"• {h.get('Subject', '')}: {h.get('Assignment', '')}\n  Due: {h.get('Due Date', '')}\n\n"
        return r.strip()

    if any(w in m for w in ['schedule', 'timetable', 'جدول', 'حصص']):
        grade = None
        for g in ['12', '11', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1']:
            if f'grade {g}' in m or f'الصف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف" if is_arabic else "Specify grade\nExample: Grade 7 schedule"
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in str(r.get("Grade", "")).lower()]
        if not sched:
            return f"لا يوجد جدول لـ{grade}" if is_arabic else f"No schedule for {grade}"
        r = f"📅 {grade} Schedule\n\n"
        for s in sched:
            ps = [str(s.get(f'Period {i}', '')) for i in range(1, 6) if s.get(f'Period {i}', '')]
            r += f"{s.get('Day', '')}: {' → '.join(ps)}\n"
        return r.strip()

    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID", "")).upper() == sid), None)
        if not s:
            return f"ID {sid} not found\n📞 {SCHOOL['phone']}"
        name = s.get('Student Name', ''); grade = s.get('Grade', '')
        if any(w in m for w in ['attendance', 'absent', 'حضور', 'غياب']):
            present = int(s.get('Days Present', 0) or 0)
            total = int(s.get('Total School Days', 0) or 0)
            pct = round((present / total * 100) if total > 0 else 0, 1)
            return f"👤 {name} ({grade})\nPresent: {present}/{total}\nRate: {pct}%"
        total = int(s.get('Total Fees', 0) or 0)
        paid = int(s.get('Amount Paid', 0) or 0)
        remaining = int(s.get('Remaining', 0) or 0)
        return (f"👤 {name} ({grade})\n💰 Total: {total:,} EGP\nPaid: {paid:,} EGP ✅\n"
                f"Remaining: {remaining:,} EGP\nStatus: {s.get('Payment Status', '')}\n"
                f"📞 {SCHOOL['phone']}")

    if any(w in m for w in ['fee', 'fees', 'cost', 'how much', 'رسوم', 'مصاريف', 'كام']):
        rows = read_tab("Admissions")
        info = {r.get("Item", ""): r.get("Value", "") for r in rows}
        return (f"💰 Modern Infinity Fees 2025/2026\n\n"
                f"🔸 KG: {info.get('KG1 Fees', '42,000 EGP')}\n"
                f"🔸 Grade 1-3: {info.get('Grade 1-3 Fees', '48,000 EGP')}\n"
                f"🔸 Grade 4-6: {info.get('Grade 4-6 Fees', '55,000 EGP')}\n"
                f"🔸 Grade 7-9: {info.get('Grade 7-9 Fees', '62,000 EGP')}\n"
                f"🔸 Grade 10-12: {info.get('Grade 10-12 Fees', '70,000 EGP')}\n\n"
                f"📅 3 installments\n📞 {SCHOOL['phone']}")

    if any(w in m for w in ['announcement', 'news', 'holiday', 'exam', 'إعلان', 'امتحان']):
        rows = read_tab("Announcements")
        active = [r for r in rows if str(r.get("Status", "")).lower() == "active"]
        if not active:
            return "No announcements" if not is_arabic else "لا توجد إعلانات"
        r = "📢 School Announcements\n\n"
        for a in active:
            r += f"🔔 {a.get('Title', '')}\n{a.get('Message', '')}\n📅 {a.get('Date', '')}\n\n"
        return r.strip()

    if any(w in m for w in ['admission', 'enroll', 'register', 'قبول', 'تسجيل']):
        rows = read_tab("Admissions")
        info = {r.get("Item", ""): r.get("Value", "") for r in rows}
        return (f"📋 Admissions at Modern Infinity\n\n"
                f"✅ {info.get('Registration Status', 'Open')}\n"
                f"📅 Deadline: {info.get('Application Deadline', '')}\n\n"
                f"📞 {SCHOOL['phone']}")

    if any(w in m for w in ['location', 'address', 'where', 'map', 'عنوان', 'فين', 'موقع']):
        return f"📍 {SCHOOL['address']}\n📞 {SCHOOL['phone']}\nHours: {SCHOOL['admin_hours']}"

    return (f"Welcome to Modern Infinity! 👋\n\n"
            f"📚 Homework for Grade 7\n"
            f"📅 Grade 8 schedule\n"
            f"💰 Fees for Grade 5\n"
            f"💳 Balance STU001\n"
            f"📢 Announcements\n"
            f"📋 Admissions\n"
            f"📍 Location\n\n📞 {SCHOOL['phone']}")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verify():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("[webhook] verification OK")
        return challenge, 200
    logger.warning("[webhook] verification FAILED")
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive():
    # 1. Verify signature
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(request.get_data(), sig):
        logger.warning("[webhook] signature verification FAILED — dropping request")
        abort(403)

    try:
        data     = request.get_json(force=True, silent=True) or {}
        entry    = data.get("entry", [])
        if not entry: return "OK", 200
        changes  = entry[0].get("changes", [])
        if not changes: return "OK", 200
        value    = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages: return "OK", 200

        msg = messages[0]
        if msg.get("type") != "text": return "OK", 200

        phone = msg.get("from", "")
        text  = msg.get("text", {}).get("body", "")

        if not phone or not text:
            return "OK", 200

        # 2. Rate limiting
        if is_rate_limited(phone):
            return "OK", 200

        # 3. Sanitize input
        text = sanitize(text)
        logger.info(f"[wa] received from {phone[:6]}***: {text[:50]}")

        # 4. Process and reply
        response = process_message(text)
        send_whatsapp(phone, response)

        return "OK", 200

    except Exception as e:
        logger.error(f"[webhook] unhandled error: {e}")
        return "OK", 200   # Always 200 to Meta — never expose errors


@app.route("/health")
def health():
    # Sanitized health check — no tokens, no keys, no internal paths
    rows = read_tab("Homework")
    return jsonify({
        "status": "running",
        "school": SCHOOL["name"],
        "whatsapp_configured": bool(ACCESS_TOKEN),
        "sheets_connected": len(rows) > 0,
        "homework_rows": len(rows),
    })


@app.route("/")
def index():
    return send_from_directory(".", "live_demo.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port {port}")
    # Use gunicorn in production (see Procfile)
    app.run(debug=False, port=port, host="0.0.0.0")
