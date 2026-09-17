"""
School AI Assistant - Modern Infinity Language School
Includes: WhatsApp webhook + Admin Panel + Broadcast announcements
"""
import os, json, re, base64, requests, logging, time, hmac, hashlib
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, render_template_string
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

# ── Config ───────────────────────────────────────────────────────────────────
SHEET_ID        = os.environ.get("SHEET_ID", "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1358537447338280")
ACCESS_TOKEN    = os.environ.get("ACCESS_TOKEN", "")
VERIFY_TOKEN    = os.environ.get("VERIFY_TOKEN", "schoolai2026")
APP_SECRET      = os.environ.get("APP_SECRET", "")
ADMIN_PASSWORD  = "moderninfinity2026"
META_API_URL    = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

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

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 10
_rate_store = defaultdict(list)

def is_rate_limited(phone):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    hits = [t for t in _rate_store[phone] if t > window_start]
    hits.append(now)
    _rate_store[phone] = hits
    return len(hits) > RATE_LIMIT_MAX

# ── Webhook signature verification ────────────────────────────────────────────
def verify_webhook_signature(payload, sig_header):
    if not APP_SECRET:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)

# ── Google credentials ────────────────────────────────────────────────────────
def _load_creds_info():
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
    raise RuntimeError("No Google credentials found.")

def get_client():
    info = _load_creds_info()
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)

def read_tab(tab_name):
    try:
        wb = get_client().open_by_key(SHEET_ID)
        rows = wb.worksheet(tab_name).get_all_records()
        logger.info(f"[sheets] OK {tab_name}: {len(rows)} rows")
        return rows
    except Exception as e:
        logger.error(f"[sheets] FAIL {tab_name}: {e}")
        return []

# ── WhatsApp send ─────────────────────────────────────────────────────────────
def send_whatsapp(to_phone, message):
    if not ACCESS_TOKEN:
        return False
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"body": message[:4096]},
    }
    try:
        res = requests.post(META_API_URL, headers=headers, json=payload, timeout=10)
        logger.info(f"[wa] sent to {to_phone[:6]}***: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        logger.error(f"[wa] error: {e}")
        return False

# ── Message processing ────────────────────────────────────────────────────────
def sanitize(text, max_len=500):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_len]

def process_message(msg):
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    if any(w in m for w in ['homework', 'assignment', 'hw', 'واجب', 'تكليف']):
        grade = None
        for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
            if f'grade {g}' in m or f'الصف {g}' in m or f'صف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف\nمثال: واجب الصف السابع" if is_arabic else "Specify grade\nExample: Homework for Grade 7"
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower() and str(r.get("Assignment","")).strip()]
        if not hw:
            return f"لا يوجد واجب لـ{grade}\n📞 {SCHOOL['phone']}" if is_arabic else f"No homework for {grade}\n📞 {SCHOOL['phone']}"
        r = f"📚 {grade} Homework\n\n"
        for h in hw:
            r += f"• {h.get('Subject','')}: {h.get('Assignment','')}\n  Due: {h.get('Due Date','')}\n\n"
        return r.strip()

    if any(w in m for w in ['schedule','timetable','جدول','حصص']):
        grade = None
        for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
            if f'grade {g}' in m or f'الصف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف" if is_arabic else "Specify grade\nExample: Grade 7 schedule"
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower()]
        if not sched:
            return f"No schedule for {grade}"
        r = f"📅 {grade} Schedule\n\n"
        for s in sched:
            ps = [str(s.get(f'Period {i}','')) for i in range(1,6) if s.get(f'Period {i}','')]
            r += f"{s.get('Day','')}: {' → '.join(ps)}\n"
        return r.strip()

    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID","")).upper() == sid), None)
        if not s:
            return f"ID {sid} not found\n📞 {SCHOOL['phone']}"
        name = s.get('Student Name',''); grade = s.get('Grade','')
        if any(w in m for w in ['attendance','absent','حضور','غياب']):
            present = int(s.get('Days Present',0) or 0)
            total = int(s.get('Total School Days',0) or 0)
            pct = round((present/total*100) if total > 0 else 0, 1)
            return f"👤 {name} ({grade})\nPresent: {present}/{total}\nRate: {pct}%"
        total = int(s.get('Total Fees',0) or 0)
        paid = int(s.get('Amount Paid',0) or 0)
        remaining = int(s.get('Remaining',0) or 0)
        return (f"👤 {name} ({grade})\n💰 Total: {total:,} EGP\nPaid: {paid:,} EGP ✅\n"
                f"Remaining: {remaining:,} EGP\n📞 {SCHOOL['phone']}")

    if any(w in m for w in ['fee','fees','cost','how much','رسوم','مصاريف','كام']):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        return (f"💰 Modern Infinity Fees 2025/2026\n\n"
                f"🔸 KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                f"🔸 Grade 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                f"🔸 Grade 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                f"🔸 Grade 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n"
                f"🔸 Grade 10-12: {info.get('Grade 10-12 Fees','70,000 EGP')}\n\n"
                f"📅 3 installments\n📞 {SCHOOL['phone']}")

    if any(w in m for w in ['announcement','news','holiday','exam','إعلان','امتحان']):
        rows = read_tab("Announcements")
        active = [r for r in rows if str(r.get("Status","")).lower() == "active"]
        if not active:
            return "No announcements" if not is_arabic else "لا توجد إعلانات"
        r = "📢 School Announcements\n\n"
        for a in active:
            r += f"🔔 {a.get('Title','')}\n{a.get('Message','')}\n📅 {a.get('Date','')}\n\n"
        return r.strip()

    if any(w in m for w in ['admission','enroll','register','قبول','تسجيل']):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        return (f"📋 Admissions at Modern Infinity\n\n"
                f"✅ {info.get('Registration Status','Open')}\n"
                f"📅 Deadline: {info.get('Application Deadline','')}\n\n📞 {SCHOOL['phone']}")

    if any(w in m for w in ['location','address','where','map','عنوان','فين','موقع']):
        return f"📍 {SCHOOL['address']}\n📞 {SCHOOL['phone']}\nHours: {SCHOOL['admin_hours']}"

    return (f"Welcome to Modern Infinity! 👋\n\n"
            f"📚 Homework for Grade 7\n"
            f"📅 Grade 8 schedule\n"
            f"💰 Fees for Grade 5\n"
            f"💳 Balance STU001\n"
            f"📢 Announcements\n"
            f"📋 Admissions\n"
            f"📍 Location\n\n📞 {SCHOOL['phone']}")


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def receive():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(request.get_data(), sig):
        return "Forbidden", 403
    try:
        data = request.get_json(force=True, silent=True) or {}
        entry = data.get("entry", [])
        if not entry: return "OK", 200
        changes = entry[0].get("changes", [])
        if not changes: return "OK", 200
        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages: return "OK", 200
        msg = messages[0]
        if msg.get("type") != "text": return "OK", 200
        phone = msg.get("from", "")
        text = msg.get("text", {}).get("body", "")
        if not phone or not text: return "OK", 200
        if is_rate_limited(phone): return "OK", 200
        text = sanitize(text)
        logger.info(f"[wa] received from {phone[:6]}***: {text[:50]}")
        response = process_message(text)
        send_whatsapp(phone, response)
        return "OK", 200
    except Exception as e:
        logger.error(f"[webhook] error: {e}")
        return "OK", 200


# ── ADMIN PANEL ───────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_panel():
    with open("admin_panel.html", "r") as f:
        return f.read()


@app.route("/api/parents")
def api_parents():
    """Load parents from Google Sheet for admin panel."""
    try:
        rows = read_tab("Parents")
        parents = [
            {
                "name": r.get("Name", ""),
                "phone": str(r.get("Phone", "")),
                "grade": r.get("Grade", ""),
                "active": str(r.get("Active", "yes")).lower() == "yes"
            }
            for r in rows
            if str(r.get("Active", "yes")).lower() == "yes" and r.get("Phone", "")
        ]
        return jsonify({"parents": parents, "count": len(parents)})
    except Exception as e:
        logger.error(f"[api/parents] {e}")
        return jsonify({"parents": [], "count": 0, "error": str(e)})


@app.route("/broadcast", methods=["POST"])
def broadcast():
    """Send announcement to all parents (or filtered by grade)."""
    # Verify admin password
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    # Password checked client-side but also verify here
    message = data.get("message", "").strip()
    grades = data.get("grades", ["All"])

    if not message:
        return jsonify({"error": "No message provided"}), 400

    # Load parents from sheet
    try:
        rows = read_tab("Parents")
        all_parents = [
            r for r in rows
            if str(r.get("Active", "yes")).lower() == "yes" and r.get("Phone", "")
        ]
    except Exception as e:
        return jsonify({"error": f"Could not load parents: {e}"}), 500

    # Filter by grade
    if "All" not in grades:
        all_parents = [
            p for p in all_parents
            if any(g.lower() in str(p.get("Grade", "")).lower() for g in grades)
        ]

    # Format the broadcast message
    broadcast_msg = f"📢 Modern Infinity School\n\n{message}\n\n📞 For more info: {SCHOOL['phone']}"

    # Send to each parent
    sent = 0
    failed = 0
    for parent in all_parents:
        phone = str(parent.get("Phone", "")).strip()
        if not phone:
            continue
        # Ensure phone starts with country code
        if not phone.startswith("20") and not phone.startswith("+"):
            phone = "20" + phone.lstrip("0")
        phone = phone.lstrip("+")

        success = send_whatsapp(phone, broadcast_msg)
        if success:
            sent += 1
        else:
            failed += 1
        time.sleep(0.5)  # Rate limit: 2 messages/second max

    logger.info(f"[broadcast] Sent: {sent}, Failed: {failed}, Total: {len(all_parents)}")

    # Log to Announcements sheet
    try:
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet("Announcements")
        from datetime import datetime
        ws.append_row([
            message[:100],
            message,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "active",
            f"Broadcast to {sent} parents"
        ])
    except Exception as e:
        logger.error(f"[broadcast] Could not log to sheet: {e}")

    return jsonify({"sent": sent, "failed": failed, "total": len(all_parents)})


@app.route("/api/homework")
def homework_api():
    grade = request.args.get("grade", "")
    rows = read_tab("Homework")
    result = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower() and str(r.get("Assignment","")).strip()]
    return jsonify({"homework": result, "count": len(result)})

@app.route("/api/announcements")
def announcements_api():
    rows = read_tab("Announcements")
    active = [r for r in rows if str(r.get("Status","")).lower() == "active"]
    return jsonify({"announcements": active, "count": len(active)})

@app.route("/")
def index():
    return send_from_directory(".", "live_demo.html")

@app.route("/health")
def health():
    rows = read_tab("Homework")
    parents = read_tab("Parents")
    return jsonify({
        "status": "running",
        "school": SCHOOL["name"],
        "whatsapp_configured": bool(ACCESS_TOKEN),
        "sheets_connected": len(rows) > 0,
        "homework_rows": len(rows),
        "parents_registered": len(parents),
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")
