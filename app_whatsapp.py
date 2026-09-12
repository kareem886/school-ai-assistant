"""
School AI Assistant - Modern Infinity Language School
Credentials loaded via GOOGLE_CREDENTIALS_B64 (base64) -- fixes Railway PEM corruption bug.
"""
import os, json, re, base64, requests
from flask import Flask, request, jsonify, send_from_directory
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SHEET_ID = os.environ.get("SHEET_ID", "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1358537447338280")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "schoolai2026")
META_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

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


def _load_creds_info():
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "").strip()
    if b64:
        print("[creds] loading from GOOGLE_CREDENTIALS_B64")
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    raw = os.environ.get("GOOGLE_CREDENTIALS", "").strip()
    if raw:
        print("[creds] loading from GOOGLE_CREDENTIALS (repairing newlines)")
        info = json.loads(raw)
        if isinstance(info.get("private_key"), str):
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info
    if os.path.exists("credentials.json"):
        print("[creds] loading from credentials.json")
        with open("credentials.json") as f:
            return json.load(f)
    raise RuntimeError("No Google credentials found. Set GOOGLE_CREDENTIALS_B64.")


def get_client():
    info = _load_creds_info()
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    print(f"[creds] authorized as {info.get('client_email')}")
    return gspread.authorize(creds)


def read_tab(tab_name):
    try:
        client = get_client()
        wb = client.open_by_key(SHEET_ID)
        rows = wb.worksheet(tab_name).get_all_records()
        print(f"[sheets] OK {tab_name}: {len(rows)} rows")
        return rows
    except Exception as e:
        print(f"[sheets] FAIL {tab_name}: {e}")
        return []


def send_whatsapp(to_phone, message):
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"body": message},
    }
    try:
        res = requests.post(META_API_URL, headers=headers, json=payload)
        print(f"[wa] sent to {to_phone}: {res.status_code}")
    except Exception as e:
        print(f"[wa] error: {e}")


def process_message(msg):
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    if any(w in m for w in ['homework', 'assignment', 'hw']):
        grade = None
        for g in ['12', '11', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1']:
            if f'grade {g}' in m:
                grade = f'Grade {g}'
                break
        if not grade:
            return "Specify grade\nExample: Homework for Grade 7"
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in str(r.get("Grade", "")).lower() and str(r.get("Assignment", "")).strip()]
        if not hw:
            return f"No homework for {grade}\n{SCHOOL['phone']}"
        r = f"Homework {grade}\n\n"
        for h in hw:
            r += f"- {h.get('Subject', '')}: {h.get('Assignment', '')} Due: {h.get('Due Date', '')}\n"
        return r.strip()

    if any(w in m for w in ['schedule', 'timetable']):
        grade = None
        for g in ['12', '11', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1']:
            if f'grade {g}' in m:
                grade = f'Grade {g}'
                break
        if not grade:
            return "Specify grade\nExample: Grade 7 schedule"
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in str(r.get("Grade", "")).lower()]
        if not sched:
            return f"No schedule for {grade}"
        r = f"Schedule {grade}\n\n"
        for s in sched:
            ps = [str(s.get(f'Period {i}', '')) for i in range(1, 6) if s.get(f'Period {i}', '')]
            r += f"{s.get('Day', '')}: {' / '.join(ps)}\n"
        return r.strip()

    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID", "")).upper() == sid), None)
        if not s:
            return f"ID {sid} not found\n{SCHOOL['phone']}"
        name = s.get('Student Name', '')
        grade = s.get('Grade', '')
        if any(w in m for w in ['attendance', 'absent']):
            present = int(s.get('Days Present', 0) or 0)
            total = int(s.get('Total School Days', 0) or 0)
            pct = round((present / total * 100) if total > 0 else 0, 1)
            return f"{name} ({grade})\nPresent: {present}/{total}\nRate: {pct}%"
        total = int(s.get('Total Fees', 0) or 0)
        paid = int(s.get('Amount Paid', 0) or 0)
        remaining = int(s.get('Remaining', 0) or 0)
        return f"{name} ({grade})\nTotal: {total:,} EGP\nPaid: {paid:,} EGP\nRemaining: {remaining:,} EGP\n{SCHOOL['phone']}"

    if any(w in m for w in ['fee', 'fees', 'cost', 'how much']):
        rows = read_tab("Admissions")
        info = {r.get("Item", ""): r.get("Value", "") for r in rows}
        return (f"Modern Infinity Fees 2025/2026\n\n"
                f"KG: {info.get('KG1 Fees', '42,000 EGP')}\n"
                f"Grade 1-3: {info.get('Grade 1-3 Fees', '48,000 EGP')}\n"
                f"Grade 4-6: {info.get('Grade 4-6 Fees', '55,000 EGP')}\n"
                f"Grade 7-9: {info.get('Grade 7-9 Fees', '62,000 EGP')}\n"
                f"Grade 10-12: {info.get('Grade 10-12 Fees', '70,000 EGP')}\n\n{SCHOOL['phone']}")

    if any(w in m for w in ['announcement', 'news', 'holiday', 'exam']):
        rows = read_tab("Announcements")
        active = [r for r in rows if str(r.get("Status", "")).lower() == "active"]
        if not active:
            return "No announcements"
        r = "School Announcements\n\n"
        for a in active:
            r += f"{a.get('Title', '')}\n{a.get('Message', '')}\n{a.get('Date', '')}\n\n"
        return r.strip()

    if any(w in m for w in ['admission', 'enroll', 'register']):
        rows = read_tab("Admissions")
        info = {r.get("Item", ""): r.get("Value", "") for r in rows}
        return (f"Admissions at Modern Infinity\n\n"
                f"{info.get('Registration Status', 'Open')}\n"
                f"Deadline: {info.get('Application Deadline', '')}\n\n{SCHOOL['phone']}")

    if any(w in m for w in ['location', 'address', 'where', 'map']):
        return f"{SCHOOL['address']}\n{SCHOOL['phone']}\nHours: {SCHOOL['admin_hours']}"

    return (f"Welcome to Modern Infinity!\n\n"
            f"Homework for Grade 7\n"
            f"Grade 8 schedule\n"
            f"Fees for Grade 5\n"
            f"Balance STU001\n"
            f"Announcements\n"
            f"Admissions\n"
            f"Location\n\n{SCHOOL['phone']}")


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
    try:
        data = request.json
        entry = data.get("entry", [])
        if not entry:
            return "OK", 200
        changes = entry[0].get("changes", [])
        if not changes:
            return "OK", 200
        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return "OK", 200
        msg = messages[0]
        if msg.get("type") != "text":
            return "OK", 200
        phone = msg.get("from", "")
        text = msg.get("text", {}).get("body", "")
        print(f"[wa] received from {phone}: {text}")
        if not text or not phone:
            return "OK", 200
        response = process_message(text)
        send_whatsapp(phone, response)
        return "OK", 200
    except Exception as e:
        print(f"[webhook] error: {e}")
        return "OK", 200


@app.route("/api/homework")
def homework_api():
    grade = request.args.get("grade", "")
    rows = read_tab("Homework")
    result = [r for r in rows if grade.lower() in str(r.get("Grade", "")).lower() and str(r.get("Assignment", "")).strip()]
    return jsonify({"homework": result, "count": len(result)})


@app.route("/api/announcements")
def announcements_api():
    rows = read_tab("Announcements")
    active = [r for r in rows if str(r.get("Status", "")).lower() == "active"]
    return jsonify({"announcements": active, "count": len(active)})


@app.route("/")
def index():
    return send_from_directory(".", "live_demo.html")


@app.route("/health")
def health():
    rows = read_tab("Homework")
    return jsonify({
        "status": "running",
        "school": SCHOOL["name"],
        "whatsapp_configured": bool(ACCESS_TOKEN),
        "sheets_connected": len(rows) > 0,
        "homework_rows": len(rows),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")
