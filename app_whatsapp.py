"""
School AI Assistant - Modern Infinity Language School
Includes: WhatsApp webhook + Admin Panel + Broadcast announcements
"""
import os, json, re, base64, requests, logging, time, hmac, hashlib
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

# ── Config ─────────────────────────────────────────────────────────────────────────────
SHEET_ID        = os.environ.get("SHEET_ID", "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1358537447338280")
ACCESS_TOKEN    = os.environ.get("ACCESS_TOKEN", "")
VERIFY_TOKEN    = os.environ.get("VERIFY_TOKEN", "schoolai2026")
APP_SECRET      = os.environ.get("APP_SECRET", "")
# ── Admin accounts ─────────────────────────────────────────────────────────────────────
ADMINS = {
    "admin":        {"password": "moderninfinity2026", "label": "Super Admin",   "grades": []},
    "admin_junior": {"password": "junior2026",         "label": "Junior Admin",  "grades": ["KG1","KG2","Grade 1","Grade 2","Grade 3","Grade 4","Grade 5","Grade 6"]},
    "admin_senior": {"password": "senior2026",         "label": "Senior Admin",  "grades": ["Grade 7","Grade 8","Grade 9","Grade 10","Grade 11","Grade 12"]},
}
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

# ── Rate limiting ─────────────────────────────────────────────────────────────────────────────
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

def verify_webhook_signature(payload, sig_header):
    if not APP_SECRET:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)

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
    """Read a sheet tab safely — tolerates duplicate/empty headers."""
    try:
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet(tab_name)
        values = ws.get_all_values()
        if not values:
            return []
        # Build unique headers — if duplicate, append _2, _3 etc.
        raw_headers = values[0]
        seen = {}
        headers = []
        for h in raw_headers:
            h = h.strip()
            if not h:
                h = "_blank"
            if h in seen:
                seen[h] += 1
                h = f"{h}_{seen[h]}"
            else:
                seen[h] = 1
            headers.append(h)
        rows = []
        for row in values[1:]:
            # Skip completely empty rows
            if not any(str(v).strip() for v in row):
                continue
            d = {}
            for i, h in enumerate(headers):
                d[h] = row[i] if i < len(row) else ""
            rows.append(d)
        logger.info(f"[sheets] OK {tab_name}: {len(rows)} rows")
        return rows
    except Exception as e:
        logger.error(f"[sheets] FAIL {tab_name}: {e}")
        return []

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


def send_whatsapp_image(to_phone, media_id, caption=""):
    """Send an image via WhatsApp using a WhatsApp media_id."""
    if not ACCESS_TOKEN:
        return False
    hdrs = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "image",
        "image": {
            "id": media_id,
            "caption": caption[:1024] if caption else ""
        },
    }
    try:
        res = requests.post(META_API_URL, headers=hdrs, json=payload, timeout=10)
        logger.info(f"[wa] image sent to {to_phone[:6]}***: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        logger.error(f"[wa] image error: {e}")
        return False

# ── Arabic support ──────────────────────────────────────────────────────────────────────
def sanitize(text, max_len=1000):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_len]

ARABIC_GRADE_MAP = {
    '\u0627\u0644\u0623\u0648\u0644': '1', '\u0627\u0644\u0627\u0648\u0644': '1', '\u0627\u0648\u0644': '1', '\u0623\u0648\u0644': '1', '\u0648\u0627\u062d\u062f': '1',
    '\u0627\u0644\u062b\u0627\u0646\u064a': '2', '\u0627\u0644\u062b\u0627\u0646\u0649': '2', '\u0627\u062a\u0646\u064a\u0646': '2', '\u062b\u0627\u0646\u064a': '2', '\u062b\u0627\u0646\u0649': '2',
    '\u0627\u0644\u062b\u0627\u0644\u062b': '3', '\u062a\u0644\u0627\u062a\u0629': '3', '\u062a\u0644\u0627\u062a\u0647': '3', '\u062b\u0627\u0644\u062b': '3',
    '\u0627\u0644\u0631\u0627\u0628\u0639': '4', '\u0627\u0631\u0628\u0639\u0629': '4', '\u0623\u0631\u0628\u0639\u0629': '4', '\u0631\u0627\u0628\u0639': '4',
    '\u0627\u0644\u062e\u0627\u0645\u0633': '5', '\u062e\u0645\u0633\u0629': '5', '\u062e\u0627\u0645\u0633': '5',
    '\u0627\u0644\u0633\u0627\u062f\u0633': '6', '\u0633\u062a\u0629': '6', '\u0633\u0627\u062f\u0633': '6',
    '\u0627\u0644\u0633\u0627\u0628\u0639': '7', '\u0633\u0628\u0639\u0629': '7', '\u0633\u0627\u0628\u0639': '7',
    '\u0627\u0644\u062b\u0627\u0645\u0646': '8', '\u062a\u0645\u0627\u0646\u064a\u0629': '8', '\u062b\u0627\u0645\u0646': '8',
    '\u0627\u0644\u062a\u0627\u0633\u0639': '9', '\u062a\u0633\u0639\u0629': '9', '\u062a\u0627\u0633\u0639': '9',
    '\u0627\u0644\u0639\u0627\u0634\u0631': '10', '\u0639\u0627\u0634\u0631': '10',
    '\u0627\u0644\u062d\u0627\u062f\u064a \u0639\u0634\u0631': '11', '\u062d\u0627\u062f\u064a \u0639\u0634\u0631': '11',
    '\u0627\u0644\u062b\u0627\u0646\u064a \u0639\u0634\u0631': '12', '\u062b\u0627\u0646\u064a \u0639\u0634\u0631': '12',
}

def extract_grade(m):
    for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
        if f'grade {g}' in m or f'grade{g}' in m:
            return f'Grade {g}'
        if f'\u0627\u0644\u0635\u0641 {g}' in m or f'\u0635\u0641 {g}' in m or f'\u0635\u0641{g}' in m:
            return f'Grade {g}'
    for word, num in ARABIC_GRADE_MAP.items():
        if word in m:
            return f'Grade {num}'
    return None

def get_parent_students(from_phone):
    """Return list of student IDs linked to this parent phone (reads Students tab)."""
    try:
        rows = read_tab("Students")
        # Normalise: strip +, spaces, leading zeros for comparison
        norm = from_phone.lstrip('+').strip()
        matched = []
        for r in rows:
            p = str(r.get("Parent Phone", "")).lstrip('+').strip()
            if not p:
                continue
            # Match if identical OR one is the local version of the other
            if p == norm or p.lstrip('0') == norm.lstrip('0'):
                sid = str(r.get("Student ID", "")).strip().upper()
                if sid:
                    matched.append(sid)
                    logger.info(f"[auth] matched {sid} for phone {norm}")
        logger.info(f"[auth] from_phone={norm} matched={matched}")
        return matched
    except Exception as e:
        logger.error(f"[get_parent_students] {e}")
        return []


def process_message(msg, from_phone=""):
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    # Greet
    greet_kw = ['\u0645\u0631\u062d\u0628\u0627','\u0627\u0647\u0644\u0627','\u0623\u0647\u0644\u0627','\u0647\u0644\u0627','\u0627\u0644\u0633\u0644\u0627\u0645','\u0635\u0628\u0627\u062d','\u0645\u0633\u0627\u0621','hi','hello','hey','\u0633\u0644\u0627\u0645']
    if any(w in m for w in greet_kw):
        if is_arabic:
            return (f"\u0623\u0647\u0644\u0627\u064b \u0648\u0633\u0647\u0644\u0627\u064b \u0641\u064a \u0645\u062f\u0631\u0633\u0629 Modern Infinity 👋\n\n"
                    f"\u0627\u062e\u062a\u0631 \u0645\u0646 \u0627\u0644\u062e\u062f\u0645\u0627\u062a \u0627\u0644\u062a\u0627\u0644\u064a\u0629:\n\n"
                    f"📚 *\u0627\u0644\u0648\u0627\u062c\u0628\u0627\u062a* \u2014 \u0645\u062b\u0627\u0644: \u0648\u0627\u062c\u0628 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u0628\u0639\n"
                    f"📅 *\u0627\u0644\u062c\u062f\u0648\u0644 \u0627\u0644\u062f\u0631\u0627\u0633\u064a* \u2014 \u0645\u062b\u0627\u0644: \u062c\u062f\u0648\u0644 \u0627\u0644\u0635\u0641 \u0627\u0644\u062e\u0627\u0645\u0633\n"
                    f"💰 *\u0627\u0644\u0631\u0633\u0648\u0645 \u0627\u0644\u062f\u0631\u0627\u0633\u064a\u0629*\n"
                    f"💳 *\u0631\u0635\u064a\u062f \u0627\u0644\u0637\u0627\u0644\u0628* \u2014 \u0645\u062b\u0627\u0644: STU001\n"
                    f"📋 *\u0627\u0644\u062d\u0636\u0648\u0631 \u0648\u0627\u0644\u063a\u064a\u0627\u0628* \u2014 \u0645\u062b\u0627\u0644: STU001 \u062d\u0636\u0648\u0631\n"
                    f"📝 *\u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a* \u2014 \u0645\u062b\u0627\u0644: STU001 \u0646\u062a\u0627\u0626\u062c\n"
                    f"📢 *\u0627\u0644\u0625\u0639\u0644\u0627\u0646\u0627\u062a*\n"
                    f"📋 *\u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0648\u0627\u0644\u0642\u0628\u0648\u0644*\n"
                    f"🚌 *\u0627\u0644\u0645\u0648\u0627\u0635\u0644\u0627\u062a*\n"
                    f"📍 *\u0645\u0648\u0642\u0639 \u0627\u0644\u0645\u062f\u0631\u0633\u0629*\n\n"
                    f"📞 {SCHOOL['phone']}")
        return (f"Welcome to Modern Infinity Language School! 👋\n\n"
                f"📚 *Homework* \u2014 e.g. Homework for Grade 7\n"
                f"📅 *Schedule* \u2014 e.g. Grade 5 schedule\n"
                f"💰 *Fees*\n"
                f"💳 *Balance* \u2014 e.g. STU001\n"
                f"📋 *Attendance* \u2014 e.g. STU001 attendance\n"
                f"📝 *Exam Results* \u2014 e.g. STU001 results\n"
                f"📢 *Announcements*\n"
                f"📋 *Admissions*\n"
                f"🚌 *Bus Routes*\n"
                f"📍 *Location*\n\n"
                f"📞 {SCHOOL['phone']}")

    # Homework
    hw_kw = ['homework','assignment','hw','\u0648\u0627\u062c\u0628','\u062a\u0643\u0644\u064a\u0641','\u0648\u0627\u062c\u0628\u0627\u062a','\u0627\u0644\u0648\u0627\u062c\u0628','\u0627\u0644\u0648\u0627\u062c\u0628\u0627\u062a','\u062a\u0643\u0644\u064a\u0641\u0627\u062a']
    if any(w in m for w in hw_kw):
        grade = extract_grade(m)
        if not grade:
            return ("📚 \u062d\u062f\u062f \u0627\u0644\u0635\u0641 \u0645\u0646 \u0641\u0636\u0644\u0643\n\u0645\u062b\u0627\u0644: *\u0648\u0627\u062c\u0628 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u0628\u0639*") if is_arabic else                    ("📚 Please specify the grade\nExample: *Homework for Grade 7*")
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower() and str(r.get("Assignment","")).strip()]
        if not hw:
            return (f"📚 \u0644\u0627 \u064a\u0648\u062c\u062f \u0648\u0627\u062c\u0628 \u0644\u0640 {grade}\n📞 {SCHOOL['phone']}") if is_arabic else                    (f"📚 No homework for {grade}\n📞 {SCHOOL['phone']}")
        if is_arabic:
            r = f"📚 \u0648\u0627\u062c\u0628\u0627\u062a {grade}\n\n"
            for h in hw:
                r += f"\u2022 {h.get('Subject','')}: {h.get('Assignment','')}\n"
                if h.get('Due Date',''): r += f"  📅 \u0645\u0648\u0639\u062f: {h.get('Due Date','')}\n"
                if h.get('Teacher',''): r += f"  👤 {h.get('Teacher','')}\n"
                r += "\n"
        else:
            r = f"📚 {grade} Homework\n\n"
            for h in hw:
                r += f"\u2022 {h.get('Subject','')}: {h.get('Assignment','')}\n"
                if h.get('Due Date',''): r += f"  📅 Due: {h.get('Due Date','')}\n"
                if h.get('Teacher',''): r += f"  👤 {h.get('Teacher','')}\n"
                r += "\n"
        return r.strip()

    # Schedule
    sched_kw = ['schedule','timetable','\u062c\u062f\u0648\u0644','\u062d\u0635\u0635','\u0627\u0644\u062c\u062f\u0648\u0644','\u0627\u0644\u0645\u0648\u0627\u062f','\u062d\u0635\u0629']
    if any(w in m for w in sched_kw):
        grade = extract_grade(m)
        if not grade:
            return ("📅 \u062d\u062f\u062f \u0627\u0644\u0635\u0641\n\u0645\u062b\u0627\u0644: *\u062c\u062f\u0648\u0644 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u062f\u0633*") if is_arabic else                    ("📅 Please specify the grade\nExample: *Grade 6 schedule*")
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower()]
        if not sched:
            return (f"📅 \u0644\u0627 \u064a\u0648\u062c\u062f \u062c\u062f\u0648\u0644 \u0644\u0640 {grade}\n📞 {SCHOOL['phone']}") if is_arabic else                    (f"📅 No schedule for {grade}\n📞 {SCHOOL['phone']}")
        r = f"📅 \u062c\u062f\u0648\u0644 {grade}\n\n" if is_arabic else f"📅 {grade} Schedule\n\n"
        for s in sched:
            ps = [str(s.get(f'Period {i}','')) for i in range(1,6) if s.get(f'Period {i}','')]
            arrow = ' \u2192 '
            r += f"{s.get('Day','')}: {arrow.join(ps)}\n"
        return r.strip()

    # Student ID — balance, attendance, EXAM RESULTS
    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID","")).upper() == sid), None)
        if not s:
            return (f"\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0627\u0644\u0637\u0627\u0644\u0628 {sid}\n📞 {SCHOOL['phone']}") if is_arabic else                    (f"\u274c Student {sid} not found\n📞 {SCHOOL['phone']}")
        name = s.get('Full Name', s.get('Student Name',''))
        grade = s.get('Grade','')

        # EXAM RESULTS — SECURED BY PARENT PHONE NUMBER + PAYMENT STATUS
        result_kw = ['result','results','exam','score','mark','marks','\u0646\u062a\u064a\u062c\u0629','\u0646\u062a\u0627\u0626\u062c','\u0627\u0645\u062a\u062d\u0627\u0646','\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a','\u062f\u0631\u062c\u0629','\u062f\u0631\u062c\u0627\u062a']
        if any(w in m for w in result_kw):
            authorized = get_parent_students(from_phone)
            if sid not in authorized:
                return (f"🔒 \u0639\u0630\u0631\u0627\u064b\u060c \u064a\u0645\u0643\u0646\u0643 \u0641\u0642\u0637 \u0627\u0644\u0627\u0637\u0644\u0627\u0639 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c \u0623\u0628\u0646\u0627\u0626\u0643 \u0627\u0644\u0645\u0633\u062c\u0644\u064a\u0646 \u0628\u0631\u0642\u0645\u0643.\n"
                        f"📞 {SCHOOL['phone']}") if is_arabic else                        (f"🔒 Sorry, you can only access results for students registered under your phone number.\n"
                        f"📞 {SCHOOL['phone']}")
            # ── PAYMENT GATE ──────────────────────────────────────────────────
            payment_status = str(s.get("Payment Status", "")).strip().lower()
            if payment_status != "paid":
                return (f"📞 \u0644\u0644\u0627\u0633\u062a\u0641\u0633\u0627\u0631 \u0639\u0646 \u0646\u062a\u0627\u0626\u062c \u0637\u0641\u0644\u0643\u060c\n"
                        f"\u064a\u0631\u062c\u0649 \u0627\u0644\u062a\u0648\u0627\u0635\u0644 \u0645\u0639 \u0645\u0643\u062a\u0628 \u0627\u0644\u0645\u062f\u0631\u0633\u0629.\n\n"
                        f"📞 {SCHOOL['phone']}") if is_arabic else (
                        f"📞 To access your child's exam results, please contact the school office.\n\n"
                        f"📞 {SCHOOL['phone']}")
            result_rows = read_tab("exam")
            student_results = [r for r in result_rows if str(r.get("Student ID","")).upper() == sid]
            if not student_results:
                return (f"📝 \u0644\u0627 \u062a\u0648\u062c\u062f \u0646\u062a\u0627\u0626\u062c \u0644\u0640 {name} \u062d\u0627\u0644\u064a\u0627\u064b\n📞 {SCHOOL['phone']}") if is_arabic else                        (f"📝 No results found for {name} yet\n📞 {SCHOOL['phone']}")
            if is_arabic:
                r = f"📝 نتائج امتحانات {name} ({grade})\n\n"
                for res in student_results:
                    gl = res.get('Grade Letter', res.get('Grade',''))
                    r += f"\u2022 {res.get('Subject','')}: {res.get('Score','')}/100 ({res.get('Percentage','')}) — {gl}\n"
                    if res.get('Rank',''): r += f"  🏆 الترتيب: {res.get('Rank','')}\n"
            else:
                r = f"📝 Exam Results — {name} ({grade})\n\n"
                for res in student_results:
                    gl = res.get('Grade Letter', res.get('Grade',''))
                    r += f"\u2022 {res.get('Subject','')}: {res.get('Score','')}/100 ({res.get('Percentage','')}) — {gl}\n"
                    if res.get('Rank',''): r += f"  🏆 Rank: {res.get('Rank','')}\n"
            return r.strip()

        # Attendance
        if any(w in m for w in ['attendance','absent','\u062d\u0636\u0648\u0631','\u063a\u064a\u0627\u0628','\u0627\u0644\u063a\u064a\u0627\u0628','\u0627\u0644\u062d\u0636\u0648\u0631']):
            present = int(s.get('Days Present',0) or 0)
            total = int(s.get('Total School Days',0) or 0)
            pct = round((present/total*100) if total>0 else 0, 1)
            low_ar = " \u26a0\ufe0f \u0623\u0642\u0644 \u0645\u0646 75%!" if pct < 75 else ""
            low_en = " \u26a0\ufe0f Below 75%!" if pct < 75 else ""
            if is_arabic:
                return (f"👤 {name} ({grade})\n"
                        f"\u2705 \u062d\u0627\u0636\u0631: {present} \u064a\u0648\u0645\n"
                        f"📅 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a: {total} \u064a\u0648\u0645\n"
                        f"📊 \u0646\u0633\u0628\u0629 \u0627\u0644\u062d\u0636\u0648\u0631: {pct}%{low_ar}")
            return (f"👤 {name} ({grade})\n"
                    f"\u2705 Present: {present} days\n"
                    f"📅 Total: {total} days\n"
                    f"📊 Rate: {pct}%{low_en}")

        # Fee Balance
        total_fees = int(s.get('Total Fees',0) or 0)
        paid = int(s.get('Amount Paid',0) or 0)
        remaining = int(s.get('Remaining',0) or 0)
        next_due = s.get('Next Due','')
        status = s.get('Payment Status','')
        status_emoji = "\u2705" if "paid" in str(status).lower() else ("\u26a0\ufe0f" if "overdue" in str(status).lower() else "\u23f3")
        if is_arabic:
            r = (f"👤 {name} ({grade})\n"
                 f"💰 \u0625\u062c\u0645\u0627\u0644\u064a \u0627\u0644\u0631\u0633\u0648\u0645: {total_fees:,} \u062c\u0646\u064a\u0647\n"
                 f"\u2705 \u0627\u0644\u0645\u062f\u0641\u0648\u0639: {paid:,} \u062c\u0646\u064a\u0647\n"
                 f"\u23f3 \u0627\u0644\u0645\u062a\u0628\u0642\u064a: {remaining:,} \u062c\u0646\u064a\u0647\n")
            if status: r += f"{status_emoji} \u0627\u0644\u062d\u0627\u0644\u0629: {status}\n"
            if next_due: r += f"📅 \u0627\u0644\u062f\u0641\u0639\u0629 \u0627\u0644\u0642\u0627\u062f\u0645\u0629: {next_due}\n"
            r += f"📞 {SCHOOL['phone']}"
        else:
            r = (f"👤 {name} ({grade})\n"
                 f"💰 Total: {total_fees:,} EGP\n"
                 f"\u2705 Paid: {paid:,} EGP\n"
                 f"\u23f3 Remaining: {remaining:,} EGP\n")
            if status: r += f"{status_emoji} Status: {status}\n"
            if next_due: r += f"📅 Next due: {next_due}\n"
            r += f"📞 {SCHOOL['phone']}"
        return r

    # Fees
    fees_kw = ['fee','fees','cost','how much','price','\u0631\u0633\u0648\u0645','\u0645\u0635\u0627\u0631\u064a\u0641','\u0643\u0627\u0645','\u0633\u0639\u0631','\u062a\u0643\u0644\u0641\u0629','\u0627\u0644\u0631\u0633\u0648\u0645','\u0627\u0644\u0645\u0635\u0627\u0631\u064a\u0641','\u0628\u0643\u0627\u0645','\u0628\u0642\u062f \u0627\u064a\u0647','\u0642\u062f\u064a\u0647','\u0642\u062f \u0627\u064a\u0647']
    if any(w in m for w in fees_kw):
        if is_arabic:
            return (
                    "💳 حالة سداد الرسوم\n\n"
                    "للاستفسار عن حالة سداد رسوم طفلك،\n"
                    "أرسل رقم الطالب. مثال: STU001\n\n"
                    f"📞 {SCHOOL['phone']}")
        return (
                "💳 Fees Status\n\n"
                "To check your child's fees status, please send their Student ID.\n"
                "Example: STU001\n\n"
                f"📞 {SCHOOL['phone']}")

    # Bus Routes
    bus_kw = ['bus','route','transport','pickup','\u0645\u0648\u0627\u0635\u0644\u0627\u062a','\u0628\u0627\u0635','\u0627\u0648\u062a\u0648\u0628\u064a\u0633','\u0646\u0642\u0644','\u062a\u0648\u0635\u064a\u0644','\u0645\u064a\u0639\u0627\u062f \u0627\u0644\u0628\u0627\u0635']
    if any(w in m for w in bus_kw):
        rows = read_tab("BusRoutes")
        if not rows:
            return (f"🚌 \u0644\u0644\u0627\u0633\u062a\u0641\u0633\u0627\u0631 \u0639\u0646 \u0627\u0644\u0645\u0648\u0627\u0635\u0644\u0627\u062a:\n📞 {SCHOOL['phone']}") if is_arabic else                    (f"🚌 For bus route enquiries:\n📞 {SCHOOL['phone']}")
        area_match = next((r for r in rows if str(r.get("Area","")).lower() in m), None)
        if area_match:
            if is_arabic:
                return (f"🚌 \u062e\u0637 {area_match.get('Route','')} \u2014 {area_match.get('Area','')}\n"
                        f"\u23f0 \u0645\u064a\u0639\u0627\u062f \u0627\u0644\u0631\u0643\u0648\u0628: {area_match.get('Pickup Time','')}\n"
                        f"\U0001f3eb \u0645\u064a\u0639\u0627\u062f \u0627\u0644\u0625\u0631\u062c\u0627\u0639: {area_match.get('Drop-off Time','')}\n"
                        f"📞 \u0627\u0644\u0633\u0627\u0626\u0642: {area_match.get('Driver Contact','')}")
            return (f"🚌 Route {area_match.get('Route','')} \u2014 {area_match.get('Area','')}\n"
                    f"\u23f0 Pickup: {area_match.get('Pickup Time','')}\n"
                    f"\U0001f3eb Drop-off: {area_match.get('Drop-off Time','')}\n"
                    f"📞 Driver: {area_match.get('Driver Contact','')}")
        r = "🚌 \u062e\u0637\u0648\u0637 \u0627\u0644\u0645\u0648\u0627\u0635\u0644\u0627\u062a:\n\n" if is_arabic else "🚌 Available Bus Routes:\n\n"
        for row in rows:
            r += f"\u2022 {row.get('Route','')} \u2014 {row.get('Area','')} ({row.get('Pickup Time','')})\n"
        r += f"\n📞 {SCHOOL['phone']}"
        return r.strip()

    # Canteen
    canteen_kw = ['canteen','cafeteria','menu','food','lunch','\u0643\u0627\u0641\u064a\u062a\u064a\u0631\u064a\u0627','\u0643\u0627\u0646\u062a\u064a\u0646','\u0627\u0643\u0644','\u0623\u0643\u0644','\u0645\u0646\u064a\u0648','\u0627\u0644\u063a\u062f\u0627\u0621','\u0648\u062c\u0628\u0629']
    if any(w in m for w in canteen_kw):
        rows = read_tab("Canteen")
        if not rows:
            return (f"🍽\ufe0f \u0644\u0644\u0627\u0633\u062a\u0641\u0633\u0627\u0631 \u0639\u0646 \u0627\u0644\u0643\u0627\u0641\u064a\u062a\u064a\u0631\u064a\u0627:\n📞 {SCHOOL['phone']}") if is_arabic else                    (f"🍽\ufe0f For canteen enquiries:\n📞 {SCHOOL['phone']}")
        r = "🍽\ufe0f \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0643\u0627\u0641\u064a\u062a\u064a\u0631\u064a\u0627 \u0627\u0644\u064a\u0648\u0645:\n\n" if is_arabic else "🍽\ufe0f Today's Canteen Menu:\n\n"
        for item in rows:
            r += f"\u2022 {item.get('Item','')} \u2014 {item.get('Price','')} EGP\n"
        return r.strip()

    # Library
    lib_kw = ['library','book','available','\u0645\u0643\u062a\u0628\u0629','\u0643\u062a\u0627\u0628','\u0643\u062a\u0628','\u0645\u062a\u0627\u062d']
    if any(w in m for w in lib_kw):
        rows = read_tab("Library")
        if not rows:
            return (f"📚 \u0644\u0644\u0627\u0633\u062a\u0641\u0633\u0627\u0631 \u0639\u0646 \u0627\u0644\u0645\u0643\u062a\u0628\u0629:\n📞 {SCHOOL['phone']}") if is_arabic else                    (f"📚 For library enquiries:\n📞 {SCHOOL['phone']}")
        available = [r for r in rows if "available" in str(r.get("Status","")).lower()]
        if not available:
            return "📚 \u0644\u0627 \u062a\u0648\u062c\u062f \u0643\u062a\u0628 \u0645\u062a\u0627\u062d\u0629 \u062d\u0627\u0644\u064a\u0627\u064b" if is_arabic else "📚 No books currently available"
        r = "📚 \u0627\u0644\u0643\u062a\u0628 \u0627\u0644\u0645\u062a\u0627\u062d\u0629:\n\n" if is_arabic else "📚 Available Books:\n\n"
        for b in available[:10]:
            r += f"\u2022 {b.get('Book Title','')} \u2014 {b.get('Author','')}\n"
        return r.strip()

    # Appointments
    appt_kw = ['appointment','meeting','teacher','\u0645\u0648\u0639\u062f','\u0645\u0642\u0627\u0628\u0644\u0629','\u0645\u062f\u0631\u0633','\u0623\u0633\u062a\u0627\u0630','\u0627\u0633\u062a\u0627\u0630']
    if any(w in m for w in appt_kw):
        return (f"📅 \u0644\u062d\u062c\u0632 \u0645\u0648\u0639\u062f \u0645\u0639 \u0627\u0644\u0645\u062f\u0631\u0633\u060c \u0623\u0631\u0633\u0644:\n\n"
                f"1. \u0627\u0633\u0645\u0643\n2. \u0627\u0633\u0645 \u0627\u0644\u0637\u0627\u0644\u0628\n3. \u0627\u0633\u0645 \u0627\u0644\u0645\u062f\u0631\u0633\n4. \u0627\u0644\u064a\u0648\u0645 \u0627\u0644\u0645\u0641\u0636\u0644\n\n"
                f"\u0633\u064a\u062a\u0645 \u062a\u0623\u0643\u064a\u062f \u0627\u0644\u0645\u0648\u0639\u062f \u0645\u0646 \u0642\u0628\u0644 \u0627\u0644\u0625\u062f\u0627\u0631\u0629.\n📞 {SCHOOL['phone']}") if is_arabic else                (f"📅 To book a parent-teacher meeting, send:\n\n"
                f"1. Your name\n2. Student name\n3. Teacher name\n4. Preferred day\n\n"
                f"Admin will confirm your appointment.\n📞 {SCHOOL['phone']}")

    # Admissions
    admit_kw = ['admission','enroll','register','apply','\u0642\u0628\u0648\u0644','\u062a\u0633\u062c\u064a\u0644','\u0627\u0644\u062a\u0633\u062c\u064a\u0644','\u0627\u0644\u0642\u0628\u0648\u0644','\u0627\u0633\u062c\u0644','\u062a\u0642\u062f\u064a\u0645','\u0627\u0644\u0627\u0644\u062a\u062d\u0627\u0642','\u0627\u0628\u0646\u064a','\u0628\u0646\u062a\u064a']
    if any(w in m for w in admit_kw):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        if is_arabic:
            return (f"📋 \u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0641\u064a Modern Infinity\n\n"
                    f"\u2705 \u0627\u0644\u062d\u0627\u0644\u0629: {info.get('Registration Status','مفتوح')}\n"
                    f"📅 \u0622\u062e\u0631 \u0645\u0648\u0639\u062f: {info.get('Application Deadline','')}\n\n"
                    f"📞 {SCHOOL['phone']}\n"
                    f"📍 {SCHOOL['address']}")
        return (f"📋 Admissions at Modern Infinity\n\n"
                f"\u2705 Status: {info.get('Registration Status','Open')}\n"
                f"📅 Deadline: {info.get('Application Deadline','')}\n\n"
                f"📞 {SCHOOL['phone']}\n"
                f"📍 {SCHOOL['address']}")

    # Location
    loc_kw = ['location','address','where','map','\u0639\u0646\u0648\u0627\u0646','\u0641\u064a\u0646','\u0645\u0648\u0642\u0639','\u0627\u0644\u0639\u0646\u0648\u0627\u0646','\u0627\u0644\u0645\u0648\u0642\u0639','\u0643\u064a\u0641 \u0627\u0648\u0635\u0644','\u0645\u0643\u0627\u0646\u0643\u0645']
    if any(w in m for w in loc_kw):
        if is_arabic:
            return (f"📍 *\u0639\u0646\u0648\u0627\u0646 \u0627\u0644\u0645\u062f\u0631\u0633\u0629*\n{SCHOOL['address']}\n\n"
                    f"📞 {SCHOOL['phone']}\n"
                    f"\u23f0 \u0645\u0648\u0627\u0639\u064a\u062f \u0627\u0644\u0639\u0645\u0644: {SCHOOL['admin_hours']}")
        return (f"📍 *School Location*\n{SCHOOL['address']}\n\n"
                f"📞 {SCHOOL['phone']}\n"
                f"\u23f0 Hours: {SCHOOL['admin_hours']}")

    # Default
    if is_arabic:
        return (f"\u0623\u0647\u0644\u0627\u064b! 👋 \u0643\u064a\u0641 \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643\u061f\n\n"
                f"📚 \u0627\u0644\u0648\u0627\u062c\u0628\u0627\u062a \u2014 \u0648\u0627\u062c\u0628 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u0628\u0639\n"
                f"📅 \u0627\u0644\u062c\u062f\u0648\u0644 \u2014 \u062c\u062f\u0648\u0644 \u0627\u0644\u0635\u0641 \u0627\u0644\u062e\u0627\u0645\u0633\n"
                f"💰 \u0627\u0644\u0631\u0633\u0648\u0645 \u0627\u0644\u062f\u0631\u0627\u0633\u064a\u0629\n"
                f"💳 \u0631\u0635\u064a\u062f \u0627\u0644\u0637\u0627\u0644\u0628 \u2014 STU001\n"
                f"📋 \u0627\u0644\u062d\u0636\u0648\u0631 \u2014 STU001 \u062d\u0636\u0648\u0631\n"
                f"📝 \u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a \u2014 STU001 \u0646\u062a\u0627\u0626\u062c\n"
                f"📢 \u0627\u0644\u0625\u0639\u0644\u0627\u0646\u0627\u062a\n"
                f"📋 \u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0648\u0627\u0644\u0642\u0628\u0648\u0644\n"
                f"🚌 \u0627\u0644\u0645\u0648\u0627\u0635\u0644\u0627\u062a\n"
                f"📍 \u0645\u0648\u0642\u0639 \u0627\u0644\u0645\u062f\u0631\u0633\u0629\n\n"
                f"📞 {SCHOOL['phone']}")
    return (f"Welcome to Modern Infinity! 👋\n\n"
            f"📚 Homework \u2014 Homework for Grade 7\n"
            f"📅 Schedule \u2014 Grade 5 schedule\n"
            f"💰 Fees\n"
            f"💳 Balance \u2014 STU001\n"
            f"📋 Attendance \u2014 STU001 attendance\n"
            f"📝 Exam Results \u2014 STU001 results\n"
            f"📢 Announcements\n"
            f"📋 Admissions\n"
            f"🚌 Bus Routes\n"
            f"📍 Location\n\n"
            f"📞 {SCHOOL['phone']}")

@app.route('/webhook', methods=['GET','POST'])
def webhook():
    if request.method == 'GET':
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if token == VERIFY_TOKEN:
            return challenge, 200
        return 'Forbidden', 403
    data = request.get_json(silent=True) or {}
    try:
        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])
        if not messages:
            return 'OK', 200
        msg_obj = messages[0]
        from_num = msg_obj.get('from', '')
        if msg_obj.get('type','') == 'text':
            body = msg_obj.get('text', {}).get('body', '')
            reply = process_message(body, from_phone=from_num)
            send_whatsapp(from_num, reply)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return 'OK', 200


@app.route('/admin')
def admin_panel():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Modern Infinity Admin Panel</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--g:#25D366;--g2:#128C7E;--n:#0F1C2E;--b:#e2e8f0;--r:#ef4444;--amber:#f59e0b;--blue:#3b82f6}
body{font-family:'Inter',sans-serif;background:#f4f6f8;color:#1a202c;min-height:100vh}

/* ── LOGIN ── */
.lw{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;background:linear-gradient(135deg,#0F1C2E 0%,#1a3a2a 100%)}
.lc{background:#fff;border-radius:24px;padding:48px 40px;width:100%;max-width:440px;box-shadow:0 20px 60px rgba(0,0,0,.25)}
.ll{text-align:center;margin-bottom:32px}
.li{width:72px;height:72px;border-radius:18px;background:var(--g);display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:32px}
.ll h1{font-size:24px;font-weight:700;color:var(--n)}
.ll p{font-size:14px;color:#64748b;margin-top:4px}
.role-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:28px}
.role-card{border:2px solid var(--b);border-radius:12px;padding:12px 8px;text-align:center;cursor:pointer;transition:all .2s}
.role-card:hover{border-color:var(--g);background:#f0fdf4}
.role-card.active{border-color:var(--g);background:#f0fdf4;box-shadow:0 0 0 3px rgba(37,211,102,.15)}
.role-card .ri{font-size:22px;margin-bottom:6px}
.role-card .rl{font-size:11px;font-weight:600;color:#374151}
.role-card .rs{font-size:10px;color:#64748b;margin-top:2px}
.fg{margin-bottom:18px}
.fg label{display:block;font-size:13px;font-weight:600;color:#374151;margin-bottom:8px}
.fg input{width:100%;padding:12px 16px;border:1.5px solid var(--b);border-radius:10px;font-size:15px;font-family:inherit;outline:none;transition:border-color .2s}
.fg input:focus{border-color:var(--g)}
.btn{width:100%;padding:14px;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;background:var(--g);color:#000;transition:all .2s}
.btn:hover{background:var(--g2);color:#fff}
.err{color:var(--r);font-size:13px;margin-top:12px;text-align:center;display:none;padding:10px;background:#fef2f2;border-radius:8px}

/* ── ADMIN BADGE on login ── */
.scope-hint{font-size:11px;color:#64748b;text-align:center;margin-bottom:16px;padding:8px 12px;background:#f8fafc;border-radius:8px;border:1px solid var(--b)}

/* ── APP WRAPPER ── */
.aw{display:none;min-height:100vh}

/* ── SIDEBAR ── */
.sb{width:270px;background:var(--n);position:fixed;top:0;left:0;height:100vh;padding:24px 18px;display:flex;flex-direction:column;z-index:100}
.sl{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.si{width:40px;height:40px;border-radius:10px;background:var(--g);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.st{color:#fff;font-size:14px;font-weight:600;line-height:1.3}
.st span{color:rgba(255,255,255,.5);font-size:11px;font-weight:400;display:block}

/* Admin role badge in sidebar */
.admin-badge{margin-bottom:24px;margin-top:4px;padding:8px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.1)}
.admin-badge .ab-label{font-size:11px;color:rgba(255,255,255,.5);font-weight:500;margin-bottom:3px}
.admin-badge .ab-name{font-size:13px;font-weight:700;color:#fff}
.admin-badge .ab-scope{font-size:11px;margin-top:4px;padding:3px 8px;border-radius:20px;display:inline-block;font-weight:600}
.scope-super{background:rgba(37,211,102,.2);color:#4ade80}
.scope-junior{background:rgba(59,130,246,.2);color:#93c5fd}
.scope-senior{background:rgba(245,158,11,.2);color:#fcd34d}

.ni{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:10px;color:rgba(255,255,255,.6);font-size:14px;font-weight:500;cursor:pointer;margin-bottom:4px;transition:all .15s;user-select:none}
.ni:hover,.ni.active{background:rgba(255,255,255,.10);color:#fff}
.ni.active{background:var(--g);color:#000}
.sb-bot{margin-top:auto}
.lo{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:10px;color:rgba(255,255,255,.5);font-size:14px;cursor:pointer;transition:all .15s}
.lo:hover{color:#fff;background:rgba(255,255,255,.08)}

/* ── MAIN ── */
.mn{margin-left:270px;padding:32px}
.tb2{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;flex-wrap:wrap;gap:12px}
.pg-t{font-size:24px;font-weight:700;color:var(--n)}
.pg-s{font-size:14px;color:#64748b;margin-top:2px}
.top-right{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.spl{display:flex;align-items:center;gap:6px;background:#dcfce7;color:#15803d;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:500}
.spd{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:pu 2s infinite}
@keyframes pu{0%,100%{opacity:1}50%{opacity:.4}}

/* scope tag in header */
.scope-tag{padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600}

/* ── STATS ── */
.sr{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:28px}
.sc{background:#fff;border-radius:16px;padding:24px;border:1px solid var(--b)}
.sl2{font-size:13px;color:#64748b;font-weight:500;margin-bottom:8px}
.sv{font-size:32px;font-weight:700;color:var(--n)}

/* ── SECTIONS ── */
.sec{background:#fff;border-radius:16px;padding:28px;border:1px solid var(--b);margin-bottom:24px}
.sec-t{font-size:16px;font-weight:700;color:var(--n);margin-bottom:20px;display:flex;align-items:center;gap:8px}
.fl{font-size:13px;font-weight:600;color:#374151;margin-bottom:8px;display:block}
textarea{width:100%;padding:14px 16px;border:1.5px solid var(--b);border-radius:10px;font-size:14px;font-family:inherit;outline:none;resize:vertical;min-height:120px;transition:border-color .2s}
textarea:focus{border-color:var(--g)}
.photo-zone{border:2px dashed var(--b);border-radius:10px;padding:18px;text-align:center;cursor:pointer;transition:.2s;margin:10px 0;position:relative;}
.photo-zone:hover{border-color:var(--g);background:#f0fdf4;}
.photo-zone.has-img{border-color:var(--g);background:#f0fdf4;}
.photo-preview{max-width:100%;max-height:200px;border-radius:8px;margin-top:10px;display:none;}
.remove-photo{position:absolute;top:8px;right:8px;background:#ef4444;color:#fff;border:none;border-radius:50%;width:24px;height:24px;cursor:pointer;font-size:14px;line-height:1;display:none;}
.cc{font-size:12px;color:#94a3b8;text-align:right;margin-top:4px}

/* ── GRADE GRID ── */
.gg{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:8px}
.gc{padding:10px;border:2px solid var(--b);border-radius:10px;text-align:center;cursor:pointer;font-size:13px;font-weight:500;color:#64748b;transition:all .15s;user-select:none}
.gc:hover:not(.locked){border-color:var(--g);color:var(--g)}
.gc.sel{border-color:var(--g);background:#f0fdf4;color:#15803d;font-weight:600}
.gc.all{grid-column:1/-1;background:var(--n);border-color:var(--n);color:#fff}
.gc.all.sel{background:var(--g);border-color:var(--g);color:#000}
.gc.locked{opacity:.35;cursor:not-allowed;background:#f8fafc}

/* ── PREVIEW ── */
.pb{background:#e5ddd5;border-radius:12px;padding:16px;margin-top:16px}
.pb-l{font-size:11px;color:#64748b;margin-bottom:10px}
.pb-b{background:#fff;border-radius:8px 8px 8px 2px;padding:10px 14px;display:inline-block;max-width:85%;font-size:14px;line-height:1.55;white-space:pre-wrap}
.pb-t{font-size:11px;color:rgba(0,0,0,.4);text-align:right;margin-top:4px}
.sr2{display:flex;align-items:center;gap:16px;margin-top:24px;flex-wrap:wrap}
.snd{display:flex;align-items:center;gap:8px;padding:14px 28px;background:var(--g);color:#000;border:none;border-radius:12px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .2s}
.snd:hover{background:var(--g2);color:#fff}
.snd:disabled{background:#94a3b8;color:#fff;cursor:not-allowed}
.rc{font-size:14px;color:#64748b}
.rc strong{color:var(--n)}

/* ── HISTORY ── */
.hi{display:flex;align-items:flex-start;gap:16px;padding:16px 0;border-bottom:1px solid var(--b)}
.hi:last-child{border-bottom:none}
.hic{flex:1;font-size:14px;line-height:1.5}
.hm{font-size:12px;color:#94a3b8;margin-top:4px}
.hb{font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;background:#dcfce7;color:#15803d}

/* ── MODAL ── */
.mo{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center;z-index:1000;padding:20px}
.mo.show{display:flex}
.md{background:#fff;border-radius:20px;padding:40px;max-width:440px;width:100%;text-align:center}
.mb2{padding:12px 32px;background:var(--g);border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;color:#000}

/* ── TABLE ── */
.ptable{width:100%;border-collapse:collapse;font-size:14px}
.ptable th{text-align:left;padding:10px 8px;border-bottom:2px solid var(--b);font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
.ptable td{padding:11px 8px;border-bottom:1px solid #f1f5f9}
.ptable tr:last-child td{border-bottom:none}
.grade-pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:600}
.g-junior{background:#dbeafe;color:#1d4ed8}
.g-senior{background:#fef3c7;color:#92400e}
.g-kg{background:#f3e8ff;color:#7e22ce}

@media(max-width:768px){.sb{display:none}.mn{margin-left:0;padding:16px}.sr{grid-template-columns:1fr}.gg{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>

<!-- ════════════════════════════════════════════════════ LOGIN ══ -->
<div class="lw" id="lw">
  <div class="lc">
    <div class="ll">
      <div class="li">📢</div>
      <h1>Modern Infinity</h1>
      <p>School Admin Panel — Sign In</p>
    </div>

    <!-- Role selector cards -->
    <div class="role-cards" id="roleCards">
      <div class="role-card active" data-u="admin" data-p="" onclick="pickRole(this)">
        <div class="ri">🌐</div>
        <div class="rl">Super Admin</div>
        <div class="rs">All Grades</div>
      </div>
      <div class="role-card" data-u="admin_junior" data-p="" onclick="pickRole(this)">
        <div class="ri">🎒</div>
        <div class="rl">Junior Admin</div>
        <div class="rs">KG – Grade 6</div>
      </div>
      <div class="role-card" data-u="admin_senior" data-p="" onclick="pickRole(this)">
        <div class="ri">🎓</div>
        <div class="rl">Senior Admin</div>
        <div class="rs">Grade 7 – 12</div>
      </div>
    </div>

    <div class="scope-hint" id="scopeHint">You are signing in as <strong>Super Admin</strong> — access to all grades</div>

    <div class="fg"><label>Username</label><input type="text" id="lu" autocomplete="off" placeholder="Enter username"/></div>
    <div class="fg"><label>Password</label><input type="password" id="lp" placeholder="Enter password" onkeydown="if(event.key==='Enter')doLogin()"/></div>
    <button class="btn" onclick="doLogin()">Sign In →</button>
    <div class="err" id="le">❌ Incorrect username or password</div>
  </div>
</div>

<!-- ════════════════════════════════════════════════════ APP ══ -->
<div class="aw" id="aw">

  <!-- SIDEBAR -->
  <div class="sb">
    <div class="sl">
      <div class="si">📢</div>
      <div class="st">Modern Infinity<span>Admin Panel</span></div>
    </div>
    <div class="admin-badge">
      <div class="ab-label">Logged in as</div>
      <div class="ab-name" id="sbAdminName">—</div>
      <div class="ab-scope" id="sbAdminScope">—</div>
    </div>
    <div class="ni active" onclick="showTab('b')">📣 Send Announcement</div>
    <div class="ni" onclick="showTab('h')">📋 History</div>
    <div class="ni" onclick="showTab('p')">👥 Parents</div>
    <div class="ni" onclick="window.open('/finance','_blank')" style="margin-top:8px;border:1px solid rgba(0,200,200,0.2)">💳 Finance Panel ↗</div>
    <div class="sb-bot">
      <div class="lo" onclick="doLogout()">🚪 Sign out</div>
    </div>
  </div>

  <!-- MAIN -->
  <div class="mn">
    <div class="tb2">
      <div>
        <div class="pg-t" id="pt">Send Announcement</div>
        <div class="pg-s" id="ps">Broadcast a message to parents via WhatsApp</div>
      </div>
      <div class="top-right">
        <div class="scope-tag" id="headerScopeTag">—</div>
        <div class="spl"><div class="spd"></div>Bot Online</div>
      </div>
    </div>

    <!-- STATS -->
    <div class="sr" id="sr">
      <div class="sc"><div class="sl2">Parents in My Scope</div><div class="sv" id="sp2">-</div></div>
      <div class="sc"><div class="sl2">Sent Today</div><div class="sv" id="st2">0</div></div>
      <div class="sc"><div class="sl2">Last Sent</div><div class="sv" id="sl3" style="font-size:18px;margin-top:6px">Never</div></div>
    </div>

    <!-- TAB: BROADCAST -->
    <div id="tab-b">
      <div class="sec">
        <div class="sec-t">✍️ Write Announcement</div>
        <label class="fl">Message</label>
        <textarea id="mt" placeholder="Type your announcement here..." oninput="updatePreview()"></textarea>
        <label class="fl" style="margin-top:14px">📷 Photo (optional)</label>
        <div class="photo-zone" id="photoZone" onclick="document.getElementById('photoInput').click()">
          <button class="remove-photo" id="removePhoto" onclick="event.stopPropagation();removePhotoFn()">✕</button>
          <div id="photoPlaceholder">📷 Click to attach a photo<br><span style="font-size:11px;color:#94a3b8">JPG, PNG — max 5MB • Photo will be sent with your message as caption</span></div>
          <img class="photo-preview" id="photoPreview" src="" alt="preview">
          <input type="file" id="photoInput" accept="image/*" style="display:none" onchange="handlePhotoSelect(event)">
        </div>
        <div id="uploadStatus" style="font-size:12px;color:#64748b;margin-top:4px;display:none"></div>
        <div class="cc"><span id="cc">0</span>/1000</div>

        <div style="margin-top:20px">
          <label class="fl">Send to <span id="gradeLabel" style="color:#64748b;font-weight:400;font-size:12px">— select grades below</span></label>
          <div class="gg" id="gradeGrid"><!-- filled by JS --></div>
        </div>

        <div style="margin-top:20px">
          <label class="fl">Preview</label>
          <div class="pb">
            <div class="pb-l">📱 WhatsApp Preview</div>
            <div id="photoPreviewBadge" style="display:none;background:#dcfce7;color:#15803d;padding:6px 10px;border-radius:6px;font-size:12px;margin-bottom:8px;">📷 Photo will be included</div>
          <div class="pb-b" id="pv">Your message will appear here...</div>
            <div class="pb-t" id="pvt">Now</div>
          </div>
        </div>

        <div class="sr2">
          <button class="snd" id="sb2" onclick="doSend()" disabled>📤 Send to Parents</button>
          <div class="rc">To <strong id="rn">-</strong> parents</div>
        </div>
      </div>
    </div>

    <!-- TAB: HISTORY -->
    <div id="tab-h" style="display:none">
      <div class="sec">
        <div class="sec-t">📋 Sent Announcements</div>
        <div id="hl"><div style="color:#94a3b8;text-align:center;padding:20px 0">No announcements yet</div></div>
      </div>
    </div>

    <!-- TAB: PARENTS -->
    <div id="tab-p" style="display:none">
      <div class="sec">
        <div class="sec-t">👥 Parents in My Scope</div>
        <p style="font-size:14px;color:#64748b;margin-bottom:16px">Showing parents from <strong>Google Sheet → Parents tab</strong> filtered to your access level.</p>
        <div id="pl2"><div style="color:#94a3b8;text-align:center;padding:20px 0">Loading...</div></div>
      </div>
    </div>
  </div>
</div>

<!-- MODAL -->
<div class="mo" id="mo">
  <div class="md">
    <div style="font-size:52px;margin-bottom:16px">🎉</div>
    <div style="font-size:22px;font-weight:700;margin-bottom:8px">Sent!</div>
    <div style="font-size:15px;color:#64748b;margin-bottom:28px" id="ms">Delivered.</div>
    <button class="mb2" onclick="closeModal()">Done</button>
  </div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────
var CURRENT_USER = null;   // {username, label, grades}
var ALL_PARENTS  = [];
var SEL_GRADES   = ['All'];

// Grade definitions per scope
var JUNIOR_GRADES = ['KG1','KG2','Grade 1','Grade 2','Grade 3','Grade 4','Grade 5','Grade 6'];
var SENIOR_GRADES = ['Grade 7','Grade 8','Grade 9','Grade 10','Grade 11','Grade 12'];
var ALL_GRADES    = ['KG1','KG2','Grade 1','Grade 2','Grade 3','Grade 4','Grade 5','Grade 6',
                     'Grade 7','Grade 8','Grade 9','Grade 10','Grade 11','Grade 12'];

// Role card pre-fill hints
var ROLE_HINTS = {
  'admin':        'You are signing in as <strong>Super Admin</strong> — access to all grades',
  'admin_junior': 'You are signing in as <strong>Junior Admin</strong> — KG to Grade 6 only',
  'admin_senior': 'You are signing in as <strong>Senior Admin</strong> — Grade 7 to Grade 12 only'
};
var ROLE_USERNAMES = {
  'admin':'admin','admin_junior':'admin_junior','admin_senior':'admin_senior'
};

// ── Role card picker ───────────────────────────────────────────
function pickRole(card){
  document.querySelectorAll('.role-card').forEach(function(c){c.classList.remove('active');});
  card.classList.add('active');
  var u = card.dataset.u;
  document.getElementById('lu').value = u;
  document.getElementById('lp').value = '';
  document.getElementById('scopeHint').innerHTML = ROLE_HINTS[u] || '';
  document.getElementById('le').style.display='none';
}

// ── Login ──────────────────────────────────────────────────────
function doLogin(){
  var u = document.getElementById('lu').value.trim();
  var p = document.getElementById('lp').value.trim();
  if(!u||!p){
    document.getElementById('le').style.display='block';
    document.getElementById('le').textContent='⚠️ Please enter username and password';
    return;
  }
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:u,password:p})
  }).then(function(r){
    if(!r.ok) throw new Error('bad');
    return r.json();
  }).then(function(d){
    if(d.ok){
      CURRENT_USER = d;
      document.getElementById('le').style.display='none';
      document.getElementById('lw').style.display='none';
      document.getElementById('aw').style.display='flex';
      setupAdminUI();
      loadStats();
      loadParents();
    } else {
      document.getElementById('le').style.display='block';
      document.getElementById('le').textContent='❌ Incorrect username or password';
    }
  }).catch(function(){
    document.getElementById('le').style.display='block';
    document.getElementById('le').textContent='❌ Incorrect username or password';
  });
}

// ── Setup UI after login ───────────────────────────────────────
function setupAdminUI(){
  var u = CURRENT_USER;
  var isSuper  = u.grades.length === 0;
  var isJunior = !isSuper && u.grades.includes('KG1');
  var isSenior = !isSuper && u.grades.includes('Grade 7');

  // Sidebar badge
  document.getElementById('sbAdminName').textContent = u.username;
  var scopeEl = document.getElementById('sbAdminScope');
  if(isSuper){  scopeEl.textContent='All Grades'; scopeEl.className='ab-scope scope-super'; }
  else if(isJunior){ scopeEl.textContent='KG – Grade 6'; scopeEl.className='ab-scope scope-junior'; }
  else{          scopeEl.textContent='Grade 7 – 12'; scopeEl.className='ab-scope scope-senior'; }

  // Header scope tag
  var ht = document.getElementById('headerScopeTag');
  if(isSuper){  ht.textContent='🌐 '+u.label; ht.className='scope-tag scope-super'; }
  else if(isJunior){ ht.textContent='🎒 '+u.label; ht.className='scope-tag scope-junior'; }
  else{          ht.textContent='🎓 '+u.label; ht.className='scope-tag scope-senior'; }

  // Build grade grid
  buildGradeGrid(isSuper, isJunior, isSenior);
}

// ── Grade grid builder ─────────────────────────────────────────
function buildGradeGrid(isSuper, isJunior, isSenior){
  var gg = document.getElementById('gradeGrid');
  var myGrades = isSuper ? ALL_GRADES : (isJunior ? JUNIOR_GRADES : SENIOR_GRADES);
  var locked   = isSuper ? [] : (isJunior ? SENIOR_GRADES : JUNIOR_GRADES);

  var html = '';

  // "All" button (scoped to this admin's grades)
  var allLabel = isSuper ? 'All Parents' : (isJunior ? 'All KG – Grade 6' : 'All Grade 7–12');
  html += '<div class="gc all sel" data-g="All" onclick="selGrade(this)">'+allLabel+'</div>';

  // Junior section
  if(isSuper || isJunior){
    if(isSuper) html += '<div style="grid-column:1/-1;font-size:11px;font-weight:600;color:#64748b;padding:8px 4px 2px;letter-spacing:.5px">🎒 JUNIOR — KG to Grade 6</div>';
    JUNIOR_GRADES.forEach(function(g){
      html += '<div class="gc" data-g="'+g+'" onclick="selGrade(this)">'+g+'</div>';
    });
  }

  // Senior section
  if(isSuper || isSenior){
    if(isSuper) html += '<div style="grid-column:1/-1;font-size:11px;font-weight:600;color:#64748b;padding:8px 4px 2px;letter-spacing:.5px">🎓 SENIOR — Grade 7 to 12</div>';
    SENIOR_GRADES.forEach(function(g){
      html += '<div class="gc" data-g="'+g+'" onclick="selGrade(this)">'+g+'</div>';
    });
  }

  gg.innerHTML = html;
  SEL_GRADES = ['All'];
}

// ── Grade selection ────────────────────────────────────────────
function selGrade(el){
  if(el.classList.contains('locked')) return;
  var g = el.dataset.g;
  if(g==='All'){
    SEL_GRADES = ['All'];
    document.querySelectorAll('.gc').forEach(function(c){c.classList.remove('sel');});
    el.classList.add('sel');
  } else {
    document.querySelector('.all').classList.remove('sel');
    el.classList.toggle('sel');
    SEL_GRADES = [].slice.call(document.querySelectorAll('.gc:not(.all).sel')).map(function(c){return c.dataset.g;});
    if(!SEL_GRADES.length){ SEL_GRADES=['All']; document.querySelector('.all').classList.add('sel'); }
  }
  updateCount();
}

// ── Stats & parents ────────────────────────────────────────────
function scopeParam(){
  var g = CURRENT_USER.grades;
  return g.length ? '?grades='+encodeURIComponent(g.join(',')) : '';
}

function loadStats(){
  fetch('/api/parents'+scopeParam()).then(function(r){return r.json();}).then(function(d){
    ALL_PARENTS = d.parents || [];
    document.getElementById('sp2').textContent = ALL_PARENTS.length;
    updateCount();
    var h = JSON.parse(localStorage.getItem('bh_'+CURRENT_USER.username)||'[]');
    document.getElementById('st2').textContent = h.filter(function(x){return new Date(x.t).toDateString()===new Date().toDateString();}).length;
    if(h.length) document.getElementById('sl3').textContent = new Date(h[0].t).toLocaleDateString('en-GB',{day:'numeric',month:'short'});
    renderHistory(h);
  }).catch(function(){ document.getElementById('sp2').textContent='0'; });
}

function loadParents(){
  fetch('/api/parents'+scopeParam()).then(function(r){return r.json();}).then(function(d){
    var pl = d.parents || [];
    var el = document.getElementById('pl2');
    if(!pl.length){
      el.innerHTML='<div style="color:#94a3b8;text-align:center;padding:20px 0">No parents in your scope yet.</div>';
      return;
    }
    el.innerHTML='<table class="ptable"><thead><tr><th>Name</th><th>Phone</th><th>Grade</th></tr></thead><tbody>'+
      pl.map(function(p){
        var grade = p.grade||'';
        var pillCls = grade.toLowerCase().includes('kg')||parseInt(grade.replace(/\\D/g,''))<7 ? 'g-junior' : 'g-senior';
        if(grade.toLowerCase().includes('kg')) pillCls='g-kg';
        return '<tr><td>'+(p.name||'—')+'</td><td>'+p.phone+'</td><td><span class="grade-pill '+pillCls+'">'+grade+'</span></td></tr>';
      }).join('')+'</tbody></table>';
  }).catch(function(){ document.getElementById('pl2').innerHTML='<div style="color:#94a3b8;text-align:center">Could not load parents.</div>'; });
}

function updateCount(){
  var c = SEL_GRADES.includes('All') ? ALL_PARENTS.length :
    ALL_PARENTS.filter(function(p){
      return SEL_GRADES.some(function(g){ return (p.grade||'').toLowerCase().includes(g.toLowerCase()); });
    }).length;
  document.getElementById('rn').textContent = c;
}

// ── Preview ────────────────────────────────────────────────────
function updatePreview(){
  var t = document.getElementById('mt').value;
  document.getElementById('cc').textContent = t.length;
  document.getElementById('pvt').textContent = new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true});
  if(t.trim()){
    document.getElementById('pv').textContent = '📢 Modern Infinity School\\n\\n'+t+'\\n\\n📞 02-3796-9155';
    document.getElementById('sb2').disabled = false;
  } else {
    document.getElementById('pv').textContent = 'Your message will appear here...';
    document.getElementById('sb2').disabled = true;
  }
  updateCount();
}

// ── Send ───────────────────────────────────────────────────────
// Photo state
var uploadedImageUrl = '';

function handlePhotoSelect(e){
  var file = e.target.files[0];
  if(!file) return;
  if(file.size > 5*1024*1024){ alert('Photo must be under 5MB'); return; }
  var reader = new FileReader();
  reader.onload = function(ev){
    document.getElementById('photoPreview').src = ev.target.result;
    document.getElementById('photoPreview').style.display='block';
    document.getElementById('photoPlaceholder').style.display='none';
    document.getElementById('photoZone').classList.add('has-img');
    document.getElementById('removePhoto').style.display='block';
    document.getElementById('photoPreviewBadge').style.display='block';
    updatePreview();
  };
  reader.readAsDataURL(file);
}

function removePhotoFn(){
  uploadedImageUrl='';
  document.getElementById('photoInput').value='';
  document.getElementById('photoPreview').src='';
  document.getElementById('photoPreview').style.display='none';
  document.getElementById('photoPlaceholder').style.display='block';
  document.getElementById('photoZone').classList.remove('has-img');
  document.getElementById('removePhoto').style.display='none';
  document.getElementById('photoPreviewBadge').style.display='none';
  document.getElementById('uploadStatus').style.display='none';
  updatePreview();
}

async function imageToBase64(file){
  // Resize to 800px max and compress to ~200KB before sending
  return new Promise(function(resolve){
    var img = new Image();
    var url = URL.createObjectURL(file);
    img.onload = function(){
      var canvas = document.createElement('canvas');
      var MAX = 800;
      var w = img.width, h = img.height;
      if(w > h){ if(w>MAX){h=Math.round(h*MAX/w);w=MAX;} }
      else      { if(h>MAX){w=Math.round(w*MAX/h);h=MAX;} }
      canvas.width = w; canvas.height = h;
      canvas.getContext('2d').drawImage(img, 0, 0, w, h);
      // Strip the data:image/jpeg;base64, prefix
      var b64 = canvas.toDataURL('image/jpeg', 0.65).split(',')[1];
      URL.revokeObjectURL(url);
      resolve(b64);
    };
    img.src = url;
  });
}

async function uploadPhoto(){
  var file = document.getElementById('photoInput').files[0];
  if(!file) return null;
  var st = document.getElementById('uploadStatus');
  st.style.display='block'; st.textContent='\u23f3 Compressing photo...';
  try{
    // Get WA credentials from server
    var cfgRes = await fetch('/api/wa-config');
    if(!cfgRes.ok){ st.textContent='❌ Config error '+cfgRes.status; return null; }
    var cfg = await cfgRes.json();
    // Resize + compress image in browser
    var blob = await new Promise(function(resolve){
      var img = new Image();
      var url = URL.createObjectURL(file);
      img.onload = function(){
        var c = document.createElement('canvas');
        var MAX=800,w=img.width,h=img.height;
        if(w>h){if(w>MAX){h=Math.round(h*MAX/w);w=MAX;}}
        else{if(h>MAX){w=Math.round(w*MAX/h);h=MAX;}}
        c.width=w; c.height=h;
        c.getContext('2d').drawImage(img,0,0,w,h);
        c.toBlob(function(b){resolve(b);},'image/jpeg',0.65);
        URL.revokeObjectURL(url);
      };
      img.src=url;
    });
    st.textContent='\u23f3 Uploading photo...';
    // Upload directly to Meta API from browser — no Railway proxy
    var fd = new FormData();
    fd.append('file', blob, 'photo.jpg');
    fd.append('messaging_product','whatsapp');
    var up = await fetch(
      'https://graph.facebook.com/v18.0/'+cfg.phone_number_id+'/media',
      {method:'POST',headers:{'Authorization':'Bearer '+cfg.access_token},body:fd}
    );
    var upd = await up.json();
    if(upd.id){
      st.textContent='\u2705 Photo ready to send';
      return upd.id;
    } else {
      st.textContent='\u274c Upload failed: '+(upd.error&&upd.error.message||JSON.stringify(upd));
      return null;
    }
  } catch(e){
    st.textContent='\u274c Upload error: '+e.message;
    return null;
  }
}

async function doSend(){
  var msg = document.getElementById('mt').value.trim();
  if(!msg) return;
  var btn = document.getElementById('sb2');
  btn.disabled=true; btn.textContent='⏳ Sending...';
  // Upload photo first if selected
  var photoFile = document.getElementById('photoInput').files[0];
  var mediaId = '';
  if(photoFile){
    btn.textContent='⏳ Uploading photo...';
    mediaId = await uploadPhoto() || '';
    if(!mediaId){ btn.disabled=false; btn.textContent='📤 Send to Parents'; return; }
  }
  btn.textContent='⏳ Sending...';
  fetch('/broadcast',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      message: msg,
      media_id: mediaId,
      grades:  SEL_GRADES,
      admin_scope: CURRENT_USER.grades   // server enforces this
    })
  }).then(function(r){return r.json();}).then(function(d){
    var h = JSON.parse(localStorage.getItem('bh_'+CURRENT_USER.username)||'[]');
    h.unshift({msg:msg,g:SEL_GRADES,s:d.sent||0,t:new Date().toISOString()});
    localStorage.setItem('bh_'+CURRENT_USER.username, JSON.stringify(h.slice(0,50)));
    renderHistory(h);
    document.getElementById('ms').textContent='Sent to '+(d.sent||0)+' parents successfully.';
    document.getElementById('mo').classList.add('show');
    document.getElementById('mt').value='';
    updatePreview();
    loadStats();
  }).catch(function(){ alert('Error sending. Please try again.'); });
  btn.disabled=false; btn.textContent='📤 Send to Parents';
}

function closeModal(){ document.getElementById('mo').classList.remove('show'); }

// ── History ────────────────────────────────────────────────────
function renderHistory(h){
  var l = document.getElementById('hl');
  if(!h||!h.length){
    l.innerHTML='<div style="color:#94a3b8;text-align:center;padding:20px 0">No announcements yet</div>';
    return;
  }
  l.innerHTML = h.map(function(x){
    return '<div class="hi">'+
      '<div style="width:40px;height:40px;border-radius:10px;background:#f0fdf4;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0">📣</div>'+
      '<div class="hic"><div>'+x.msg+'</div>'+
      '<div class="hm">'+
        new Date(x.t).toLocaleString('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})+
        ' · '+(x.g||['All']).join(', ')+
        ' · <span class="hb">✅ '+(x.s||0)+' sent</span>'+
      '</div></div></div>';
  }).join('');
}

// ── Tabs ───────────────────────────────────────────────────────
function showTab(t){
  document.getElementById('tab-b').style.display = t==='b'?'block':'none';
  document.getElementById('tab-h').style.display = t==='h'?'block':'none';
  document.getElementById('tab-p').style.display = t==='p'?'block':'none';
  document.getElementById('sr').style.display    = t==='b'?'grid':'none';
  var tt={b:['📣 Send Announcement','Broadcast a message to parents via WhatsApp'],
          h:['📋 History','All announcements sent from this account'],
          p:['👥 Parents','Parents registered within your grade scope']};
  document.getElementById('pt').textContent = tt[t][0];
  document.getElementById('ps').textContent = tt[t][1];
  document.querySelectorAll('.ni').forEach(function(e,i){ e.classList.toggle('active',['b','h','p'][i]===t); });
  if(t==='p') loadParents();
}

// ── Logout ─────────────────────────────────────────────────────
function doLogout(){
  CURRENT_USER=null; ALL_PARENTS=[]; SEL_GRADES=['All'];
  document.getElementById('aw').style.display='none';
  document.getElementById('lw').style.display='flex';
  document.getElementById('lu').value='';
  document.getElementById('lp').value='';
  document.getElementById('le').style.display='none';
  document.querySelectorAll('.role-card').forEach(function(c,i){c.classList.toggle('active',i===0);});
  document.getElementById('scopeHint').innerHTML = ROLE_HINTS['admin'];
  document.getElementById('mt').value='';
}
</script>
</body>
</html>
"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route('/api/login', methods=['POST'])
def api_login():
    """Validate admin credentials and return role info."""
    data = request.get_json(force=True, silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    admin = ADMINS.get(username)
    if admin and admin["password"] == password:
        return jsonify({
            "ok": True,
            "username": username,
            "label": admin["label"],
            "grades": admin["grades"]   # empty list = all grades
        })
    return jsonify({"ok": False}), 401


@app.route('/api/parents')
def api_parents():
    """Load parents from Students tab, optionally filtered by admin grade scope."""
    try:
        grades_param = request.args.get("grades", "")
        allowed = [g.strip() for g in grades_param.split(",") if g.strip()]
        rows = read_tab("Students")
        parents = []
        seen = set()
        for r in rows:
            if not (str(r.get("Active", "yes")).lower() == "yes" and r.get("Parent Phone", "")):
                continue
            grade = r.get("Grade", "")
            if allowed:
                if not any(a.lower() in grade.lower() for a in allowed):
                    continue
            phone = str(r.get("Parent Phone", "")).strip()
            if phone in seen:
                continue
            seen.add(phone)
            parents.append({
                "name":  r.get("Parent Name", ""),
                "phone": phone,
                "grade": grade,
                "active": True
            })
        return jsonify({"parents": parents, "count": len(parents)})
    except Exception as e:
        logger.error(f"[api/parents] {e}")
        return jsonify({"parents": [], "count": 0, "error": str(e)})


@app.route('/broadcast', methods=['POST'])
def broadcast():
    """Send announcement to parents filtered by selected grades AND admin scope."""
    data = request.get_json(force=True, silent=True) or {}
    message      = data.get("message", "").strip()
    grades       = data.get("grades", ["All"])        # grades chosen in UI
    admin_scope  = data.get("admin_scope", [])        # grades this admin is allowed (empty=all)

    if not message:
        return jsonify({"error": "No message provided"}), 400

    try:
        rows = read_tab("Students")
        seen_phones = set()
        all_parents = []
        for r in rows:
            if not (str(r.get("Active", "yes")).lower() == "yes" and r.get("Parent Phone", "")):
                continue
            ph = str(r.get("Parent Phone", "")).strip()
            if ph in seen_phones:
                continue
            seen_phones.add(ph)
            all_parents.append({
                "Name": r.get("Parent Name", ""),
                "Phone": ph,
                "Grade": r.get("Grade", "")
            })
    except Exception as e:
        return jsonify({"error": f"Could not load parents: {e}"}), 500

    # Enforce admin scope first (server-side security)
    if admin_scope:
        all_parents = [
            p for p in all_parents
            if any(s.lower() in str(p.get("Grade", "")).lower() for s in admin_scope)
        ]

    # Then apply the UI grade selection
    if "All" not in grades:
        all_parents = [
            p for p in all_parents
            if any(g.lower() in str(p.get("Grade", "")).lower() for g in grades)
        ]

    broadcast_msg = f"📢 Modern Infinity School\n\n{message}\n\n📞 For more info: {SCHOOL['phone']}"
    image_url = data.get("image_url", "").strip()   # kept for backward compat
    media_id  = data.get("media_id", "").strip()

    sent = 0
    failed = 0
    already_sent = set()   # prevent duplicate sends to same number
    for parent in all_parents:
        phone = str(parent.get("Phone", "")).strip()
        if not phone:
            continue
        if not phone.startswith("20") and not phone.startswith("+"):
            phone = "20" + phone.lstrip("0")
        phone = phone.lstrip("+")
        if phone in already_sent:
            logger.warning(f"[broadcast] Skipping duplicate phone: {phone[:6]}***")
            continue
        already_sent.add(phone)
        if media_id:
            # Send image with caption using WhatsApp media_id ONLY
            ok = send_whatsapp_image(phone, media_id, caption=broadcast_msg)
        else:
            # Text only — ignore image_url (old/unused path)
            ok = send_whatsapp(phone, broadcast_msg)
        if ok:
            sent += 1
        else:
            failed += 1
        import time as _t; _t.sleep(0.5)

    logger.info(f"[broadcast] Sent: {sent}, Failed: {failed}, Total: {len(all_parents)}")
    return jsonify({"sent": sent, "failed": failed, "total": len(all_parents)})


@app.route('/api/homework', methods=['GET','POST'])
def homework_api():
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        grade      = str(data.get('grade', '')).strip()
        subject    = str(data.get('subject', '')).strip()
        assignment = str(data.get('assignment', '')).strip()
        due_date   = str(data.get('due_date', '')).strip()
        notes      = str(data.get('notes', '')).strip()
        teacher    = str(data.get('teacher', '')).strip()
        if not assignment or not due_date:
            return jsonify({'ok': False, 'error': 'Assignment and due date are required'}), 400
        try:
            wb = get_client().open_by_key(SHEET_ID)
            ws = wb.worksheet('Homework')
            headers = ws.row_values(1) if ws.row_count > 0 else []
            if not headers:
                ws.update('A1', [['Grade','Subject','Assignment','Due Date','Teacher','Notes','Status']])
            ws.append_row([grade, subject, assignment, due_date, teacher, notes, 'Active'],
                          value_input_option='USER_ENTERED')
            logger.info(f"[homework] Added by {teacher}: {grade} {subject}")
            return jsonify({'ok': True})
        except Exception as e:
            logger.error(f"[homework POST] {e}")
            return jsonify({'ok': False, 'error': str(e)}), 500
    # GET
    grade = request.args.get('grade', '')
    rows = read_tab('Homework')
    result = [r for r in rows if grade.lower() in str(r.get('Grade','')).lower() and str(r.get('Assignment','')).strip()]
    return jsonify({'homework': result, 'count': len(result)})

@app.route('/api/announcements')
def announcements_api():
    rows = read_tab("Announcements")
    active = [r for r in rows if str(r.get("Status","")).lower() == "active"]
    return jsonify({"announcements": active, "count": len(active)})

@app.route('/health')
def health():
    rows = read_tab("Homework")
    parents = read_tab("Students")
    return jsonify({
        "status": "running",
        "school": SCHOOL["name"],
        "whatsapp_configured": bool(ACCESS_TOKEN),
        "sheets_connected": len(rows) > 0,
        "homework_rows": len(rows),
        "parents_registered": len(parents),
    })




@app.route('/homework-panel')
def homework_panel():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Teacher Panel — Modern Infinity School</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #f0f4f8; min-height: 100vh; padding: 20px 16px; }

/* ── Login screen ─────────────────────────────────────────── */
#login-screen {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; margin: -20px -16px;
  background: linear-gradient(135deg, #0F1C2E 0%, #1E3A5F 100%);
}
.login-box {
  background: #fff; border-radius: 16px; padding: 40px 36px;
  width: 100%; max-width: 420px; box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}
.login-logo { text-align: center; margin-bottom: 28px; }
.login-logo .icon { font-size: 48px; }
.login-logo h1 { font-size: 22px; font-weight: 700; color: #0F1C2E; margin-top: 10px; }
.login-logo p  { font-size: 13px; color: #64748b; margin-top: 4px; }
.login-field { margin-bottom: 16px; }
.login-field label { display: block; font-size: 12px; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing:.05em; margin-bottom: 6px; }
.login-field input {
  width: 100%; padding: 13px 14px; border: 2px solid #e2e8f0;
  border-radius: 9px; font-size: 15px; color: #1e293b; background: #f8fafc;
  outline: none; transition: border-color .2s;
}
.login-field input:focus { border-color: #1D4ED8; background: #fff; }
.login-btn {
  width: 100%; padding: 14px; background: #16a34a; color: #fff;
  border: none; border-radius: 9px; font-size: 15px; font-weight: 700;
  cursor: pointer; margin-top: 8px; transition: background .2s;
}
.login-btn:hover { background: #15803d; }
.login-error {
  background: #fee2e2; color: #dc2626; border-radius: 8px;
  padding: 10px 14px; font-size: 13px; font-weight: 600;
  margin-top: 14px; text-align: center; display: none;
}

/* ── Main panel ───────────────────────────────────────────── */
#main-panel { display: none; }

.topbar {
  background: #0F1C2E; color: #fff;
  width: 100%; max-width: 720px; margin: 0 auto;
  border-radius: 14px 14px 0 0;
  padding: 16px 24px;
  display: flex; align-items: center; justify-content: space-between;
}
.topbar-left { display: flex; align-items: center; gap: 12px; }
.topbar-left .icon { font-size: 26px; }
.topbar-left h1 { font-size: 16px; font-weight: 700; }
.topbar-left p  { font-size: 11px; opacity: 0.6; margin-top: 2px; }
.topbar-right { display: flex; align-items: center; gap: 12px; }
.teacher-badge {
  background: rgba(34,197,94,0.2); color: #86efac;
  font-size: 11px; font-weight: 700; padding: 4px 10px;
  border-radius: 20px; border: 1px solid rgba(34,197,94,0.3);
}
.logout-btn {
  background: rgba(255,255,255,0.1); color: #fff;
  border: 1px solid rgba(255,255,255,0.2); border-radius: 6px;
  padding: 5px 12px; font-size: 12px; cursor: pointer;
  transition: background .2s;
}
.logout-btn:hover { background: rgba(255,255,255,0.2); }

.tabs {
  background: #1E3A5F;
  width: 100%; max-width: 720px; margin: 0 auto;
  display: flex;
}
.tab-btn {
  flex: 1; padding: 13px; border: none; cursor: pointer;
  font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.6);
  background: transparent; transition: .2s;
}
.tab-btn.active { color: #fff; background: #0F1C2E; border-bottom: 3px solid #22c55e; }
.tab-btn:hover:not(.active) { color: #fff; background: rgba(255,255,255,0.08); }

.card {
  background: #fff; width: 100%; max-width: 720px;
  margin: 0 auto; border-radius: 0 0 14px 14px;
  padding: 24px 28px; box-shadow: 0 4px 24px rgba(0,0,0,0.08);
}

.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── Fields ───────────────────────────────────────────────── */
.field { margin-bottom: 16px; }
.field label {
  display: block; font-size: 11px; font-weight: 700;
  color: #475569; text-transform: uppercase;
  letter-spacing: .05em; margin-bottom: 5px;
}
.field label span { color: #e53e3e; }
input[type=text], input[type=number], input[type=date], select, textarea {
  width: 100%; padding: 11px 13px;
  border: 2px solid #e2e8f0; border-radius: 8px;
  font-size: 14px; color: #1e293b; background: #f8fafc;
  transition: border-color .2s; outline: none;
}
input:focus, select:focus, textarea:focus {
  border-color: #1D4ED8; background: #fff;
  box-shadow: 0 0 0 3px rgba(29,78,216,.1);
}
textarea { resize: vertical; min-height: 70px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
@media (max-width: 540px) {
  .row-2 { grid-template-columns: 1fr; }
  .row-3 { grid-template-columns: 1fr; }
}

.submit-btn {
  width: 100%; padding: 15px; background: #16a34a; color: #fff;
  border: none; border-radius: 10px; font-size: 15px; font-weight: 700;
  cursor: pointer; margin-top: 8px; transition: background .2s;
}
.submit-btn:hover { background: #15803d; }
.submit-btn:disabled { background: #86efac; cursor: not-allowed; }

.status {
  margin-top: 14px; padding: 12px 16px; border-radius: 8px;
  font-size: 13px; font-weight: 600; text-align: center; display: none;
}
.status.success { background: #dcfce7; color: #15803d; display: block; }
.status.error   { background: #fee2e2; color: #dc2626; display: block; }

.history { margin-top: 24px; }
.history h3 { font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing:.05em; margin-bottom:10px; }
.hist-item {
  background: #f8fafc; border: 1px solid #e2e8f0;
  border-left: 4px solid #1D4ED8; border-radius: 6px;
  padding: 9px 13px; margin-bottom: 7px; font-size: 12px; color: #334155;
}
.hist-item strong { color: #0F1C2E; }
.hist-item .meta { color: #94a3b8; font-size: 11px; margin-top: 2px; }
.no-hist { color: #94a3b8; font-size: 13px; text-align: center; padding: 12px; }

/* ── Exam table ───────────────────────────────────────────── */
.load-btn {
  width: 100%; padding: 12px; background: #1D4ED8; color: #fff;
  border: none; border-radius: 8px; font-size: 14px; font-weight: 600;
  cursor: pointer; margin-top: 4px; transition: background .2s;
}
.load-btn:hover { background: #1e40af; }
.load-btn:disabled { background: #93c5fd; cursor: not-allowed; }

.student-table { width: 100%; border-collapse: collapse; margin-top: 16px; }
.student-table th {
  background: #0F1C2E; color: #fff; font-size: 12px;
  padding: 10px; text-align: left; font-weight: 600;
}
.student-table td { padding: 6px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
.student-table tr:hover td { background: #f8fafc; }
.student-table input { padding: 7px 9px; font-size: 13px; border-radius: 6px; border: 1.5px solid #e2e8f0; }
.student-table input:focus { border-color: #1D4ED8; outline: none; }
.grade-badge {
  display: inline-block; padding: 2px 8px; border-radius: 20px;
  font-size: 11px; font-weight: 700; background: #e2e8f0; color: #475569;
}
.grade-badge.A { background: #dcfce7; color: #15803d; }
.grade-badge.B { background: #dbeafe; color: #1D4ED8; }
.grade-badge.C { background: #fef9c3; color: #854d0e; }
.grade-badge.D { background: #ffedd5; color: #c2410c; }
.grade-badge.F { background: #fee2e2; color: #dc2626; }

.student-count { font-size: 12px; color: #64748b; margin-top: 8px; }
.exam-submit-btn {
  width: 100%; padding: 15px; background: #7c3aed; color: #fff;
  border: none; border-radius: 10px; font-size: 15px; font-weight: 700;
  cursor: pointer; margin-top: 16px; transition: background .2s;
}
.exam-submit-btn:hover { background: #6d28d9; }
.exam-submit-btn:disabled { background: #c4b5fd; cursor: not-allowed; }
.divider { height: 1px; background: #f1f5f9; margin: 20px 0; }
</style>
</head>
<body>

<!-- ── LOGIN SCREEN ─────────────────────────────────────────── -->
<div id="login-screen">
  <div class="login-box">
    <div class="login-logo">
      <div class="icon">🏫</div>
      <h1>Teacher Panel</h1>
      <p>Modern Infinity Language School</p>
    </div>
    <div class="login-field">
      <label>Username</label>
      <input type="text" id="login-user" placeholder="Enter username" autocomplete="username">
    </div>
    <div class="login-field">
      <label>Password</label>
      <input type="password" id="login-pass" placeholder="Enter password" autocomplete="current-password"
        onkeydown="if(event.key==='Enter') doTeacherLogin()">
    </div>
    <button class="login-btn" onclick="doTeacherLogin()">Sign In →</button>
    <div class="login-error" id="login-error">❌ Incorrect username or password</div>
  </div>
</div>

<!-- ── MAIN PANEL ───────────────────────────────────────────── -->
<div id="main-panel">

  <div class="topbar">
    <div class="topbar-left">
      <div class="icon">🏫</div>
      <div>
        <h1>Teacher Panel</h1>
        <p>Modern Infinity Language School</p>
      </div>
    </div>
    <div class="topbar-right">
      <span class="teacher-badge" id="teacher-badge">👤 Teacher</span>
      <button class="logout-btn" onclick="doLogout()">Sign out</button>
    </div>
  </div>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('homework', this)">📚 Add Homework</button>
    <button class="tab-btn" onclick="switchTab('exams', this)">📝 Exam Results</button>
  </div>

  <div class="card">

    <!-- ── HOMEWORK TAB ──────────────────────── -->
    <div class="tab-panel active" id="tab-homework">
      <div class="field">
        <label>👤 Teacher Name <span>*</span></label>
        <input type="text" id="teacher" placeholder="e.g. Ms. Sara">
      </div>
      <div class="row-2">
        <div class="field">
          <label>🎓 Grade <span>*</span></label>
          <select id="grade">
            <option value="">Select grade…</option>
            <option>KG1</option><option>KG2</option>
            <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
            <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
            <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
            <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
          </select>
        </div>
        <div class="field">
          <label>📖 Subject <span>*</span></label>
          <select id="subject">
            <option value="">Select subject…</option>
            <option>Math</option><option>Arabic</option><option>English</option>
            <option>Science</option><option>Social Studies</option><option>Physics</option>
            <option>Chemistry</option><option>Biology</option><option>French</option>
            <option>Computer</option><option>Islamic Studies</option><option>Art</option>
            <option>Music</option><option>PE</option><option>History</option>
            <option>Geography</option><option>Activities</option><option>Other</option>
          </select>
        </div>
      </div>
      <div class="field">
        <label>📝 Assignment <span>*</span></label>
        <textarea id="assignment" placeholder="Describe the homework clearly…"></textarea>
      </div>
      <div class="row-2">
        <div class="field">
          <label>📅 Due Date <span>*</span></label>
          <input type="date" id="due_date">
        </div>
        <div class="field">
          <label>🗂️ Type</label>
          <select id="type">
            <option>Worksheet</option><option>Workbook</option><option>Essay</option>
            <option>Reading</option><option>Summary</option><option>Research</option>
            <option>Memorization</option><option>Drawing</option><option>Study</option>
            <option>Exercises</option><option>Lab Report</option><option>Revision</option>
            <option>Exam Practice</option><option>Other</option>
          </select>
        </div>
      </div>
      <div class="field">
        <label>💬 Notes (optional)</label>
        <input type="text" id="notes" placeholder="e.g. Bring calculator, handwritten only…">
      </div>
      <button class="submit-btn" onclick="submitHomework()">➕ ADD HOMEWORK</button>
      <div class="status" id="hw-status"></div>
      <div class="history">
        <h3>📋 Recently Added (this session)</h3>
        <div id="hw-history"><div class="no-hist">Nothing added yet.</div></div>
      </div>
    </div>

    <!-- ── EXAM TAB ──────────────────────────── -->
    <div class="tab-panel" id="tab-exams">
      <div class="field">
        <label>👤 Teacher Name <span>*</span></label>
        <input type="text" id="ex-teacher" placeholder="e.g. Mr. Hassan">
      </div>
      <div class="row-2">
        <div class="field">
          <label>🎓 Grade <span>*</span></label>
          <select id="ex-grade" onchange="clearStudents()">
            <option value="">Select grade…</option>
            <option>KG1</option><option>KG2</option>
            <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
            <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
            <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
            <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
          </select>
        </div>
        <div class="field">
          <label>📖 Subject <span>*</span></label>
          <select id="ex-subject">
            <option value="">Select subject…</option>
            <option>Math</option><option>Arabic</option><option>English</option>
            <option>Science</option><option>Social Studies</option><option>Physics</option>
            <option>Chemistry</option><option>Biology</option><option>French</option>
            <option>Computer</option><option>Islamic Studies</option><option>Art</option>
            <option>Music</option><option>PE</option><option>History</option>
            <option>Geography</option><option>Activities</option><option>Other</option>
          </select>
        </div>
      </div>
      <div class="row-3">
        <div class="field">
          <label>📅 Exam Date</label>
          <input type="date" id="ex-date">
        </div>
        <div class="field">
          <label>📋 Term</label>
          <select id="ex-term">
            <option>Term 1</option><option>Term 2</option><option>Term 3</option>
            <option>Midterm</option><option>Final</option><option>Quiz</option>
          </select>
        </div>
        <div class="field">
          <label>🔢 Total Marks</label>
          <input type="number" id="ex-total" value="100" min="1" max="1000">
        </div>
      </div>
      <button class="load-btn" id="loadBtn" onclick="loadStudents()">
        👥 Load Students for This Grade
      </button>
      <div id="ex-status" class="status"></div>
      <div id="students-section" style="display:none">
        <p class="student-count" id="student-count"></p>
        <table class="student-table">
          <thead>
            <tr>
              <th>#</th><th>Student Name</th><th>ID</th>
              <th>Score</th><th>%</th><th>Grade</th><th>Notes</th>
            </tr>
          </thead>
          <tbody id="students-tbody"></tbody>
        </table>
        <button class="exam-submit-btn" id="examSubmitBtn" onclick="submitExamResults()">
          💾 SAVE ALL RESULTS TO SHEET
        </button>
      </div>
      <div class="divider"></div>
      <div class="history">
        <h3>✅ Recently Saved (this session)</h3>
        <div id="ex-history"><div class="no-hist">Nothing saved yet.</div></div>
      </div>
    </div>

  </div><!-- .card -->
</div><!-- #main-panel -->

<script>
// ── Credentials (checked client-side + server validates on API calls) ─────
// ── Auth state ────────────────────────────────────────────────────────────
var CURRENT_TEACHER = "";
var CURRENT_FULL_NAME = "";

// ── Login ──────────────────────────────────────────────────────────────────
async function doTeacherLogin() {
  const user = document.getElementById('login-user').value.trim().toLowerCase();
  const pass = document.getElementById('login-pass').value;
  const err  = document.getElementById('login-error');
  const btn  = document.querySelector('.login-btn');
  if (!user || !pass) {
    err.textContent = '\u26a0\ufe0f Please enter username and password';
    err.style.display = 'block'; return;
  }
  btn.textContent = 'Signing in...'; btn.disabled = true;
  err.style.display = 'none';
  try {
    const res = await fetch('/api/teacher-login', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username: user, password: pass})
    });
    const data = await res.json();
    if (data.ok) {
      CURRENT_TEACHER   = user;
      CURRENT_FULL_NAME = data.full_name;
      sessionStorage.setItem('teacher_user', user);
      sessionStorage.setItem('teacher_name', data.full_name);
      document.getElementById('login-screen').style.display = 'none';
      document.getElementById('main-panel').style.display = 'block';
      document.getElementById('teacher-badge').textContent = '👤 ' + data.full_name;
      document.getElementById('teacher').value = data.full_name;
      document.getElementById('ex-teacher').value = data.full_name;
      localStorage.setItem('hw_teacher', data.full_name);
    } else {
      err.textContent = '❌ ' + data.error;
      err.style.display = 'block';
      document.getElementById('login-pass').value = '';
      document.getElementById('login-pass').focus();
    }
  } catch(e) {
    err.textContent = '❌ Connection error. Please try again.';
    err.style.display = 'block';
  }
  btn.textContent = 'Sign In →'; btn.disabled = false;
}

function doLogout() {
  sessionStorage.removeItem('teacher_user');
  sessionStorage.removeItem('teacher_name');
  CURRENT_TEACHER = ""; CURRENT_FULL_NAME = "";
  document.getElementById('main-panel').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('login-user').value = '';
  document.getElementById('login-pass').value = '';
}

window.onload = function() {
  const tmr = new Date(); tmr.setDate(tmr.getDate()+1);
  document.getElementById('due_date').value = tmr.toISOString().split('T')[0];
  document.getElementById('ex-date').value = new Date().toISOString().split('T')[0];
  const s = sessionStorage.getItem('teacher_user');
  const n = sessionStorage.getItem('teacher_name');
  if (s && n) {
    CURRENT_TEACHER = s; CURRENT_FULL_NAME = n;
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('main-panel').style.display = 'block';
    document.getElementById('teacher-badge').textContent = '👤 ' + n;
    document.getElementById('teacher').value = n;
    document.getElementById('ex-teacher').value = n;
  }
  const saved = localStorage.getItem('hw_teacher');
  if (saved && !CURRENT_TEACHER) {
    document.getElementById('teacher').value = saved;
    document.getElementById('ex-teacher').value = saved;
  }
};

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(tab, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  btn.classList.add('active');
}

// ── Sync teacher name across tabs ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  ['teacher','ex-teacher'].forEach(function(id) {
    document.getElementById(id)?.addEventListener('input', function() {
      const other = id === 'teacher' ? 'ex-teacher' : 'teacher';
      document.getElementById(other).value = this.value;
    });
  });
});

// ── HOMEWORK ──────────────────────────────────────────────────────────────
const hwHistory = [];
async function submitHomework() {
  const btn    = document.querySelector('#tab-homework .submit-btn');
  const status = document.getElementById('hw-status');
  const teacher    = document.getElementById('teacher').value.trim();
  const grade      = document.getElementById('grade').value;
  const subject    = document.getElementById('subject').value;
  const assignment = document.getElementById('assignment').value.trim();
  const due_date   = document.getElementById('due_date').value;
  const type       = document.getElementById('type').value;
  const notes      = document.getElementById('notes').value.trim();

  const missing = [];
  if (!teacher) missing.push('Teacher Name');
  if (!grade) missing.push('Grade');
  if (!subject) missing.push('Subject');
  if (!assignment) missing.push('Assignment');
  if (!due_date) missing.push('Due Date');

  if (missing.length) {
    status.className = 'status error';
    status.textContent = '⚠️ Please fill in: ' + missing.join(', ');
    return;
  }

  btn.disabled = true; btn.textContent = 'Saving…';
  status.className = 'status';

  try {
    const res = await fetch('/api/add-homework', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({teacher, grade, subject, assignment, due_date, type, notes})
    });
    const data = await res.json();
    if (data.ok) {
      status.className = 'status success';
      status.textContent = '✅ ' + data.message;
      localStorage.setItem('hw_teacher', teacher);
      hwHistory.unshift({teacher, grade, subject, assignment, due_date, type});
      renderHwHistory();
      document.getElementById('subject').value = '';
      document.getElementById('assignment').value = '';
      document.getElementById('notes').value = '';
      const tmr = new Date(); tmr.setDate(tmr.getDate()+1);
      document.getElementById('due_date').value = tmr.toISOString().split('T')[0];
    } else {
      status.className = 'status error';
      status.textContent = '❌ ' + data.error;
    }
  } catch(e) {
    status.className = 'status error';
    status.textContent = '❌ Network error. Try again.';
  }
  btn.disabled = false; btn.textContent = '➕ ADD HOMEWORK';
}

function renderHwHistory() {
  const el = document.getElementById('hw-history');
  if (!hwHistory.length) { el.innerHTML = '<div class="no-hist">Nothing added yet.</div>'; return; }
  el.innerHTML = hwHistory.slice(0,5).map(h =>
    `<div class="hist-item"><strong>${h.grade} — ${h.subject}</strong>: ${h.assignment.substring(0,70)}${h.assignment.length>70?'…':''}
     <div class="meta">📅 ${h.due_date} · 👤 ${h.teacher} · 🗂️ ${h.type}</div></div>`
  ).join('');
}

// ── EXAM RESULTS ──────────────────────────────────────────────────────────
var loadedStudents = [];
const exHistory = [];

function clearStudents() {
  loadedStudents = [];
  document.getElementById('students-section').style.display = 'none';
  document.getElementById('ex-status').className = 'status';
}

async function loadStudents() {
  const grade = document.getElementById('ex-grade').value;
  if (!grade) { alert('Please select a grade first.'); return; }
  const btn = document.getElementById('loadBtn');
  const status = document.getElementById('ex-status');
  btn.disabled = true; btn.textContent = '⏳ Loading students…';
  status.className = 'status';
  try {
    const res = await fetch('/api/students-by-grade?grade=' + encodeURIComponent(grade));
    const data = await res.json();
    if (!data.ok || !data.students.length) {
      status.className = 'status error';
      status.textContent = '❌ No students found for ' + grade;
      btn.disabled = false; btn.textContent = '👥 Load Students for This Grade';
      return;
    }
    loadedStudents = data.students;
    renderStudentTable(loadedStudents);
    document.getElementById('students-section').style.display = 'block';
    document.getElementById('student-count').textContent =
      `📋 ${data.count} students in ${grade} — enter each score below:`;
    status.className = 'status';
  } catch(e) {
    status.className = 'status error';
    status.textContent = '❌ Error loading students: ' + e.message;
  }
  btn.disabled = false; btn.textContent = '👥 Load Students for This Grade';
}

function renderStudentTable(students) {
  document.getElementById('students-tbody').innerHTML = students.map((s, i) => `
    <tr>
      <td style="color:#94a3b8;font-size:12px;width:30px">${i+1}</td>
      <td style="font-weight:600;font-size:13px">${s.name}</td>
      <td style="color:#94a3b8;font-size:11px">${s.id}</td>
      <td style="width:80px">
        <input type="number" id="score-${i}" min="0" placeholder="—"
          oninput="autoCalc(${i})" style="width:70px;text-align:center">
      </td>
      <td style="width:60px"><span id="pct-${i}" style="font-size:13px;color:#64748b">—</span></td>
      <td style="width:55px"><span class="grade-badge" id="gl-${i}">—</span></td>
      <td><input type="text" id="note-${i}" placeholder="optional" style="font-size:12px"></td>
    </tr>
  `).join('');
}

function autoCalc(i) {
  const total = parseFloat(document.getElementById('ex-total').value) || 100;
  const score = parseFloat(document.getElementById('score-'+i).value);
  if (isNaN(score)) {
    document.getElementById('pct-'+i).textContent = '—';
    document.getElementById('gl-'+i).textContent = '—';
    document.getElementById('gl-'+i).className = 'grade-badge';
    return;
  }
  const pct = Math.round(score / total * 100);
  document.getElementById('pct-'+i).textContent = pct + '%';
  let gl = 'F', cls = 'F';
  if (pct>=95){gl='A+';cls='A';} else if(pct>=90){gl='A';cls='A';}
  else if(pct>=85){gl='B+';cls='B';} else if(pct>=80){gl='B';cls='B';}
  else if(pct>=75){gl='C+';cls='C';} else if(pct>=70){gl='C';cls='C';}
  else if(pct>=65){gl='D+';cls='D';} else if(pct>=60){gl='D';cls='D';}
  const el = document.getElementById('gl-'+i);
  el.textContent = gl; el.className = 'grade-badge '+cls;
}

async function submitExamResults() {
  const teacher  = document.getElementById('ex-teacher').value.trim();
  const grade    = document.getElementById('ex-grade').value;
  const subject  = document.getElementById('ex-subject').value;
  const term     = document.getElementById('ex-term').value;
  const examDate = document.getElementById('ex-date').value;
  const total    = document.getElementById('ex-total').value || '100';
  const status   = document.getElementById('ex-status');

  if (!teacher || !grade || !subject) {
    status.className = 'status error';
    status.textContent = '⚠️ Please fill in Teacher, Grade and Subject.';
    return;
  }

  const results = loadedStudents.map((s, i) => ({
    student_id: s.id, student_name: s.name, grade: s.grade,
    score: document.getElementById('score-'+i)?.value.trim() || '',
    total,
    percentage: document.getElementById('pct-'+i)?.textContent || '',
    grade_letter: document.getElementById('gl-'+i)?.textContent || '',
    notes: document.getElementById('note-'+i)?.value.trim() || ''
  })).filter(r => r.score !== '');

  if (!results.length) {
    status.className = 'status error';
    status.textContent = '⚠️ No scores entered yet.';
    return;
  }

  const btn = document.getElementById('examSubmitBtn');
  btn.disabled = true; btn.textContent = '⏳ Saving to sheet…';
  status.className = 'status';

  try {
    const res = await fetch('/api/add-exam-results', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({results, subject, term, teacher, exam_date: examDate})
    });
    const data = await res.json();
    if (data.ok) {
      status.className = 'status success';
      status.textContent = '✅ ' + data.message;
      localStorage.setItem('hw_teacher', teacher);
      exHistory.unshift({grade, subject, term, count: data.saved, teacher});
      renderExHistory();
    } else {
      status.className = 'status error';
      status.textContent = '❌ ' + data.error;
    }
  } catch(e) {
    status.className = 'status error';
    status.textContent = '❌ Network error: ' + e.message;
  }
  btn.disabled = false; btn.textContent = '💾 SAVE ALL RESULTS TO SHEET';
}

function renderExHistory() {
  const el = document.getElementById('ex-history');
  if (!exHistory.length) { el.innerHTML = '<div class="no-hist">Nothing saved yet.</div>'; return; }
  el.innerHTML = exHistory.slice(0,5).map(h =>
    `<div class="hist-item"><strong>${h.grade} — ${h.subject}</strong> (${h.term})
     <div class="meta">✅ ${h.count} results saved · 👤 ${h.teacher}</div></div>`
  ).join('');
}

// Ctrl+Enter to submit active tab
document.addEventListener('keydown', e => {
  if ((e.ctrlKey||e.metaKey) && e.key==='Enter') {
    if (document.getElementById('tab-homework').classList.contains('active')) submitHomework();
    else submitExamResults();
  }
});
</script>
</body>
</html>
"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route('/api/add-homework', methods=['POST'])
def api_add_homework():
    """Append a homework row to the Homework sheet tab."""
    data = request.get_json(force=True, silent=True) or {}
    teacher    = str(data.get("teacher",    "")).strip()
    grade      = str(data.get("grade",      "")).strip()
    subject    = str(data.get("subject",    "")).strip()
    assignment = str(data.get("assignment", "")).strip()
    due_date   = str(data.get("due_date",   "")).strip()
    hw_type    = str(data.get("type",       "Worksheet")).strip()
    notes      = str(data.get("notes",      "")).strip()

    missing = [f for f, v in [("Teacher",teacher),("Grade",grade),
               ("Subject",subject),("Assignment",assignment),("Due Date",due_date)] if not v]
    if missing:
        return jsonify({"ok": False, "error": "Missing: " + ", ".join(missing)}), 400

    try:
        gc = get_client()
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.worksheet("Homework")
        # Find the actual last data row (avoid appending after 1000 blank rows)
        col_a = ws.col_values(1)  # Grade column — find last non-empty
        last_row = len([v for v in col_a if str(v).strip()])
        next_row = last_row + 1
        ws.update(f"A{next_row}:H{next_row}",
                  [[grade, subject, assignment, due_date, teacher, hw_type, notes, "Active"]],
                  value_input_option="USER_ENTERED")
        logger.info(f"[homework] Added row {next_row}: {grade} {subject} by {teacher}")
        return jsonify({"ok": True, "message": f"Added: {grade} — {subject}: {assignment[:50]}"})
    except Exception as e:
        logger.error(f"[homework] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route('/api/upload-image', methods=['POST'])
def upload_image():
    """Accept base64 image JSON, upload to WhatsApp Media API, return media_id."""
    if not ACCESS_TOKEN:
        return jsonify({"ok": False, "error": "WhatsApp not configured"}), 500
    try:
        import base64 as _b64
        data = request.get_json(force=True, silent=True) or {}
        b64_str = data.get("image_b64", "")
        filename = data.get("filename", "photo.jpg")
        if not b64_str:
            return jsonify({"ok": False, "error": "No image data provided"}), 400
        # Decode base64 → bytes
        img_bytes = _b64.b64decode(b64_str)
        logger.info(f"[upload] Image size: {len(img_bytes)/1024:.1f}KB")
        # Upload to WhatsApp Media API
        upload_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
        res = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            files={"file": (filename, img_bytes, "image/jpeg")},
            data={"messaging_product": "whatsapp"},
            timeout=30
        )
        if res.status_code == 200:
            media_id = res.json().get("id")
            logger.info(f"[upload] WhatsApp media_id: {media_id}")
            return jsonify({"ok": True, "media_id": media_id})
        logger.error(f"[upload] WA upload failed: {res.status_code} {res.text[:300]}")
        return jsonify({"ok": False, "error": f"WhatsApp upload failed ({res.status_code}): {res.text[:150]}"}), 500
    except Exception as e:
        logger.error(f"[upload] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/wa-config')
def wa_config():
    """Return WhatsApp config for direct browser-to-Meta uploads."""
    return jsonify({"phone_number_id": PHONE_NUMBER_ID, "access_token": ACCESS_TOKEN})



@app.route('/api/students-by-grade')
def students_by_grade():
    """Return students filtered by grade for exam entry."""
    grade = request.args.get("grade", "").strip()
    if not grade:
        return jsonify({"ok": False, "error": "Grade required"}), 400
    try:
        rows = read_tab("Students")
        students = []
        for r in rows:
            if str(r.get("Grade","")).strip().lower() == grade.lower():
                students.append({
                    "id":   str(r.get("Student ID","")).strip(),
                    "name": str(r.get("Full Name", r.get("Student Name",""))).strip(),
                    "grade": str(r.get("Grade","")).strip(),
                    "section": str(r.get("Section","")).strip(),
                })
        students.sort(key=lambda x: x["name"])
        return jsonify({"ok": True, "students": students, "count": len(students)})
    except Exception as e:
        logger.error(f"[students-by-grade] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/add-exam-results', methods=['POST'])
def api_add_exam_results():
    """Append multiple exam result rows to the exam sheet tab."""
    data = request.get_json(force=True, silent=True) or {}
    results  = data.get("results", [])
    subject  = str(data.get("subject",  "")).strip()
    term     = str(data.get("term",     "Term 1")).strip()
    teacher  = str(data.get("teacher",  "")).strip()
    exam_date = str(data.get("exam_date","")).strip()

    if not results:
        return jsonify({"ok": False, "error": "No results provided"}), 400

    try:
        import datetime
        if not exam_date:
            exam_date = datetime.datetime.now().strftime("%Y-%m-%d")

        gc = get_client()
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.worksheet("exam")

        # Find last data row
        col_a = ws.col_values(1)
        last_row = len([v for v in col_a if str(v).strip()])
        next_row = last_row + 1

        rows_to_write = []
        for res in results:
            score      = str(res.get("score","")).strip()
            total      = str(res.get("total","100")).strip()
            percentage = str(res.get("percentage","")).strip()
            grade_letter = str(res.get("grade_letter","")).strip()
            notes      = str(res.get("notes","")).strip()
            student_id = str(res.get("student_id","")).strip()
            student_name = str(res.get("student_name","")).strip()
            grade      = str(res.get("grade","")).strip()

            if not score:  # skip empty rows
                continue

            # Auto-calculate percentage if missing
            if not percentage and score and total:
                try:
                    percentage = f"{round(float(score)/float(total)*100)}%"
                except: pass

            # Auto grade letter if missing
            if not grade_letter and percentage:
                try:
                    pct = float(percentage.replace("%",""))
                    if pct >= 95: grade_letter = "A+"
                    elif pct >= 90: grade_letter = "A"
                    elif pct >= 85: grade_letter = "B+"
                    elif pct >= 80: grade_letter = "B"
                    elif pct >= 75: grade_letter = "C+"
                    elif pct >= 70: grade_letter = "C"
                    elif pct >= 65: grade_letter = "D+"
                    elif pct >= 60: grade_letter = "D"
                    else: grade_letter = "F"
                except: pass

            rows_to_write.append([
                student_id, student_name, grade, subject,
                score, total, percentage, grade_letter,
                "", exam_date, term, notes
            ])

        if not rows_to_write:
            return jsonify({"ok": False, "error": "No valid scores entered"}), 400

        ws.update(
            f"A{next_row}:L{next_row + len(rows_to_write) - 1}",
            rows_to_write,
            value_input_option="USER_ENTERED"
        )
        logger.info(f"[exam] Wrote {len(rows_to_write)} results for {subject} {term}")
        return jsonify({"ok": True, "saved": len(rows_to_write),
                        "message": f"Saved {len(rows_to_write)} results for {subject} — {term}"})
    except Exception as e:
        logger.error(f"[exam-results] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route('/api/teacher-login', methods=['POST'])
def teacher_login():
    """Validate teacher credentials against the Teachers sheet tab."""
    data = request.get_json(force=True, silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password required"}), 400
    try:
        rows = read_tab("Teachers")
        for row in rows:
            u = str(row.get("Username", "")).strip().lower()
            p = str(row.get("Password", "")).strip()
            active = str(row.get("Active", "yes")).strip().lower()
            if u == username and p == password:
                if active != "yes":
                    return jsonify({"ok": False, "error": "Account is inactive. Contact admin."}), 403
                full_name = str(row.get("Full Name", username.replace(".", " ").title())).strip()
                return jsonify({"ok": True, "full_name": full_name, "username": username})
        return jsonify({"ok": False, "error": "Incorrect username or password"}), 401
    except Exception as e:
        logger.error(f"[teacher-login] {e}")
        return jsonify({"ok": False, "error": "Login service unavailable"}), 500



@app.route('/setup-teachers-tab')
def setup_teachers_tab():
    """Create the Teachers tab with initial accounts."""
    import time as _t
    try:
        gc = get_client()
        sh = gc.open_by_key(SHEET_ID)

        # Delete existing if present
        try:
            sh.del_worksheet(sh.worksheet("Teachers"))
            _t.sleep(2)
        except: pass

        ws = sh.add_worksheet("Teachers", rows=50, cols=6)
        _t.sleep(2)

        # Headers + initial teachers
        rows = [
            ["Username", "Password", "Full Name", "Active"],
            ["ms.sara",    "sara2026",    "Ms. Sara",    "yes"],
            ["mr.hassan",  "hassan2026",  "Mr. Hassan",  "yes"],
            ["ms.fatima",  "fatima2026",  "Ms. Fatima",  "yes"],
            ["mr.tarek",   "tarek2026",   "Mr. Tarek",   "yes"],
            ["ms.hana",    "hana2026",    "Ms. Hana",    "yes"],
            ["mr.khaled",  "khaled2026",  "Mr. Khaled",  "yes"],
            ["mr.omar",    "omar2026",    "Mr. Omar",    "yes"],
            ["ms.nadia",   "nadia2026",   "Ms. Nadia",   "yes"],
            ["ms.claire",  "claire2026",  "Ms. Claire",  "yes"],
            ["mr.ahmed",   "ahmed2026",   "Mr. Ahmed",   "yes"],
        ]
        ws.update("A1:D11", rows, value_input_option="USER_ENTERED")
        _t.sleep(2)

        # Format header row
        sid = ws.id
        sh.batch_update({"requests": [
            {"repeatCell": {
                "range": {"sheetId":sid,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":4},
                "cell": {"userEnteredFormat": {
                    "backgroundColor":{"red":0.059,"green":0.110,"blue":0.180},
                    "textFormat":{"bold":True,"foregroundColor":{"red":1,"green":1,"blue":1}},
                    "horizontalAlignment":"CENTER"
                }},
                "fields":"userEnteredFormat"
            }},
            {"updateDimensionProperties":{
                "range":{"sheetId":sid,"dimension":"COLUMNS","startIndex":0,"endIndex":4},
                "properties":{"pixelSize":160},"fields":"pixelSize"
            }}
        ]})

        return jsonify({"ok": True, "message": f"Teachers tab created with {len(rows)-1} accounts"})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()[-400:]}), 500




@app.route('/finance')
def finance_panel():
    """Finance team panel — manage student payment status."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Finance Panel — Modern Infinity School</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;min-height:100vh}
/* LOGIN */
#login-screen{display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#0F1C2E,#1a3a2a)}
.login-box{background:#fff;border-radius:16px;padding:40px 36px;width:100%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.login-logo{text-align:center;margin-bottom:28px}
.login-logo .icon{font-size:48px}
.login-logo h1{font-size:22px;font-weight:700;color:#0F1C2E;margin-top:10px}
.login-logo p{font-size:13px;color:#64748b;margin-top:4px}
.field{margin-bottom:16px}
.field label{display:block;font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.field input,.field select{width:100%;padding:13px 14px;border:2px solid #e2e8f0;border-radius:9px;font-size:15px;outline:none;transition:border-color .2s;background:#f8fafc}
.field input:focus,.field select:focus{border-color:#16a34a;background:#fff}
.login-btn{width:100%;padding:14px;background:#16a34a;color:#fff;border:none;border-radius:9px;font-size:15px;font-weight:700;cursor:pointer;transition:background .2s}
.login-btn:hover{background:#15803d}
.login-error{background:#fee2e2;color:#dc2626;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:600;margin-top:14px;text-align:center;display:none}
/* MAIN */
#main-panel{display:none}
.topbar{background:#0F1C2E;color:#fff;padding:16px 28px;display:flex;align-items:center;justify-content:space-between}
.topbar h1{font-size:18px;font-weight:700}
.topbar p{font-size:12px;opacity:.6;margin-top:2px}
.topbar-right{display:flex;align-items:center;gap:14px}
.badge{background:rgba(22,163,74,.25);color:#86efac;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700;border:1px solid rgba(22,163,74,.3)}
.logout-btn{background:rgba(255,255,255,.1);color:#fff;border:1px solid rgba(255,255,255,.2);border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer}
.logout-btn:hover{background:rgba(255,255,255,.2)}
.content{max-width:1000px;margin:28px auto;padding:0 20px}
/* FILTERS */
.filters{background:#fff;border-radius:12px;padding:20px 24px;margin-bottom:20px;border:1px solid #e2e8f0;display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end}
.filter-group{display:flex;flex-direction:column;gap:6px;min-width:180px}
.filter-group label{font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em}
.filter-group select,.filter-group input{padding:10px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none;background:#f8fafc}
.filter-group select:focus,.filter-group input:focus{border-color:#16a34a}
.load-btn{padding:10px 24px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap}
.load-btn:hover{background:#15803d}
/* STATS */
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px}
.stat-card{background:#fff;border-radius:12px;padding:20px 24px;border:1px solid #e2e8f0}
.stat-label{font-size:13px;color:#64748b;font-weight:500;margin-bottom:6px}
.stat-value{font-size:28px;font-weight:700;color:#0F1C2E}
.stat-value.green{color:#16a34a}
.stat-value.red{color:#dc2626}
/* SAVE BAR */
.save-bar{background:#fff;border-radius:12px;padding:16px 24px;margin-bottom:20px;border:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;display:none}
.save-bar.show{display:flex}
.save-info{font-size:14px;color:#374151}<br>.save-info span{font-weight:700;color:#dc2626}
.save-btn{padding:10px 28px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer}
.save-btn:hover{background:#15803d}
.save-btn:disabled{background:#86efac;cursor:not-allowed}
/* TABLE */
.table-wrap{background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden}
.table-header{padding:16px 24px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between}
.table-title{font-size:15px;font-weight:700;color:#0F1C2E}
.student-count{font-size:13px;color:#64748b}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:12px 16px;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;background:#f8fafc;border-bottom:1px solid #e2e8f0}
td{padding:12px 16px;border-bottom:1px solid #f1f5f9;font-size:14px;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafbfc}
.grade-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;background:#e2e8f0;color:#475569}
/* TOGGLE */
.toggle-wrap{display:flex;align-items:center;gap:10px}
.toggle{position:relative;width:52px;height:28px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#e2e8f0;border-radius:28px;transition:.3s}
.slider:before{content:'';position:absolute;height:20px;width:20px;left:4px;bottom:4px;background:#fff;border-radius:50%;transition:.3s;box-shadow:0 1px 4px rgba(0,0,0,.2)}
input:checked+.slider{background:#16a34a}
input:checked+.slider:before{transform:translateX(24px)}
.toggle-label{font-size:13px;font-weight:600}
.toggle-label.paid{color:#16a34a}
.toggle-label.unpaid{color:#dc2626}
/* Status */
.status-msg{padding:12px 16px;border-radius:8px;font-size:13px;font-weight:600;margin-top:12px;display:none}
.status-msg.success{background:#dcfce7;color:#15803d;display:block}
.status-msg.error{background:#fee2e2;color:#dc2626;display:block}
.empty{text-align:center;padding:40px;color:#94a3b8;font-size:14px}
@media(max-width:600px){.stats{grid-template-columns:1fr}.filters{flex-direction:column}}
</style>
</head>
<body>

<!-- LOGIN -->
<div id="login-screen">
  <div class="login-box">
    <div class="login-logo">
      <div class="icon">💳</div>
      <h1>Finance Panel</h1>
      <p>Modern Infinity Language School</p>
    </div>
    <div class="field"><label>Username</label>
      <input type="text" id="fu" placeholder="Finance username" autocomplete="off">
    </div>
    <div class="field"><label>Password</label>
      <input type="password" id="fp" placeholder="Password" onkeydown="if(event.key==='Enter')doLogin()">
    </div>
    <button class="login-btn" onclick="doLogin()">Sign In →</button>
    <div class="login-error" id="fe">❌ Incorrect username or password</div>
  </div>
</div>

<!-- MAIN -->
<div id="main-panel">
  <div class="topbar">
    <div>
      <h1>💳 Finance Panel</h1>
      <p>Modern Infinity Language School — Payment Management</p>
    </div>
    <div class="topbar-right">
      <span class="badge">Finance Team</span>
      <button class="logout-btn" onclick="doLogout()">Sign out</button>
    </div>
  </div>

  <div class="content">
    <!-- Filters -->
    <div class="filters">
      <div class="filter-group">
        <label>Grade</label>
        <select id="gradeFilter">
          <option value="">All Grades</option>
          <option>KG1</option><option>KG2</option>
          <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
          <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
          <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
          <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Payment Status</label>
        <select id="statusFilter">
          <option value="">All Students</option>
          <option value="paid">Paid Only</option>
          <option value="unpaid">Not Paid Only</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Search</label>
        <input type="text" id="searchInput" placeholder="Name or Student ID..." oninput="filterTable()">
      </div>
      <button class="load-btn" onclick="loadStudents()">🔄 Load Students</button>
    </div>

    <!-- Stats -->
    <div class="stats">
      <div class="stat-card"><div class="stat-label">Total Students</div><div class="stat-value" id="statTotal">—</div></div>
      <div class="stat-card"><div class="stat-label">Paid</div><div class="stat-value green" id="statPaid">—</div></div>
      <div class="stat-card"><div class="stat-label">Not Paid</div><div class="stat-value red" id="statUnpaid">—</div></div>
    </div>

    <!-- Save bar -->
    <div class="save-bar" id="saveBar">
      <div class="save-info">⚠️ You have <span id="changeCount">0</span> unsaved changes</div>
      <button class="save-btn" id="saveBtn" onclick="saveChanges()">💾 Save All Changes</button>
    </div>

    <div id="statusMsg" class="status-msg"></div>

    <!-- Table -->
    <div class="table-wrap">
      <div class="table-header">
        <div class="table-title">Students</div>
        <div class="student-count" id="studentCount">Load students to begin</div>
      </div>
      <div id="tableContainer">
        <div class="empty">Click "Load Students" to begin</div>
      </div>
    </div>
  </div>
</div>

<script>
var FINANCE_USERS = {"finance": "finance2026", "finance_admin": "moderninfinity2026"};
var allStudents = [];
var changes = {};

function doLogin(){
  var u = document.getElementById('fu').value.trim();
  var p = document.getElementById('fp').value.trim();
  var err = document.getElementById('fe');
  if(!u||!p){err.textContent='⚠️ Enter username and password';err.style.display='block';return;}
  if(FINANCE_USERS[u] && FINANCE_USERS[u]===p){
    err.style.display='none';
    document.getElementById('login-screen').style.display='none';
    document.getElementById('main-panel').style.display='block';
    loadStudents();
  } else {
    err.textContent='❌ Incorrect username or password';
    err.style.display='block';
    document.getElementById('fp').value='';
  }
}

function doLogout(){
  allStudents=[]; changes={};
  document.getElementById('main-panel').style.display='none';
  document.getElementById('login-screen').style.display='flex';
  document.getElementById('fu').value='';
  document.getElementById('fp').value='';
}

async function loadStudents(){
  var btn = document.querySelector('.load-btn');
  btn.textContent='⏳ Loading...'; btn.disabled=true;
  changes={};
  updateSaveBar();
  try{
    var grade = document.getElementById('gradeFilter').value;
    var url = '/api/finance/students' + (grade ? '?grade='+encodeURIComponent(grade) : '');
    var res = await fetch(url);
    var data = await res.json();
    allStudents = data.students || [];
    renderTable(allStudents);
    updateStats(allStudents);
    document.getElementById('statusMsg').style.display='none';
  } catch(e){
    showStatus('❌ Error loading students: '+e.message, 'error');
  }
  btn.textContent='🔄 Load Students'; btn.disabled=false;
}

function renderTable(students){
  var status = document.getElementById('statusFilter').value;
  var search = document.getElementById('searchInput').value.toLowerCase();
  var filtered = students.filter(function(s){
    var matchStatus = !status ||
      (status==='paid' && (s.payment_status||'').toLowerCase()==='paid') ||
      (status==='unpaid' && (s.payment_status||'').toLowerCase()!=='paid');
    var matchSearch = !search ||
      (s.name||'').toLowerCase().includes(search) ||
      (s.id||'').toLowerCase().includes(search);
    return matchStatus && matchSearch;
  });
  document.getElementById('studentCount').textContent = filtered.length + ' students shown';
  if(!filtered.length){
    document.getElementById('tableContainer').innerHTML='<div class="empty">No students match your filters</div>';
    return;
  }
  var rows = filtered.map(function(s, i){
    var isPaid = (changes[s.row_index] !== undefined)
      ? changes[s.row_index]==='paid'
      : (s.payment_status||'').toLowerCase()==='paid';
    var label = isPaid
      ? '<span class="toggle-label paid">✅ Paid</span>'
      : '<span class="toggle-label unpaid">❌ Not Paid</span>';
    return '<tr>'+
      '<td style="font-weight:600">'+s.name+'</td>'+
      '<td style="color:#64748b;font-size:12px">'+s.id+'</td>'+
      '<td><span class="grade-badge">'+s.grade+'</span></td>'+
      '<td>'+
        '<div class="toggle-wrap">'+
          '<label class="toggle">'+
            '<input type="checkbox" '+(isPaid?'checked':'')+' onchange="togglePayment(this,'+s.row_index+')">'+
            '<span class="slider"></span>'+
          '</label>'+
          '<span id="lbl-'+s.row_index+'">'+label+'</span>'+
        '</div>'+
      '</td>'+
    '</tr>';
  }).join('');
  document.getElementById('tableContainer').innerHTML =
    '<table><thead><tr><th>Student Name</th><th>ID</th><th>Grade</th><th>Payment Status</th></tr></thead>'+
    '<tbody>'+rows+'</tbody></table>';
}

function filterTable(){ renderTable(allStudents); }

function togglePayment(checkbox, rowIndex){
  var isPaid = checkbox.checked;
  changes[rowIndex] = isPaid ? 'paid' : 'unpaid';
  var lbl = document.getElementById('lbl-'+rowIndex);
  if(lbl) lbl.innerHTML = isPaid
    ? '<span class="toggle-label paid">✅ Paid</span>'
    : '<span class="toggle-label unpaid">❌ Not Paid</span>';
  updateSaveBar();
  updateStats(allStudents);
}

function updateSaveBar(){
  var count = Object.keys(changes).length;
  document.getElementById('changeCount').textContent = count;
  document.getElementById('saveBar').className = count > 0 ? 'save-bar show' : 'save-bar';
}

function updateStats(students){
  var total = students.length;
  var paid = students.filter(function(s){
    var status = (changes[s.row_index] !== undefined) ? changes[s.row_index] : (s.payment_status||'').toLowerCase();
    return status === 'paid';
  }).length;
  document.getElementById('statTotal').textContent = total;
  document.getElementById('statPaid').textContent = paid;
  document.getElementById('statUnpaid').textContent = total - paid;
}

async function saveChanges(){
  var btn = document.getElementById('saveBtn');
  btn.disabled=true; btn.textContent='⏳ Saving...';
  try{
    var res = await fetch('/api/finance/update-payment', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({changes: changes})
    });
    var data = await res.json();
    if(data.ok){
      showStatus('✅ Saved successfully — '+data.updated+' students updated', 'success');
      // Update local student data
      Object.keys(changes).forEach(function(rowIndex){
        var s = allStudents.find(function(x){ return x.row_index == rowIndex; });
        if(s) s.payment_status = changes[rowIndex];
      });
      changes = {};
      updateSaveBar();
      updateStats(allStudents);
      renderTable(allStudents);
    } else {
      showStatus('❌ Error: '+data.error, 'error');
    }
  } catch(e){
    showStatus('❌ Network error: '+e.message, 'error');
  }
  btn.disabled=false; btn.textContent='💾 Save All Changes';
}

function showStatus(msg, type){
  var el = document.getElementById('statusMsg');
  el.textContent = msg;
  el.className = 'status-msg '+type;
  setTimeout(function(){ el.style.display='none'; }, 5000);
}
</script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route('/api/finance/students')
def finance_students():
    """Return all students with their payment status and sheet row index."""
    try:
        grade = request.args.get("grade", "").strip()
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet("Students")
        values = ws.get_all_values()
        if not values:
            return jsonify({"students": []})
        headers = [h.strip() for h in values[0]]
        students = []
        for i, row in enumerate(values[1:], start=2):  # row index in sheet (1-based, +1 for header)
            d = {headers[j]: row[j] if j < len(row) else "" for j in range(len(headers))}
            if not str(d.get("Student ID", "")).strip():
                continue
            student_grade = str(d.get("Grade", "")).strip()
            if grade and grade.lower() not in student_grade.lower():
                continue
            if str(d.get("Active", "yes")).strip().lower() != "yes":
                continue
            payment_col = next((h for h in headers if "payment" in h.lower() and "status" in h.lower()), "Payment Status")
            students.append({
                "id": str(d.get("Student ID", "")).strip(),
                "name": str(d.get("Full Name", d.get("Student Name", ""))).strip(),
                "grade": student_grade,
                "payment_status": str(d.get(payment_col, "")).strip(),
                "row_index": i
            })
        students.sort(key=lambda x: (x["grade"], x["name"]))
        return jsonify({"students": students, "count": len(students)})
    except Exception as e:
        logger.error(f"[finance/students] {e}")
        return jsonify({"students": [], "error": str(e)}), 500


@app.route('/api/finance/update-payment', methods=['POST'])
def finance_update_payment():
    """Update Payment Status column for multiple students by row index."""
    data = request.get_json(force=True, silent=True) or {}
    changes = data.get("changes", {})
    if not changes:
        return jsonify({"ok": False, "error": "No changes provided"}), 400
    try:
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet("Students")
        headers = [h.strip() for h in ws.row_values(1)]
        # Find or create Payment Status column
        payment_col_name = "Payment Status"
        if payment_col_name in headers:
            col_idx = headers.index(payment_col_name) + 1  # 1-based
        else:
            # Add new column
            col_idx = len(headers) + 1
            ws.update_cell(1, col_idx, payment_col_name)
            logger.info(f"[finance] Created Payment Status column at col {col_idx}")
        updated = 0
        for row_index, status in changes.items():
            try:
                row_int = int(row_index)
                value = "Paid" if str(status).lower() == "paid" else "Not Paid"
                ws.update_cell(row_int, col_idx, value)
                updated += 1
                import time as _t; _t.sleep(0.1)
            except Exception as e2:
                logger.error(f"[finance] row {row_index}: {e2}")
        logger.info(f"[finance] Updated {updated} students")
        return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        logger.error(f"[finance/update] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route('/sheet-audit')
def sheet_audit():
    """Temporary: read all tabs and return their headers and row counts."""
    try:
        wb = get_client().open_by_key(SHEET_ID)
        result = {}
        for ws in wb.worksheets():
            values = ws.get_all_values()
            headers = values[0] if values else []
            row_count = len(values) - 1 if len(values) > 1 else 0
            result[ws.title] = {
                "headers": headers,
                "rows": row_count,
                "total_columns": len(headers)
            }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/portal')
@app.route('/portal/')
def unified_portal():
    import base64 as b64
    h = b64.b64decode(
        'PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPWRldmljZS13aWR0aCxpbml0aWFsLXNjYWxlPTEuMCI+Cjx0aXRsZT5Nb2Rlcm4gSW5maW5pdHkgU2Nob29sICYjODIxMjsgU3RhZmYgUG9ydGFsPC90aXRsZT4KPHN0eWxlPgoqe2JveC1zaXppbmc6Ym9yZGVyLWJveDttYXJnaW46MDtwYWRkaW5nOjB9CiNwb3J0YWwtbG9naW57ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO21pbi1oZWlnaHQ6MTAwdmg7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLCMwRjFDMkUsIzFhM2EyYSl9Ci5sYm94e2JhY2tncm91bmQ6I2ZmZjtib3JkZXItcmFkaXVzOjE2cHg7cGFkZGluZzo0MHB4IDM2cHg7d2lkdGg6MTAwJTttYXgtd2lkdGg6NDIwcHg7Ym94LXNoYWRvdzowIDIwcHggNjBweCByZ2JhKDAsMCwwLC40KX0KLmxpY297dGV4dC1hbGlnbjpjZW50ZXI7Zm9udC1zaXplOjQ4cHg7bWFyZ2luLWJvdHRvbTo4cHh9Ci5sYm94IGgxe3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6ZToyMnB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjojMEYxQzJFfQoubGJveCBwe3RleHQtYWxpZ246Y2VudGVyO2ZvbnQtc2l6ZToxM3B4O2NvbG9yOiM2NDc0OGI7bWFyZ2luOjRweCAwIDI0cHh9Ci5sZnttYXJnaW4tYm90dG9tOjE2cHh9Ci5sZiBsYWJlbHtkaXNwbGF5OmJsb2NrO2ZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjojNDc1NTY5O3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtsZXR0ZXItc3BhY2luZzouMDVlbTttYXJnaW4tYm90dG9tOjZweH0KLmxmIGlucHV0e3dpZHRoOjEwMCU7cGFkZGluZzoxM3B4IDE0cHg7Ym9yZGVyOjJweCBzb2xpZCAjZTJlOGYwO2JvcmRlci1yYWRpdXM6OXB4O2ZvbnQtc2l6ZToxNXB4O291dGxpbmU6bm9uZTtiYWNrZ3JvdW5kOiNmOGZhZmM7dHJhbnNpdGlvbjpib3JkZXItY29sb3IgLjJzfQoubGYgaW5wdXQ6Zm9jdXN7Ym9yZGVyLWNvbG9yOiMwMEM4Qzg7YmFja2dyb3VuZDojZmZmfQojcHNpZ25pbnt3aWR0aDoxMDAlO3BhZGRpbmc6MTRweDtiYWNrZ3JvdW5kOiMwRjFDMkU7Y29sb3I6I2ZmZjtib3JkZXI6bm9uZTtib3JkZXItcmFkaXVzOjlweDtmb250LXNpemU6MTVweDtmb250LXdlaWdodDo3MDA7Y3Vyc29yOnBvaW50ZXJ9CiNwc2lnbmluOmhvdmVye29wYWNpdHk6Ljg1fQojcHNpZ25pbjpkaXNhYmxlZHtvcGFjaXR5Oi42O2N1cnNvcjpub3QtYWxsb3dlZH0KI3BlcnJ7ZGlzcGxheTpub25lO2JhY2tncm91bmQ6I2ZlZTJlMjtjb2xvcjojZGMyNjI2O2JvcmRlci1yYWRpdXM6OHB4O3BhZGRpbmc6MTBweCAxNHB4O2ZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjYwMDttYXJnaW4tdG9wOjE0cHg7dGV4dC1hbGlnbjpjZW50ZXJ9CiNwYW5lbC1ob3N0e2Rpc3BsYXk6bm9uZTt3aWR0aDoxMDAlO21pbi1oZWlnaHQ6MTAwdmh9Ci5wYW5le2Rpc3BsYXk6bm9uZX0KLnBhbmUub257ZGlzcGxheTpibG9ja30KPC9zdHlsZT4KPHN0eWxlPgoqe2JveC1zaXppbmc6Ym9yZGVyLWJveDttYXJnaW46MDtwYWRkaW5nOjB9Cjpyb290ey0tZzojMjVEMzY2Oy0tZzI6IzEyOEM3RTstLW46IzBGMUMyRTstLWI6I2UyZThmMDstLXI6I2VmNDQ0NDstLWFtYmVyOiNmNTllMGI7LS1ibHVlOiMzYjgyZjZ9CmJvZHl7Zm9udC1mYW1pbHk6J0ludGVyJyxzYW5zLXNlcmlmO2JhY2tncm91bmQ6I2Y0ZjZmODtjb2xvcjojMWEyMDJjO21pbi1oZWlnaHQ6MTAwdmh9CgovKiDilIDilIAgTE9HSU4g4pSA4pSAICovCi5sd3tkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7bWluLWhlaWdodDoxMDB2aDtwYWRkaW5nOjI0cHg7YmFja2dyb3VuZDpsaW5lYXItZ3JhZGllbnQoMTM1ZGVnLCMwRjFDMkUgMCUsIzFhM2EyYSAxMDAlKX0KLmxje2JhY2tncm91bmQ6I2ZmZjtib3JkZXItcmFkaXVzOjI0cHg7cGFkZGluZzo0OHB4IDQwcHg7d2lkdGg6MTAwJTttYXgtd2lkdGg6NDQwcHg7Ym94LXNoYWRvdzowIDIwcHggNjBweCByZ2JhKDAsMCwwLC4yNSl9Ci5sbHt0ZXh0LWFsaWduOmNlbnRlcjttYXJnaW4tYm90dG9tOjMycHh9Ci5saXt3aWR0aDo3MnB4O2hlaWdodDo3MnB4O2JvcmRlci1yYWRpdXM6MThweDtiYWNrZ3JvdW5kOnZhcigtLWcpO2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjttYXJnaW46MCBhdXRvIDE2cHg7Zm9udC1zaXplOjMycHh9Ci5sbCBoMXtmb250LXNpemU6MjRweDtmb250LXdlaWdodDo3MDA7Y29sb3I6dmFyKC0tbil9Ci5sbCBwe2ZvbnQtc2l6ZToxNHB4O2NvbG9yOiM2NDc0OGI7bWFyZ2luLXRvcDo0cHh9Ci5yb2xlLWNhcmRze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDMsMWZyKTtnYXA6MTBweDttYXJnaW4tYm90dG9tOjI4cHh9Ci5yb2xlLWNhcmR7Ym9yZGVyOjJweCBzb2xpZCB2YXIoLS1iKTtib3JkZXItcmFkaXVzOjEycHg7cGFkZGluZzoxMnB4IDhweDt0ZXh0LWFsaWduOmNlbnRlcjtjdXJzb3I6cG9pbnRlcjt0cmFuc2l0aW9uOmFsbCAuMnN9Ci5yb2xlLWNhcmQ6aG92ZXJ7Ym9yZGVyLWNvbG9yOnZhcigtLWcpO2JhY2tncm91bmQ6I2YwZmRmNH0KLnJvbGUtY2FyZC5hY3RpdmV7Ym9yZGVyLWNvbG9yOnZhcigtLWcpO2JhY2tncm91bmQ6I2YwZmRmNDtib3gtc2hhZG93OjAgMCAwIDNweCByZ2JhKDM3LDIxMSwxMDIsLjE1KX0KLnJvbGUtY2FyZCAucml7Zm9udC1zaXplOjIycHg7bWFyZ2luLWJvdHRvbTo2cHh9Ci5yb2xlLWNhcmQgLnJse2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjojMzc0MTUxfQoucm9sZS1jYXJkIC5yc3tmb250LXNpemU6MTBweDtjb2xvcjojNjQ3NDhiO21hcmdpbi10b3A6MnB4fQouZmd7bWFyZ2luLWJvdHRvbToxOHB4fQouZmcgbGFiZWx7ZGlzcGxheTpibG9jaztmb250LXNpemU6MTNweDtmb250LXdlaWdodDo2MDA7Y29sb3I6IzM3NDE1MTttYXJnaW4tYm90dG9tOjhweH0KLmZnIGlucHV0e3dpZHRoOjEwMCU7cGFkZGluZzoxMnB4IDE2cHg7Ym9yZGVyOjEuNXB4IHNvbGlkIHZhcigtLWIpO2JvcmRlci1yYWRpdXM6MTBweDtmb250LXNpemU6MTVweDtmb250LWZhbWlseTppbmhlcml0O291dGxpbmU6bm9uZTt0cmFuc2l0aW9uOmJvcmRlci1jb2xvciAuMnN9Ci5mZyBpbnB1dDpmb2N1c3tib3JkZXItY29sb3I6dmFyKC0tZyl9Ci5idG57d2lkdGg6MTAwJTtwYWRkaW5nOjE0cHg7Ym9yZGVyOm5vbmU7Ym9yZGVyLXJhZGl1czoxMHB4O2ZvbnQtc2l6ZToxNXB4O2ZvbnQtd2VpZ2h0OjYwMDtjdXJzb3I6cG9pbnRlcjtmb250LWZhbWlseTppbmhlcml0O2JhY2tncm91bmQ6dmFyKC0tZyk7Y29sb3I6IzAwMDt0cmFuc2l0aW9uOmFsbCAuMnN9Ci5idG46aG92ZXJ7YmFja2dyb3VuZDp2YXIoLS1nMik7Y29sb3I6I2ZmZn0KLmVycntjb2xvcjp2YXIoLS1yKTtmb250LXNpemU6MTNweDttYXJnaW4tdG9wOjEycHg7dGV4dC1hbGlnbjpjZW50ZXI7ZGlzcGxheTpub25lO3BhZGRpbmc6MTBweDtiYWNrZ3JvdW5kOiNmZWYyZjI7Ym9yZGVyLXJhZGl1czo4cHh9CgovKiDilIDilIAgQURNSU4gQkFER0Ugb24gbG9naW4g4pSA4pSAICovCi5zY29wZS1oaW50e2ZvbnQtc2l6ZToxMXB4O2NvbG9yOiM2NDc0OGI7dGV4dC1hbGlnbjpjZW50ZXI7bWFyZ2luLWJvdHRvbToxNnB4O3BhZGRpbmc6OHB4IDEycHg7YmFja2dyb3VuZDojZjhmYWZjO2JvcmRlci1yYWRpdXM6OHB4O2JvcmRlcjoxcHggc29saWQgdmFyKC0tYil9CgovKiDilIDilIAgQVBQIFdSQVBQRVIg4pSA4pSAICovCi5hd3tkaXNwbGF5Om5vbmU7bWluLWhlaWdodDoxMDB2aH0KCi8qIOKUgOKUgCBTSURFQkFSIOKUgOKUgCAqLwouc2J7d2lkdGg6MjcwcHg7YmFja2dyb3VuZDp2YXIoLS1uKTtwb3NpdGlvbjpmaXhlZDt0b3A6MDtsZWZ0OjA7aGVpZ2h0OjEwMHZoO3BhZGRpbmc6MjRweCAxOHB4O2Rpc3BsYXk6ZmxleDtmbGV4LWRpcmVjdGlvbjpjb2x1bW47ei1pbmRleDoxMDB9Ci5zbHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4O21hcmdpbi1ib3R0b206OHB4fQouc2l7d2lkdGg6NDBweDtoZWlnaHQ6NDBweDtib3JkZXItcmFkaXVzOjEwcHg7YmFja2dyb3VuZDp2YXIoLS1nKTtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7Zm9udC1zaXplOjE4cHg7ZmxleC1zaHJpbms6MH0KLnN0e2NvbG9yOiNmZmY7Zm9udC1zaXplOjE0cHg7Zm9udC13ZWlnaHQ6NjAwO2xpbmUtaGVpZ2h0OjEuM30KLnN0IHNwYW57Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwuNSk7Zm9udC1zaXplOjExcHg7Zm9udC13ZWlnaHQ6NDAwO2Rpc3BsYXk6YmxvY2t9CgovKiBBZG1pbiByb2xlIGJhZGdlIGluIHNpZGViYXIgKi8KLmFkbWluLWJhZGdle21hcmdpbi1ib3R0b206MjRweDttYXJnaW4tdG9wOjRweDtwYWRkaW5nOjhweCAxMnB4O2JvcmRlci1yYWRpdXM6MTBweDtib3JkZXI6MXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsLjEpfQouYWRtaW4tYmFkZ2UgLmFiLWxhYmVse2ZvbnQtc2l6ZToxMXB4O2NvbG9yOnJnYmEoMjU1LDI1NSwyNTUsLjUpO2ZvbnQtd2VpZ2h0OjUwMDttYXJnaW4tYm90dG9tOjNweH0KLmFkbWluLWJhZGdlIC5hYi1uYW1le2ZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjojZmZmfQouYWRtaW4tYmFkZ2UgLmFiLXNjb3Ble2ZvbnQtc2l6ZToxMXB4O21hcmdpbi10b3A6NHB4O3BhZGRpbmc6M3B4IDhweDtib3JkZXItcmFkaXVzOjIwcHg7ZGlzcGxheTppbmxpbmUtYmxvY2s7Zm9udC13ZWlnaHQ6NjAwfQouc2NvcGUtc3VwZXJ7YmFja2dyb3VuZDpyZ2JhKDM3LDIxMSwxMDIsLjIpO2NvbG9yOiM0YWRlODB9Ci5zY29wZS1qdW5pb3J7YmFja2dyb3VuZDpyZ2JhKDU5LDEzMCwyNDYsLjIpO2NvbG9yOiM5M2M1ZmR9Ci5zY29wZS1zZW5pb3J7YmFja2dyb3VuZDpyZ2JhKDI0NSwxNTgsMTEsLjIpO2NvbG9yOiNmY2QzNGR9Cgoubml7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjExcHggMTRweDtib3JkZXItcmFkaXVzOjEwcHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwuNik7Zm9udC1zaXplOjE0cHg7Zm9udC13ZWlnaHQ6NTAwO2N1cnNvcjpwb2ludGVyO21hcmdpbi1ib3R0b206NHB4O3RyYW5zaXRpb246YWxsIC4xNXM7dXNlci1zZWxlY3Q6bm9uZX0KLm5pOmhvdmVyLC5uaS5hY3RpdmV7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LC4xMCk7Y29sb3I6I2ZmZn0KLm5pLmFjdGl2ZXtiYWNrZ3JvdW5kOnZhcigtLWcpO2NvbG9yOiMwMDB9Ci5zYi1ib3R7bWFyZ2luLXRvcDphdXRvfQoubG97ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTBweDtwYWRkaW5nOjExcHggMTRweDtib3JkZXItcmFkaXVzOjEwcHg7Y29sb3I6cmdiYSgyNTUsMjU1LDI1NSwuNSk7Zm9udC1zaXplOjE0cHg7Y3Vyc29yOnBvaW50ZXI7dHJhbnNpdGlvbjphbGwgLjE1c30KLmxvOmhvdmVye2NvbG9yOiNmZmY7YmFja2dyb3VuZDpyZ2JhKDI1NSwyNTUsMjU1LC4wOCl9CgovKiDilIDilIAgTUFJTiDilIDilIAgKi8KLm1ue21hcmdpbi1sZWZ0OjI3MHB4O3BhZGRpbmc6MzJweH0KLnRiMntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVuO21hcmdpbi1ib3R0b206MjhweDtmbGV4LXdyYXA6d3JhcDtnYXA6MTJweH0KLnBnLXR7Zm9udC1zaXplOjI0cHg7Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOnZhcigtLW4pfQoucGctc3tmb250LXNpemU6MTRweDtjb2xvcjojNjQ3NDhiO21hcmdpbi10b3A6MnB4fQoudG9wLXJpZ2h0e2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjEycHg7ZmxleC13cmFwOndyYXB9Ci5zcGx7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NnB4O2JhY2tncm91bmQ6I2RjZmNlNztjb2xvcjojMTU4MDNkO3BhZGRpbmc6NnB4IDE0cHg7Ym9yZGVyLXJhZGl1czoyMHB4O2ZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjUwMH0KLnNwZHt3aWR0aDo3cHg7aGVpZ2h0OjdweDtib3JkZXItcmFkaXVzOjUwJTtiYWNrZ3JvdW5kOiMyMmM1NWU7YW5pbWF0aW9uOnB1IDJzIGluZmluaXRlfQpAa2V5ZnJhbWVzIHB1ezAlLDEwMCV7b3BhY2l0eToxfTUwJXtvcGFjaXR5Oi40fX0KCi8qIHNjb3BlIHRhZyBpbiBoZWFkZXIgKi8KLnNjb3BlLXRhZ3twYWRkaW5nOjVweCAxMnB4O2JvcmRlci1yYWRpdXM6MjBweDtmb250LXNpemU6MTJweDtmb250LXdlaWdodDo2MDB9CgovKiDilIDilIAgU1RBVFMg4pSA4pSAICovCi5zcntkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgzLDFmcik7Z2FwOjIwcHg7bWFyZ2luLWJvdHRvbToyOHB4fQouc2N7YmFja2dyb3VuZDojZmZmO2JvcmRlci1yYWRpdXM6MTZweDtwYWRkaW5nOjI0cHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1iKX0KLnNsMntmb250LXNpemU6MTNweDtjb2xvcjojNjQ3NDhiO2ZvbnQtd2VpZ2h0OjUwMDttYXJnaW4tYm90dG9tOjhweH0KLnN2e2ZvbnQtc2l6ZTozMnB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjp2YXIoLS1uKX0KCi8qIOKUgOKUgCBTRUNUSU9OUyDilIDilIAgKi8KLnNlY3tiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJhZGl1czoxNnB4O3BhZGRpbmc6MjhweDtib3JkZXI6MXB4IHNvbGlkIHZhcigtLWIpO21hcmdpbi1ib3R0b206MjRweH0KLnNlYy10e2ZvbnQtc2l6ZToxNnB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjp2YXIoLS1uKTttYXJnaW4tYm90dG9tOjIwcHg7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6OHB4fQouZmx7Zm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NjAwO2NvbG9yOiMzNzQxNTE7bWFyZ2luLWJvdHRvbTo4cHg7ZGlzcGxheTpibG9ja30KdGV4dGFyZWF7d2lkdGg6MTAwJTtwYWRkaW5nOjE0cHggMTZweDtib3JkZXI6MS41cHggc29saWQgdmFyKC0tYik7Ym9yZGVyLXJhZGl1czoxMHB4O2ZvbnQtc2l6ZToxNHB4O2ZvbnQtZmFtaWx5OmluaGVyaXQ7b3V0bGluZTpub25lO3Jlc2l6ZTp2ZXJ0aWNhbDttaW4taGVpZ2h0OjEyMHB4O3RyYW5zaXRpb246Ym9yZGVyLWNvbG9yIC4yc30KdGV4dGFyZWE6Zm9jdXN7Ym9yZGVyLWNvbG9yOnZhcigtLWcpfQoucGhvdG8tem9uZXtib3JkZXI6MnB4IGRhc2hlZCB2YXIoLS1iKTtib3JkZXItcmFkaXVzOjEwcHg7cGFkZGluZzoxOHB4O3RleHQtYWxpZ246Y2VudGVyO2N1cnNvcjpwb2ludGVyO3RyYW5zaXRpb246LjJzO21hcmdpbjoxMHB4IDA7cG9zaXRpb246cmVsYXRpdmU7fQoucGhvdG8tem9uZTpob3Zlcntib3JkZXItY29sb3I6dmFyKC0tZyk7YmFja2dyb3VuZDojZjBmZGY0O30KLnBob3RvLXpvbmUuaGFzLWltZ3tib3JkZXItY29sb3I6dmFyKC0tZyk7YmFja2dyb3VuZDojZjBmZGY0O30KLnBob3RvLXByZXZpZXd7bWF4LXdpZHRoOjEwMCU7bWF4LWhlaWdodDoyMDBweDtib3JkZXItcmFkaXVzOjhweDttYXJnaW4tdG9wOjEwcHg7ZGlzcGxheTpub25lO30KLnJlbW92ZS1waG90b3twb3NpdGlvbjphYnNvbHV0ZTt0b3A6OHB4O3JpZ2h0OjhweDtiYWNrZ3JvdW5kOiNlZjQ0NDQ7Y29sb3I6I2ZmZjtib3JkZXI6bm9uZTtib3JkZXItcmFkaXVzOjUwJTt3aWR0aDoyNHB4O2hlaWdodDoyNHB4O2N1cnNvcjpwb2ludGVyO2ZvbnQtc2l6ZToxNHB4O2xpbmUtaGVpZ2h0OjE7ZGlzcGxheTpub25lO30KLmNje2ZvbnQtc2l6ZToxMnB4O2NvbG9yOiM5NGEzYjg7dGV4dC1hbGlnbjpyaWdodDttYXJnaW4tdG9wOjRweH0KCi8qIOKUgOKUgCBHUkFERSBHUklEIOKUgOKUgCAqLwouZ2d7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczpyZXBlYXQoNCwxZnIpO2dhcDoxMHB4O21hcmdpbi10b3A6OHB4fQouZ2N7cGFkZGluZzoxMHB4O2JvcmRlcjoycHggc29saWQgdmFyKC0tYik7Ym9yZGVyLXJhZGl1czoxMHB4O3RleHQtYWxpZ246Y2VudGVyO2N1cnNvcjpwb2ludGVyO2ZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjUwMDtjb2xvcjojNjQ3NDhiO3RyYW5zaXRpb246YWxsIC4xNXM7dXNlci1zZWxlY3Q6bm9uZX0KLmdjOmhvdmVyOm5vdCgubG9ja2VkKXtib3JkZXItY29sb3I6dmFyKC0tZyk7Y29sb3I6dmFyKC0tZyl9Ci5nYy5zZWx7Ym9yZGVyLWNvbG9yOnZhcigtLWcpO2JhY2tncm91bmQ6I2YwZmRmNDtjb2xvcjojMTU4MDNkO2ZvbnQtd2VpZ2h0OjYwMH0KLmdjLmFsbHtncmlkLWNvbHVtbjoxLy0xO2JhY2tncm91bmQ6dmFyKC0tbik7Ym9yZGVyLWNvbG9yOnZhcigtLW4pO2NvbG9yOiNmZmZ9Ci5nYy5hbGwuc2Vse2JhY2tncm91bmQ6dmFyKC0tZyk7Ym9yZGVyLWNvbG9yOnZhcigtLWcpO2NvbG9yOiMwMDB9Ci5nYy5sb2NrZWR7b3BhY2l0eTouMzU7Y3Vyc29yOm5vdC1hbGxvd2VkO2JhY2tncm91bmQ6I2Y4ZmFmY30KCi8qIOKUgOKUgCBQUkVWSUVXIOKUgOKUgCAqLwoucGJ7YmFja2dyb3VuZDojZTVkZGQ1O2JvcmRlci1yYWRpdXM6MTJweDtwYWRkaW5nOjE2cHg7bWFyZ2luLXRvcDoxNnB4fQoucGItbHtmb250LXNpemU6MTFweDtjb2xvcjojNjQ3NDhiO21hcmdpbi1ib3R0b206MTBweH0KLnBiLWJ7YmFja2dyb3VuZDojZmZmO2JvcmRlci1yYWRpdXM6OHB4IDhweCA4cHggMnB4O3BhZGRpbmc6MTBweCAxNHB4O2Rpc3BsYXk6aW5saW5lLWJsb2NrO21heC13aWR0aDo4NSU7Zm9udC1zaXplOjE0cHg7bGluZS1oZWlnaHQ6MS41NTt3aGl0ZS1zcGFjZTpwcmUtd3JhcH0KLnBiLXR7Zm9udC1zaXplOjExcHg7Y29sb3I6cmdiYSgwLDAsMCwuNCk7dGV4dC1hbGlnbjpyaWdodDttYXJnaW4tdG9wOjRweH0KLnNyMntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxNnB4O21hcmdpbi10b3A6MjRweDtmbGV4LXdyYXA6d3JhcH0KLnNuZHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDo4cHg7cGFkZGluZzoxNHB4IDI4cHg7YmFja2dyb3VuZDp2YXIoLS1nKTtjb2xvcjojMDAwO2JvcmRlcjpub25lO2JvcmRlci1yYWRpdXM6MTJweDtmb250LXNpemU6MTVweDtmb250LXdlaWdodDo3MDA7Y3Vyc29yOnBvaW50ZXI7Zm9udC1mYW1pbHk6aW5oZXJpdDt0cmFuc2l0aW9uOmFsbCAuMnN9Ci5zbmQ6aG92ZXJ7YmFja2dyb3VuZDp2YXIoLS1nMik7Y29sb3I6I2ZmZn0KLnNuZDpkaXNhYmxlZHtiYWNrZ3JvdW5kOiM5NGEzYjg7Y29sb3I6I2ZmZjtjdXJzb3I6bm90LWFsbG93ZWR9Ci5yY3tmb250LXNpemU6MTRweDtjb2xvcjojNjQ3NDhifQoucmMgc3Ryb25ne2NvbG9yOnZhcigtLW4pfQoKLyog4pSA4pSAIEhJU1RPUlkg4pSA4pSAICovCi5oaXtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6ZmxleC1zdGFydDtnYXA6MTZweDtwYWRkaW5nOjE2cHggMDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIoLS1iKX0KLmhpOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTpub25lfQouaGlje2ZsZXg6MTtmb250LXNpemU6MTRweDtsaW5lLWhlaWdodDoxLjV9Ci5obXtmb250LXNpemU6MTJweDtjb2xvcjojOTRhM2I4O21hcmdpbi10b3A6NHB4fQouaGJ7Zm9udC1zaXplOjExcHg7Zm9udC13ZWlnaHQ6NjAwO3BhZGRpbmc6M3B4IDEwcHg7Ym9yZGVyLXJhZGl1czoyMHB4O2JhY2tncm91bmQ6I2RjZmNlNztjb2xvcjojMTU4MDNkfQoKLyog4pSA4pSAIE1PREFMIOKUgOKUgCAqLwoubW97cG9zaXRpb246Zml4ZWQ7aW5zZXQ6MDtiYWNrZ3JvdW5kOnJnYmEoMCwwLDAsLjUpO2Rpc3BsYXk6bm9uZTthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjt6LWluZGV4OjEwMDA7cGFkZGluZzoyMHB4fQoubW8uc2hvd3tkaXNwbGF5OmZsZXh9Ci5tZHtiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJhZGl1czoyMHB4O3BhZGRpbmc6NDBweDttYXgtd2lkdGg6NDQwcHg7d2lkdGg6MTAwJTt0ZXh0LWFsaWduOmNlbnRlcn0KLm1iMntwYWRkaW5nOjEycHggMzJweDtiYWNrZ3JvdW5kOnZhcigtLWcpO2JvcmRlcjpub25lO2JvcmRlci1yYWRpdXM6MTBweDtmb250LXNpemU6MTVweDtmb250LXdlaWdodDo2MDA7Y3Vyc29yOnBvaW50ZXI7Zm9udC1mYW1pbHk6aW5oZXJpdDtjb2xvcjojMDAwfQoKLyog4pSA4pSAIFRBQkxFIOKUgOKUgCAqLwoucHRhYmxle3dpZHRoOjEwMCU7Ym9yZGVyLWNvbGxhcHNlOmNvbGxhcHNlO2ZvbnQtc2l6ZToxNHB4fQoucHRhYmxlIHRoe3RleHQtYWxpZ246bGVmdDtwYWRkaW5nOjEwcHggOHB4O2JvcmRlci1ib3R0b206MnB4IHNvbGlkIHZhcigtLWIpO2ZvbnQtc2l6ZToxMnB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjojNjQ3NDhiO3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtsZXR0ZXItc3BhY2luZzouNXB4fQoucHRhYmxlIHRke3BhZGRpbmc6MTFweCA4cHg7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgI2YxZjVmOX0KLnB0YWJsZSB0cjpsYXN0LWNoaWxkIHRke2JvcmRlci1ib3R0b206bm9uZX0KLmdyYWRlLXBpbGx7ZGlzcGxheTppbmxpbmUtYmxvY2s7cGFkZGluZzoycHggMTBweDtib3JkZXItcmFkaXVzOjIwcHg7Zm9udC1zaXplOjExcHg7Zm9udC13ZWlnaHQ6NjAwfQouZy1qdW5pb3J7YmFja2dyb3VuZDojZGJlYWZlO2NvbG9yOiMxZDRlZDh9Ci5nLXNlbmlvcntiYWNrZ3JvdW5kOiNmZWYzYzc7Y29sb3I6IzkyNDAwZX0KLmcta2d7YmFja2dyb3VuZDojZjNlOGZmO2NvbG9yOiM3ZTIyY2V9CgpAbWVkaWEobWF4LXdpZHRoOjc2OHB4KXsuc2J7ZGlzcGxheTpub25lfS5tbnttYXJnaW4tbGVmdDowO3BhZGRpbmc6MTZweH0uc3J7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjFmcn0uZ2d7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdCgyLDFmcil9fQo8L3N0eWxlPgo8c3R5bGU+CiogeyBib3gtc2l6aW5nOiBib3JkZXItYm94OyBtYXJnaW46IDA7IHBhZGRpbmc6IDA7IH0KYm9keSB7IGZvbnQtZmFtaWx5OiAnU2Vnb2UgVUknLCBBcmlhbCwgc2Fucy1zZXJpZjsgYmFja2dyb3VuZDogI2YwZjRmODsgbWluLWhlaWdodDogMTAwdmg7IHBhZGRpbmc6IDIwcHggMTZweDsgfQoKLyog4pSA4pSAIExvZ2luIHNjcmVlbiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KI2xvZ2luLXNjcmVlbiB7CiAgZGlzcGxheTogZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsganVzdGlmeS1jb250ZW50OiBjZW50ZXI7CiAgbWluLWhlaWdodDogMTAwdmg7IG1hcmdpbjogLTIwcHggLTE2cHg7CiAgYmFja2dyb3VuZDogbGluZWFyLWdyYWRpZW50KDEzNWRlZywgIzBGMUMyRSAwJSwgIzFFM0E1RiAxMDAlKTsKfQoubG9naW4tYm94IHsKICBiYWNrZ3JvdW5kOiAjZmZmOyBib3JkZXItcmFkaXVzOiAxNnB4OyBwYWRkaW5nOiA0MHB4IDM2cHg7CiAgd2lkdGg6IDEwMCU7IG1heC13aWR0aDogNDIwcHg7IGJveC1zaGFkb3c6IDAgMjBweCA2MHB4IHJnYmEoMCwwLDAsMC4zKTsKfQoubG9naW4tbG9nbyB7IHRleHQtYWxpZ246IGNlbnRlcjsgbWFyZ2luLWJvdHRvbTogMjhweDsgfQoubG9naW4tbG9nbyAuaWNvbiB7IGZvbnQtc2l6ZTogNDhweDsgfQoubG9naW4tbG9nbyBoMSB7IGZvbnQtc2l6ZTogMjJweDsgZm9udC13ZWlnaHQ6IDcwMDsgY29sb3I6ICMwRjFDMkU7IG1hcmdpbi10b3A6IDEwcHg7IH0KLmxvZ2luLWxvZ28gcCAgeyBmb250LXNpemU6IDEzcHg7IGNvbG9yOiAjNjQ3NDhiOyBtYXJnaW4tdG9wOiA0cHg7IH0KLmxvZ2luLWZpZWxkIHsgbWFyZ2luLWJvdHRvbTogMTZweDsgfQoubG9naW4tZmllbGQgbGFiZWwgeyBkaXNwbGF5OiBibG9jazsgZm9udC1zaXplOiAxMnB4OyBmb250LXdlaWdodDogNzAwOyBjb2xvcjogIzQ3NTU2OTsgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsgbGV0dGVyLXNwYWNpbmc6LjA1ZW07IG1hcmdpbi1ib3R0b206IDZweDsgfQoubG9naW4tZmllbGQgaW5wdXQgewogIHdpZHRoOiAxMDAlOyBwYWRkaW5nOiAxM3B4IDE0cHg7IGJvcmRlcjogMnB4IHNvbGlkICNlMmU4ZjA7CiAgYm9yZGVyLXJhZGl1czogOXB4OyBmb250LXNpemU6IDE1cHg7IGNvbG9yOiAjMWUyOTNiOyBiYWNrZ3JvdW5kOiAjZjhmYWZjOwogIG91dGxpbmU6IG5vbmU7IHRyYW5zaXRpb246IGJvcmRlci1jb2xvciAuMnM7Cn0KLmxvZ2luLWZpZWxkIGlucHV0OmZvY3VzIHsgYm9yZGVyLWNvbG9yOiAjMUQ0RUQ4OyBiYWNrZ3JvdW5kOiAjZmZmOyB9Ci5sb2dpbi1idG4gewogIHdpZHRoOiAxMDAlOyBwYWRkaW5nOiAxNHB4OyBiYWNrZ3JvdW5kOiAjMTZhMzRhOyBjb2xvcjogI2ZmZjsKICBib3JkZXI6IG5vbmU7IGJvcmRlci1yYWRpdXM6IDlweDsgZm9udC1zaXplOiAxNXB4OyBmb250LXdlaWdodDogNzAwOwogIGN1cnNvcjogcG9pbnRlcjsgbWFyZ2luLXRvcDogOHB4OyB0cmFuc2l0aW9uOiBiYWNrZ3JvdW5kIC4yczsKfQoubG9naW4tYnRuOmhvdmVyIHsgYmFja2dyb3VuZDogIzE1ODAzZDsgfQoubG9naW4tZXJyb3IgewogIGJhY2tncm91bmQ6ICNmZWUyZTI7IGNvbG9yOiAjZGMyNjI2OyBib3JkZXItcmFkaXVzOiA4cHg7CiAgcGFkZGluZzogMTBweCAxNHB4OyBmb250LXNpemU6IDEzcHg7IGZvbnQtd2VpZ2h0OiA2MDA7CiAgbWFyZ2luLXRvcDogMTRweDsgdGV4dC1hbGlnbjogY2VudGVyOyBkaXNwbGF5OiBub25lOwp9CgovKiDilIDilIAgTWFpbiBwYW5lbCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KI21haW4tcGFuZWwgeyBkaXNwbGF5OiBub25lOyB9CgoudG9wYmFyIHsKICBiYWNrZ3JvdW5kOiAjMEYxQzJFOyBjb2xvcjogI2ZmZjsKICB3aWR0aDogMTAwJTsgbWF4LXdpZHRoOiA3MjBweDsgbWFyZ2luOiAwIGF1dG87CiAgYm9yZGVyLXJhZGl1czogMTRweCAxNHB4IDAgMDsKICBwYWRkaW5nOiAxNnB4IDI0cHg7CiAgZGlzcGxheTogZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsganVzdGlmeS1jb250ZW50OiBzcGFjZS1iZXR3ZWVuOwp9Ci50b3BiYXItbGVmdCB7IGRpc3BsYXk6IGZsZXg7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGdhcDogMTJweDsgfQoudG9wYmFyLWxlZnQgLmljb24geyBmb250LXNpemU6IDI2cHg7IH0KLnRvcGJhci1sZWZ0IGgxIHsgZm9udC1zaXplOiAxNnB4OyBmb250LXdlaWdodDogNzAwOyB9Ci50b3BiYXItbGVmdCBwICB7IGZvbnQtc2l6ZTogMTFweDsgb3BhY2l0eTogMC42OyBtYXJnaW4tdG9wOiAycHg7IH0KLnRvcGJhci1yaWdodCB7IGRpc3BsYXk6IGZsZXg7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGdhcDogMTJweDsgfQoudGVhY2hlci1iYWRnZSB7CiAgYmFja2dyb3VuZDogcmdiYSgzNCwxOTcsOTQsMC4yKTsgY29sb3I6ICM4NmVmYWM7CiAgZm9udC1zaXplOiAxMXB4OyBmb250LXdlaWdodDogNzAwOyBwYWRkaW5nOiA0cHggMTBweDsKICBib3JkZXItcmFkaXVzOiAyMHB4OyBib3JkZXI6IDFweCBzb2xpZCByZ2JhKDM0LDE5Nyw5NCwwLjMpOwp9Ci5sb2dvdXQtYnRuIHsKICBiYWNrZ3JvdW5kOiByZ2JhKDI1NSwyNTUsMjU1LDAuMSk7IGNvbG9yOiAjZmZmOwogIGJvcmRlcjogMXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4yKTsgYm9yZGVyLXJhZGl1czogNnB4OwogIHBhZGRpbmc6IDVweCAxMnB4OyBmb250LXNpemU6IDEycHg7IGN1cnNvcjogcG9pbnRlcjsKICB0cmFuc2l0aW9uOiBiYWNrZ3JvdW5kIC4yczsKfQoubG9nb3V0LWJ0bjpob3ZlciB7IGJhY2tncm91bmQ6IHJnYmEoMjU1LDI1NSwyNTUsMC4yKTsgfQoKLnRhYnMgewogIGJhY2tncm91bmQ6ICMxRTNBNUY7CiAgd2lkdGg6IDEwMCU7IG1heC13aWR0aDogNzIwcHg7IG1hcmdpbjogMCBhdXRvOwogIGRpc3BsYXk6IGZsZXg7Cn0KLnRhYi1idG4gewogIGZsZXg6IDE7IHBhZGRpbmc6IDEzcHg7IGJvcmRlcjogbm9uZTsgY3Vyc29yOiBwb2ludGVyOwogIGZvbnQtc2l6ZTogMTRweDsgZm9udC13ZWlnaHQ6IDYwMDsgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsMC42KTsKICBiYWNrZ3JvdW5kOiB0cmFuc3BhcmVudDsgdHJhbnNpdGlvbjogLjJzOwp9Ci50YWItYnRuLmFjdGl2ZSB7IGNvbG9yOiAjZmZmOyBiYWNrZ3JvdW5kOiAjMEYxQzJFOyBib3JkZXItYm90dG9tOiAzcHggc29saWQgIzIyYzU1ZTsgfQoudGFiLWJ0bjpob3Zlcjpub3QoLmFjdGl2ZSkgeyBjb2xvcjogI2ZmZjsgYmFja2dyb3VuZDogcmdiYSgyNTUsMjU1LDI1NSwwLjA4KTsgfQoKLmNhcmQgewogIGJhY2tncm91bmQ6ICNmZmY7IHdpZHRoOiAxMDAlOyBtYXgtd2lkdGg6IDcyMHB4OwogIG1hcmdpbjogMCBhdXRvOyBib3JkZXItcmFkaXVzOiAwIDAgMTRweCAxNHB4OwogIHBhZGRpbmc6IDI0cHggMjhweDsgYm94LXNoYWRvdzogMCA0cHggMjRweCByZ2JhKDAsMCwwLDAuMDgpOwp9CgoudGFiLXBhbmVsIHsgZGlzcGxheTogbm9uZTsgfQoudGFiLXBhbmVsLmFjdGl2ZSB7IGRpc3BsYXk6IGJsb2NrOyB9CgovKiDilIDilIAgRmllbGRzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwouZmllbGQgeyBtYXJnaW4tYm90dG9tOiAxNnB4OyB9Ci5maWVsZCBsYWJlbCB7CiAgZGlzcGxheTogYmxvY2s7IGZvbnQtc2l6ZTogMTFweDsgZm9udC13ZWlnaHQ6IDcwMDsKICBjb2xvcjogIzQ3NTU2OTsgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsKICBsZXR0ZXItc3BhY2luZzogLjA1ZW07IG1hcmdpbi1ib3R0b206IDVweDsKfQouZmllbGQgbGFiZWwgc3BhbiB7IGNvbG9yOiAjZTUzZTNlOyB9CmlucHV0W3R5cGU9dGV4dF0sIGlucHV0W3R5cGU9bnVtYmVyXSwgaW5wdXRbdHlwZT1kYXRlXSwgc2VsZWN0LCB0ZXh0YXJlYSB7CiAgd2lkdGg6IDEwMCU7IHBhZGRpbmc6IDExcHggMTNweDsKICBib3JkZXI6IDJweCBzb2xpZCAjZTJlOGYwOyBib3JkZXItcmFkaXVzOiA4cHg7CiAgZm9udC1zaXplOiAxNHB4OyBjb2xvcjogIzFlMjkzYjsgYmFja2dyb3VuZDogI2Y4ZmFmYzsKICB0cmFuc2l0aW9uOiBib3JkZXItY29sb3IgLjJzOyBvdXRsaW5lOiBub25lOwp9CmlucHV0OmZvY3VzLCBzZWxlY3Q6Zm9jdXMsIHRleHRhcmVhOmZvY3VzIHsKICBib3JkZXItY29sb3I6ICMxRDRFRDg7IGJhY2tncm91bmQ6ICNmZmY7CiAgYm94LXNoYWRvdzogMCAwIDAgM3B4IHJnYmEoMjksNzgsMjE2LC4xKTsKfQp0ZXh0YXJlYSB7IHJlc2l6ZTogdmVydGljYWw7IG1pbi1oZWlnaHQ6IDcwcHg7IH0KLnJvdy0yIHsgZGlzcGxheTogZ3JpZDsgZ3JpZC10ZW1wbGF0ZS1jb2x1bW5zOiAxZnIgMWZyOyBnYXA6IDE0cHg7IH0KLnJvdy0zIHsgZGlzcGxheTogZ3JpZDsgZ3JpZC10ZW1wbGF0ZS1jb2x1bW5zOiAxZnIgMWZyIDFmcjsgZ2FwOiAxMnB4OyB9CkBtZWRpYSAobWF4LXdpZHRoOiA1NDBweCkgewogIC5yb3ctMiB7IGdyaWQtdGVtcGxhdGUtY29sdW1uczogMWZyOyB9CiAgLnJvdy0zIHsgZ3JpZC10ZW1wbGF0ZS1jb2x1bW5zOiAxZnI7IH0KfQoKLnN1Ym1pdC1idG4gewogIHdpZHRoOiAxMDAlOyBwYWRkaW5nOiAxNXB4OyBiYWNrZ3JvdW5kOiAjMTZhMzRhOyBjb2xvcjogI2ZmZjsKICBib3JkZXI6IG5vbmU7IGJvcmRlci1yYWRpdXM6IDEwcHg7IGZvbnQtc2l6ZTogMTVweDsgZm9udC13ZWlnaHQ6IDcwMDsKICBjdXJzb3I6IHBvaW50ZXI7IG1hcmdpbi10b3A6IDhweDsgdHJhbnNpdGlvbjogYmFja2dyb3VuZCAuMnM7Cn0KLnN1Ym1pdC1idG46aG92ZXIgeyBiYWNrZ3JvdW5kOiAjMTU4MDNkOyB9Ci5zdWJtaXQtYnRuOmRpc2FibGVkIHsgYmFja2dyb3VuZDogIzg2ZWZhYzsgY3Vyc29yOiBub3QtYWxsb3dlZDsgfQoKLnN0YXR1cyB7CiAgbWFyZ2luLXRvcDogMTRweDsgcGFkZGluZzogMTJweCAxNnB4OyBib3JkZXItcmFkaXVzOiA4cHg7CiAgZm9udC1zaXplOiAxM3B4OyBmb250LXdlaWdodDogNjAwOyB0ZXh0LWFsaWduOiBjZW50ZXI7IGRpc3BsYXk6IG5vbmU7Cn0KLnN0YXR1cy5zdWNjZXNzIHsgYmFja2dyb3VuZDogI2RjZmNlNzsgY29sb3I6ICMxNTgwM2Q7IGRpc3BsYXk6IGJsb2NrOyB9Ci5zdGF0dXMuZXJyb3IgICB7IGJhY2tncm91bmQ6ICNmZWUyZTI7IGNvbG9yOiAjZGMyNjI2OyBkaXNwbGF5OiBibG9jazsgfQoKLmhpc3RvcnkgeyBtYXJnaW4tdG9wOiAyNHB4OyB9Ci5oaXN0b3J5IGgzIHsgZm9udC1zaXplOiAxMXB4OyBmb250LXdlaWdodDogNzAwOyBjb2xvcjogIzY0NzQ4YjsgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsgbGV0dGVyLXNwYWNpbmc6LjA1ZW07IG1hcmdpbi1ib3R0b206MTBweDsgfQouaGlzdC1pdGVtIHsKICBiYWNrZ3JvdW5kOiAjZjhmYWZjOyBib3JkZXI6IDFweCBzb2xpZCAjZTJlOGYwOwogIGJvcmRlci1sZWZ0OiA0cHggc29saWQgIzFENEVEODsgYm9yZGVyLXJhZGl1czogNnB4OwogIHBhZGRpbmc6IDlweCAxM3B4OyBtYXJnaW4tYm90dG9tOiA3cHg7IGZvbnQtc2l6ZTogMTJweDsgY29sb3I6ICMzMzQxNTU7Cn0KLmhpc3QtaXRlbSBzdHJvbmcgeyBjb2xvcjogIzBGMUMyRTsgfQouaGlzdC1pdGVtIC5tZXRhIHsgY29sb3I6ICM5NGEzYjg7IGZvbnQtc2l6ZTogMTFweDsgbWFyZ2luLXRvcDogMnB4OyB9Ci5uby1oaXN0IHsgY29sb3I6ICM5NGEzYjg7IGZvbnQtc2l6ZTogMTNweDsgdGV4dC1hbGlnbjogY2VudGVyOyBwYWRkaW5nOiAxMnB4OyB9CgovKiDilIDilIAgRXhhbSB0YWJsZSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KLmxvYWQtYnRuIHsKICB3aWR0aDogMTAwJTsgcGFkZGluZzogMTJweDsgYmFja2dyb3VuZDogIzFENEVEODsgY29sb3I6ICNmZmY7CiAgYm9yZGVyOiBub25lOyBib3JkZXItcmFkaXVzOiA4cHg7IGZvbnQtc2l6ZTogMTRweDsgZm9udC13ZWlnaHQ6IDYwMDsKICBjdXJzb3I6IHBvaW50ZXI7IG1hcmdpbi10b3A6IDRweDsgdHJhbnNpdGlvbjogYmFja2dyb3VuZCAuMnM7Cn0KLmxvYWQtYnRuOmhvdmVyIHsgYmFja2dyb3VuZDogIzFlNDBhZjsgfQoubG9hZC1idG46ZGlzYWJsZWQgeyBiYWNrZ3JvdW5kOiAjOTNjNWZkOyBjdXJzb3I6IG5vdC1hbGxvd2VkOyB9Cgouc3R1ZGVudC10YWJsZSB7IHdpZHRoOiAxMDAlOyBib3JkZXItY29sbGFwc2U6IGNvbGxhcHNlOyBtYXJnaW4tdG9wOiAxNnB4OyB9Ci5zdHVkZW50LXRhYmxlIHRoIHsKICBiYWNrZ3JvdW5kOiAjMEYxQzJFOyBjb2xvcjogI2ZmZjsgZm9udC1zaXplOiAxMnB4OwogIHBhZGRpbmc6IDEwcHg7IHRleHQtYWxpZ246IGxlZnQ7IGZvbnQtd2VpZ2h0OiA2MDA7Cn0KLnN0dWRlbnQtdGFibGUgdGQgeyBwYWRkaW5nOiA2cHg7IGJvcmRlci1ib3R0b206IDFweCBzb2xpZCAjZjFmNWY5OyB2ZXJ0aWNhbC1hbGlnbjogbWlkZGxlOyB9Ci5zdHVkZW50LXRhYmxlIHRyOmhvdmVyIHRkIHsgYmFja2dyb3VuZDogI2Y4ZmFmYzsgfQouc3R1ZGVudC10YWJsZSBpbnB1dCB7IHBhZGRpbmc6IDdweCA5cHg7IGZvbnQtc2l6ZTogMTNweDsgYm9yZGVyLXJhZGl1czogNnB4OyBib3JkZXI6IDEuNXB4IHNvbGlkICNlMmU4ZjA7IH0KLnN0dWRlbnQtdGFibGUgaW5wdXQ6Zm9jdXMgeyBib3JkZXItY29sb3I6ICMxRDRFRDg7IG91dGxpbmU6IG5vbmU7IH0KLmdyYWRlLWJhZGdlIHsKICBkaXNwbGF5OiBpbmxpbmUtYmxvY2s7IHBhZGRpbmc6IDJweCA4cHg7IGJvcmRlci1yYWRpdXM6IDIwcHg7CiAgZm9udC1zaXplOiAxMXB4OyBmb250LXdlaWdodDogNzAwOyBiYWNrZ3JvdW5kOiAjZTJlOGYwOyBjb2xvcjogIzQ3NTU2OTsKfQouZ3JhZGUtYmFkZ2UuQSB7IGJhY2tncm91bmQ6ICNkY2ZjZTc7IGNvbG9yOiAjMTU4MDNkOyB9Ci5ncmFkZS1iYWRnZS5CIHsgYmFja2dyb3VuZDogI2RiZWFmZTsgY29sb3I6ICMxRDRFRDg7IH0KLmdyYWRlLWJhZGdlLkMgeyBiYWNrZ3JvdW5kOiAjZmVmOWMzOyBjb2xvcjogIzg1NGQwZTsgfQouZ3JhZGUtYmFkZ2UuRCB7IGJhY2tncm91bmQ6ICNmZmVkZDU7IGNvbG9yOiAjYzI0MTBjOyB9Ci5ncmFkZS1iYWRnZS5GIHsgYmFja2dyb3VuZDogI2ZlZTJlMjsgY29sb3I6ICNkYzI2MjY7IH0KCi5zdHVkZW50LWNvdW50IHsgZm9udC1zaXplOiAxMnB4OyBjb2xvcjogIzY0NzQ4YjsgbWFyZ2luLXRvcDogOHB4OyB9Ci5leGFtLXN1Ym1pdC1idG4gewogIHdpZHRoOiAxMDAlOyBwYWRkaW5nOiAxNXB4OyBiYWNrZ3JvdW5kOiAjN2MzYWVkOyBjb2xvcjogI2ZmZjsKICBib3JkZXI6IG5vbmU7IGJvcmRlci1yYWRpdXM6IDEwcHg7IGZvbnQtc2l6ZTogMTVweDsgZm9udC13ZWlnaHQ6IDcwMDsKICBjdXJzb3I6IHBvaW50ZXI7IG1hcmdpbi10b3A6IDE2cHg7IHRyYW5zaXRpb246IGJhY2tncm91bmQgLjJzOwp9Ci5leGFtLXN1Ym1pdC1idG46aG92ZXIgeyBiYWNrZ3JvdW5kOiAjNmQyOGQ5OyB9Ci5leGFtLXN1Ym1pdC1idG46ZGlzYWJsZWQgeyBiYWNrZ3JvdW5kOiAjYzRiNWZkOyBjdXJzb3I6IG5vdC1hbGxvd2VkOyB9Ci5kaXZpZGVyIHsgaGVpZ2h0OiAxcHg7IGJhY2tncm91bmQ6ICNmMWY1Zjk7IG1hcmdpbjogMjBweCAwOyB9Cjwvc3R5bGU+CjxzdHlsZT4KKntib3gtc2l6aW5nOmJvcmRlci1ib3g7bWFyZ2luOjA7cGFkZGluZzowfQpib2R5e2ZvbnQtZmFtaWx5OidTZWdvZSBVSScsQXJpYWwsc2Fucy1zZXJpZjtiYWNrZ3JvdW5kOiNmMGY0Zjg7bWluLWhlaWdodDoxMDB2aH0KLyogTE9HSU4gKi8KI2xvZ2luLXNjcmVlbntkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpjZW50ZXI7bWluLWhlaWdodDoxMDB2aDtiYWNrZ3JvdW5kOmxpbmVhci1ncmFkaWVudCgxMzVkZWcsIzBGMUMyRSwjMWEzYTJhKX0KLmxvZ2luLWJveHtiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJhZGl1czoxNnB4O3BhZGRpbmc6NDBweCAzNnB4O3dpZHRoOjEwMCU7bWF4LXdpZHRoOjQyMHB4O2JveC1zaGFkb3c6MCAyMHB4IDYwcHggcmdiYSgwLDAsMCwuMyl9Ci5sb2dpbi1sb2dve3RleHQtYWxpZ246Y2VudGVyO21hcmdpbi1ib3R0b206MjhweH0KLmxvZ2luLWxvZ28gLmljb257Zm9udC1zaXplOjQ4cHh9Ci5sb2dpbi1sb2dvIGgxe2ZvbnQtc2l6ZToyMnB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjojMEYxQzJFO21hcmdpbi10b3A6MTBweH0KLmxvZ2luLWxvZ28gcHtmb250LXNpemU6MTNweDtjb2xvcjojNjQ3NDhiO21hcmdpbi10b3A6NHB4fQouZmllbGR7bWFyZ2luLWJvdHRvbToxNnB4fQouZmllbGQgbGFiZWx7ZGlzcGxheTpibG9jaztmb250LXNpemU6MTJweDtmb250LXdlaWdodDo3MDA7Y29sb3I6IzQ3NTU2OTt0ZXh0LXRyYW5zZm9ybTp1cHBlcmNhc2U7bGV0dGVyLXNwYWNpbmc6LjA1ZW07bWFyZ2luLWJvdHRvbTo2cHh9Ci5maWVsZCBpbnB1dCwuZmllbGQgc2VsZWN0e3dpZHRoOjEwMCU7cGFkZGluZzoxM3B4IDE0cHg7Ym9yZGVyOjJweCBzb2xpZCAjZTJlOGYwO2JvcmRlci1yYWRpdXM6OXB4O2ZvbnQtc2l6ZToxNXB4O291dGxpbmU6bm9uZTt0cmFuc2l0aW9uOmJvcmRlci1jb2xvciAuMnM7YmFja2dyb3VuZDojZjhmYWZjfQouZmllbGQgaW5wdXQ6Zm9jdXMsLmZpZWxkIHNlbGVjdDpmb2N1c3tib3JkZXItY29sb3I6IzE2YTM0YTtiYWNrZ3JvdW5kOiNmZmZ9Ci5sb2dpbi1idG57d2lkdGg6MTAwJTtwYWRkaW5nOjE0cHg7YmFja2dyb3VuZDojMTZhMzRhO2NvbG9yOiNmZmY7Ym9yZGVyOm5vbmU7Ym9yZGVyLXJhZGl1czo5cHg7Zm9udC1zaXplOjE1cHg7Zm9udC13ZWlnaHQ6NzAwO2N1cnNvcjpwb2ludGVyO3RyYW5zaXRpb246YmFja2dyb3VuZCAuMnN9Ci5sb2dpbi1idG46aG92ZXJ7YmFja2dyb3VuZDojMTU4MDNkfQoubG9naW4tZXJyb3J7YmFja2dyb3VuZDojZmVlMmUyO2NvbG9yOiNkYzI2MjY7Ym9yZGVyLXJhZGl1czo4cHg7cGFkZGluZzoxMHB4IDE0cHg7Zm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NjAwO21hcmdpbi10b3A6MTRweDt0ZXh0LWFsaWduOmNlbnRlcjtkaXNwbGF5Om5vbmV9Ci8qIE1BSU4gKi8KI21haW4tcGFuZWx7ZGlzcGxheTpub25lfQoudG9wYmFye2JhY2tncm91bmQ6IzBGMUMyRTtjb2xvcjojZmZmO3BhZGRpbmc6MTZweCAyOHB4O2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW59Ci50b3BiYXIgaDF7Zm9udC1zaXplOjE4cHg7Zm9udC13ZWlnaHQ6NzAwfQoudG9wYmFyIHB7Zm9udC1zaXplOjEycHg7b3BhY2l0eTouNjttYXJnaW4tdG9wOjJweH0KLnRvcGJhci1yaWdodHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxNHB4fQouYmFkZ2V7YmFja2dyb3VuZDpyZ2JhKDIyLDE2Myw3NCwuMjUpO2NvbG9yOiM4NmVmYWM7cGFkZGluZzo1cHggMTRweDtib3JkZXItcmFkaXVzOjIwcHg7Zm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NzAwO2JvcmRlcjoxcHggc29saWQgcmdiYSgyMiwxNjMsNzQsLjMpfQoubG9nb3V0LWJ0bntiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsLjEpO2NvbG9yOiNmZmY7Ym9yZGVyOjFweCBzb2xpZCByZ2JhKDI1NSwyNTUsMjU1LC4yKTtib3JkZXItcmFkaXVzOjZweDtwYWRkaW5nOjZweCAxNHB4O2ZvbnQtc2l6ZToxMnB4O2N1cnNvcjpwb2ludGVyfQoubG9nb3V0LWJ0bjpob3ZlcntiYWNrZ3JvdW5kOnJnYmEoMjU1LDI1NSwyNTUsLjIpfQouY29udGVudHttYXgtd2lkdGg6MTAwMHB4O21hcmdpbjoyOHB4IGF1dG87cGFkZGluZzowIDIwcHh9Ci8qIEZJTFRFUlMgKi8KLmZpbHRlcnN7YmFja2dyb3VuZDojZmZmO2JvcmRlci1yYWRpdXM6MTJweDtwYWRkaW5nOjIwcHggMjRweDttYXJnaW4tYm90dG9tOjIwcHg7Ym9yZGVyOjFweCBzb2xpZCAjZTJlOGYwO2Rpc3BsYXk6ZmxleDtnYXA6MTZweDtmbGV4LXdyYXA6d3JhcDthbGlnbi1pdGVtczpmbGV4LWVuZH0KLmZpbHRlci1ncm91cHtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO2dhcDo2cHg7bWluLXdpZHRoOjE4MHB4fQouZmlsdGVyLWdyb3VwIGxhYmVse2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjcwMDtjb2xvcjojNDc1NTY5O3RleHQtdHJhbnNmb3JtOnVwcGVyY2FzZTtsZXR0ZXItc3BhY2luZzouMDVlbX0KLmZpbHRlci1ncm91cCBzZWxlY3QsLmZpbHRlci1ncm91cCBpbnB1dHtwYWRkaW5nOjEwcHggMTJweDtib3JkZXI6MS41cHggc29saWQgI2UyZThmMDtib3JkZXItcmFkaXVzOjhweDtmb250LXNpemU6MTRweDtvdXRsaW5lOm5vbmU7YmFja2dyb3VuZDojZjhmYWZjfQouZmlsdGVyLWdyb3VwIHNlbGVjdDpmb2N1cywuZmlsdGVyLWdyb3VwIGlucHV0OmZvY3Vze2JvcmRlci1jb2xvcjojMTZhMzRhfQoubG9hZC1idG57cGFkZGluZzoxMHB4IDI0cHg7YmFja2dyb3VuZDojMTZhMzRhO2NvbG9yOiNmZmY7Ym9yZGVyOm5vbmU7Ym9yZGVyLXJhZGl1czo4cHg7Zm9udC1zaXplOjE0cHg7Zm9udC13ZWlnaHQ6NjAwO2N1cnNvcjpwb2ludGVyO3doaXRlLXNwYWNlOm5vd3JhcH0KLmxvYWQtYnRuOmhvdmVye2JhY2tncm91bmQ6IzE1ODAzZH0KLyogU1RBVFMgKi8KLnN0YXRze2Rpc3BsYXk6Z3JpZDtncmlkLXRlbXBsYXRlLWNvbHVtbnM6cmVwZWF0KDMsMWZyKTtnYXA6MTZweDttYXJnaW4tYm90dG9tOjIwcHh9Ci5zdGF0LWNhcmR7YmFja2dyb3VuZDojZmZmO2JvcmRlci1yYWRpdXM6MTJweDtwYWRkaW5nOjIwcHggMjRweDtib3JkZXI6MXB4IHNvbGlkICNlMmU4ZjB9Ci5zdGF0LWxhYmVse2ZvbnQtc2l6ZToxM3B4O2NvbG9yOiM2NDc0OGI7Zm9udC13ZWlnaHQ6NTAwO21hcmdpbi1ib3R0b206NnB4fQouc3RhdC12YWx1ZXtmb250LXNpemU6MjhweDtmb250LXdlaWdodDo3MDA7Y29sb3I6IzBGMUMyRX0KLnN0YXQtdmFsdWUuZ3JlZW57Y29sb3I6IzE2YTM0YX0KLnN0YXQtdmFsdWUucmVke2NvbG9yOiNkYzI2MjZ9Ci8qIFNBVkUgQkFSICovCi5zYXZlLWJhcntiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJhZGl1czoxMnB4O3BhZGRpbmc6MTZweCAyNHB4O21hcmdpbi1ib3R0b206MjBweDtib3JkZXI6MXB4IHNvbGlkICNlMmU4ZjA7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6c3BhY2UtYmV0d2VlbjtmbGV4LXdyYXA6d3JhcDtnYXA6MTJweDtkaXNwbGF5Om5vbmV9Ci5zYXZlLWJhci5zaG93e2Rpc3BsYXk6ZmxleH0KLnNhdmUtaW5mb3tmb250LXNpemU6MTRweDtjb2xvcjojMzc0MTUxfTxicj4uc2F2ZS1pbmZvIHNwYW57Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOiNkYzI2MjZ9Ci5zYXZlLWJ0bntwYWRkaW5nOjEwcHggMjhweDtiYWNrZ3JvdW5kOiMxNmEzNGE7Y29sb3I6I2ZmZjtib3JkZXI6bm9uZTtib3JkZXItcmFkaXVzOjhweDtmb250LXNpemU6MTRweDtmb250LXdlaWdodDo3MDA7Y3Vyc29yOnBvaW50ZXJ9Ci5zYXZlLWJ0bjpob3ZlcntiYWNrZ3JvdW5kOiMxNTgwM2R9Ci5zYXZlLWJ0bjpkaXNhYmxlZHtiYWNrZ3JvdW5kOiM4NmVmYWM7Y3Vyc29yOm5vdC1hbGxvd2VkfQovKiBUQUJMRSAqLwoudGFibGUtd3JhcHtiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJhZGl1czoxMnB4O2JvcmRlcjoxcHggc29saWQgI2UyZThmMDtvdmVyZmxvdzpoaWRkZW59Ci50YWJsZS1oZWFkZXJ7cGFkZGluZzoxNnB4IDI0cHg7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgI2UyZThmMDtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2p1c3RpZnktY29udGVudDpzcGFjZS1iZXR3ZWVufQoudGFibGUtdGl0bGV7Zm9udC1zaXplOjE1cHg7Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOiMwRjFDMkV9Ci5zdHVkZW50LWNvdW50e2ZvbnQtc2l6ZToxM3B4O2NvbG9yOiM2NDc0OGJ9CnRhYmxle3dpZHRoOjEwMCU7Ym9yZGVyLWNvbGxhcHNlOmNvbGxhcHNlfQp0aHt0ZXh0LWFsaWduOmxlZnQ7cGFkZGluZzoxMnB4IDE2cHg7Zm9udC1zaXplOjExcHg7Zm9udC13ZWlnaHQ6NzAwO2NvbG9yOiM2NDc0OGI7dGV4dC10cmFuc2Zvcm06dXBwZXJjYXNlO2xldHRlci1zcGFjaW5nOi4wNWVtO2JhY2tncm91bmQ6I2Y4ZmFmYztib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZTJlOGYwfQp0ZHtwYWRkaW5nOjEycHggMTZweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCAjZjFmNWY5O2ZvbnQtc2l6ZToxNHB4O3ZlcnRpY2FsLWFsaWduOm1pZGRsZX0KdHI6bGFzdC1jaGlsZCB0ZHtib3JkZXItYm90dG9tOm5vbmV9CnRyOmhvdmVyIHRke2JhY2tncm91bmQ6I2ZhZmJmY30KLmdyYWRlLWJhZGdle2Rpc3BsYXk6aW5saW5lLWJsb2NrO3BhZGRpbmc6M3B4IDEwcHg7Ym9yZGVyLXJhZGl1czoyMHB4O2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjYwMDtiYWNrZ3JvdW5kOiNlMmU4ZjA7Y29sb3I6IzQ3NTU2OX0KLyogVE9HR0xFICovCi50b2dnbGUtd3JhcHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMHB4fQoudG9nZ2xle3Bvc2l0aW9uOnJlbGF0aXZlO3dpZHRoOjUycHg7aGVpZ2h0OjI4cHg7Y3Vyc29yOnBvaW50ZXJ9Ci50b2dnbGUgaW5wdXR7b3BhY2l0eTowO3dpZHRoOjA7aGVpZ2h0OjB9Ci5zbGlkZXJ7cG9zaXRpb246YWJzb2x1dGU7aW5zZXQ6MDtiYWNrZ3JvdW5kOiNlMmU4ZjA7Ym9yZGVyLXJhZGl1czoyOHB4O3RyYW5zaXRpb246LjNzfQouc2xpZGVyOmJlZm9yZXtjb250ZW50OicnO3Bvc2l0aW9uOmFic29sdXRlO2hlaWdodDoyMHB4O3dpZHRoOjIwcHg7bGVmdDo0cHg7Ym90dG9tOjRweDtiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJhZGl1czo1MCU7dHJhbnNpdGlvbjouM3M7Ym94LXNoYWRvdzowIDFweCA0cHggcmdiYSgwLDAsMCwuMil9CmlucHV0OmNoZWNrZWQrLnNsaWRlcntiYWNrZ3JvdW5kOiMxNmEzNGF9CmlucHV0OmNoZWNrZWQrLnNsaWRlcjpiZWZvcmV7dHJhbnNmb3JtOnRyYW5zbGF0ZVgoMjRweCl9Ci50b2dnbGUtbGFiZWx7Zm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6NjAwfQoudG9nZ2xlLWxhYmVsLnBhaWR7Y29sb3I6IzE2YTM0YX0KLnRvZ2dsZS1sYWJlbC51bnBhaWR7Y29sb3I6I2RjMjYyNn0KLyogU3RhdHVzICovCi5zdGF0dXMtbXNne3BhZGRpbmc6MTJweCAxNnB4O2JvcmRlci1yYWRpdXM6OHB4O2ZvbnQtc2l6ZToxM3B4O2ZvbnQtd2VpZ2h0OjYwMDttYXJnaW4tdG9wOjEycHg7ZGlzcGxheTpub25lfQouc3RhdHVzLW1zZy5zdWNjZXNze2JhY2tncm91bmQ6I2RjZmNlNztjb2xvcjojMTU4MDNkO2Rpc3BsYXk6YmxvY2t9Ci5zdGF0dXMtbXNnLmVycm9ye2JhY2tncm91bmQ6I2ZlZTJlMjtjb2xvcjojZGMyNjI2O2Rpc3BsYXk6YmxvY2t9Ci5lbXB0eXt0ZXh0LWFsaWduOmNlbnRlcjtwYWRkaW5nOjQwcHg7Y29sb3I6Izk0YTNiODtmb250LXNpemU6MTRweH0KQG1lZGlhKG1heC13aWR0aDo2MDBweCl7LnN0YXRze2dyaWQtdGVtcGxhdGUtY29sdW1uczoxZnJ9LmZpbHRlcnN7ZmxleC1kaXJlY3Rpb246Y29sdW1ufX0KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KPGRpdiBpZD0icG9ydGFsLWxvZ2luIj4KICA8ZGl2IGNsYXNzPSJsYm94Ij4KICAgIDxkaXYgY2xhc3M9ImxpY28iPiYjMTI3OTgzOzwvZGl2PgogICAgPGgxPk1vZGVybiBJbmZpbml0eSBTY2hvb2w8L2gxPgogICAgPHA+U3RhZmYgUG9ydGFsICZtZGFzaDsgUG93ZXJlZCBieSBTbWFydmV4PC9wPgogICAgPGRpdiBjbGFzcz0ibGYiPjxsYWJlbD5Vc2VybmFtZTwvbGFiZWw+CiAgICAgIDxpbnB1dCB0eXBlPSJ0ZXh0IiBpZD0icHUiIHBsYWNlaG9sZGVyPSJFbnRlciB5b3VyIHVzZXJuYW1lIiBhdXRvY29tcGxldGU9Im9mZiI+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImxmIj48bGFiZWw+UGFzc3dvcmQ8L2xhYmVsPgogICAgICA8aW5wdXQgdHlwZT0icGFzc3dvcmQiIGlkPSJwcCIgcGxhY2Vob2xkZXI9IkVudGVyIHlvdXIgcGFzc3dvcmQiIG9ua2V5ZG93bj0iaWYoZXZlbnQua2V5PT09J0VudGVyJylwTG9naW4oKSI+CiAgICA8L2Rpdj4KICAgIDxidXR0b24gaWQ9InBzaWduaW4iIG9uY2xpY2s9InBMb2dpbigpIj5TaWduIEluPC9idXR0b24+CiAgICA8ZGl2IGlkPSJwZXJyIj48L2Rpdj4KICA8L2Rpdj4KPC9kaXY+CjxkaXYgaWQ9InBhbmVsLWhvc3QiPgogIDxkaXYgY2xhc3M9InBhbmUiIGlkPSJwYW5lLWFkbWluIj48IS0tIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBMT0dJTiDilZDilZAgLS0+CjxkaXYgY2xhc3M9Imx3IiBpZD0ibHciPgogIDxkaXYgY2xhc3M9ImxjIj4KICAgIDxkaXYgY2xhc3M9ImxsIj4KICAgICAgPGRpdiBjbGFzcz0ibGkiPvCfk6I8L2Rpdj4KICAgICAgPGgxPk1vZGVybiBJbmZpbml0eTwvaDE+CiAgICAgIDxwPlNjaG9vbCBBZG1pbiBQYW5lbCDigJQgU2lnbiBJbjwvcD4KICAgIDwvZGl2PgoKICAgIDwhLS0gUm9sZSBzZWxlY3RvciBjYXJkcyAtLT4KICAgIDxkaXYgY2xhc3M9InJvbGUtY2FyZHMiIGlkPSJyb2xlQ2FyZHMiPgogICAgICA8ZGl2IGNsYXNzPSJyb2xlLWNhcmQgYWN0aXZlIiBkYXRhLXU9ImFkbWluIiBkYXRhLXA9IiIgb25jbGljaz0icGlja1JvbGUodGhpcykiPgogICAgICAgIDxkaXYgY2xhc3M9InJpIj7wn4yQPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0icmwiPlN1cGVyIEFkbWluPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0icnMiPkFsbCBHcmFkZXM8L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InJvbGUtY2FyZCIgZGF0YS11PSJhZG1pbl9qdW5pb3IiIGRhdGEtcD0iIiBvbmNsaWNrPSJwaWNrUm9sZSh0aGlzKSI+CiAgICAgICAgPGRpdiBjbGFzcz0icmkiPvCfjpI8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJybCI+SnVuaW9yIEFkbWluPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0icnMiPktHIOKAkyBHcmFkZSA2PC9kaXY+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJyb2xlLWNhcmQiIGRhdGEtdT0iYWRtaW5fc2VuaW9yIiBkYXRhLXA9IiIgb25jbGljaz0icGlja1JvbGUodGhpcykiPgogICAgICAgIDxkaXYgY2xhc3M9InJpIj7wn46TPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0icmwiPlNlbmlvciBBZG1pbjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InJzIj5HcmFkZSA3IOKAkyAxMjwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDxkaXYgY2xhc3M9InNjb3BlLWhpbnQiIGlkPSJzY29wZUhpbnQiPllvdSBhcmUgc2lnbmluZyBpbiBhcyA8c3Ryb25nPlN1cGVyIEFkbWluPC9zdHJvbmc+IOKAlCBhY2Nlc3MgdG8gYWxsIGdyYWRlczwvZGl2PgoKICAgIDxkaXYgY2xhc3M9ImZnIj48bGFiZWw+VXNlcm5hbWU8L2xhYmVsPjxpbnB1dCB0eXBlPSJ0ZXh0IiBpZD0ibHUiIGF1dG9jb21wbGV0ZT0ib2ZmIiBwbGFjZWhvbGRlcj0iRW50ZXIgdXNlcm5hbWUiLz48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImZnIj48bGFiZWw+UGFzc3dvcmQ8L2xhYmVsPjxpbnB1dCB0eXBlPSJwYXNzd29yZCIgaWQ9ImxwIiBwbGFjZWhvbGRlcj0iRW50ZXIgcGFzc3dvcmQiIG9ua2V5ZG93bj0iaWYoZXZlbnQua2V5PT09J0VudGVyJylkb0xvZ2luKCkiLz48L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImJ0biIgb25jbGljaz0iZG9Mb2dpbigpIj5TaWduIEluIOKGkjwvYnV0dG9uPgogICAgPGRpdiBjbGFzcz0iZXJyIiBpZD0ibGUiPuKdjCBJbmNvcnJlY3QgdXNlcm5hbWUgb3IgcGFzc3dvcmQ8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkOKVkCBBUFAg4pWQ4pWQIC0tPgo8ZGl2IGNsYXNzPSJhdyIgaWQ9ImF3Ij4KCiAgPCEtLSBTSURFQkFSIC0tPgogIDxkaXYgY2xhc3M9InNiIj4KICAgIDxkaXYgY2xhc3M9InNsIj4KICAgICAgPGRpdiBjbGFzcz0ic2kiPvCfk6I8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3QiPk1vZGVybiBJbmZpbml0eTxzcGFuPkFkbWluIFBhbmVsPC9zcGFuPjwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJhZG1pbi1iYWRnZSI+CiAgICAgIDxkaXYgY2xhc3M9ImFiLWxhYmVsIj5Mb2dnZWQgaW4gYXM8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iYWItbmFtZSIgaWQ9InNiQWRtaW5OYW1lIj7igJQ8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iYWItc2NvcGUiIGlkPSJzYkFkbWluU2NvcGUiPuKAlDwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJuaSBhY3RpdmUiIG9uY2xpY2s9InNob3dUYWIoJ2InKSI+8J+ToyBTZW5kIEFubm91bmNlbWVudDwvZGl2PgogICAgPGRpdiBjbGFzcz0ibmkiIG9uY2xpY2s9InNob3dUYWIoJ2gnKSI+8J+TiyBIaXN0b3J5PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJuaSIgb25jbGljaz0ic2hvd1RhYigncCcpIj7wn5GlIFBhcmVudHM8L2Rpdj4KICAgIDxkaXYgY2xhc3M9Im5pIiBvbmNsaWNrPSJ3aW5kb3cub3BlbignL2ZpbmFuY2UnLCdfYmxhbmsnKSIgc3R5bGU9Im1hcmdpbi10b3A6OHB4O2JvcmRlcjoxcHggc29saWQgcmdiYSgwLDIwMCwyMDAsMC4yKSI+8J+SsyBGaW5hbmNlIFBhbmVsIOKGlzwvZGl2PgogICAgPGRpdiBjbGFzcz0ic2ItYm90Ij4KICAgICAgPGRpdiBjbGFzcz0ibG8iIG9uY2xpY2s9ImRvTG9nb3V0KCkiPvCfmqogU2lnbiBvdXQ8L2Rpdj4KICAgIDwvZGl2PgogIDwvZGl2PgoKICA8IS0tIE1BSU4gLS0+CiAgPGRpdiBjbGFzcz0ibW4iPgogICAgPGRpdiBjbGFzcz0idGIyIj4KICAgICAgPGRpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJwZy10IiBpZD0icHQiPlNlbmQgQW5ub3VuY2VtZW50PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0icGctcyIgaWQ9InBzIj5Ccm9hZGNhc3QgYSBtZXNzYWdlIHRvIHBhcmVudHMgdmlhIFdoYXRzQXBwPC9kaXY+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJ0b3AtcmlnaHQiPgogICAgICAgIDxkaXYgY2xhc3M9InNjb3BlLXRhZyIgaWQ9ImhlYWRlclNjb3BlVGFnIj7igJQ8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzcGwiPjxkaXYgY2xhc3M9InNwZCI+PC9kaXY+Qm90IE9ubGluZTwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0gU1RBVFMgLS0+CiAgICA8ZGl2IGNsYXNzPSJzciIgaWQ9InNyIj4KICAgICAgPGRpdiBjbGFzcz0ic2MiPjxkaXYgY2xhc3M9InNsMiI+UGFyZW50cyBpbiBNeSBTY29wZTwvZGl2PjxkaXYgY2xhc3M9InN2IiBpZD0ic3AyIj4tPC9kaXY+PC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InNjIj48ZGl2IGNsYXNzPSJzbDIiPlNlbnQgVG9kYXk8L2Rpdj48ZGl2IGNsYXNzPSJzdiIgaWQ9InN0MiI+MDwvZGl2PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzYyI+PGRpdiBjbGFzcz0ic2wyIj5MYXN0IFNlbnQ8L2Rpdj48ZGl2IGNsYXNzPSJzdiIgaWQ9InNsMyIgc3R5bGU9ImZvbnQtc2l6ZToxOHB4O21hcmdpbi10b3A6NnB4Ij5OZXZlcjwvZGl2PjwvZGl2PgogICAgPC9kaXY+CgogICAgPCEtLSBUQUI6IEJST0FEQ0FTVCAtLT4KICAgIDxkaXYgaWQ9InRhYi1iIj4KICAgICAgPGRpdiBjbGFzcz0ic2VjIj4KICAgICAgICA8ZGl2IGNsYXNzPSJzZWMtdCI+4pyN77iPIFdyaXRlIEFubm91bmNlbWVudDwvZGl2PgogICAgICAgIDxsYWJlbCBjbGFzcz0iZmwiPk1lc3NhZ2U8L2xhYmVsPgogICAgICAgIDx0ZXh0YXJlYSBpZD0ibXQiIHBsYWNlaG9sZGVyPSJUeXBlIHlvdXIgYW5ub3VuY2VtZW50IGhlcmUuLi4iIG9uaW5wdXQ9InVwZGF0ZVByZXZpZXcoKSI+PC90ZXh0YXJlYT4KICAgICAgICA8bGFiZWwgY2xhc3M9ImZsIiBzdHlsZT0ibWFyZ2luLXRvcDoxNHB4Ij7wn5O3IFBob3RvIChvcHRpb25hbCk8L2xhYmVsPgogICAgICAgIDxkaXYgY2xhc3M9InBob3RvLXpvbmUiIGlkPSJwaG90b1pvbmUiIG9uY2xpY2s9ImRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b0lucHV0JykuY2xpY2soKSI+CiAgICAgICAgICA8YnV0dG9uIGNsYXNzPSJyZW1vdmUtcGhvdG8iIGlkPSJyZW1vdmVQaG90byIgb25jbGljaz0iZXZlbnQuc3RvcFByb3BhZ2F0aW9uKCk7cmVtb3ZlUGhvdG9GbigpIj7inJU8L2J1dHRvbj4KICAgICAgICAgIDxkaXYgaWQ9InBob3RvUGxhY2Vob2xkZXIiPvCfk7cgQ2xpY2sgdG8gYXR0YWNoIGEgcGhvdG88YnI+PHNwYW4gc3R5bGU9ImZvbnQtc2l6ZToxMXB4O2NvbG9yOiM5NGEzYjgiPkpQRywgUE5HIOKAlCBtYXggNU1CIOKAoiBQaG90byB3aWxsIGJlIHNlbnQgd2l0aCB5b3VyIG1lc3NhZ2UgYXMgY2FwdGlvbjwvc3Bhbj48L2Rpdj4KICAgICAgICAgIDxpbWcgY2xhc3M9InBob3RvLXByZXZpZXciIGlkPSJwaG90b1ByZXZpZXciIHNyYz0iIiBhbHQ9InByZXZpZXciPgogICAgICAgICAgPGlucHV0IHR5cGU9ImZpbGUiIGlkPSJwaG90b0lucHV0IiBhY2NlcHQ9ImltYWdlLyoiIHN0eWxlPSJkaXNwbGF5Om5vbmUiIG9uY2hhbmdlPSJoYW5kbGVQaG90b1NlbGVjdChldmVudCkiPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgaWQ9InVwbG9hZFN0YXR1cyIgc3R5bGU9ImZvbnQtc2l6ZToxMnB4O2NvbG9yOiM2NDc0OGI7bWFyZ2luLXRvcDo0cHg7ZGlzcGxheTpub25lIj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJjYyI+PHNwYW4gaWQ9ImNjIj4wPC9zcGFuPi8xMDAwPC9kaXY+CgogICAgICAgIDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6MjBweCI+CiAgICAgICAgICA8bGFiZWwgY2xhc3M9ImZsIj5TZW5kIHRvIDxzcGFuIGlkPSJncmFkZUxhYmVsIiBzdHlsZT0iY29sb3I6IzY0NzQ4Yjtmb250LXdlaWdodDo0MDA7Zm9udC1zaXplOjEycHgiPuKAlCBzZWxlY3QgZ3JhZGVzIGJlbG93PC9zcGFuPjwvbGFiZWw+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJnZyIgaWQ9ImdyYWRlR3JpZCI+PCEtLSBmaWxsZWQgYnkgSlMgLS0+PC9kaXY+CiAgICAgICAgPC9kaXY+CgogICAgICAgIDxkaXYgc3R5bGU9Im1hcmdpbi10b3A6MjBweCI+CiAgICAgICAgICA8bGFiZWwgY2xhc3M9ImZsIj5QcmV2aWV3PC9sYWJlbD4KICAgICAgICAgIDxkaXYgY2xhc3M9InBiIj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0icGItbCI+8J+TsSBXaGF0c0FwcCBQcmV2aWV3PC9kaXY+CiAgICAgICAgICAgIDxkaXYgaWQ9InBob3RvUHJldmlld0JhZGdlIiBzdHlsZT0iZGlzcGxheTpub25lO2JhY2tncm91bmQ6I2RjZmNlNztjb2xvcjojMTU4MDNkO3BhZGRpbmc6NnB4IDEwcHg7Ym9yZGVyLXJhZGl1czo2cHg7Zm9udC1zaXplOjEycHg7bWFyZ2luLWJvdHRvbTo4cHg7Ij7wn5O3IFBob3RvIHdpbGwgYmUgaW5jbHVkZWQ8L2Rpdj4KICAgICAgICAgIDxkaXYgY2xhc3M9InBiLWIiIGlkPSJwdiI+WW91ciBtZXNzYWdlIHdpbGwgYXBwZWFyIGhlcmUuLi48L2Rpdj4KICAgICAgICAgICAgPGRpdiBjbGFzcz0icGItdCIgaWQ9InB2dCI+Tm93PC9kaXY+CiAgICAgICAgICA8L2Rpdj4KICAgICAgICA8L2Rpdj4KCiAgICAgICAgPGRpdiBjbGFzcz0ic3IyIj4KICAgICAgICAgIDxidXR0b24gY2xhc3M9InNuZCIgaWQ9InNiMiIgb25jbGljaz0iZG9TZW5kKCkiIGRpc2FibGVkPvCfk6QgU2VuZCB0byBQYXJlbnRzPC9idXR0b24+CiAgICAgICAgICA8ZGl2IGNsYXNzPSJyYyI+VG8gPHN0cm9uZyBpZD0icm4iPi08L3N0cm9uZz4gcGFyZW50czwvZGl2PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0gVEFCOiBISVNUT1JZIC0tPgogICAgPGRpdiBpZD0idGFiLWgiIHN0eWxlPSJkaXNwbGF5Om5vbmUiPgogICAgICA8ZGl2IGNsYXNzPSJzZWMiPgogICAgICAgIDxkaXYgY2xhc3M9InNlYy10Ij7wn5OLIFNlbnQgQW5ub3VuY2VtZW50czwvZGl2PgogICAgICAgIDxkaXYgaWQ9ImhsIj48ZGl2IHN0eWxlPSJjb2xvcjojOTRhM2I4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjBweCAwIj5ObyBhbm5vdW5jZW1lbnRzIHlldDwvZGl2PjwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgoKICAgIDwhLS0gVEFCOiBQQVJFTlRTIC0tPgogICAgPGRpdiBpZD0idGFiLXAiIHN0eWxlPSJkaXNwbGF5Om5vbmUiPgogICAgICA8ZGl2IGNsYXNzPSJzZWMiPgogICAgICAgIDxkaXYgY2xhc3M9InNlYy10Ij7wn5GlIFBhcmVudHMgaW4gTXkgU2NvcGU8L2Rpdj4KICAgICAgICA8cCBzdHlsZT0iZm9udC1zaXplOjE0cHg7Y29sb3I6IzY0NzQ4YjttYXJnaW4tYm90dG9tOjE2cHgiPlNob3dpbmcgcGFyZW50cyBmcm9tIDxzdHJvbmc+R29vZ2xlIFNoZWV0IOKGkiBQYXJlbnRzIHRhYjwvc3Ryb25nPiBmaWx0ZXJlZCB0byB5b3VyIGFjY2VzcyBsZXZlbC48L3A+CiAgICAgICAgPGRpdiBpZD0icGwyIj48ZGl2IHN0eWxlPSJjb2xvcjojOTRhM2I4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjBweCAwIj5Mb2FkaW5nLi4uPC9kaXY+PC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CiAgPC9kaXY+CjwvZGl2PgoKPCEtLSBNT0RBTCAtLT4KPGRpdiBjbGFzcz0ibW8iIGlkPSJtbyI+CiAgPGRpdiBjbGFzcz0ibWQiPgogICAgPGRpdiBzdHlsZT0iZm9udC1zaXplOjUycHg7bWFyZ2luLWJvdHRvbToxNnB4Ij7wn46JPC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJmb250LXNpemU6MjJweDtmb250LXdlaWdodDo3MDA7bWFyZ2luLWJvdHRvbTo4cHgiPlNlbnQhPC9kaXY+CiAgICA8ZGl2IHN0eWxlPSJmb250LXNpemU6MTVweDtjb2xvcjojNjQ3NDhiO21hcmdpbi1ib3R0b206MjhweCIgaWQ9Im1zIj5EZWxpdmVyZWQuPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJtYjIiIG9uY2xpY2s9ImNsb3NlTW9kYWwoKSI+RG9uZTwvYnV0dG9uPgogIDwvZGl2Pgo8L2Rpdj4KCjxzY3JpcHQ+Ci8vIOKUgOKUgCBTdGF0ZSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKdmFyIENVUlJFTlRfVVNFUiA9IG51bGw7ICAgLy8ge3VzZXJuYW1lLCBsYWJlbCwgZ3JhZGVzfQp2YXIgQUxMX1BBUkVOVFMgID0gW107CnZhciBTRUxfR1JBREVTICAgPSBbJ0FsbCddOwoKLy8gR3JhZGUgZGVmaW5pdGlvbnMgcGVyIHNjb3BlCnZhciBKVU5JT1JfR1JBREVTID0gWydLRzEnLCdLRzInLCdHcmFkZSAxJywnR3JhZGUgMicsJ0dyYWRlIDMnLCdHcmFkZSA0JywnR3JhZGUgNScsJ0dyYWRlIDYnXTsKdmFyIFNFTklPUl9HUkFERVMgPSBbJ0dyYWRlIDcnLCdHcmFkZSA4JywnR3JhZGUgOScsJ0dyYWRlIDEwJywnR3JhZGUgMTEnLCdHcmFkZSAxMiddOwp2YXIgQUxMX0dSQURFUyAgICA9IFsnS0cxJywnS0cyJywnR3JhZGUgMScsJ0dyYWRlIDInLCdHcmFkZSAzJywnR3JhZGUgNCcsJ0dyYWRlIDUnLCdHcmFkZSA2JywKICAgICAgICAgICAgICAgICAgICAgJ0dyYWRlIDcnLCdHcmFkZSA4JywnR3JhZGUgOScsJ0dyYWRlIDEwJywnR3JhZGUgMTEnLCdHcmFkZSAxMiddOwoKLy8gUm9sZSBjYXJkIHByZS1maWxsIGhpbnRzCnZhciBST0xFX0hJTlRTID0gewogICdhZG1pbic6ICAgICAgICAnWW91IGFyZSBzaWduaW5nIGluIGFzIDxzdHJvbmc+U3VwZXIgQWRtaW48L3N0cm9uZz4g4oCUIGFjY2VzcyB0byBhbGwgZ3JhZGVzJywKICAnYWRtaW5fanVuaW9yJzogJ1lvdSBhcmUgc2lnbmluZyBpbiBhcyA8c3Ryb25nPkp1bmlvciBBZG1pbjwvc3Ryb25nPiDigJQgS0cgdG8gR3JhZGUgNiBvbmx5JywKICAnYWRtaW5fc2VuaW9yJzogJ1lvdSBhcmUgc2lnbmluZyBpbiBhcyA8c3Ryb25nPlNlbmlvciBBZG1pbjwvc3Ryb25nPiDigJQgR3JhZGUgNyB0byBHcmFkZSAxMiBvbmx5Jwp9Owp2YXIgUk9MRV9VU0VSTkFNRVMgPSB7CiAgJ2FkbWluJzonYWRtaW4nLCdhZG1pbl9qdW5pb3InOidhZG1pbl9qdW5pb3InLCdhZG1pbl9zZW5pb3InOidhZG1pbl9zZW5pb3InCn07CgovLyDilIDilIAgUm9sZSBjYXJkIHBpY2tlciDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gcGlja1JvbGUoY2FyZCl7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnJvbGUtY2FyZCcpLmZvckVhY2goZnVuY3Rpb24oYyl7Yy5jbGFzc0xpc3QucmVtb3ZlKCdhY3RpdmUnKTt9KTsKICBjYXJkLmNsYXNzTGlzdC5hZGQoJ2FjdGl2ZScpOwogIHZhciB1ID0gY2FyZC5kYXRhc2V0LnU7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2x1JykudmFsdWUgPSB1OwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdscCcpLnZhbHVlID0gJyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Njb3BlSGludCcpLmlubmVySFRNTCA9IFJPTEVfSElOVFNbdV0gfHwgJyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xlJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7Cn0KCi8vIOKUgOKUgCBMb2dpbiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gZG9Mb2dpbigpewogIHZhciB1ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2x1JykudmFsdWUudHJpbSgpOwogIHZhciBwID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xwJykudmFsdWUudHJpbSgpOwogIGlmKCF1fHwhcCl7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS50ZXh0Q29udGVudD0n4pqg77iPIFBsZWFzZSBlbnRlciB1c2VybmFtZSBhbmQgcGFzc3dvcmQnOwogICAgcmV0dXJuOwogIH0KICBmZXRjaCgnL2FwaS9sb2dpbicse21ldGhvZDonUE9TVCcsaGVhZGVyczp7J0NvbnRlbnQtVHlwZSc6J2FwcGxpY2F0aW9uL2pzb24nfSwKICAgIGJvZHk6SlNPTi5zdHJpbmdpZnkoe3VzZXJuYW1lOnUscGFzc3dvcmQ6cH0pCiAgfSkudGhlbihmdW5jdGlvbihyKXsKICAgIGlmKCFyLm9rKSB0aHJvdyBuZXcgRXJyb3IoJ2JhZCcpOwogICAgcmV0dXJuIHIuanNvbigpOwogIH0pLnRoZW4oZnVuY3Rpb24oZCl7CiAgICBpZihkLm9rKXsKICAgICAgQ1VSUkVOVF9VU0VSID0gZDsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xlJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsdycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXcnKS5zdHlsZS5kaXNwbGF5PSdmbGV4JzsKICAgICAgc2V0dXBBZG1pblVJKCk7CiAgICAgIGxvYWRTdGF0cygpOwogICAgICBsb2FkUGFyZW50cygpOwogICAgfSBlbHNlIHsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xlJykuc3R5bGUuZGlzcGxheT0nYmxvY2snOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS50ZXh0Q29udGVudD0n4p2MIEluY29ycmVjdCB1c2VybmFtZSBvciBwYXNzd29yZCc7CiAgICB9CiAgfSkuY2F0Y2goZnVuY3Rpb24oKXsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsZScpLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsZScpLnRleHRDb250ZW50PSfinYwgSW5jb3JyZWN0IHVzZXJuYW1lIG9yIHBhc3N3b3JkJzsKICB9KTsKfQoKLy8g4pSA4pSAIFNldHVwIFVJIGFmdGVyIGxvZ2luIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBzZXR1cEFkbWluVUkoKXsKICB2YXIgdSA9IENVUlJFTlRfVVNFUjsKICB2YXIgaXNTdXBlciAgPSB1LmdyYWRlcy5sZW5ndGggPT09IDA7CiAgdmFyIGlzSnVuaW9yID0gIWlzU3VwZXIgJiYgdS5ncmFkZXMuaW5jbHVkZXMoJ0tHMScpOwogIHZhciBpc1NlbmlvciA9ICFpc1N1cGVyICYmIHUuZ3JhZGVzLmluY2x1ZGVzKCdHcmFkZSA3Jyk7CgogIC8vIFNpZGViYXIgYmFkZ2UKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc2JBZG1pbk5hbWUnKS50ZXh0Q29udGVudCA9IHUudXNlcm5hbWU7CiAgdmFyIHNjb3BlRWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc2JBZG1pblNjb3BlJyk7CiAgaWYoaXNTdXBlcil7ICBzY29wZUVsLnRleHRDb250ZW50PSdBbGwgR3JhZGVzJzsgc2NvcGVFbC5jbGFzc05hbWU9J2FiLXNjb3BlIHNjb3BlLXN1cGVyJzsgfQogIGVsc2UgaWYoaXNKdW5pb3IpeyBzY29wZUVsLnRleHRDb250ZW50PSdLRyDigJMgR3JhZGUgNic7IHNjb3BlRWwuY2xhc3NOYW1lPSdhYi1zY29wZSBzY29wZS1qdW5pb3InOyB9CiAgZWxzZXsgICAgICAgICAgc2NvcGVFbC50ZXh0Q29udGVudD0nR3JhZGUgNyDigJMgMTInOyBzY29wZUVsLmNsYXNzTmFtZT0nYWItc2NvcGUgc2NvcGUtc2VuaW9yJzsgfQoKICAvLyBIZWFkZXIgc2NvcGUgdGFnCiAgdmFyIGh0ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2hlYWRlclNjb3BlVGFnJyk7CiAgaWYoaXNTdXBlcil7ICBodC50ZXh0Q29udGVudD0n8J+MkCAnK3UubGFiZWw7IGh0LmNsYXNzTmFtZT0nc2NvcGUtdGFnIHNjb3BlLXN1cGVyJzsgfQogIGVsc2UgaWYoaXNKdW5pb3IpeyBodC50ZXh0Q29udGVudD0n8J+OkiAnK3UubGFiZWw7IGh0LmNsYXNzTmFtZT0nc2NvcGUtdGFnIHNjb3BlLWp1bmlvcic7IH0KICBlbHNleyAgICAgICAgICBodC50ZXh0Q29udGVudD0n8J+OkyAnK3UubGFiZWw7IGh0LmNsYXNzTmFtZT0nc2NvcGUtdGFnIHNjb3BlLXNlbmlvcic7IH0KCiAgLy8gQnVpbGQgZ3JhZGUgZ3JpZAogIGJ1aWxkR3JhZGVHcmlkKGlzU3VwZXIsIGlzSnVuaW9yLCBpc1Nlbmlvcik7Cn0KCi8vIOKUgOKUgCBHcmFkZSBncmlkIGJ1aWxkZXIg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIGJ1aWxkR3JhZGVHcmlkKGlzU3VwZXIsIGlzSnVuaW9yLCBpc1Nlbmlvcil7CiAgdmFyIGdnID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2dyYWRlR3JpZCcpOwogIHZhciBteUdyYWRlcyA9IGlzU3VwZXIgPyBBTExfR1JBREVTIDogKGlzSnVuaW9yID8gSlVOSU9SX0dSQURFUyA6IFNFTklPUl9HUkFERVMpOwogIHZhciBsb2NrZWQgICA9IGlzU3VwZXIgPyBbXSA6IChpc0p1bmlvciA/IFNFTklPUl9HUkFERVMgOiBKVU5JT1JfR1JBREVTKTsKCiAgdmFyIGh0bWwgPSAnJzsKCiAgLy8gIkFsbCIgYnV0dG9uIChzY29wZWQgdG8gdGhpcyBhZG1pbidzIGdyYWRlcykKICB2YXIgYWxsTGFiZWwgPSBpc1N1cGVyID8gJ0FsbCBQYXJlbnRzJyA6IChpc0p1bmlvciA/ICdBbGwgS0cg4oCTIEdyYWRlIDYnIDogJ0FsbCBHcmFkZSA34oCTMTInKTsKICBodG1sICs9ICc8ZGl2IGNsYXNzPSJnYyBhbGwgc2VsIiBkYXRhLWc9IkFsbCIgb25jbGljaz0ic2VsR3JhZGUodGhpcykiPicrYWxsTGFiZWwrJzwvZGl2Pic7CgogIC8vIEp1bmlvciBzZWN0aW9uCiAgaWYoaXNTdXBlciB8fCBpc0p1bmlvcil7CiAgICBpZihpc1N1cGVyKSBodG1sICs9ICc8ZGl2IHN0eWxlPSJncmlkLWNvbHVtbjoxLy0xO2ZvbnQtc2l6ZToxMXB4O2ZvbnQtd2VpZ2h0OjYwMDtjb2xvcjojNjQ3NDhiO3BhZGRpbmc6OHB4IDRweCAycHg7bGV0dGVyLXNwYWNpbmc6LjVweCI+8J+OkiBKVU5JT1Ig4oCUIEtHIHRvIEdyYWRlIDY8L2Rpdj4nOwogICAgSlVOSU9SX0dSQURFUy5mb3JFYWNoKGZ1bmN0aW9uKGcpewogICAgICBodG1sICs9ICc8ZGl2IGNsYXNzPSJnYyIgZGF0YS1nPSInK2crJyIgb25jbGljaz0ic2VsR3JhZGUodGhpcykiPicrZysnPC9kaXY+JzsKICAgIH0pOwogIH0KCiAgLy8gU2VuaW9yIHNlY3Rpb24KICBpZihpc1N1cGVyIHx8IGlzU2VuaW9yKXsKICAgIGlmKGlzU3VwZXIpIGh0bWwgKz0gJzxkaXYgc3R5bGU9ImdyaWQtY29sdW1uOjEvLTE7Zm9udC1zaXplOjExcHg7Zm9udC13ZWlnaHQ6NjAwO2NvbG9yOiM2NDc0OGI7cGFkZGluZzo4cHggNHB4IDJweDtsZXR0ZXItc3BhY2luZzouNXB4Ij7wn46TIFNFTklPUiDigJQgR3JhZGUgNyB0byAxMjwvZGl2Pic7CiAgICBTRU5JT1JfR1JBREVTLmZvckVhY2goZnVuY3Rpb24oZyl7CiAgICAgIGh0bWwgKz0gJzxkaXYgY2xhc3M9ImdjIiBkYXRhLWc9IicrZysnIiBvbmNsaWNrPSJzZWxHcmFkZSh0aGlzKSI+JytnKyc8L2Rpdj4nOwogICAgfSk7CiAgfQoKICBnZy5pbm5lckhUTUwgPSBodG1sOwogIFNFTF9HUkFERVMgPSBbJ0FsbCddOwp9CgovLyDilIDilIAgR3JhZGUgc2VsZWN0aW9uIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBzZWxHcmFkZShlbCl7CiAgaWYoZWwuY2xhc3NMaXN0LmNvbnRhaW5zKCdsb2NrZWQnKSkgcmV0dXJuOwogIHZhciBnID0gZWwuZGF0YXNldC5nOwogIGlmKGc9PT0nQWxsJyl7CiAgICBTRUxfR1JBREVTID0gWydBbGwnXTsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoJy5nYycpLmZvckVhY2goZnVuY3Rpb24oYyl7Yy5jbGFzc0xpc3QucmVtb3ZlKCdzZWwnKTt9KTsKICAgIGVsLmNsYXNzTGlzdC5hZGQoJ3NlbCcpOwogIH0gZWxzZSB7CiAgICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yKCcuYWxsJykuY2xhc3NMaXN0LnJlbW92ZSgnc2VsJyk7CiAgICBlbC5jbGFzc0xpc3QudG9nZ2xlKCdzZWwnKTsKICAgIFNFTF9HUkFERVMgPSBbXS5zbGljZS5jYWxsKGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoJy5nYzpub3QoLmFsbCkuc2VsJykpLm1hcChmdW5jdGlvbihjKXtyZXR1cm4gYy5kYXRhc2V0Lmc7fSk7CiAgICBpZighU0VMX0dSQURFUy5sZW5ndGgpeyBTRUxfR1JBREVTPVsnQWxsJ107IGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJy5hbGwnKS5jbGFzc0xpc3QuYWRkKCdzZWwnKTsgfQogIH0KICB1cGRhdGVDb3VudCgpOwp9CgovLyDilIDilIAgU3RhdHMgJiBwYXJlbnRzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBzY29wZVBhcmFtKCl7CiAgdmFyIGcgPSBDVVJSRU5UX1VTRVIuZ3JhZGVzOwogIHJldHVybiBnLmxlbmd0aCA/ICc/Z3JhZGVzPScrZW5jb2RlVVJJQ29tcG9uZW50KGcuam9pbignLCcpKSA6ICcnOwp9CgpmdW5jdGlvbiBsb2FkU3RhdHMoKXsKICBmZXRjaCgnL2FwaS9wYXJlbnRzJytzY29wZVBhcmFtKCkpLnRoZW4oZnVuY3Rpb24ocil7cmV0dXJuIHIuanNvbigpO30pLnRoZW4oZnVuY3Rpb24oZCl7CiAgICBBTExfUEFSRU5UUyA9IGQucGFyZW50cyB8fCBbXTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzcDInKS50ZXh0Q29udGVudCA9IEFMTF9QQVJFTlRTLmxlbmd0aDsKICAgIHVwZGF0ZUNvdW50KCk7CiAgICB2YXIgaCA9IEpTT04ucGFyc2UobG9jYWxTdG9yYWdlLmdldEl0ZW0oJ2JoXycrQ1VSUkVOVF9VU0VSLnVzZXJuYW1lKXx8J1tdJyk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3QyJykudGV4dENvbnRlbnQgPSBoLmZpbHRlcihmdW5jdGlvbih4KXtyZXR1cm4gbmV3IERhdGUoeC50KS50b0RhdGVTdHJpbmcoKT09PW5ldyBEYXRlKCkudG9EYXRlU3RyaW5nKCk7fSkubGVuZ3RoOwogICAgaWYoaC5sZW5ndGgpIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzbDMnKS50ZXh0Q29udGVudCA9IG5ldyBEYXRlKGhbMF0udCkudG9Mb2NhbGVEYXRlU3RyaW5nKCdlbi1HQicse2RheTonbnVtZXJpYycsbW9udGg6J3Nob3J0J30pOwogICAgcmVuZGVySGlzdG9yeShoKTsKICB9KS5jYXRjaChmdW5jdGlvbigpeyBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3AyJykudGV4dENvbnRlbnQ9JzAnOyB9KTsKfQoKZnVuY3Rpb24gbG9hZFBhcmVudHMoKXsKICBmZXRjaCgnL2FwaS9wYXJlbnRzJytzY29wZVBhcmFtKCkpLnRoZW4oZnVuY3Rpb24ocil7cmV0dXJuIHIuanNvbigpO30pLnRoZW4oZnVuY3Rpb24oZCl7CiAgICB2YXIgcGwgPSBkLnBhcmVudHMgfHwgW107CiAgICB2YXIgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGwyJyk7CiAgICBpZighcGwubGVuZ3RoKXsKICAgICAgZWwuaW5uZXJIVE1MPSc8ZGl2IHN0eWxlPSJjb2xvcjojOTRhM2I4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjBweCAwIj5ObyBwYXJlbnRzIGluIHlvdXIgc2NvcGUgeWV0LjwvZGl2Pic7CiAgICAgIHJldHVybjsKICAgIH0KICAgIGVsLmlubmVySFRNTD0nPHRhYmxlIGNsYXNzPSJwdGFibGUiPjx0aGVhZD48dHI+PHRoPk5hbWU8L3RoPjx0aD5QaG9uZTwvdGg+PHRoPkdyYWRlPC90aD48L3RyPjwvdGhlYWQ+PHRib2R5PicrCiAgICAgIHBsLm1hcChmdW5jdGlvbihwKXsKICAgICAgICB2YXIgZ3JhZGUgPSBwLmdyYWRlfHwnJzsKICAgICAgICB2YXIgcGlsbENscyA9IGdyYWRlLnRvTG93ZXJDYXNlKCkuaW5jbHVkZXMoJ2tnJyl8fHBhcnNlSW50KGdyYWRlLnJlcGxhY2UoL1xcRC9nLCcnKSk8NyA/ICdnLWp1bmlvcicgOiAnZy1zZW5pb3InOwogICAgICAgIGlmKGdyYWRlLnRvTG93ZXJDYXNlKCkuaW5jbHVkZXMoJ2tnJykpIHBpbGxDbHM9J2cta2cnOwogICAgICAgIHJldHVybiAnPHRyPjx0ZD4nKyhwLm5hbWV8fCfigJQnKSsnPC90ZD48dGQ+JytwLnBob25lKyc8L3RkPjx0ZD48c3BhbiBjbGFzcz0iZ3JhZGUtcGlsbCAnK3BpbGxDbHMrJyI+JytncmFkZSsnPC9zcGFuPjwvdGQ+PC90cj4nOwogICAgICB9KS5qb2luKCcnKSsnPC90Ym9keT48L3RhYmxlPic7CiAgfSkuY2F0Y2goZnVuY3Rpb24oKXsgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3BsMicpLmlubmVySFRNTD0nPGRpdiBzdHlsZT0iY29sb3I6Izk0YTNiODt0ZXh0LWFsaWduOmNlbnRlciI+Q291bGQgbm90IGxvYWQgcGFyZW50cy48L2Rpdj4nOyB9KTsKfQoKZnVuY3Rpb24gdXBkYXRlQ291bnQoKXsKICB2YXIgYyA9IFNFTF9HUkFERVMuaW5jbHVkZXMoJ0FsbCcpID8gQUxMX1BBUkVOVFMubGVuZ3RoIDoKICAgIEFMTF9QQVJFTlRTLmZpbHRlcihmdW5jdGlvbihwKXsKICAgICAgcmV0dXJuIFNFTF9HUkFERVMuc29tZShmdW5jdGlvbihnKXsgcmV0dXJuIChwLmdyYWRlfHwnJykudG9Mb3dlckNhc2UoKS5pbmNsdWRlcyhnLnRvTG93ZXJDYXNlKCkpOyB9KTsKICAgIH0pLmxlbmd0aDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncm4nKS50ZXh0Q29udGVudCA9IGM7Cn0KCi8vIOKUgOKUgCBQcmV2aWV3IOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiB1cGRhdGVQcmV2aWV3KCl7CiAgdmFyIHQgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXQnKS52YWx1ZTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY2MnKS50ZXh0Q29udGVudCA9IHQubGVuZ3RoOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwdnQnKS50ZXh0Q29udGVudCA9IG5ldyBEYXRlKCkudG9Mb2NhbGVUaW1lU3RyaW5nKCdlbi1VUycse2hvdXI6J251bWVyaWMnLG1pbnV0ZTonMi1kaWdpdCcsaG91cjEyOnRydWV9KTsKICBpZih0LnRyaW0oKSl7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncHYnKS50ZXh0Q29udGVudCA9ICfwn5OiIE1vZGVybiBJbmZpbml0eSBTY2hvb2xcXG5cXG4nK3QrJ1xcblxcbvCfk54gMDItMzc5Ni05MTU1JzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYjInKS5kaXNhYmxlZCA9IGZhbHNlOwogIH0gZWxzZSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncHYnKS50ZXh0Q29udGVudCA9ICdZb3VyIG1lc3NhZ2Ugd2lsbCBhcHBlYXIgaGVyZS4uLic7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc2IyJykuZGlzYWJsZWQgPSB0cnVlOwogIH0KICB1cGRhdGVDb3VudCgpOwp9CgovLyDilIDilIAgU2VuZCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKLy8gUGhvdG8gc3RhdGUKdmFyIHVwbG9hZGVkSW1hZ2VVcmwgPSAnJzsKCmZ1bmN0aW9uIGhhbmRsZVBob3RvU2VsZWN0KGUpewogIHZhciBmaWxlID0gZS50YXJnZXQuZmlsZXNbMF07CiAgaWYoIWZpbGUpIHJldHVybjsKICBpZihmaWxlLnNpemUgPiA1KjEwMjQqMTAyNCl7IGFsZXJ0KCdQaG90byBtdXN0IGJlIHVuZGVyIDVNQicpOyByZXR1cm47IH0KICB2YXIgcmVhZGVyID0gbmV3IEZpbGVSZWFkZXIoKTsKICByZWFkZXIub25sb2FkID0gZnVuY3Rpb24oZXYpewogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvUHJldmlldycpLnNyYyA9IGV2LnRhcmdldC5yZXN1bHQ7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9QcmV2aWV3Jykuc3R5bGUuZGlzcGxheT0nYmxvY2snOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvUGxhY2Vob2xkZXInKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1pvbmUnKS5jbGFzc0xpc3QuYWRkKCdoYXMtaW1nJyk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncmVtb3ZlUGhvdG8nKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9QcmV2aWV3QmFkZ2UnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICB1cGRhdGVQcmV2aWV3KCk7CiAgfTsKICByZWFkZXIucmVhZEFzRGF0YVVSTChmaWxlKTsKfQoKZnVuY3Rpb24gcmVtb3ZlUGhvdG9GbigpewogIHVwbG9hZGVkSW1hZ2VVcmw9Jyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvSW5wdXQnKS52YWx1ZT0nJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9QcmV2aWV3Jykuc3JjPScnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1ByZXZpZXcnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9QbGFjZWhvbGRlcicpLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9ab25lJykuY2xhc3NMaXN0LnJlbW92ZSgnaGFzLWltZycpOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdyZW1vdmVQaG90bycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1ByZXZpZXdCYWRnZScpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWRTdGF0dXMnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICB1cGRhdGVQcmV2aWV3KCk7Cn0KCmFzeW5jIGZ1bmN0aW9uIGltYWdlVG9CYXNlNjQoZmlsZSl7CiAgLy8gUmVzaXplIHRvIDgwMHB4IG1heCBhbmQgY29tcHJlc3MgdG8gfjIwMEtCIGJlZm9yZSBzZW5kaW5nCiAgcmV0dXJuIG5ldyBQcm9taXNlKGZ1bmN0aW9uKHJlc29sdmUpewogICAgdmFyIGltZyA9IG5ldyBJbWFnZSgpOwogICAgdmFyIHVybCA9IFVSTC5jcmVhdGVPYmplY3RVUkwoZmlsZSk7CiAgICBpbWcub25sb2FkID0gZnVuY3Rpb24oKXsKICAgICAgdmFyIGNhbnZhcyA9IGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2NhbnZhcycpOwogICAgICB2YXIgTUFYID0gODAwOwogICAgICB2YXIgdyA9IGltZy53aWR0aCwgaCA9IGltZy5oZWlnaHQ7CiAgICAgIGlmKHcgPiBoKXsgaWYodz5NQVgpe2g9TWF0aC5yb3VuZChoKk1BWC93KTt3PU1BWDt9IH0KICAgICAgZWxzZSAgICAgIHsgaWYoaD5NQVgpe3c9TWF0aC5yb3VuZCh3Kk1BWC9oKTtoPU1BWDt9IH0KICAgICAgY2FudmFzLndpZHRoID0gdzsgY2FudmFzLmhlaWdodCA9IGg7CiAgICAgIGNhbnZhcy5nZXRDb250ZXh0KCcyZCcpLmRyYXdJbWFnZShpbWcsIDAsIDAsIHcsIGgpOwogICAgICAvLyBTdHJpcCB0aGUgZGF0YTppbWFnZS9qcGVnO2Jhc2U2NCwgcHJlZml4CiAgICAgIHZhciBiNjQgPSBjYW52YXMudG9EYXRhVVJMKCdpbWFnZS9qcGVnJywgMC42NSkuc3BsaXQoJywnKVsxXTsKICAgICAgVVJMLnJldm9rZU9iamVjdFVSTCh1cmwpOwogICAgICByZXNvbHZlKGI2NCk7CiAgICB9OwogICAgaW1nLnNyYyA9IHVybDsKICB9KTsKfQoKYXN5bmMgZnVuY3Rpb24gdXBsb2FkUGhvdG8oKXsKICB2YXIgZmlsZSA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b0lucHV0JykuZmlsZXNbMF07CiAgaWYoIWZpbGUpIHJldHVybiBudWxsOwogIHZhciBzdCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd1cGxvYWRTdGF0dXMnKTsKICBzdC5zdHlsZS5kaXNwbGF5PSdibG9jayc7IHN0LnRleHRDb250ZW50PSdcdTIzZjMgQ29tcHJlc3NpbmcgcGhvdG8uLi4nOwogIHRyeXsKICAgIC8vIEdldCBXQSBjcmVkZW50aWFscyBmcm9tIHNlcnZlcgogICAgdmFyIGNmZ1JlcyA9IGF3YWl0IGZldGNoKCcvYXBpL3dhLWNvbmZpZycpOwogICAgaWYoIWNmZ1Jlcy5vayl7IHN0LnRleHRDb250ZW50PSfinYwgQ29uZmlnIGVycm9yICcrY2ZnUmVzLnN0YXR1czsgcmV0dXJuIG51bGw7IH0KICAgIHZhciBjZmcgPSBhd2FpdCBjZmdSZXMuanNvbigpOwogICAgLy8gUmVzaXplICsgY29tcHJlc3MgaW1hZ2UgaW4gYnJvd3NlcgogICAgdmFyIGJsb2IgPSBhd2FpdCBuZXcgUHJvbWlzZShmdW5jdGlvbihyZXNvbHZlKXsKICAgICAgdmFyIGltZyA9IG5ldyBJbWFnZSgpOwogICAgICB2YXIgdXJsID0gVVJMLmNyZWF0ZU9iamVjdFVSTChmaWxlKTsKICAgICAgaW1nLm9ubG9hZCA9IGZ1bmN0aW9uKCl7CiAgICAgICAgdmFyIGMgPSBkb2N1bWVudC5jcmVhdGVFbGVtZW50KCdjYW52YXMnKTsKICAgICAgICB2YXIgTUFYPTgwMCx3PWltZy53aWR0aCxoPWltZy5oZWlnaHQ7CiAgICAgICAgaWYodz5oKXtpZih3Pk1BWCl7aD1NYXRoLnJvdW5kKGgqTUFYL3cpO3c9TUFYO319CiAgICAgICAgZWxzZXtpZihoPk1BWCl7dz1NYXRoLnJvdW5kKHcqTUFYL2gpO2g9TUFYO319CiAgICAgICAgYy53aWR0aD13OyBjLmhlaWdodD1oOwogICAgICAgIGMuZ2V0Q29udGV4dCgnMmQnKS5kcmF3SW1hZ2UoaW1nLDAsMCx3LGgpOwogICAgICAgIGMudG9CbG9iKGZ1bmN0aW9uKGIpe3Jlc29sdmUoYik7fSwnaW1hZ2UvanBlZycsMC42NSk7CiAgICAgICAgVVJMLnJldm9rZU9iamVjdFVSTCh1cmwpOwogICAgICB9OwogICAgICBpbWcuc3JjPXVybDsKICAgIH0pOwogICAgc3QudGV4dENvbnRlbnQ9J1x1MjNmMyBVcGxvYWRpbmcgcGhvdG8uLi4nOwogICAgLy8gVXBsb2FkIGRpcmVjdGx5IHRvIE1ldGEgQVBJIGZyb20gYnJvd3NlciDigJQgbm8gUmFpbHdheSBwcm94eQogICAgdmFyIGZkID0gbmV3IEZvcm1EYXRhKCk7CiAgICBmZC5hcHBlbmQoJ2ZpbGUnLCBibG9iLCAncGhvdG8uanBnJyk7CiAgICBmZC5hcHBlbmQoJ21lc3NhZ2luZ19wcm9kdWN0Jywnd2hhdHNhcHAnKTsKICAgIHZhciB1cCA9IGF3YWl0IGZldGNoKAogICAgICAnaHR0cHM6Ly9ncmFwaC5mYWNlYm9vay5jb20vdjE4LjAvJytjZmcucGhvbmVfbnVtYmVyX2lkKycvbWVkaWEnLAogICAgICB7bWV0aG9kOidQT1NUJyxoZWFkZXJzOnsnQXV0aG9yaXphdGlvbic6J0JlYXJlciAnK2NmZy5hY2Nlc3NfdG9rZW59LGJvZHk6ZmR9CiAgICApOwogICAgdmFyIHVwZCA9IGF3YWl0IHVwLmpzb24oKTsKICAgIGlmKHVwZC5pZCl7CiAgICAgIHN0LnRleHRDb250ZW50PSdcdTI3MDUgUGhvdG8gcmVhZHkgdG8gc2VuZCc7CiAgICAgIHJldHVybiB1cGQuaWQ7CiAgICB9IGVsc2UgewogICAgICBzdC50ZXh0Q29udGVudD0nXHUyNzRjIFVwbG9hZCBmYWlsZWQ6ICcrKHVwZC5lcnJvciYmdXBkLmVycm9yLm1lc3NhZ2V8fEpTT04uc3RyaW5naWZ5KHVwZCkpOwogICAgICByZXR1cm4gbnVsbDsKICAgIH0KICB9IGNhdGNoKGUpewogICAgc3QudGV4dENvbnRlbnQ9J1x1Mjc0YyBVcGxvYWQgZXJyb3I6ICcrZS5tZXNzYWdlOwogICAgcmV0dXJuIG51bGw7CiAgfQp9Cgphc3luYyBmdW5jdGlvbiBkb1NlbmQoKXsKICB2YXIgbXNnID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ210JykudmFsdWUudHJpbSgpOwogIGlmKCFtc2cpIHJldHVybjsKICB2YXIgYnRuID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NiMicpOwogIGJ0bi5kaXNhYmxlZD10cnVlOyBidG4udGV4dENvbnRlbnQ9J+KPsyBTZW5kaW5nLi4uJzsKICAvLyBVcGxvYWQgcGhvdG8gZmlyc3QgaWYgc2VsZWN0ZWQKICB2YXIgcGhvdG9GaWxlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvSW5wdXQnKS5maWxlc1swXTsKICB2YXIgbWVkaWFJZCA9ICcnOwogIGlmKHBob3RvRmlsZSl7CiAgICBidG4udGV4dENvbnRlbnQ9J+KPsyBVcGxvYWRpbmcgcGhvdG8uLi4nOwogICAgbWVkaWFJZCA9IGF3YWl0IHVwbG9hZFBob3RvKCkgfHwgJyc7CiAgICBpZighbWVkaWFJZCl7IGJ0bi5kaXNhYmxlZD1mYWxzZTsgYnRuLnRleHRDb250ZW50PSfwn5OkIFNlbmQgdG8gUGFyZW50cyc7IHJldHVybjsgfQogIH0KICBidG4udGV4dENvbnRlbnQ9J+KPsyBTZW5kaW5nLi4uJzsKICBmZXRjaCgnL2Jyb2FkY2FzdCcsewogICAgbWV0aG9kOidQT1NUJywKICAgIGhlYWRlcnM6eydDb250ZW50LVR5cGUnOidhcHBsaWNhdGlvbi9qc29uJ30sCiAgICBib2R5OkpTT04uc3RyaW5naWZ5KHsKICAgICAgbWVzc2FnZTogbXNnLAogICAgICBtZWRpYV9pZDogbWVkaWFJZCwKICAgICAgZ3JhZGVzOiAgU0VMX0dSQURFUywKICAgICAgYWRtaW5fc2NvcGU6IENVUlJFTlRfVVNFUi5ncmFkZXMgICAvLyBzZXJ2ZXIgZW5mb3JjZXMgdGhpcwogICAgfSkKICB9KS50aGVuKGZ1bmN0aW9uKHIpe3JldHVybiByLmpzb24oKTt9KS50aGVuKGZ1bmN0aW9uKGQpewogICAgdmFyIGggPSBKU09OLnBhcnNlKGxvY2FsU3RvcmFnZS5nZXRJdGVtKCdiaF8nK0NVUlJFTlRfVVNFUi51c2VybmFtZSl8fCdbXScpOwogICAgaC51bnNoaWZ0KHttc2c6bXNnLGc6U0VMX0dSQURFUyxzOmQuc2VudHx8MCx0Om5ldyBEYXRlKCkudG9JU09TdHJpbmcoKX0pOwogICAgbG9jYWxTdG9yYWdlLnNldEl0ZW0oJ2JoXycrQ1VSUkVOVF9VU0VSLnVzZXJuYW1lLCBKU09OLnN0cmluZ2lmeShoLnNsaWNlKDAsNTApKSk7CiAgICByZW5kZXJIaXN0b3J5KGgpOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21zJykudGV4dENvbnRlbnQ9J1NlbnQgdG8gJysoZC5zZW50fHwwKSsnIHBhcmVudHMgc3VjY2Vzc2Z1bGx5Lic7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbW8nKS5jbGFzc0xpc3QuYWRkKCdzaG93Jyk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXQnKS52YWx1ZT0nJzsKICAgIHVwZGF0ZVByZXZpZXcoKTsKICAgIGxvYWRTdGF0cygpOwogIH0pLmNhdGNoKGZ1bmN0aW9uKCl7IGFsZXJ0KCdFcnJvciBzZW5kaW5nLiBQbGVhc2UgdHJ5IGFnYWluLicpOyB9KTsKICBidG4uZGlzYWJsZWQ9ZmFsc2U7IGJ0bi50ZXh0Q29udGVudD0n8J+TpCBTZW5kIHRvIFBhcmVudHMnOwp9CgpmdW5jdGlvbiBjbG9zZU1vZGFsKCl7IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtbycpLmNsYXNzTGlzdC5yZW1vdmUoJ3Nob3cnKTsgfQoKLy8g4pSA4pSAIEhpc3Rvcnkg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIHJlbmRlckhpc3RvcnkoaCl7CiAgdmFyIGwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnaGwnKTsKICBpZighaHx8IWgubGVuZ3RoKXsKICAgIGwuaW5uZXJIVE1MPSc8ZGl2IHN0eWxlPSJjb2xvcjojOTRhM2I4O3RleHQtYWxpZ246Y2VudGVyO3BhZGRpbmc6MjBweCAwIj5ObyBhbm5vdW5jZW1lbnRzIHlldDwvZGl2Pic7CiAgICByZXR1cm47CiAgfQogIGwuaW5uZXJIVE1MID0gaC5tYXAoZnVuY3Rpb24oeCl7CiAgICByZXR1cm4gJzxkaXYgY2xhc3M9ImhpIj4nKwogICAgICAnPGRpdiBzdHlsZT0id2lkdGg6NDBweDtoZWlnaHQ6NDBweDtib3JkZXItcmFkaXVzOjEwcHg7YmFja2dyb3VuZDojZjBmZGY0O2Rpc3BsYXk6ZmxleDthbGlnbi1pdGVtczpjZW50ZXI7anVzdGlmeS1jb250ZW50OmNlbnRlcjtmb250LXNpemU6MThweDtmbGV4LXNocmluazowIj7wn5OjPC9kaXY+JysKICAgICAgJzxkaXYgY2xhc3M9ImhpYyI+PGRpdj4nK3gubXNnKyc8L2Rpdj4nKwogICAgICAnPGRpdiBjbGFzcz0iaG0iPicrCiAgICAgICAgbmV3IERhdGUoeC50KS50b0xvY2FsZVN0cmluZygnZW4tR0InLHtkYXk6J251bWVyaWMnLG1vbnRoOidzaG9ydCcsaG91cjonMi1kaWdpdCcsbWludXRlOicyLWRpZ2l0J30pKwogICAgICAgICcgwrcgJysoeC5nfHxbJ0FsbCddKS5qb2luKCcsICcpKwogICAgICAgICcgwrcgPHNwYW4gY2xhc3M9ImhiIj7inIUgJysoeC5zfHwwKSsnIHNlbnQ8L3NwYW4+JysKICAgICAgJzwvZGl2PjwvZGl2PjwvZGl2Pic7CiAgfSkuam9pbignJyk7Cn0KCi8vIOKUgOKUgCBUYWJzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBzaG93VGFiKHQpewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0YWItYicpLnN0eWxlLmRpc3BsYXkgPSB0PT09J2InPydibG9jayc6J25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0YWItaCcpLnN0eWxlLmRpc3BsYXkgPSB0PT09J2gnPydibG9jayc6J25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0YWItcCcpLnN0eWxlLmRpc3BsYXkgPSB0PT09J3AnPydibG9jayc6J25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzcicpLnN0eWxlLmRpc3BsYXkgICAgPSB0PT09J2InPydncmlkJzonbm9uZSc7CiAgdmFyIHR0PXtiOlsn8J+ToyBTZW5kIEFubm91bmNlbWVudCcsJ0Jyb2FkY2FzdCBhIG1lc3NhZ2UgdG8gcGFyZW50cyB2aWEgV2hhdHNBcHAnXSwKICAgICAgICAgIGg6Wyfwn5OLIEhpc3RvcnknLCdBbGwgYW5ub3VuY2VtZW50cyBzZW50IGZyb20gdGhpcyBhY2NvdW50J10sCiAgICAgICAgICBwOlsn8J+RpSBQYXJlbnRzJywnUGFyZW50cyByZWdpc3RlcmVkIHdpdGhpbiB5b3VyIGdyYWRlIHNjb3BlJ119OwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwdCcpLnRleHRDb250ZW50ID0gdHRbdF1bMF07CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3BzJykudGV4dENvbnRlbnQgPSB0dFt0XVsxXTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcubmknKS5mb3JFYWNoKGZ1bmN0aW9uKGUsaSl7IGUuY2xhc3NMaXN0LnRvZ2dsZSgnYWN0aXZlJyxbJ2InLCdoJywncCddW2ldPT09dCk7IH0pOwogIGlmKHQ9PT0ncCcpIGxvYWRQYXJlbnRzKCk7Cn0KCi8vIOKUgOKUgCBMb2dvdXQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIGRvTG9nb3V0KCl7CiAgQ1VSUkVOVF9VU0VSPW51bGw7IEFMTF9QQVJFTlRTPVtdOyBTRUxfR1JBREVTPVsnQWxsJ107CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2F3Jykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2x3Jykuc3R5bGUuZGlzcGxheT0nZmxleCc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2x1JykudmFsdWU9Jyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xwJykudmFsdWU9Jyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xlJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnJvbGUtY2FyZCcpLmZvckVhY2goZnVuY3Rpb24oYyxpKXtjLmNsYXNzTGlzdC50b2dnbGUoJ2FjdGl2ZScsaT09PTApO30pOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzY29wZUhpbnQnKS5pbm5lckhUTUwgPSBST0xFX0hJTlRTWydhZG1pbiddOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtdCcpLnZhbHVlPScnOwp9Cjwvc2NyaXB0PjwvZGl2PgogIDxkaXYgY2xhc3M9InBhbmUiIGlkPSJwYW5lLWh3Ij48IS0tIOKUgOKUgCBMT0dJTiBTQ1JFRU4g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAIC0tPgo8ZGl2IGlkPSJsb2dpbi1zY3JlZW4iPgogIDxkaXYgY2xhc3M9ImxvZ2luLWJveCI+CiAgICA8ZGl2IGNsYXNzPSJsb2dpbi1sb2dvIj4KICAgICAgPGRpdiBjbGFzcz0iaWNvbiI+8J+PqzwvZGl2PgogICAgICA8aDE+VGVhY2hlciBQYW5lbDwvaDE+CiAgICAgIDxwPk1vZGVybiBJbmZpbml0eSBMYW5ndWFnZSBTY2hvb2w8L3A+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImxvZ2luLWZpZWxkIj4KICAgICAgPGxhYmVsPlVzZXJuYW1lPC9sYWJlbD4KICAgICAgPGlucHV0IHR5cGU9InRleHQiIGlkPSJsb2dpbi11c2VyIiBwbGFjZWhvbGRlcj0iRW50ZXIgdXNlcm5hbWUiIGF1dG9jb21wbGV0ZT0idXNlcm5hbWUiPgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJsb2dpbi1maWVsZCI+CiAgICAgIDxsYWJlbD5QYXNzd29yZDwvbGFiZWw+CiAgICAgIDxpbnB1dCB0eXBlPSJwYXNzd29yZCIgaWQ9ImxvZ2luLXBhc3MiIHBsYWNlaG9sZGVyPSJFbnRlciBwYXNzd29yZCIgYXV0b2NvbXBsZXRlPSJjdXJyZW50LXBhc3N3b3JkIgogICAgICAgIG9ua2V5ZG93bj0iaWYoZXZlbnQua2V5PT09J0VudGVyJykgZG9UZWFjaGVyTG9naW4oKSI+CiAgICA8L2Rpdj4KICAgIDxidXR0b24gY2xhc3M9ImxvZ2luLWJ0biIgb25jbGljaz0iZG9UZWFjaGVyTG9naW4oKSI+U2lnbiBJbiDihpI8L2J1dHRvbj4KICAgIDxkaXYgY2xhc3M9ImxvZ2luLWVycm9yIiBpZD0ibG9naW4tZXJyb3IiPuKdjCBJbmNvcnJlY3QgdXNlcm5hbWUgb3IgcGFzc3dvcmQ8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIOKUgOKUgCBNQUlOIFBBTkVMIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAtLT4KPGRpdiBpZD0ibWFpbi1wYW5lbCI+CgogIDxkaXYgY2xhc3M9InRvcGJhciI+CiAgICA8ZGl2IGNsYXNzPSJ0b3BiYXItbGVmdCI+CiAgICAgIDxkaXYgY2xhc3M9Imljb24iPvCfj6s8L2Rpdj4KICAgICAgPGRpdj4KICAgICAgICA8aDE+VGVhY2hlciBQYW5lbDwvaDE+CiAgICAgICAgPHA+TW9kZXJuIEluZmluaXR5IExhbmd1YWdlIFNjaG9vbDwvcD4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InRvcGJhci1yaWdodCI+CiAgICAgIDxzcGFuIGNsYXNzPSJ0ZWFjaGVyLWJhZGdlIiBpZD0idGVhY2hlci1iYWRnZSI+8J+RpCBUZWFjaGVyPC9zcGFuPgogICAgICA8YnV0dG9uIGNsYXNzPSJsb2dvdXQtYnRuIiBvbmNsaWNrPSJkb0xvZ291dCgpIj5TaWduIG91dDwvYnV0dG9uPgogICAgPC9kaXY+CiAgPC9kaXY+CgogIDxkaXYgY2xhc3M9InRhYnMiPgogICAgPGJ1dHRvbiBjbGFzcz0idGFiLWJ0biBhY3RpdmUiIG9uY2xpY2s9InN3aXRjaFRhYignaG9tZXdvcmsnLCB0aGlzKSI+8J+TmiBBZGQgSG9tZXdvcms8L2J1dHRvbj4KICAgIDxidXR0b24gY2xhc3M9InRhYi1idG4iIG9uY2xpY2s9InN3aXRjaFRhYignZXhhbXMnLCB0aGlzKSI+8J+TnSBFeGFtIFJlc3VsdHM8L2J1dHRvbj4KICA8L2Rpdj4KCiAgPGRpdiBjbGFzcz0iY2FyZCI+CgogICAgPCEtLSDilIDilIAgSE9NRVdPUksgVEFCIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAtLT4KICAgIDxkaXYgY2xhc3M9InRhYi1wYW5lbCBhY3RpdmUiIGlkPSJ0YWItaG9tZXdvcmsiPgogICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+CiAgICAgICAgPGxhYmVsPvCfkaQgVGVhY2hlciBOYW1lIDxzcGFuPio8L3NwYW4+PC9sYWJlbD4KICAgICAgICA8aW5wdXQgdHlwZT0idGV4dCIgaWQ9InRlYWNoZXIiIHBsYWNlaG9sZGVyPSJlLmcuIE1zLiBTYXJhIj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InJvdy0yIj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+CiAgICAgICAgICA8bGFiZWw+8J+OkyBHcmFkZSA8c3Bhbj4qPC9zcGFuPjwvbGFiZWw+CiAgICAgICAgICA8c2VsZWN0IGlkPSJncmFkZSI+CiAgICAgICAgICAgIDxvcHRpb24gdmFsdWU9IiI+U2VsZWN0IGdyYWRl4oCmPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+S0cxPC9vcHRpb24+PG9wdGlvbj5LRzI8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5HcmFkZSAxPC9vcHRpb24+PG9wdGlvbj5HcmFkZSAyPC9vcHRpb24+PG9wdGlvbj5HcmFkZSAzPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+R3JhZGUgNDwvb3B0aW9uPjxvcHRpb24+R3JhZGUgNTwvb3B0aW9uPjxvcHRpb24+R3JhZGUgNjwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPkdyYWRlIDc8L29wdGlvbj48b3B0aW9uPkdyYWRlIDg8L29wdGlvbj48b3B0aW9uPkdyYWRlIDk8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5HcmFkZSAxMDwvb3B0aW9uPjxvcHRpb24+R3JhZGUgMTE8L29wdGlvbj48b3B0aW9uPkdyYWRlIDEyPC9vcHRpb24+CiAgICAgICAgICA8L3NlbGVjdD4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+CiAgICAgICAgICA8bGFiZWw+8J+TliBTdWJqZWN0IDxzcGFuPio8L3NwYW4+PC9sYWJlbD4KICAgICAgICAgIDxzZWxlY3QgaWQ9InN1YmplY3QiPgogICAgICAgICAgICA8b3B0aW9uIHZhbHVlPSIiPlNlbGVjdCBzdWJqZWN04oCmPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+TWF0aDwvb3B0aW9uPjxvcHRpb24+QXJhYmljPC9vcHRpb24+PG9wdGlvbj5FbmdsaXNoPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+U2NpZW5jZTwvb3B0aW9uPjxvcHRpb24+U29jaWFsIFN0dWRpZXM8L29wdGlvbj48b3B0aW9uPlBoeXNpY3M8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5DaGVtaXN0cnk8L29wdGlvbj48b3B0aW9uPkJpb2xvZ3k8L29wdGlvbj48b3B0aW9uPkZyZW5jaDwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPkNvbXB1dGVyPC9vcHRpb24+PG9wdGlvbj5Jc2xhbWljIFN0dWRpZXM8L29wdGlvbj48b3B0aW9uPkFydDwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPk11c2ljPC9vcHRpb24+PG9wdGlvbj5QRTwvb3B0aW9uPjxvcHRpb24+SGlzdG9yeTwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPkdlb2dyYXBoeTwvb3B0aW9uPjxvcHRpb24+QWN0aXZpdGllczwvb3B0aW9uPjxvcHRpb24+T3RoZXI8L29wdGlvbj4KICAgICAgICAgIDwvc2VsZWN0PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPgogICAgICAgIDxsYWJlbD7wn5OdIEFzc2lnbm1lbnQgPHNwYW4+Kjwvc3Bhbj48L2xhYmVsPgogICAgICAgIDx0ZXh0YXJlYSBpZD0iYXNzaWdubWVudCIgcGxhY2Vob2xkZXI9IkRlc2NyaWJlIHRoZSBob21ld29yayBjbGVhcmx54oCmIj48L3RleHRhcmVhPgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0icm93LTIiPgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj4KICAgICAgICAgIDxsYWJlbD7wn5OFIER1ZSBEYXRlIDxzcGFuPio8L3NwYW4+PC9sYWJlbD4KICAgICAgICAgIDxpbnB1dCB0eXBlPSJkYXRlIiBpZD0iZHVlX2RhdGUiPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj4KICAgICAgICAgIDxsYWJlbD7wn5eC77iPIFR5cGU8L2xhYmVsPgogICAgICAgICAgPHNlbGVjdCBpZD0idHlwZSI+CiAgICAgICAgICAgIDxvcHRpb24+V29ya3NoZWV0PC9vcHRpb24+PG9wdGlvbj5Xb3JrYm9vazwvb3B0aW9uPjxvcHRpb24+RXNzYXk8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5SZWFkaW5nPC9vcHRpb24+PG9wdGlvbj5TdW1tYXJ5PC9vcHRpb24+PG9wdGlvbj5SZXNlYXJjaDwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPk1lbW9yaXphdGlvbjwvb3B0aW9uPjxvcHRpb24+RHJhd2luZzwvb3B0aW9uPjxvcHRpb24+U3R1ZHk8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5FeGVyY2lzZXM8L29wdGlvbj48b3B0aW9uPkxhYiBSZXBvcnQ8L29wdGlvbj48b3B0aW9uPlJldmlzaW9uPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+RXhhbSBQcmFjdGljZTwvb3B0aW9uPjxvcHRpb24+T3RoZXI8L29wdGlvbj4KICAgICAgICAgIDwvc2VsZWN0PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPgogICAgICAgIDxsYWJlbD7wn5KsIE5vdGVzIChvcHRpb25hbCk8L2xhYmVsPgogICAgICAgIDxpbnB1dCB0eXBlPSJ0ZXh0IiBpZD0ibm90ZXMiIHBsYWNlaG9sZGVyPSJlLmcuIEJyaW5nIGNhbGN1bGF0b3IsIGhhbmR3cml0dGVuIG9ubHnigKYiPgogICAgICA8L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0ic3VibWl0LWJ0biIgb25jbGljaz0ic3VibWl0SG9tZXdvcmsoKSI+4p6VIEFERCBIT01FV09SSzwvYnV0dG9uPgogICAgICA8ZGl2IGNsYXNzPSJzdGF0dXMiIGlkPSJody1zdGF0dXMiPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJoaXN0b3J5Ij4KICAgICAgICA8aDM+8J+TiyBSZWNlbnRseSBBZGRlZCAodGhpcyBzZXNzaW9uKTwvaDM+CiAgICAgICAgPGRpdiBpZD0iaHctaGlzdG9yeSI+PGRpdiBjbGFzcz0ibm8taGlzdCI+Tm90aGluZyBhZGRlZCB5ZXQuPC9kaXY+PC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CgogICAgPCEtLSDilIDilIAgRVhBTSBUQUIg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAIC0tPgogICAgPGRpdiBjbGFzcz0idGFiLXBhbmVsIiBpZD0idGFiLWV4YW1zIj4KICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPgogICAgICAgIDxsYWJlbD7wn5GkIFRlYWNoZXIgTmFtZSA8c3Bhbj4qPC9zcGFuPjwvbGFiZWw+CiAgICAgICAgPGlucHV0IHR5cGU9InRleHQiIGlkPSJleC10ZWFjaGVyIiBwbGFjZWhvbGRlcj0iZS5nLiBNci4gSGFzc2FuIj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InJvdy0yIj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+CiAgICAgICAgICA8bGFiZWw+8J+OkyBHcmFkZSA8c3Bhbj4qPC9zcGFuPjwvbGFiZWw+CiAgICAgICAgICA8c2VsZWN0IGlkPSJleC1ncmFkZSIgb25jaGFuZ2U9ImNsZWFyU3R1ZGVudHMoKSI+CiAgICAgICAgICAgIDxvcHRpb24gdmFsdWU9IiI+U2VsZWN0IGdyYWRl4oCmPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+S0cxPC9vcHRpb24+PG9wdGlvbj5LRzI8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5HcmFkZSAxPC9vcHRpb24+PG9wdGlvbj5HcmFkZSAyPC9vcHRpb24+PG9wdGlvbj5HcmFkZSAzPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+R3JhZGUgNDwvb3B0aW9uPjxvcHRpb24+R3JhZGUgNTwvb3B0aW9uPjxvcHRpb24+R3JhZGUgNjwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPkdyYWRlIDc8L29wdGlvbj48b3B0aW9uPkdyYWRlIDg8L29wdGlvbj48b3B0aW9uPkdyYWRlIDk8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5HcmFkZSAxMDwvb3B0aW9uPjxvcHRpb24+R3JhZGUgMTE8L29wdGlvbj48b3B0aW9uPkdyYWRlIDEyPC9vcHRpb24+CiAgICAgICAgICA8L3NlbGVjdD4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+CiAgICAgICAgICA8bGFiZWw+8J+TliBTdWJqZWN0IDxzcGFuPio8L3NwYW4+PC9sYWJlbD4KICAgICAgICAgIDxzZWxlY3QgaWQ9ImV4LXN1YmplY3QiPgogICAgICAgICAgICA8b3B0aW9uIHZhbHVlPSIiPlNlbGVjdCBzdWJqZWN04oCmPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+TWF0aDwvb3B0aW9uPjxvcHRpb24+QXJhYmljPC9vcHRpb24+PG9wdGlvbj5FbmdsaXNoPC9vcHRpb24+CiAgICAgICAgICAgIDxvcHRpb24+U2NpZW5jZTwvb3B0aW9uPjxvcHRpb24+U29jaWFsIFN0dWRpZXM8L29wdGlvbj48b3B0aW9uPlBoeXNpY3M8L29wdGlvbj4KICAgICAgICAgICAgPG9wdGlvbj5DaGVtaXN0cnk8L29wdGlvbj48b3B0aW9uPkJpb2xvZ3k8L29wdGlvbj48b3B0aW9uPkZyZW5jaDwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPkNvbXB1dGVyPC9vcHRpb24+PG9wdGlvbj5Jc2xhbWljIFN0dWRpZXM8L29wdGlvbj48b3B0aW9uPkFydDwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPk11c2ljPC9vcHRpb24+PG9wdGlvbj5QRTwvb3B0aW9uPjxvcHRpb24+SGlzdG9yeTwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPkdlb2dyYXBoeTwvb3B0aW9uPjxvcHRpb24+QWN0aXZpdGllczwvb3B0aW9uPjxvcHRpb24+T3RoZXI8L29wdGlvbj4KICAgICAgICAgIDwvc2VsZWN0PgogICAgICAgIDwvZGl2PgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0icm93LTMiPgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj4KICAgICAgICAgIDxsYWJlbD7wn5OFIEV4YW0gRGF0ZTwvbGFiZWw+CiAgICAgICAgICA8aW5wdXQgdHlwZT0iZGF0ZSIgaWQ9ImV4LWRhdGUiPgogICAgICAgIDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj4KICAgICAgICAgIDxsYWJlbD7wn5OLIFRlcm08L2xhYmVsPgogICAgICAgICAgPHNlbGVjdCBpZD0iZXgtdGVybSI+CiAgICAgICAgICAgIDxvcHRpb24+VGVybSAxPC9vcHRpb24+PG9wdGlvbj5UZXJtIDI8L29wdGlvbj48b3B0aW9uPlRlcm0gMzwvb3B0aW9uPgogICAgICAgICAgICA8b3B0aW9uPk1pZHRlcm08L29wdGlvbj48b3B0aW9uPkZpbmFsPC9vcHRpb24+PG9wdGlvbj5RdWl6PC9vcHRpb24+CiAgICAgICAgICA8L3NlbGVjdD4KICAgICAgICA8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+CiAgICAgICAgICA8bGFiZWw+8J+UoiBUb3RhbCBNYXJrczwvbGFiZWw+CiAgICAgICAgICA8aW5wdXQgdHlwZT0ibnVtYmVyIiBpZD0iZXgtdG90YWwiIHZhbHVlPSIxMDAiIG1pbj0iMSIgbWF4PSIxMDAwIj4KICAgICAgICA8L2Rpdj4KICAgICAgPC9kaXY+CiAgICAgIDxidXR0b24gY2xhc3M9ImxvYWQtYnRuIiBpZD0ibG9hZEJ0biIgb25jbGljaz0ibG9hZFN0dWRlbnRzKCkiPgogICAgICAgIPCfkaUgTG9hZCBTdHVkZW50cyBmb3IgVGhpcyBHcmFkZQogICAgICA8L2J1dHRvbj4KICAgICAgPGRpdiBpZD0iZXgtc3RhdHVzIiBjbGFzcz0ic3RhdHVzIj48L2Rpdj4KICAgICAgPGRpdiBpZD0ic3R1ZGVudHMtc2VjdGlvbiIgc3R5bGU9ImRpc3BsYXk6bm9uZSI+CiAgICAgICAgPHAgY2xhc3M9InN0dWRlbnQtY291bnQiIGlkPSJzdHVkZW50LWNvdW50Ij48L3A+CiAgICAgICAgPHRhYmxlIGNsYXNzPSJzdHVkZW50LXRhYmxlIj4KICAgICAgICAgIDx0aGVhZD4KICAgICAgICAgICAgPHRyPgogICAgICAgICAgICAgIDx0aD4jPC90aD48dGg+U3R1ZGVudCBOYW1lPC90aD48dGg+SUQ8L3RoPgogICAgICAgICAgICAgIDx0aD5TY29yZTwvdGg+PHRoPiU8L3RoPjx0aD5HcmFkZTwvdGg+PHRoPk5vdGVzPC90aD4KICAgICAgICAgICAgPC90cj4KICAgICAgICAgIDwvdGhlYWQ+CiAgICAgICAgICA8dGJvZHkgaWQ9InN0dWRlbnRzLXRib2R5Ij48L3Rib2R5PgogICAgICAgIDwvdGFibGU+CiAgICAgICAgPGJ1dHRvbiBjbGFzcz0iZXhhbS1zdWJtaXQtYnRuIiBpZD0iZXhhbVN1Ym1pdEJ0biIgb25jbGljaz0ic3VibWl0RXhhbVJlc3VsdHMoKSI+CiAgICAgICAgICDwn5K+IFNBVkUgQUxMIFJFU1VMVFMgVE8gU0hFRVQKICAgICAgICA8L2J1dHRvbj4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImRpdmlkZXIiPjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJoaXN0b3J5Ij4KICAgICAgICA8aDM+4pyFIFJlY2VudGx5IFNhdmVkICh0aGlzIHNlc3Npb24pPC9oMz4KICAgICAgICA8ZGl2IGlkPSJleC1oaXN0b3J5Ij48ZGl2IGNsYXNzPSJuby1oaXN0Ij5Ob3RoaW5nIHNhdmVkIHlldC48L2Rpdj48L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KCiAgPC9kaXY+PCEtLSAuY2FyZCAtLT4KPC9kaXY+PCEtLSAjbWFpbi1wYW5lbCAtLT4KCjxzY3JpcHQ+Ci8vIOKUgOKUgCBDcmVkZW50aWFscyAoY2hlY2tlZCBjbGllbnQtc2lkZSArIHNlcnZlciB2YWxpZGF0ZXMgb24gQVBJIGNhbGxzKSDilIDilIDilIDilIDilIAKLy8g4pSA4pSAIEF1dGggc3RhdGUg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACnZhciBDVVJSRU5UX1RFQUNIRVIgPSAiIjsKdmFyIENVUlJFTlRfRlVMTF9OQU1FID0gIiI7CgovLyDilIDilIAgTG9naW4g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmFzeW5jIGZ1bmN0aW9uIGRvVGVhY2hlckxvZ2luKCkgewogIGNvbnN0IHVzZXIgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tdXNlcicpLnZhbHVlLnRyaW0oKS50b0xvd2VyQ2FzZSgpOwogIGNvbnN0IHBhc3MgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tcGFzcycpLnZhbHVlOwogIGNvbnN0IGVyciAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tZXJyb3InKTsKICBjb25zdCBidG4gID0gZG9jdW1lbnQucXVlcnlTZWxlY3RvcignLmxvZ2luLWJ0bicpOwogIGlmICghdXNlciB8fCAhcGFzcykgewogICAgZXJyLnRleHRDb250ZW50ID0gJ1x1MjZhMFx1ZmUwZiBQbGVhc2UgZW50ZXIgdXNlcm5hbWUgYW5kIHBhc3N3b3JkJzsKICAgIGVyci5zdHlsZS5kaXNwbGF5ID0gJ2Jsb2NrJzsgcmV0dXJuOwogIH0KICBidG4udGV4dENvbnRlbnQgPSAnU2lnbmluZyBpbi4uLic7IGJ0bi5kaXNhYmxlZCA9IHRydWU7CiAgZXJyLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7CiAgdHJ5IHsKICAgIGNvbnN0IHJlcyA9IGF3YWl0IGZldGNoKCcvYXBpL3RlYWNoZXItbG9naW4nLCB7CiAgICAgIG1ldGhvZDogJ1BPU1QnLCBoZWFkZXJzOiB7J0NvbnRlbnQtVHlwZSc6J2FwcGxpY2F0aW9uL2pzb24nfSwKICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoe3VzZXJuYW1lOiB1c2VyLCBwYXNzd29yZDogcGFzc30pCiAgICB9KTsKICAgIGNvbnN0IGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgaWYgKGRhdGEub2spIHsKICAgICAgQ1VSUkVOVF9URUFDSEVSICAgPSB1c2VyOwogICAgICBDVVJSRU5UX0ZVTExfTkFNRSA9IGRhdGEuZnVsbF9uYW1lOwogICAgICBzZXNzaW9uU3RvcmFnZS5zZXRJdGVtKCd0ZWFjaGVyX3VzZXInLCB1c2VyKTsKICAgICAgc2Vzc2lvblN0b3JhZ2Uuc2V0SXRlbSgndGVhY2hlcl9uYW1lJywgZGF0YS5mdWxsX25hbWUpOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5ID0gJ2Jsb2NrJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RlYWNoZXItYmFkZ2UnKS50ZXh0Q29udGVudCA9ICfwn5GkICcgKyBkYXRhLmZ1bGxfbmFtZTsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RlYWNoZXInKS52YWx1ZSA9IGRhdGEuZnVsbF9uYW1lOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtdGVhY2hlcicpLnZhbHVlID0gZGF0YS5mdWxsX25hbWU7CiAgICAgIGxvY2FsU3RvcmFnZS5zZXRJdGVtKCdod190ZWFjaGVyJywgZGF0YS5mdWxsX25hbWUpOwogICAgfSBlbHNlIHsKICAgICAgZXJyLnRleHRDb250ZW50ID0gJ+KdjCAnICsgZGF0YS5lcnJvcjsKICAgICAgZXJyLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tcGFzcycpLnZhbHVlID0gJyc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi1wYXNzJykuZm9jdXMoKTsKICAgIH0KICB9IGNhdGNoKGUpIHsKICAgIGVyci50ZXh0Q29udGVudCA9ICfinYwgQ29ubmVjdGlvbiBlcnJvci4gUGxlYXNlIHRyeSBhZ2Fpbi4nOwogICAgZXJyLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogIH0KICBidG4udGV4dENvbnRlbnQgPSAnU2lnbiBJbiDihpInOyBidG4uZGlzYWJsZWQgPSBmYWxzZTsKfQoKZnVuY3Rpb24gZG9Mb2dvdXQoKSB7CiAgc2Vzc2lvblN0b3JhZ2UucmVtb3ZlSXRlbSgndGVhY2hlcl91c2VyJyk7CiAgc2Vzc2lvblN0b3JhZ2UucmVtb3ZlSXRlbSgndGVhY2hlcl9uYW1lJyk7CiAgQ1VSUkVOVF9URUFDSEVSID0gIiI7IENVUlJFTlRfRlVMTF9OQU1FID0gIiI7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5ID0gJ25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi1zY3JlZW4nKS5zdHlsZS5kaXNwbGF5ID0gJ2ZsZXgnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi11c2VyJykudmFsdWUgPSAnJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tcGFzcycpLnZhbHVlID0gJyc7Cn0KCndpbmRvdy5vbmxvYWQgPSBmdW5jdGlvbigpIHsKICBjb25zdCB0bXIgPSBuZXcgRGF0ZSgpOyB0bXIuc2V0RGF0ZSh0bXIuZ2V0RGF0ZSgpKzEpOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkdWVfZGF0ZScpLnZhbHVlID0gdG1yLnRvSVNPU3RyaW5nKCkuc3BsaXQoJ1QnKVswXTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtZGF0ZScpLnZhbHVlID0gbmV3IERhdGUoKS50b0lTT1N0cmluZygpLnNwbGl0KCdUJylbMF07CiAgY29uc3QgcyA9IHNlc3Npb25TdG9yYWdlLmdldEl0ZW0oJ3RlYWNoZXJfdXNlcicpOwogIGNvbnN0IG4gPSBzZXNzaW9uU3RvcmFnZS5nZXRJdGVtKCd0ZWFjaGVyX25hbWUnKTsKICBpZiAocyAmJiBuKSB7CiAgICBDVVJSRU5UX1RFQUNIRVIgPSBzOyBDVVJSRU5UX0ZVTExfTkFNRSA9IG47CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtYWluLXBhbmVsJykuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGVhY2hlci1iYWRnZScpLnRleHRDb250ZW50ID0gJ/CfkaQgJyArIG47CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGVhY2hlcicpLnZhbHVlID0gbjsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10ZWFjaGVyJykudmFsdWUgPSBuOwogIH0KICBjb25zdCBzYXZlZCA9IGxvY2FsU3RvcmFnZS5nZXRJdGVtKCdod190ZWFjaGVyJyk7CiAgaWYgKHNhdmVkICYmICFDVVJSRU5UX1RFQUNIRVIpIHsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0ZWFjaGVyJykudmFsdWUgPSBzYXZlZDsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10ZWFjaGVyJykudmFsdWUgPSBzYXZlZDsKICB9Cn07CgovLyDilIDilIAgVGFiIHN3aXRjaGluZyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gc3dpdGNoVGFiKHRhYiwgYnRuKSB7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnRhYi1wYW5lbCcpLmZvckVhY2gocCA9PiBwLmNsYXNzTGlzdC5yZW1vdmUoJ2FjdGl2ZScpKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcudGFiLWJ0bicpLmZvckVhY2goYiA9PiBiLmNsYXNzTGlzdC5yZW1vdmUoJ2FjdGl2ZScpKTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGFiLScgKyB0YWIpLmNsYXNzTGlzdC5hZGQoJ2FjdGl2ZScpOwogIGJ0bi5jbGFzc0xpc3QuYWRkKCdhY3RpdmUnKTsKfQoKLy8g4pSA4pSAIFN5bmMgdGVhY2hlciBuYW1lIGFjcm9zcyB0YWJzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCdET01Db250ZW50TG9hZGVkJywgZnVuY3Rpb24oKSB7CiAgWyd0ZWFjaGVyJywnZXgtdGVhY2hlciddLmZvckVhY2goZnVuY3Rpb24oaWQpIHsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKT8uYWRkRXZlbnRMaXN0ZW5lcignaW5wdXQnLCBmdW5jdGlvbigpIHsKICAgICAgY29uc3Qgb3RoZXIgPSBpZCA9PT0gJ3RlYWNoZXInID8gJ2V4LXRlYWNoZXInIDogJ3RlYWNoZXInOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZChvdGhlcikudmFsdWUgPSB0aGlzLnZhbHVlOwogICAgfSk7CiAgfSk7Cn0pOwoKLy8g4pSA4pSAIEhPTUVXT1JLIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApjb25zdCBod0hpc3RvcnkgPSBbXTsKYXN5bmMgZnVuY3Rpb24gc3VibWl0SG9tZXdvcmsoKSB7CiAgY29uc3QgYnRuICAgID0gZG9jdW1lbnQucXVlcnlTZWxlY3RvcignI3RhYi1ob21ld29yayAuc3VibWl0LWJ0bicpOwogIGNvbnN0IHN0YXR1cyA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdody1zdGF0dXMnKTsKICBjb25zdCB0ZWFjaGVyICAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RlYWNoZXInKS52YWx1ZS50cmltKCk7CiAgY29uc3QgZ3JhZGUgICAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdncmFkZScpLnZhbHVlOwogIGNvbnN0IHN1YmplY3QgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3ViamVjdCcpLnZhbHVlOwogIGNvbnN0IGFzc2lnbm1lbnQgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXNzaWdubWVudCcpLnZhbHVlLnRyaW0oKTsKICBjb25zdCBkdWVfZGF0ZSAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2R1ZV9kYXRlJykudmFsdWU7CiAgY29uc3QgdHlwZSAgICAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0eXBlJykudmFsdWU7CiAgY29uc3Qgbm90ZXMgICAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdub3RlcycpLnZhbHVlLnRyaW0oKTsKCiAgY29uc3QgbWlzc2luZyA9IFtdOwogIGlmICghdGVhY2hlcikgbWlzc2luZy5wdXNoKCdUZWFjaGVyIE5hbWUnKTsKICBpZiAoIWdyYWRlKSBtaXNzaW5nLnB1c2goJ0dyYWRlJyk7CiAgaWYgKCFzdWJqZWN0KSBtaXNzaW5nLnB1c2goJ1N1YmplY3QnKTsKICBpZiAoIWFzc2lnbm1lbnQpIG1pc3NpbmcucHVzaCgnQXNzaWdubWVudCcpOwogIGlmICghZHVlX2RhdGUpIG1pc3NpbmcucHVzaCgnRHVlIERhdGUnKTsKCiAgaWYgKG1pc3NpbmcubGVuZ3RoKSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pqg77iPIFBsZWFzZSBmaWxsIGluOiAnICsgbWlzc2luZy5qb2luKCcsICcpOwogICAgcmV0dXJuOwogIH0KCiAgYnRuLmRpc2FibGVkID0gdHJ1ZTsgYnRuLnRleHRDb250ZW50ID0gJ1NhdmluZ+KApic7CiAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwoKICB0cnkgewogICAgY29uc3QgcmVzID0gYXdhaXQgZmV0Y2goJy9hcGkvYWRkLWhvbWV3b3JrJywgewogICAgICBtZXRob2Q6ICdQT1NUJywgaGVhZGVyczogeydDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbid9LAogICAgICBib2R5OiBKU09OLnN0cmluZ2lmeSh7dGVhY2hlciwgZ3JhZGUsIHN1YmplY3QsIGFzc2lnbm1lbnQsIGR1ZV9kYXRlLCB0eXBlLCBub3Rlc30pCiAgICB9KTsKICAgIGNvbnN0IGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgaWYgKGRhdGEub2spIHsKICAgICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMgc3VjY2Vzcyc7CiAgICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICfinIUgJyArIGRhdGEubWVzc2FnZTsKICAgICAgbG9jYWxTdG9yYWdlLnNldEl0ZW0oJ2h3X3RlYWNoZXInLCB0ZWFjaGVyKTsKICAgICAgaHdIaXN0b3J5LnVuc2hpZnQoe3RlYWNoZXIsIGdyYWRlLCBzdWJqZWN0LCBhc3NpZ25tZW50LCBkdWVfZGF0ZSwgdHlwZX0pOwogICAgICByZW5kZXJId0hpc3RvcnkoKTsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N1YmplY3QnKS52YWx1ZSA9ICcnOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXNzaWdubWVudCcpLnZhbHVlID0gJyc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdub3RlcycpLnZhbHVlID0gJyc7CiAgICAgIGNvbnN0IHRtciA9IG5ldyBEYXRlKCk7IHRtci5zZXREYXRlKHRtci5nZXREYXRlKCkrMSk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkdWVfZGF0ZScpLnZhbHVlID0gdG1yLnRvSVNPU3RyaW5nKCkuc3BsaXQoJ1QnKVswXTsKICAgIH0gZWxzZSB7CiAgICAgIHN0YXR1cy5jbGFzc05hbWUgPSAnc3RhdHVzIGVycm9yJzsKICAgICAgc3RhdHVzLnRleHRDb250ZW50ID0gJ+KdjCAnICsgZGF0YS5lcnJvcjsKICAgIH0KICB9IGNhdGNoKGUpIHsKICAgIHN0YXR1cy5jbGFzc05hbWUgPSAnc3RhdHVzIGVycm9yJzsKICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICfinYwgTmV0d29yayBlcnJvci4gVHJ5IGFnYWluLic7CiAgfQogIGJ0bi5kaXNhYmxlZCA9IGZhbHNlOyBidG4udGV4dENvbnRlbnQgPSAn4p6VIEFERCBIT01FV09SSyc7Cn0KCmZ1bmN0aW9uIHJlbmRlckh3SGlzdG9yeSgpIHsKICBjb25zdCBlbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdody1oaXN0b3J5Jyk7CiAgaWYgKCFod0hpc3RvcnkubGVuZ3RoKSB7IGVsLmlubmVySFRNTCA9ICc8ZGl2IGNsYXNzPSJuby1oaXN0Ij5Ob3RoaW5nIGFkZGVkIHlldC48L2Rpdj4nOyByZXR1cm47IH0KICBlbC5pbm5lckhUTUwgPSBod0hpc3Rvcnkuc2xpY2UoMCw1KS5tYXAoaCA9PgogICAgYDxkaXYgY2xhc3M9Imhpc3QtaXRlbSI+PHN0cm9uZz4ke2guZ3JhZGV9IOKAlCAke2guc3ViamVjdH08L3N0cm9uZz46ICR7aC5hc3NpZ25tZW50LnN1YnN0cmluZygwLDcwKX0ke2guYXNzaWdubWVudC5sZW5ndGg+NzA/J+KApic6Jyd9CiAgICAgPGRpdiBjbGFzcz0ibWV0YSI+8J+ThSAke2guZHVlX2RhdGV9IMK3IPCfkaQgJHtoLnRlYWNoZXJ9IMK3IPCfl4LvuI8gJHtoLnR5cGV9PC9kaXY+PC9kaXY+YAogICkuam9pbignJyk7Cn0KCi8vIOKUgOKUgCBFWEFNIFJFU1VMVFMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACnZhciBsb2FkZWRTdHVkZW50cyA9IFtdOwpjb25zdCBleEhpc3RvcnkgPSBbXTsKCmZ1bmN0aW9uIGNsZWFyU3R1ZGVudHMoKSB7CiAgbG9hZGVkU3R1ZGVudHMgPSBbXTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3R1ZGVudHMtc2VjdGlvbicpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXN0YXR1cycpLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwp9Cgphc3luYyBmdW5jdGlvbiBsb2FkU3R1ZGVudHMoKSB7CiAgY29uc3QgZ3JhZGUgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtZ3JhZGUnKS52YWx1ZTsKICBpZiAoIWdyYWRlKSB7IGFsZXJ0KCdQbGVhc2Ugc2VsZWN0IGEgZ3JhZGUgZmlyc3QuJyk7IHJldHVybjsgfQogIGNvbnN0IGJ0biA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2FkQnRuJyk7CiAgY29uc3Qgc3RhdHVzID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXN0YXR1cycpOwogIGJ0bi5kaXNhYmxlZCA9IHRydWU7IGJ0bi50ZXh0Q29udGVudCA9ICfij7MgTG9hZGluZyBzdHVkZW50c+KApic7CiAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwogIHRyeSB7CiAgICBjb25zdCByZXMgPSBhd2FpdCBmZXRjaCgnL2FwaS9zdHVkZW50cy1ieS1ncmFkZT9ncmFkZT0nICsgZW5jb2RlVVJJQ29tcG9uZW50KGdyYWRlKSk7CiAgICBjb25zdCBkYXRhID0gYXdhaXQgcmVzLmpzb24oKTsKICAgIGlmICghZGF0YS5vayB8fCAhZGF0YS5zdHVkZW50cy5sZW5ndGgpIHsKICAgICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMgZXJyb3InOwogICAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4p2MIE5vIHN0dWRlbnRzIGZvdW5kIGZvciAnICsgZ3JhZGU7CiAgICAgIGJ0bi5kaXNhYmxlZCA9IGZhbHNlOyBidG4udGV4dENvbnRlbnQgPSAn8J+RpSBMb2FkIFN0dWRlbnRzIGZvciBUaGlzIEdyYWRlJzsKICAgICAgcmV0dXJuOwogICAgfQogICAgbG9hZGVkU3R1ZGVudHMgPSBkYXRhLnN0dWRlbnRzOwogICAgcmVuZGVyU3R1ZGVudFRhYmxlKGxvYWRlZFN0dWRlbnRzKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdHVkZW50cy1zZWN0aW9uJykuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3R1ZGVudC1jb3VudCcpLnRleHRDb250ZW50ID0KICAgICAgYPCfk4sgJHtkYXRhLmNvdW50fSBzdHVkZW50cyBpbiAke2dyYWRlfSDigJQgZW50ZXIgZWFjaCBzY29yZSBiZWxvdzpgOwogICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwogIH0gY2F0Y2goZSkgewogICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMgZXJyb3InOwogICAgc3RhdHVzLnRleHRDb250ZW50ID0gJ+KdjCBFcnJvciBsb2FkaW5nIHN0dWRlbnRzOiAnICsgZS5tZXNzYWdlOwogIH0KICBidG4uZGlzYWJsZWQgPSBmYWxzZTsgYnRuLnRleHRDb250ZW50ID0gJ/CfkaUgTG9hZCBTdHVkZW50cyBmb3IgVGhpcyBHcmFkZSc7Cn0KCmZ1bmN0aW9uIHJlbmRlclN0dWRlbnRUYWJsZShzdHVkZW50cykgewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdHVkZW50cy10Ym9keScpLmlubmVySFRNTCA9IHN0dWRlbnRzLm1hcCgocywgaSkgPT4gYAogICAgPHRyPgogICAgICA8dGQgc3R5bGU9ImNvbG9yOiM5NGEzYjg7Zm9udC1zaXplOjEycHg7d2lkdGg6MzBweCI+JHtpKzF9PC90ZD4KICAgICAgPHRkIHN0eWxlPSJmb250LXdlaWdodDo2MDA7Zm9udC1zaXplOjEzcHgiPiR7cy5uYW1lfTwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0iY29sb3I6Izk0YTNiODtmb250LXNpemU6MTFweCI+JHtzLmlkfTwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0id2lkdGg6ODBweCI+CiAgICAgICAgPGlucHV0IHR5cGU9Im51bWJlciIgaWQ9InNjb3JlLSR7aX0iIG1pbj0iMCIgcGxhY2Vob2xkZXI9IuKAlCIKICAgICAgICAgIG9uaW5wdXQ9ImF1dG9DYWxjKCR7aX0pIiBzdHlsZT0id2lkdGg6NzBweDt0ZXh0LWFsaWduOmNlbnRlciI+CiAgICAgIDwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0id2lkdGg6NjBweCI+PHNwYW4gaWQ9InBjdC0ke2l9IiBzdHlsZT0iZm9udC1zaXplOjEzcHg7Y29sb3I6IzY0NzQ4YiI+4oCUPC9zcGFuPjwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0id2lkdGg6NTVweCI+PHNwYW4gY2xhc3M9ImdyYWRlLWJhZGdlIiBpZD0iZ2wtJHtpfSI+4oCUPC9zcGFuPjwvdGQ+CiAgICAgIDx0ZD48aW5wdXQgdHlwZT0idGV4dCIgaWQ9Im5vdGUtJHtpfSIgcGxhY2Vob2xkZXI9Im9wdGlvbmFsIiBzdHlsZT0iZm9udC1zaXplOjEycHgiPjwvdGQ+CiAgICA8L3RyPgogIGApLmpvaW4oJycpOwp9CgpmdW5jdGlvbiBhdXRvQ2FsYyhpKSB7CiAgY29uc3QgdG90YWwgPSBwYXJzZUZsb2F0KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10b3RhbCcpLnZhbHVlKSB8fCAxMDA7CiAgY29uc3Qgc2NvcmUgPSBwYXJzZUZsb2F0KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzY29yZS0nK2kpLnZhbHVlKTsKICBpZiAoaXNOYU4oc2NvcmUpKSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGN0LScraSkudGV4dENvbnRlbnQgPSAn4oCUJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdnbC0nK2kpLnRleHRDb250ZW50ID0gJ+KAlCc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZ2wtJytpKS5jbGFzc05hbWUgPSAnZ3JhZGUtYmFkZ2UnOwogICAgcmV0dXJuOwogIH0KICBjb25zdCBwY3QgPSBNYXRoLnJvdW5kKHNjb3JlIC8gdG90YWwgKiAxMDApOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwY3QtJytpKS50ZXh0Q29udGVudCA9IHBjdCArICclJzsKICBsZXQgZ2wgPSAnRicsIGNscyA9ICdGJzsKICBpZiAocGN0Pj05NSl7Z2w9J0ErJztjbHM9J0EnO30gZWxzZSBpZihwY3Q+PTkwKXtnbD0nQSc7Y2xzPSdBJzt9CiAgZWxzZSBpZihwY3Q+PTg1KXtnbD0nQisnO2Nscz0nQic7fSBlbHNlIGlmKHBjdD49ODApe2dsPSdCJztjbHM9J0InO30KICBlbHNlIGlmKHBjdD49NzUpe2dsPSdDKyc7Y2xzPSdDJzt9IGVsc2UgaWYocGN0Pj03MCl7Z2w9J0MnO2Nscz0nQyc7fQogIGVsc2UgaWYocGN0Pj02NSl7Z2w9J0QrJztjbHM9J0QnO30gZWxzZSBpZihwY3Q+PTYwKXtnbD0nRCc7Y2xzPSdEJzt9CiAgY29uc3QgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZ2wtJytpKTsKICBlbC50ZXh0Q29udGVudCA9IGdsOyBlbC5jbGFzc05hbWUgPSAnZ3JhZGUtYmFkZ2UgJytjbHM7Cn0KCmFzeW5jIGZ1bmN0aW9uIHN1Ym1pdEV4YW1SZXN1bHRzKCkgewogIGNvbnN0IHRlYWNoZXIgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXRlYWNoZXInKS52YWx1ZS50cmltKCk7CiAgY29uc3QgZ3JhZGUgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtZ3JhZGUnKS52YWx1ZTsKICBjb25zdCBzdWJqZWN0ICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC1zdWJqZWN0JykudmFsdWU7CiAgY29uc3QgdGVybSAgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtdGVybScpLnZhbHVlOwogIGNvbnN0IGV4YW1EYXRlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LWRhdGUnKS52YWx1ZTsKICBjb25zdCB0b3RhbCAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10b3RhbCcpLnZhbHVlIHx8ICcxMDAnOwogIGNvbnN0IHN0YXR1cyAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXN0YXR1cycpOwoKICBpZiAoIXRlYWNoZXIgfHwgIWdyYWRlIHx8ICFzdWJqZWN0KSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pqg77iPIFBsZWFzZSBmaWxsIGluIFRlYWNoZXIsIEdyYWRlIGFuZCBTdWJqZWN0Lic7CiAgICByZXR1cm47CiAgfQoKICBjb25zdCByZXN1bHRzID0gbG9hZGVkU3R1ZGVudHMubWFwKChzLCBpKSA9PiAoewogICAgc3R1ZGVudF9pZDogcy5pZCwgc3R1ZGVudF9uYW1lOiBzLm5hbWUsIGdyYWRlOiBzLmdyYWRlLAogICAgc2NvcmU6IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzY29yZS0nK2kpPy52YWx1ZS50cmltKCkgfHwgJycsCiAgICB0b3RhbCwKICAgIHBlcmNlbnRhZ2U6IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwY3QtJytpKT8udGV4dENvbnRlbnQgfHwgJycsCiAgICBncmFkZV9sZXR0ZXI6IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdnbC0nK2kpPy50ZXh0Q29udGVudCB8fCAnJywKICAgIG5vdGVzOiBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbm90ZS0nK2kpPy52YWx1ZS50cmltKCkgfHwgJycKICB9KSkuZmlsdGVyKHIgPT4gci5zY29yZSAhPT0gJycpOwoKICBpZiAoIXJlc3VsdHMubGVuZ3RoKSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pqg77iPIE5vIHNjb3JlcyBlbnRlcmVkIHlldC4nOwogICAgcmV0dXJuOwogIH0KCiAgY29uc3QgYnRuID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4YW1TdWJtaXRCdG4nKTsKICBidG4uZGlzYWJsZWQgPSB0cnVlOyBidG4udGV4dENvbnRlbnQgPSAn4o+zIFNhdmluZyB0byBzaGVldOKApic7CiAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwoKICB0cnkgewogICAgY29uc3QgcmVzID0gYXdhaXQgZmV0Y2goJy9hcGkvYWRkLWV4YW0tcmVzdWx0cycsIHsKICAgICAgbWV0aG9kOiAnUE9TVCcsIGhlYWRlcnM6IHsnQ29udGVudC1UeXBlJzogJ2FwcGxpY2F0aW9uL2pzb24nfSwKICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoe3Jlc3VsdHMsIHN1YmplY3QsIHRlcm0sIHRlYWNoZXIsIGV4YW1fZGF0ZTogZXhhbURhdGV9KQogICAgfSk7CiAgICBjb25zdCBkYXRhID0gYXdhaXQgcmVzLmpzb24oKTsKICAgIGlmIChkYXRhLm9rKSB7CiAgICAgIHN0YXR1cy5jbGFzc05hbWUgPSAnc3RhdHVzIHN1Y2Nlc3MnOwogICAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pyFICcgKyBkYXRhLm1lc3NhZ2U7CiAgICAgIGxvY2FsU3RvcmFnZS5zZXRJdGVtKCdod190ZWFjaGVyJywgdGVhY2hlcik7CiAgICAgIGV4SGlzdG9yeS51bnNoaWZ0KHtncmFkZSwgc3ViamVjdCwgdGVybSwgY291bnQ6IGRhdGEuc2F2ZWQsIHRlYWNoZXJ9KTsKICAgICAgcmVuZGVyRXhIaXN0b3J5KCk7CiAgICB9IGVsc2UgewogICAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICfinYwgJyArIGRhdGEuZXJyb3I7CiAgICB9CiAgfSBjYXRjaChlKSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4p2MIE5ldHdvcmsgZXJyb3I6ICcgKyBlLm1lc3NhZ2U7CiAgfQogIGJ0bi5kaXNhYmxlZCA9IGZhbHNlOyBidG4udGV4dENvbnRlbnQgPSAn8J+SviBTQVZFIEFMTCBSRVNVTFRTIFRPIFNIRUVUJzsKfQoKZnVuY3Rpb24gcmVuZGVyRXhIaXN0b3J5KCkgewogIGNvbnN0IGVsID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LWhpc3RvcnknKTsKICBpZiAoIWV4SGlzdG9yeS5sZW5ndGgpIHsgZWwuaW5uZXJIVE1MID0gJzxkaXYgY2xhc3M9Im5vLWhpc3QiPk5vdGhpbmcgc2F2ZWQgeWV0LjwvZGl2Pic7IHJldHVybjsgfQogIGVsLmlubmVySFRNTCA9IGV4SGlzdG9yeS5zbGljZSgwLDUpLm1hcChoID0+CiAgICBgPGRpdiBjbGFzcz0iaGlzdC1pdGVtIj48c3Ryb25nPiR7aC5ncmFkZX0g4oCUICR7aC5zdWJqZWN0fTwvc3Ryb25nPiAoJHtoLnRlcm19KQogICAgIDxkaXYgY2xhc3M9Im1ldGEiPuKchSAke2guY291bnR9IHJlc3VsdHMgc2F2ZWQgwrcg8J+RpCAke2gudGVhY2hlcn08L2Rpdj48L2Rpdj5gCiAgKS5qb2luKCcnKTsKfQoKLy8gQ3RybCtFbnRlciB0byBzdWJtaXQgYWN0aXZlIHRhYgpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCdrZXlkb3duJywgZSA9PiB7CiAgaWYgKChlLmN0cmxLZXl8fGUubWV0YUtleSkgJiYgZS5rZXk9PT0nRW50ZXInKSB7CiAgICBpZiAoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RhYi1ob21ld29yaycpLmNsYXNzTGlzdC5jb250YWlucygnYWN0aXZlJykpIHN1Ym1pdEhvbWV3b3JrKCk7CiAgICBlbHNlIHN1Ym1pdEV4YW1SZXN1bHRzKCk7CiAgfQp9KTsKPC9zY3JpcHQ+PC9kaXY+CiAgPGRpdiBjbGFzcz0icGFuZSIgaWQ9InBhbmUtZmluYW5jZSI+PCEtLSBMT0dJTiAtLT4KPGRpdiBpZD0ibG9naW4tc2NyZWVuIj4KICA8ZGl2IGNsYXNzPSJsb2dpbi1ib3giPgogICAgPGRpdiBjbGFzcz0ibG9naW4tbG9nbyI+CiAgICAgIDxkaXYgY2xhc3M9Imljb24iPvCfkrM8L2Rpdj4KICAgICAgPGgxPkZpbmFuY2UgUGFuZWw8L2gxPgogICAgICA8cD5Nb2Rlcm4gSW5maW5pdHkgTGFuZ3VhZ2UgU2Nob29sPC9wPgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PGxhYmVsPlVzZXJuYW1lPC9sYWJlbD4KICAgICAgPGlucHV0IHR5cGU9InRleHQiIGlkPSJmdSIgcGxhY2Vob2xkZXI9IkZpbmFuY2UgdXNlcm5hbWUiIGF1dG9jb21wbGV0ZT0ib2ZmIj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0iZmllbGQiPjxsYWJlbD5QYXNzd29yZDwvbGFiZWw+CiAgICAgIDxpbnB1dCB0eXBlPSJwYXNzd29yZCIgaWQ9ImZwIiBwbGFjZWhvbGRlcj0iUGFzc3dvcmQiIG9ua2V5ZG93bj0iaWYoZXZlbnQua2V5PT09J0VudGVyJylkb0xvZ2luKCkiPgogICAgPC9kaXY+CiAgICA8YnV0dG9uIGNsYXNzPSJsb2dpbi1idG4iIG9uY2xpY2s9ImRvTG9naW4oKSI+U2lnbiBJbiDihpI8L2J1dHRvbj4KICAgIDxkaXYgY2xhc3M9ImxvZ2luLWVycm9yIiBpZD0iZmUiPuKdjCBJbmNvcnJlY3QgdXNlcm5hbWUgb3IgcGFzc3dvcmQ8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIE1BSU4gLS0+CjxkaXYgaWQ9Im1haW4tcGFuZWwiPgogIDxkaXYgY2xhc3M9InRvcGJhciI+CiAgICA8ZGl2PgogICAgICA8aDE+8J+SsyBGaW5hbmNlIFBhbmVsPC9oMT4KICAgICAgPHA+TW9kZXJuIEluZmluaXR5IExhbmd1YWdlIFNjaG9vbCDigJQgUGF5bWVudCBNYW5hZ2VtZW50PC9wPgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJ0b3BiYXItcmlnaHQiPgogICAgICA8c3BhbiBjbGFzcz0iYmFkZ2UiPkZpbmFuY2UgVGVhbTwvc3Bhbj4KICAgICAgPGJ1dHRvbiBjbGFzcz0ibG9nb3V0LWJ0biIgb25jbGljaz0iZG9Mb2dvdXQoKSI+U2lnbiBvdXQ8L2J1dHRvbj4KICAgIDwvZGl2PgogIDwvZGl2PgoKICA8ZGl2IGNsYXNzPSJjb250ZW50Ij4KICAgIDwhLS0gRmlsdGVycyAtLT4KICAgIDxkaXYgY2xhc3M9ImZpbHRlcnMiPgogICAgICA8ZGl2IGNsYXNzPSJmaWx0ZXItZ3JvdXAiPgogICAgICAgIDxsYWJlbD5HcmFkZTwvbGFiZWw+CiAgICAgICAgPHNlbGVjdCBpZD0iZ3JhZGVGaWx0ZXIiPgogICAgICAgICAgPG9wdGlvbiB2YWx1ZT0iIj5BbGwgR3JhZGVzPC9vcHRpb24+CiAgICAgICAgICA8b3B0aW9uPktHMTwvb3B0aW9uPjxvcHRpb24+S0cyPC9vcHRpb24+CiAgICAgICAgICA8b3B0aW9uPkdyYWRlIDE8L29wdGlvbj48b3B0aW9uPkdyYWRlIDI8L29wdGlvbj48b3B0aW9uPkdyYWRlIDM8L29wdGlvbj4KICAgICAgICAgIDxvcHRpb24+R3JhZGUgNDwvb3B0aW9uPjxvcHRpb24+R3JhZGUgNTwvb3B0aW9uPjxvcHRpb24+R3JhZGUgNjwvb3B0aW9uPgogICAgICAgICAgPG9wdGlvbj5HcmFkZSA3PC9vcHRpb24+PG9wdGlvbj5HcmFkZSA4PC9vcHRpb24+PG9wdGlvbj5HcmFkZSA5PC9vcHRpb24+CiAgICAgICAgICA8b3B0aW9uPkdyYWRlIDEwPC9vcHRpb24+PG9wdGlvbj5HcmFkZSAxMTwvb3B0aW9uPjxvcHRpb24+R3JhZGUgMTI8L29wdGlvbj4KICAgICAgICA8L3NlbGVjdD4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9ImZpbHRlci1ncm91cCI+CiAgICAgICAgPGxhYmVsPlBheW1lbnQgU3RhdHVzPC9sYWJlbD4KICAgICAgICA8c2VsZWN0IGlkPSJzdGF0dXNGaWx0ZXIiPgogICAgICAgICAgPG9wdGlvbiB2YWx1ZT0iIj5BbGwgU3R1ZGVudHM8L29wdGlvbj4KICAgICAgICAgIDxvcHRpb24gdmFsdWU9InBhaWQiPlBhaWQgT25seTwvb3B0aW9uPgogICAgICAgICAgPG9wdGlvbiB2YWx1ZT0idW5wYWlkIj5Ob3QgUGFpZCBPbmx5PC9vcHRpb24+CiAgICAgICAgPC9zZWxlY3Q+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJmaWx0ZXItZ3JvdXAiPgogICAgICAgIDxsYWJlbD5TZWFyY2g8L2xhYmVsPgogICAgICAgIDxpbnB1dCB0eXBlPSJ0ZXh0IiBpZD0ic2VhcmNoSW5wdXQiIHBsYWNlaG9sZGVyPSJOYW1lIG9yIFN0dWRlbnQgSUQuLi4iIG9uaW5wdXQ9ImZpbHRlclRhYmxlKCkiPgogICAgICA8L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0ibG9hZC1idG4iIG9uY2xpY2s9ImxvYWRTdHVkZW50cygpIj7wn5SEIExvYWQgU3R1ZGVudHM8L2J1dHRvbj4KICAgIDwvZGl2PgoKICAgIDwhLS0gU3RhdHMgLS0+CiAgICA8ZGl2IGNsYXNzPSJzdGF0cyI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtY2FyZCI+PGRpdiBjbGFzcz0ic3RhdC1sYWJlbCI+VG90YWwgU3R1ZGVudHM8L2Rpdj48ZGl2IGNsYXNzPSJzdGF0LXZhbHVlIiBpZD0ic3RhdFRvdGFsIj7igJQ8L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC1jYXJkIj48ZGl2IGNsYXNzPSJzdGF0LWxhYmVsIj5QYWlkPC9kaXY+PGRpdiBjbGFzcz0ic3RhdC12YWx1ZSBncmVlbiIgaWQ9InN0YXRQYWlkIj7igJQ8L2Rpdj48L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC1jYXJkIj48ZGl2IGNsYXNzPSJzdGF0LWxhYmVsIj5Ob3QgUGFpZDwvZGl2PjxkaXYgY2xhc3M9InN0YXQtdmFsdWUgcmVkIiBpZD0ic3RhdFVucGFpZCI+4oCUPC9kaXY+PC9kaXY+CiAgICA8L2Rpdj4KCiAgICA8IS0tIFNhdmUgYmFyIC0tPgogICAgPGRpdiBjbGFzcz0ic2F2ZS1iYXIiIGlkPSJzYXZlQmFyIj4KICAgICAgPGRpdiBjbGFzcz0ic2F2ZS1pbmZvIj7imqDvuI8gWW91IGhhdmUgPHNwYW4gaWQ9ImNoYW5nZUNvdW50Ij4wPC9zcGFuPiB1bnNhdmVkIGNoYW5nZXM8L2Rpdj4KICAgICAgPGJ1dHRvbiBjbGFzcz0ic2F2ZS1idG4iIGlkPSJzYXZlQnRuIiBvbmNsaWNrPSJzYXZlQ2hhbmdlcygpIj7wn5K+IFNhdmUgQWxsIENoYW5nZXM8L2J1dHRvbj4KICAgIDwvZGl2PgoKICAgIDxkaXYgaWQ9InN0YXR1c01zZyIgY2xhc3M9InN0YXR1cy1tc2ciPjwvZGl2PgoKICAgIDwhLS0gVGFibGUgLS0+CiAgICA8ZGl2IGNsYXNzPSJ0YWJsZS13cmFwIj4KICAgICAgPGRpdiBjbGFzcz0idGFibGUtaGVhZGVyIj4KICAgICAgICA8ZGl2IGNsYXNzPSJ0YWJsZS10aXRsZSI+U3R1ZGVudHM8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdHVkZW50LWNvdW50IiBpZD0ic3R1ZGVudENvdW50Ij5Mb2FkIHN0dWRlbnRzIHRvIGJlZ2luPC9kaXY+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGlkPSJ0YWJsZUNvbnRhaW5lciI+CiAgICAgICAgPGRpdiBjbGFzcz0iZW1wdHkiPkNsaWNrICJMb2FkIFN0dWRlbnRzIiB0byBiZWdpbjwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgogIDwvZGl2Pgo8L2Rpdj4KCjxzY3JpcHQ+CnZhciBGSU5BTkNFX1VTRVJTID0geyJmaW5hbmNlIjogImZpbmFuY2UyMDI2IiwgImZpbmFuY2VfYWRtaW4iOiAibW9kZXJuaW5maW5pdHkyMDI2In07CnZhciBhbGxTdHVkZW50cyA9IFtdOwp2YXIgY2hhbmdlcyA9IHt9OwoKZnVuY3Rpb24gZG9Mb2dpbigpewogIHZhciB1ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2Z1JykudmFsdWUudHJpbSgpOwogIHZhciBwID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2ZwJykudmFsdWUudHJpbSgpOwogIHZhciBlcnIgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZmUnKTsKICBpZighdXx8IXApe2Vyci50ZXh0Q29udGVudD0n4pqg77iPIEVudGVyIHVzZXJuYW1lIGFuZCBwYXNzd29yZCc7ZXJyLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJztyZXR1cm47fQogIGlmKEZJTkFOQ0VfVVNFUlNbdV0gJiYgRklOQU5DRV9VU0VSU1t1XT09PXApewogICAgZXJyLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xvZ2luLXNjcmVlbicpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBsb2FkU3R1ZGVudHMoKTsKICB9IGVsc2UgewogICAgZXJyLnRleHRDb250ZW50PSfinYwgSW5jb3JyZWN0IHVzZXJuYW1lIG9yIHBhc3N3b3JkJzsKICAgIGVyci5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZnAnKS52YWx1ZT0nJzsKICB9Cn0KCmZ1bmN0aW9uIGRvTG9nb3V0KCl7CiAgYWxsU3R1ZGVudHM9W107IGNoYW5nZXM9e307CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheT0nZmxleCc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2Z1JykudmFsdWU9Jyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2ZwJykudmFsdWU9Jyc7Cn0KCmFzeW5jIGZ1bmN0aW9uIGxvYWRTdHVkZW50cygpewogIHZhciBidG4gPSBkb2N1bWVudC5xdWVyeVNlbGVjdG9yKCcubG9hZC1idG4nKTsKICBidG4udGV4dENvbnRlbnQ9J+KPsyBMb2FkaW5nLi4uJzsgYnRuLmRpc2FibGVkPXRydWU7CiAgY2hhbmdlcz17fTsKICB1cGRhdGVTYXZlQmFyKCk7CiAgdHJ5ewogICAgdmFyIGdyYWRlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2dyYWRlRmlsdGVyJykudmFsdWU7CiAgICB2YXIgdXJsID0gJy9hcGkvZmluYW5jZS9zdHVkZW50cycgKyAoZ3JhZGUgPyAnP2dyYWRlPScrZW5jb2RlVVJJQ29tcG9uZW50KGdyYWRlKSA6ICcnKTsKICAgIHZhciByZXMgPSBhd2FpdCBmZXRjaCh1cmwpOwogICAgdmFyIGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgYWxsU3R1ZGVudHMgPSBkYXRhLnN0dWRlbnRzIHx8IFtdOwogICAgcmVuZGVyVGFibGUoYWxsU3R1ZGVudHMpOwogICAgdXBkYXRlU3RhdHMoYWxsU3R1ZGVudHMpOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXR1c01zZycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIH0gY2F0Y2goZSl7CiAgICBzaG93U3RhdHVzKCfinYwgRXJyb3IgbG9hZGluZyBzdHVkZW50czogJytlLm1lc3NhZ2UsICdlcnJvcicpOwogIH0KICBidG4udGV4dENvbnRlbnQ9J/CflIQgTG9hZCBTdHVkZW50cyc7IGJ0bi5kaXNhYmxlZD1mYWxzZTsKfQoKZnVuY3Rpb24gcmVuZGVyVGFibGUoc3R1ZGVudHMpewogIHZhciBzdGF0dXMgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdHVzRmlsdGVyJykudmFsdWU7CiAgdmFyIHNlYXJjaCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzZWFyY2hJbnB1dCcpLnZhbHVlLnRvTG93ZXJDYXNlKCk7CiAgdmFyIGZpbHRlcmVkID0gc3R1ZGVudHMuZmlsdGVyKGZ1bmN0aW9uKHMpewogICAgdmFyIG1hdGNoU3RhdHVzID0gIXN0YXR1cyB8fAogICAgICAoc3RhdHVzPT09J3BhaWQnICYmIChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKT09PSdwYWlkJykgfHwKICAgICAgKHN0YXR1cz09PSd1bnBhaWQnICYmIChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKSE9PSdwYWlkJyk7CiAgICB2YXIgbWF0Y2hTZWFyY2ggPSAhc2VhcmNoIHx8CiAgICAgIChzLm5hbWV8fCcnKS50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKHNlYXJjaCkgfHwKICAgICAgKHMuaWR8fCcnKS50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKHNlYXJjaCk7CiAgICByZXR1cm4gbWF0Y2hTdGF0dXMgJiYgbWF0Y2hTZWFyY2g7CiAgfSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0dWRlbnRDb3VudCcpLnRleHRDb250ZW50ID0gZmlsdGVyZWQubGVuZ3RoICsgJyBzdHVkZW50cyBzaG93bic7CiAgaWYoIWZpbHRlcmVkLmxlbmd0aCl7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGFibGVDb250YWluZXInKS5pbm5lckhUTUw9JzxkaXYgY2xhc3M9ImVtcHR5Ij5ObyBzdHVkZW50cyBtYXRjaCB5b3VyIGZpbHRlcnM8L2Rpdj4nOwogICAgcmV0dXJuOwogIH0KICB2YXIgcm93cyA9IGZpbHRlcmVkLm1hcChmdW5jdGlvbihzLCBpKXsKICAgIHZhciBpc1BhaWQgPSAoY2hhbmdlc1tzLnJvd19pbmRleF0gIT09IHVuZGVmaW5lZCkKICAgICAgPyBjaGFuZ2VzW3Mucm93X2luZGV4XT09PSdwYWlkJwogICAgICA6IChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKT09PSdwYWlkJzsKICAgIHZhciBsYWJlbCA9IGlzUGFpZAogICAgICA/ICc8c3BhbiBjbGFzcz0idG9nZ2xlLWxhYmVsIHBhaWQiPuKchSBQYWlkPC9zcGFuPicKICAgICAgOiAnPHNwYW4gY2xhc3M9InRvZ2dsZS1sYWJlbCB1bnBhaWQiPuKdjCBOb3QgUGFpZDwvc3Bhbj4nOwogICAgcmV0dXJuICc8dHI+JysKICAgICAgJzx0ZCBzdHlsZT0iZm9udC13ZWlnaHQ6NjAwIj4nK3MubmFtZSsnPC90ZD4nKwogICAgICAnPHRkIHN0eWxlPSJjb2xvcjojNjQ3NDhiO2ZvbnQtc2l6ZToxMnB4Ij4nK3MuaWQrJzwvdGQ+JysKICAgICAgJzx0ZD48c3BhbiBjbGFzcz0iZ3JhZGUtYmFkZ2UiPicrcy5ncmFkZSsnPC9zcGFuPjwvdGQ+JysKICAgICAgJzx0ZD4nKwogICAgICAgICc8ZGl2IGNsYXNzPSJ0b2dnbGUtd3JhcCI+JysKICAgICAgICAgICc8bGFiZWwgY2xhc3M9InRvZ2dsZSI+JysKICAgICAgICAgICAgJzxpbnB1dCB0eXBlPSJjaGVja2JveCIgJysoaXNQYWlkPydjaGVja2VkJzonJykrJyBvbmNoYW5nZT0idG9nZ2xlUGF5bWVudCh0aGlzLCcrcy5yb3dfaW5kZXgrJykiPicrCiAgICAgICAgICAgICc8c3BhbiBjbGFzcz0ic2xpZGVyIj48L3NwYW4+JysKICAgICAgICAgICc8L2xhYmVsPicrCiAgICAgICAgICAnPHNwYW4gaWQ9ImxibC0nK3Mucm93X2luZGV4KyciPicrbGFiZWwrJzwvc3Bhbj4nKwogICAgICAgICc8L2Rpdj4nKwogICAgICAnPC90ZD4nKwogICAgJzwvdHI+JzsKICB9KS5qb2luKCcnKTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGFibGVDb250YWluZXInKS5pbm5lckhUTUwgPQogICAgJzx0YWJsZT48dGhlYWQ+PHRyPjx0aD5TdHVkZW50IE5hbWU8L3RoPjx0aD5JRDwvdGg+PHRoPkdyYWRlPC90aD48dGg+UGF5bWVudCBTdGF0dXM8L3RoPjwvdHI+PC90aGVhZD4nKwogICAgJzx0Ym9keT4nK3Jvd3MrJzwvdGJvZHk+PC90YWJsZT4nOwp9CgpmdW5jdGlvbiBmaWx0ZXJUYWJsZSgpeyByZW5kZXJUYWJsZShhbGxTdHVkZW50cyk7IH0KCmZ1bmN0aW9uIHRvZ2dsZVBheW1lbnQoY2hlY2tib3gsIHJvd0luZGV4KXsKICB2YXIgaXNQYWlkID0gY2hlY2tib3guY2hlY2tlZDsKICBjaGFuZ2VzW3Jvd0luZGV4XSA9IGlzUGFpZCA/ICdwYWlkJyA6ICd1bnBhaWQnOwogIHZhciBsYmwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGJsLScrcm93SW5kZXgpOwogIGlmKGxibCkgbGJsLmlubmVySFRNTCA9IGlzUGFpZAogICAgPyAnPHNwYW4gY2xhc3M9InRvZ2dsZS1sYWJlbCBwYWlkIj7inIUgUGFpZDwvc3Bhbj4nCiAgICA6ICc8c3BhbiBjbGFzcz0idG9nZ2xlLWxhYmVsIHVucGFpZCI+4p2MIE5vdCBQYWlkPC9zcGFuPic7CiAgdXBkYXRlU2F2ZUJhcigpOwogIHVwZGF0ZVN0YXRzKGFsbFN0dWRlbnRzKTsKfQoKZnVuY3Rpb24gdXBkYXRlU2F2ZUJhcigpewogIHZhciBjb3VudCA9IE9iamVjdC5rZXlzKGNoYW5nZXMpLmxlbmd0aDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY2hhbmdlQ291bnQnKS50ZXh0Q29udGVudCA9IGNvdW50OwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYXZlQmFyJykuY2xhc3NOYW1lID0gY291bnQgPiAwID8gJ3NhdmUtYmFyIHNob3cnIDogJ3NhdmUtYmFyJzsKfQoKZnVuY3Rpb24gdXBkYXRlU3RhdHMoc3R1ZGVudHMpewogIHZhciB0b3RhbCA9IHN0dWRlbnRzLmxlbmd0aDsKICB2YXIgcGFpZCA9IHN0dWRlbnRzLmZpbHRlcihmdW5jdGlvbihzKXsKICAgIHZhciBzdGF0dXMgPSAoY2hhbmdlc1tzLnJvd19pbmRleF0gIT09IHVuZGVmaW5lZCkgPyBjaGFuZ2VzW3Mucm93X2luZGV4XSA6IChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKTsKICAgIHJldHVybiBzdGF0dXMgPT09ICdwYWlkJzsKICB9KS5sZW5ndGg7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXRUb3RhbCcpLnRleHRDb250ZW50ID0gdG90YWw7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXRQYWlkJykudGV4dENvbnRlbnQgPSBwYWlkOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGF0VW5wYWlkJykudGV4dENvbnRlbnQgPSB0b3RhbCAtIHBhaWQ7Cn0KCmFzeW5jIGZ1bmN0aW9uIHNhdmVDaGFuZ2VzKCl7CiAgdmFyIGJ0biA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYXZlQnRuJyk7CiAgYnRuLmRpc2FibGVkPXRydWU7IGJ0bi50ZXh0Q29udGVudD0n4o+zIFNhdmluZy4uLic7CiAgdHJ5ewogICAgdmFyIHJlcyA9IGF3YWl0IGZldGNoKCcvYXBpL2ZpbmFuY2UvdXBkYXRlLXBheW1lbnQnLCB7CiAgICAgIG1ldGhvZDonUE9TVCcsCiAgICAgIGhlYWRlcnM6eydDb250ZW50LVR5cGUnOidhcHBsaWNhdGlvbi9qc29uJ30sCiAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KHtjaGFuZ2VzOiBjaGFuZ2VzfSkKICAgIH0pOwogICAgdmFyIGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgaWYoZGF0YS5vayl7CiAgICAgIHNob3dTdGF0dXMoJ+KchSBTYXZlZCBzdWNjZXNzZnVsbHkg4oCUICcrZGF0YS51cGRhdGVkKycgc3R1ZGVudHMgdXBkYXRlZCcsICdzdWNjZXNzJyk7CiAgICAgIC8vIFVwZGF0ZSBsb2NhbCBzdHVkZW50IGRhdGEKICAgICAgT2JqZWN0LmtleXMoY2hhbmdlcykuZm9yRWFjaChmdW5jdGlvbihyb3dJbmRleCl7CiAgICAgICAgdmFyIHMgPSBhbGxTdHVkZW50cy5maW5kKGZ1bmN0aW9uKHgpeyByZXR1cm4geC5yb3dfaW5kZXggPT0gcm93SW5kZXg7IH0pOwogICAgICAgIGlmKHMpIHMucGF5bWVudF9zdGF0dXMgPSBjaGFuZ2VzW3Jvd0luZGV4XTsKICAgICAgfSk7CiAgICAgIGNoYW5nZXMgPSB7fTsKICAgICAgdXBkYXRlU2F2ZUJhcigpOwogICAgICB1cGRhdGVTdGF0cyhhbGxTdHVkZW50cyk7CiAgICAgIHJlbmRlclRhYmxlKGFsbFN0dWRlbnRzKTsKICAgIH0gZWxzZSB7CiAgICAgIHNob3dTdGF0dXMoJ+KdjCBFcnJvcjogJytkYXRhLmVycm9yLCAnZXJyb3InKTsKICAgIH0KICB9IGNhdGNoKGUpewogICAgc2hvd1N0YXR1cygn4p2MIE5ldHdvcmsgZXJyb3I6ICcrZS5tZXNzYWdlLCAnZXJyb3InKTsKICB9CiAgYnRuLmRpc2FibGVkPWZhbHNlOyBidG4udGV4dENvbnRlbnQ9J/Cfkr4gU2F2ZSBBbGwgQ2hhbmdlcyc7Cn0KCmZ1bmN0aW9uIHNob3dTdGF0dXMobXNnLCB0eXBlKXsKICB2YXIgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdHVzTXNnJyk7CiAgZWwudGV4dENvbnRlbnQgPSBtc2c7CiAgZWwuY2xhc3NOYW1lID0gJ3N0YXR1cy1tc2cgJyt0eXBlOwogIHNldFRpbWVvdXQoZnVuY3Rpb24oKXsgZWwuc3R5bGUuZGlzcGxheT0nbm9uZSc7IH0sIDUwMDApOwp9Cjwvc2NyaXB0PjwvZGl2Pgo8L2Rpdj4KPHNjcmlwdD4KLy8g4pSA4pSAIFN0YXRlIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAp2YXIgQ1VSUkVOVF9VU0VSID0gbnVsbDsgICAvLyB7dXNlcm5hbWUsIGxhYmVsLCBncmFkZXN9CnZhciBBTExfUEFSRU5UUyAgPSBbXTsKdmFyIFNFTF9HUkFERVMgICA9IFsnQWxsJ107CgovLyBHcmFkZSBkZWZpbml0aW9ucyBwZXIgc2NvcGUKdmFyIEpVTklPUl9HUkFERVMgPSBbJ0tHMScsJ0tHMicsJ0dyYWRlIDEnLCdHcmFkZSAyJywnR3JhZGUgMycsJ0dyYWRlIDQnLCdHcmFkZSA1JywnR3JhZGUgNiddOwp2YXIgU0VOSU9SX0dSQURFUyA9IFsnR3JhZGUgNycsJ0dyYWRlIDgnLCdHcmFkZSA5JywnR3JhZGUgMTAnLCdHcmFkZSAxMScsJ0dyYWRlIDEyJ107CnZhciBBTExfR1JBREVTICAgID0gWydLRzEnLCdLRzInLCdHcmFkZSAxJywnR3JhZGUgMicsJ0dyYWRlIDMnLCdHcmFkZSA0JywnR3JhZGUgNScsJ0dyYWRlIDYnLAogICAgICAgICAgICAgICAgICAgICAnR3JhZGUgNycsJ0dyYWRlIDgnLCdHcmFkZSA5JywnR3JhZGUgMTAnLCdHcmFkZSAxMScsJ0dyYWRlIDEyJ107CgovLyBSb2xlIGNhcmQgcHJlLWZpbGwgaGludHMKdmFyIFJPTEVfSElOVFMgPSB7CiAgJ2FkbWluJzogICAgICAgICdZb3UgYXJlIHNpZ25pbmcgaW4gYXMgPHN0cm9uZz5TdXBlciBBZG1pbjwvc3Ryb25nPiDigJQgYWNjZXNzIHRvIGFsbCBncmFkZXMnLAogICdhZG1pbl9qdW5pb3InOiAnWW91IGFyZSBzaWduaW5nIGluIGFzIDxzdHJvbmc+SnVuaW9yIEFkbWluPC9zdHJvbmc+IOKAlCBLRyB0byBHcmFkZSA2IG9ubHknLAogICdhZG1pbl9zZW5pb3InOiAnWW91IGFyZSBzaWduaW5nIGluIGFzIDxzdHJvbmc+U2VuaW9yIEFkbWluPC9zdHJvbmc+IOKAlCBHcmFkZSA3IHRvIEdyYWRlIDEyIG9ubHknCn07CnZhciBST0xFX1VTRVJOQU1FUyA9IHsKICAnYWRtaW4nOidhZG1pbicsJ2FkbWluX2p1bmlvcic6J2FkbWluX2p1bmlvcicsJ2FkbWluX3Nlbmlvcic6J2FkbWluX3NlbmlvcicKfTsKCi8vIOKUgOKUgCBSb2xlIGNhcmQgcGlja2VyIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBwaWNrUm9sZShjYXJkKXsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcucm9sZS1jYXJkJykuZm9yRWFjaChmdW5jdGlvbihjKXtjLmNsYXNzTGlzdC5yZW1vdmUoJ2FjdGl2ZScpO30pOwogIGNhcmQuY2xhc3NMaXN0LmFkZCgnYWN0aXZlJyk7CiAgdmFyIHUgPSBjYXJkLmRhdGFzZXQudTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbHUnKS52YWx1ZSA9IHU7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xwJykudmFsdWUgPSAnJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc2NvcGVIaW50JykuaW5uZXJIVE1MID0gUk9MRV9ISU5UU1t1XSB8fCAnJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKfQoKLy8g4pSA4pSAIExvZ2luIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBkb0xvZ2luKCl7CiAgdmFyIHUgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbHUnKS52YWx1ZS50cmltKCk7CiAgdmFyIHAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbHAnKS52YWx1ZS50cmltKCk7CiAgaWYoIXV8fCFwKXsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsZScpLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsZScpLnRleHRDb250ZW50PSfimqDvuI8gUGxlYXNlIGVudGVyIHVzZXJuYW1lIGFuZCBwYXNzd29yZCc7CiAgICByZXR1cm47CiAgfQogIGZldGNoKCcvYXBpL2xvZ2luJyx7bWV0aG9kOidQT1NUJyxoZWFkZXJzOnsnQ29udGVudC1UeXBlJzonYXBwbGljYXRpb24vanNvbid9LAogICAgYm9keTpKU09OLnN0cmluZ2lmeSh7dXNlcm5hbWU6dSxwYXNzd29yZDpwfSkKICB9KS50aGVuKGZ1bmN0aW9uKHIpewogICAgaWYoIXIub2spIHRocm93IG5ldyBFcnJvcignYmFkJyk7CiAgICByZXR1cm4gci5qc29uKCk7CiAgfSkudGhlbihmdW5jdGlvbihkKXsKICAgIGlmKGQub2spewogICAgICBDVVJSRU5UX1VTRVIgPSBkOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2x3Jykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdhdycpLnN0eWxlLmRpc3BsYXk9J2ZsZXgnOwogICAgICBzZXR1cEFkbWluVUkoKTsKICAgICAgbG9hZFN0YXRzKCk7CiAgICAgIGxvYWRQYXJlbnRzKCk7CiAgICB9IGVsc2UgewogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsZScpLnRleHRDb250ZW50PSfinYwgSW5jb3JyZWN0IHVzZXJuYW1lIG9yIHBhc3N3b3JkJzsKICAgIH0KICB9KS5jYXRjaChmdW5jdGlvbigpewogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xlJykuc3R5bGUuZGlzcGxheT0nYmxvY2snOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xlJykudGV4dENvbnRlbnQ9J+KdjCBJbmNvcnJlY3QgdXNlcm5hbWUgb3IgcGFzc3dvcmQnOwogIH0pOwp9CgovLyDilIDilIAgU2V0dXAgVUkgYWZ0ZXIgbG9naW4g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIHNldHVwQWRtaW5VSSgpewogIHZhciB1ID0gQ1VSUkVOVF9VU0VSOwogIHZhciBpc1N1cGVyICA9IHUuZ3JhZGVzLmxlbmd0aCA9PT0gMDsKICB2YXIgaXNKdW5pb3IgPSAhaXNTdXBlciAmJiB1LmdyYWRlcy5pbmNsdWRlcygnS0cxJyk7CiAgdmFyIGlzU2VuaW9yID0gIWlzU3VwZXIgJiYgdS5ncmFkZXMuaW5jbHVkZXMoJ0dyYWRlIDcnKTsKCiAgLy8gU2lkZWJhciBiYWRnZQogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYkFkbWluTmFtZScpLnRleHRDb250ZW50ID0gdS51c2VybmFtZTsKICB2YXIgc2NvcGVFbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYkFkbWluU2NvcGUnKTsKICBpZihpc1N1cGVyKXsgIHNjb3BlRWwudGV4dENvbnRlbnQ9J0FsbCBHcmFkZXMnOyBzY29wZUVsLmNsYXNzTmFtZT0nYWItc2NvcGUgc2NvcGUtc3VwZXInOyB9CiAgZWxzZSBpZihpc0p1bmlvcil7IHNjb3BlRWwudGV4dENvbnRlbnQ9J0tHIOKAkyBHcmFkZSA2Jzsgc2NvcGVFbC5jbGFzc05hbWU9J2FiLXNjb3BlIHNjb3BlLWp1bmlvcic7IH0KICBlbHNleyAgICAgICAgICBzY29wZUVsLnRleHRDb250ZW50PSdHcmFkZSA3IOKAkyAxMic7IHNjb3BlRWwuY2xhc3NOYW1lPSdhYi1zY29wZSBzY29wZS1zZW5pb3InOyB9CgogIC8vIEhlYWRlciBzY29wZSB0YWcKICB2YXIgaHQgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnaGVhZGVyU2NvcGVUYWcnKTsKICBpZihpc1N1cGVyKXsgIGh0LnRleHRDb250ZW50PSfwn4yQICcrdS5sYWJlbDsgaHQuY2xhc3NOYW1lPSdzY29wZS10YWcgc2NvcGUtc3VwZXInOyB9CiAgZWxzZSBpZihpc0p1bmlvcil7IGh0LnRleHRDb250ZW50PSfwn46SICcrdS5sYWJlbDsgaHQuY2xhc3NOYW1lPSdzY29wZS10YWcgc2NvcGUtanVuaW9yJzsgfQogIGVsc2V7ICAgICAgICAgIGh0LnRleHRDb250ZW50PSfwn46TICcrdS5sYWJlbDsgaHQuY2xhc3NOYW1lPSdzY29wZS10YWcgc2NvcGUtc2VuaW9yJzsgfQoKICAvLyBCdWlsZCBncmFkZSBncmlkCiAgYnVpbGRHcmFkZUdyaWQoaXNTdXBlciwgaXNKdW5pb3IsIGlzU2VuaW9yKTsKfQoKLy8g4pSA4pSAIEdyYWRlIGdyaWQgYnVpbGRlciDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gYnVpbGRHcmFkZUdyaWQoaXNTdXBlciwgaXNKdW5pb3IsIGlzU2VuaW9yKXsKICB2YXIgZ2cgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZ3JhZGVHcmlkJyk7CiAgdmFyIG15R3JhZGVzID0gaXNTdXBlciA/IEFMTF9HUkFERVMgOiAoaXNKdW5pb3IgPyBKVU5JT1JfR1JBREVTIDogU0VOSU9SX0dSQURFUyk7CiAgdmFyIGxvY2tlZCAgID0gaXNTdXBlciA/IFtdIDogKGlzSnVuaW9yID8gU0VOSU9SX0dSQURFUyA6IEpVTklPUl9HUkFERVMpOwoKICB2YXIgaHRtbCA9ICcnOwoKICAvLyAiQWxsIiBidXR0b24gKHNjb3BlZCB0byB0aGlzIGFkbWluJ3MgZ3JhZGVzKQogIHZhciBhbGxMYWJlbCA9IGlzU3VwZXIgPyAnQWxsIFBhcmVudHMnIDogKGlzSnVuaW9yID8gJ0FsbCBLRyDigJMgR3JhZGUgNicgOiAnQWxsIEdyYWRlIDfigJMxMicpOwogIGh0bWwgKz0gJzxkaXYgY2xhc3M9ImdjIGFsbCBzZWwiIGRhdGEtZz0iQWxsIiBvbmNsaWNrPSJzZWxHcmFkZSh0aGlzKSI+JythbGxMYWJlbCsnPC9kaXY+JzsKCiAgLy8gSnVuaW9yIHNlY3Rpb24KICBpZihpc1N1cGVyIHx8IGlzSnVuaW9yKXsKICAgIGlmKGlzU3VwZXIpIGh0bWwgKz0gJzxkaXYgc3R5bGU9ImdyaWQtY29sdW1uOjEvLTE7Zm9udC1zaXplOjExcHg7Zm9udC13ZWlnaHQ6NjAwO2NvbG9yOiM2NDc0OGI7cGFkZGluZzo4cHggNHB4IDJweDtsZXR0ZXItc3BhY2luZzouNXB4Ij7wn46SIEpVTklPUiDigJQgS0cgdG8gR3JhZGUgNjwvZGl2Pic7CiAgICBKVU5JT1JfR1JBREVTLmZvckVhY2goZnVuY3Rpb24oZyl7CiAgICAgIGh0bWwgKz0gJzxkaXYgY2xhc3M9ImdjIiBkYXRhLWc9IicrZysnIiBvbmNsaWNrPSJzZWxHcmFkZSh0aGlzKSI+JytnKyc8L2Rpdj4nOwogICAgfSk7CiAgfQoKICAvLyBTZW5pb3Igc2VjdGlvbgogIGlmKGlzU3VwZXIgfHwgaXNTZW5pb3IpewogICAgaWYoaXNTdXBlcikgaHRtbCArPSAnPGRpdiBzdHlsZT0iZ3JpZC1jb2x1bW46MS8tMTtmb250LXNpemU6MTFweDtmb250LXdlaWdodDo2MDA7Y29sb3I6IzY0NzQ4YjtwYWRkaW5nOjhweCA0cHggMnB4O2xldHRlci1zcGFjaW5nOi41cHgiPvCfjpMgU0VOSU9SIOKAlCBHcmFkZSA3IHRvIDEyPC9kaXY+JzsKICAgIFNFTklPUl9HUkFERVMuZm9yRWFjaChmdW5jdGlvbihnKXsKICAgICAgaHRtbCArPSAnPGRpdiBjbGFzcz0iZ2MiIGRhdGEtZz0iJytnKyciIG9uY2xpY2s9InNlbEdyYWRlKHRoaXMpIj4nK2crJzwvZGl2Pic7CiAgICB9KTsKICB9CgogIGdnLmlubmVySFRNTCA9IGh0bWw7CiAgU0VMX0dSQURFUyA9IFsnQWxsJ107Cn0KCi8vIOKUgOKUgCBHcmFkZSBzZWxlY3Rpb24g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIHNlbEdyYWRlKGVsKXsKICBpZihlbC5jbGFzc0xpc3QuY29udGFpbnMoJ2xvY2tlZCcpKSByZXR1cm47CiAgdmFyIGcgPSBlbC5kYXRhc2V0Lmc7CiAgaWYoZz09PSdBbGwnKXsKICAgIFNFTF9HUkFERVMgPSBbJ0FsbCddOwogICAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLmdjJykuZm9yRWFjaChmdW5jdGlvbihjKXtjLmNsYXNzTGlzdC5yZW1vdmUoJ3NlbCcpO30pOwogICAgZWwuY2xhc3NMaXN0LmFkZCgnc2VsJyk7CiAgfSBlbHNlIHsKICAgIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3IoJy5hbGwnKS5jbGFzc0xpc3QucmVtb3ZlKCdzZWwnKTsKICAgIGVsLmNsYXNzTGlzdC50b2dnbGUoJ3NlbCcpOwogICAgU0VMX0dSQURFUyA9IFtdLnNsaWNlLmNhbGwoZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLmdjOm5vdCguYWxsKS5zZWwnKSkubWFwKGZ1bmN0aW9uKGMpe3JldHVybiBjLmRhdGFzZXQuZzt9KTsKICAgIGlmKCFTRUxfR1JBREVTLmxlbmd0aCl7IFNFTF9HUkFERVM9WydBbGwnXTsgZG9jdW1lbnQucXVlcnlTZWxlY3RvcignLmFsbCcpLmNsYXNzTGlzdC5hZGQoJ3NlbCcpOyB9CiAgfQogIHVwZGF0ZUNvdW50KCk7Cn0KCi8vIOKUgOKUgCBTdGF0cyAmIHBhcmVudHMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIHNjb3BlUGFyYW0oKXsKICB2YXIgZyA9IENVUlJFTlRfVVNFUi5ncmFkZXM7CiAgcmV0dXJuIGcubGVuZ3RoID8gJz9ncmFkZXM9JytlbmNvZGVVUklDb21wb25lbnQoZy5qb2luKCcsJykpIDogJyc7Cn0KCmZ1bmN0aW9uIGxvYWRTdGF0cygpewogIGZldGNoKCcvYXBpL3BhcmVudHMnK3Njb3BlUGFyYW0oKSkudGhlbihmdW5jdGlvbihyKXtyZXR1cm4gci5qc29uKCk7fSkudGhlbihmdW5jdGlvbihkKXsKICAgIEFMTF9QQVJFTlRTID0gZC5wYXJlbnRzIHx8IFtdOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NwMicpLnRleHRDb250ZW50ID0gQUxMX1BBUkVOVFMubGVuZ3RoOwogICAgdXBkYXRlQ291bnQoKTsKICAgIHZhciBoID0gSlNPTi5wYXJzZShsb2NhbFN0b3JhZ2UuZ2V0SXRlbSgnYmhfJytDVVJSRU5UX1VTRVIudXNlcm5hbWUpfHwnW10nKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdDInKS50ZXh0Q29udGVudCA9IGguZmlsdGVyKGZ1bmN0aW9uKHgpe3JldHVybiBuZXcgRGF0ZSh4LnQpLnRvRGF0ZVN0cmluZygpPT09bmV3IERhdGUoKS50b0RhdGVTdHJpbmcoKTt9KS5sZW5ndGg7CiAgICBpZihoLmxlbmd0aCkgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NsMycpLnRleHRDb250ZW50ID0gbmV3IERhdGUoaFswXS50KS50b0xvY2FsZURhdGVTdHJpbmcoJ2VuLUdCJyx7ZGF5OidudW1lcmljJyxtb250aDonc2hvcnQnfSk7CiAgICByZW5kZXJIaXN0b3J5KGgpOwogIH0pLmNhdGNoKGZ1bmN0aW9uKCl7IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzcDInKS50ZXh0Q29udGVudD0nMCc7IH0pOwp9CgpmdW5jdGlvbiBsb2FkUGFyZW50cygpewogIGZldGNoKCcvYXBpL3BhcmVudHMnK3Njb3BlUGFyYW0oKSkudGhlbihmdW5jdGlvbihyKXtyZXR1cm4gci5qc29uKCk7fSkudGhlbihmdW5jdGlvbihkKXsKICAgIHZhciBwbCA9IGQucGFyZW50cyB8fCBbXTsKICAgIHZhciBlbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwbDInKTsKICAgIGlmKCFwbC5sZW5ndGgpewogICAgICBlbC5pbm5lckhUTUw9JzxkaXYgc3R5bGU9ImNvbG9yOiM5NGEzYjg7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoyMHB4IDAiPk5vIHBhcmVudHMgaW4geW91ciBzY29wZSB5ZXQuPC9kaXY+JzsKICAgICAgcmV0dXJuOwogICAgfQogICAgZWwuaW5uZXJIVE1MPSc8dGFibGUgY2xhc3M9InB0YWJsZSI+PHRoZWFkPjx0cj48dGg+TmFtZTwvdGg+PHRoPlBob25lPC90aD48dGg+R3JhZGU8L3RoPjwvdHI+PC90aGVhZD48dGJvZHk+JysKICAgICAgcGwubWFwKGZ1bmN0aW9uKHApewogICAgICAgIHZhciBncmFkZSA9IHAuZ3JhZGV8fCcnOwogICAgICAgIHZhciBwaWxsQ2xzID0gZ3JhZGUudG9Mb3dlckNhc2UoKS5pbmNsdWRlcygna2cnKXx8cGFyc2VJbnQoZ3JhZGUucmVwbGFjZSgvXFxEL2csJycpKTw3ID8gJ2ctanVuaW9yJyA6ICdnLXNlbmlvcic7CiAgICAgICAgaWYoZ3JhZGUudG9Mb3dlckNhc2UoKS5pbmNsdWRlcygna2cnKSkgcGlsbENscz0nZy1rZyc7CiAgICAgICAgcmV0dXJuICc8dHI+PHRkPicrKHAubmFtZXx8J+KAlCcpKyc8L3RkPjx0ZD4nK3AucGhvbmUrJzwvdGQ+PHRkPjxzcGFuIGNsYXNzPSJncmFkZS1waWxsICcrcGlsbENscysnIj4nK2dyYWRlKyc8L3NwYW4+PC90ZD48L3RyPic7CiAgICAgIH0pLmpvaW4oJycpKyc8L3Rib2R5PjwvdGFibGU+JzsKICB9KS5jYXRjaChmdW5jdGlvbigpeyBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGwyJykuaW5uZXJIVE1MPSc8ZGl2IHN0eWxlPSJjb2xvcjojOTRhM2I4O3RleHQtYWxpZ246Y2VudGVyIj5Db3VsZCBub3QgbG9hZCBwYXJlbnRzLjwvZGl2Pic7IH0pOwp9CgpmdW5jdGlvbiB1cGRhdGVDb3VudCgpewogIHZhciBjID0gU0VMX0dSQURFUy5pbmNsdWRlcygnQWxsJykgPyBBTExfUEFSRU5UUy5sZW5ndGggOgogICAgQUxMX1BBUkVOVFMuZmlsdGVyKGZ1bmN0aW9uKHApewogICAgICByZXR1cm4gU0VMX0dSQURFUy5zb21lKGZ1bmN0aW9uKGcpeyByZXR1cm4gKHAuZ3JhZGV8fCcnKS50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKGcudG9Mb3dlckNhc2UoKSk7IH0pOwogICAgfSkubGVuZ3RoOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdybicpLnRleHRDb250ZW50ID0gYzsKfQoKLy8g4pSA4pSAIFByZXZpZXcg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIHVwZGF0ZVByZXZpZXcoKXsKICB2YXIgdCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtdCcpLnZhbHVlOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdjYycpLnRleHRDb250ZW50ID0gdC5sZW5ndGg7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3B2dCcpLnRleHRDb250ZW50ID0gbmV3IERhdGUoKS50b0xvY2FsZVRpbWVTdHJpbmcoJ2VuLVVTJyx7aG91cjonbnVtZXJpYycsbWludXRlOicyLWRpZ2l0Jyxob3VyMTI6dHJ1ZX0pOwogIGlmKHQudHJpbSgpKXsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwdicpLnRleHRDb250ZW50ID0gJ/Cfk6IgTW9kZXJuIEluZmluaXR5IFNjaG9vbFxcblxcbicrdCsnXFxuXFxu8J+TniAwMi0zNzk2LTkxNTUnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NiMicpLmRpc2FibGVkID0gZmFsc2U7CiAgfSBlbHNlIHsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwdicpLnRleHRDb250ZW50ID0gJ1lvdXIgbWVzc2FnZSB3aWxsIGFwcGVhciBoZXJlLi4uJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYjInKS5kaXNhYmxlZCA9IHRydWU7CiAgfQogIHVwZGF0ZUNvdW50KCk7Cn0KCi8vIOKUgOKUgCBTZW5kIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAovLyBQaG90byBzdGF0ZQp2YXIgdXBsb2FkZWRJbWFnZVVybCA9ICcnOwoKZnVuY3Rpb24gaGFuZGxlUGhvdG9TZWxlY3QoZSl7CiAgdmFyIGZpbGUgPSBlLnRhcmdldC5maWxlc1swXTsKICBpZighZmlsZSkgcmV0dXJuOwogIGlmKGZpbGUuc2l6ZSA+IDUqMTAyNCoxMDI0KXsgYWxlcnQoJ1Bob3RvIG11c3QgYmUgdW5kZXIgNU1CJyk7IHJldHVybjsgfQogIHZhciByZWFkZXIgPSBuZXcgRmlsZVJlYWRlcigpOwogIHJlYWRlci5vbmxvYWQgPSBmdW5jdGlvbihldil7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9QcmV2aWV3Jykuc3JjID0gZXYudGFyZ2V0LnJlc3VsdDsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1ByZXZpZXcnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9QbGFjZWhvbGRlcicpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvWm9uZScpLmNsYXNzTGlzdC5hZGQoJ2hhcy1pbWcnKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdyZW1vdmVQaG90bycpLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1ByZXZpZXdCYWRnZScpLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsKICAgIHVwZGF0ZVByZXZpZXcoKTsKICB9OwogIHJlYWRlci5yZWFkQXNEYXRhVVJMKGZpbGUpOwp9CgpmdW5jdGlvbiByZW1vdmVQaG90b0ZuKCl7CiAgdXBsb2FkZWRJbWFnZVVybD0nJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9JbnB1dCcpLnZhbHVlPScnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1ByZXZpZXcnKS5zcmM9Jyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvUHJldmlldycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1BsYWNlaG9sZGVyJykuc3R5bGUuZGlzcGxheT0nYmxvY2snOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwaG90b1pvbmUnKS5jbGFzc0xpc3QucmVtb3ZlKCdoYXMtaW1nJyk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3JlbW92ZVBob3RvJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvUHJldmlld0JhZGdlJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3VwbG9hZFN0YXR1cycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIHVwZGF0ZVByZXZpZXcoKTsKfQoKYXN5bmMgZnVuY3Rpb24gaW1hZ2VUb0Jhc2U2NChmaWxlKXsKICAvLyBSZXNpemUgdG8gODAwcHggbWF4IGFuZCBjb21wcmVzcyB0byB+MjAwS0IgYmVmb3JlIHNlbmRpbmcKICByZXR1cm4gbmV3IFByb21pc2UoZnVuY3Rpb24ocmVzb2x2ZSl7CiAgICB2YXIgaW1nID0gbmV3IEltYWdlKCk7CiAgICB2YXIgdXJsID0gVVJMLmNyZWF0ZU9iamVjdFVSTChmaWxlKTsKICAgIGltZy5vbmxvYWQgPSBmdW5jdGlvbigpewogICAgICB2YXIgY2FudmFzID0gZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgnY2FudmFzJyk7CiAgICAgIHZhciBNQVggPSA4MDA7CiAgICAgIHZhciB3ID0gaW1nLndpZHRoLCBoID0gaW1nLmhlaWdodDsKICAgICAgaWYodyA+IGgpeyBpZih3Pk1BWCl7aD1NYXRoLnJvdW5kKGgqTUFYL3cpO3c9TUFYO30gfQogICAgICBlbHNlICAgICAgeyBpZihoPk1BWCl7dz1NYXRoLnJvdW5kKHcqTUFYL2gpO2g9TUFYO30gfQogICAgICBjYW52YXMud2lkdGggPSB3OyBjYW52YXMuaGVpZ2h0ID0gaDsKICAgICAgY2FudmFzLmdldENvbnRleHQoJzJkJykuZHJhd0ltYWdlKGltZywgMCwgMCwgdywgaCk7CiAgICAgIC8vIFN0cmlwIHRoZSBkYXRhOmltYWdlL2pwZWc7YmFzZTY0LCBwcmVmaXgKICAgICAgdmFyIGI2NCA9IGNhbnZhcy50b0RhdGFVUkwoJ2ltYWdlL2pwZWcnLCAwLjY1KS5zcGxpdCgnLCcpWzFdOwogICAgICBVUkwucmV2b2tlT2JqZWN0VVJMKHVybCk7CiAgICAgIHJlc29sdmUoYjY0KTsKICAgIH07CiAgICBpbWcuc3JjID0gdXJsOwogIH0pOwp9Cgphc3luYyBmdW5jdGlvbiB1cGxvYWRQaG90bygpewogIHZhciBmaWxlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Bob3RvSW5wdXQnKS5maWxlc1swXTsKICBpZighZmlsZSkgcmV0dXJuIG51bGw7CiAgdmFyIHN0ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3VwbG9hZFN0YXR1cycpOwogIHN0LnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsgc3QudGV4dENvbnRlbnQ9J1x1MjNmMyBDb21wcmVzc2luZyBwaG90by4uLic7CiAgdHJ5ewogICAgLy8gR2V0IFdBIGNyZWRlbnRpYWxzIGZyb20gc2VydmVyCiAgICB2YXIgY2ZnUmVzID0gYXdhaXQgZmV0Y2goJy9hcGkvd2EtY29uZmlnJyk7CiAgICBpZighY2ZnUmVzLm9rKXsgc3QudGV4dENvbnRlbnQ9J+KdjCBDb25maWcgZXJyb3IgJytjZmdSZXMuc3RhdHVzOyByZXR1cm4gbnVsbDsgfQogICAgdmFyIGNmZyA9IGF3YWl0IGNmZ1Jlcy5qc29uKCk7CiAgICAvLyBSZXNpemUgKyBjb21wcmVzcyBpbWFnZSBpbiBicm93c2VyCiAgICB2YXIgYmxvYiA9IGF3YWl0IG5ldyBQcm9taXNlKGZ1bmN0aW9uKHJlc29sdmUpewogICAgICB2YXIgaW1nID0gbmV3IEltYWdlKCk7CiAgICAgIHZhciB1cmwgPSBVUkwuY3JlYXRlT2JqZWN0VVJMKGZpbGUpOwogICAgICBpbWcub25sb2FkID0gZnVuY3Rpb24oKXsKICAgICAgICB2YXIgYyA9IGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2NhbnZhcycpOwogICAgICAgIHZhciBNQVg9ODAwLHc9aW1nLndpZHRoLGg9aW1nLmhlaWdodDsKICAgICAgICBpZih3Pmgpe2lmKHc+TUFYKXtoPU1hdGgucm91bmQoaCpNQVgvdyk7dz1NQVg7fX0KICAgICAgICBlbHNle2lmKGg+TUFYKXt3PU1hdGgucm91bmQodypNQVgvaCk7aD1NQVg7fX0KICAgICAgICBjLndpZHRoPXc7IGMuaGVpZ2h0PWg7CiAgICAgICAgYy5nZXRDb250ZXh0KCcyZCcpLmRyYXdJbWFnZShpbWcsMCwwLHcsaCk7CiAgICAgICAgYy50b0Jsb2IoZnVuY3Rpb24oYil7cmVzb2x2ZShiKTt9LCdpbWFnZS9qcGVnJywwLjY1KTsKICAgICAgICBVUkwucmV2b2tlT2JqZWN0VVJMKHVybCk7CiAgICAgIH07CiAgICAgIGltZy5zcmM9dXJsOwogICAgfSk7CiAgICBzdC50ZXh0Q29udGVudD0nXHUyM2YzIFVwbG9hZGluZyBwaG90by4uLic7CiAgICAvLyBVcGxvYWQgZGlyZWN0bHkgdG8gTWV0YSBBUEkgZnJvbSBicm93c2VyIOKAlCBubyBSYWlsd2F5IHByb3h5CiAgICB2YXIgZmQgPSBuZXcgRm9ybURhdGEoKTsKICAgIGZkLmFwcGVuZCgnZmlsZScsIGJsb2IsICdwaG90by5qcGcnKTsKICAgIGZkLmFwcGVuZCgnbWVzc2FnaW5nX3Byb2R1Y3QnLCd3aGF0c2FwcCcpOwogICAgdmFyIHVwID0gYXdhaXQgZmV0Y2goCiAgICAgICdodHRwczovL2dyYXBoLmZhY2Vib29rLmNvbS92MTguMC8nK2NmZy5waG9uZV9udW1iZXJfaWQrJy9tZWRpYScsCiAgICAgIHttZXRob2Q6J1BPU1QnLGhlYWRlcnM6eydBdXRob3JpemF0aW9uJzonQmVhcmVyICcrY2ZnLmFjY2Vzc190b2tlbn0sYm9keTpmZH0KICAgICk7CiAgICB2YXIgdXBkID0gYXdhaXQgdXAuanNvbigpOwogICAgaWYodXBkLmlkKXsKICAgICAgc3QudGV4dENvbnRlbnQ9J1x1MjcwNSBQaG90byByZWFkeSB0byBzZW5kJzsKICAgICAgcmV0dXJuIHVwZC5pZDsKICAgIH0gZWxzZSB7CiAgICAgIHN0LnRleHRDb250ZW50PSdcdTI3NGMgVXBsb2FkIGZhaWxlZDogJysodXBkLmVycm9yJiZ1cGQuZXJyb3IubWVzc2FnZXx8SlNPTi5zdHJpbmdpZnkodXBkKSk7CiAgICAgIHJldHVybiBudWxsOwogICAgfQogIH0gY2F0Y2goZSl7CiAgICBzdC50ZXh0Q29udGVudD0nXHUyNzRjIFVwbG9hZCBlcnJvcjogJytlLm1lc3NhZ2U7CiAgICByZXR1cm4gbnVsbDsKICB9Cn0KCmFzeW5jIGZ1bmN0aW9uIGRvU2VuZCgpewogIHZhciBtc2cgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXQnKS52YWx1ZS50cmltKCk7CiAgaWYoIW1zZykgcmV0dXJuOwogIHZhciBidG4gPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc2IyJyk7CiAgYnRuLmRpc2FibGVkPXRydWU7IGJ0bi50ZXh0Q29udGVudD0n4o+zIFNlbmRpbmcuLi4nOwogIC8vIFVwbG9hZCBwaG90byBmaXJzdCBpZiBzZWxlY3RlZAogIHZhciBwaG90b0ZpbGUgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGhvdG9JbnB1dCcpLmZpbGVzWzBdOwogIHZhciBtZWRpYUlkID0gJyc7CiAgaWYocGhvdG9GaWxlKXsKICAgIGJ0bi50ZXh0Q29udGVudD0n4o+zIFVwbG9hZGluZyBwaG90by4uLic7CiAgICBtZWRpYUlkID0gYXdhaXQgdXBsb2FkUGhvdG8oKSB8fCAnJzsKICAgIGlmKCFtZWRpYUlkKXsgYnRuLmRpc2FibGVkPWZhbHNlOyBidG4udGV4dENvbnRlbnQ9J/Cfk6QgU2VuZCB0byBQYXJlbnRzJzsgcmV0dXJuOyB9CiAgfQogIGJ0bi50ZXh0Q29udGVudD0n4o+zIFNlbmRpbmcuLi4nOwogIGZldGNoKCcvYnJvYWRjYXN0Jyx7CiAgICBtZXRob2Q6J1BPU1QnLAogICAgaGVhZGVyczp7J0NvbnRlbnQtVHlwZSc6J2FwcGxpY2F0aW9uL2pzb24nfSwKICAgIGJvZHk6SlNPTi5zdHJpbmdpZnkoewogICAgICBtZXNzYWdlOiBtc2csCiAgICAgIG1lZGlhX2lkOiBtZWRpYUlkLAogICAgICBncmFkZXM6ICBTRUxfR1JBREVTLAogICAgICBhZG1pbl9zY29wZTogQ1VSUkVOVF9VU0VSLmdyYWRlcyAgIC8vIHNlcnZlciBlbmZvcmNlcyB0aGlzCiAgICB9KQogIH0pLnRoZW4oZnVuY3Rpb24ocil7cmV0dXJuIHIuanNvbigpO30pLnRoZW4oZnVuY3Rpb24oZCl7CiAgICB2YXIgaCA9IEpTT04ucGFyc2UobG9jYWxTdG9yYWdlLmdldEl0ZW0oJ2JoXycrQ1VSUkVOVF9VU0VSLnVzZXJuYW1lKXx8J1tdJyk7CiAgICBoLnVuc2hpZnQoe21zZzptc2csZzpTRUxfR1JBREVTLHM6ZC5zZW50fHwwLHQ6bmV3IERhdGUoKS50b0lTT1N0cmluZygpfSk7CiAgICBsb2NhbFN0b3JhZ2Uuc2V0SXRlbSgnYmhfJytDVVJSRU5UX1VTRVIudXNlcm5hbWUsIEpTT04uc3RyaW5naWZ5KGguc2xpY2UoMCw1MCkpKTsKICAgIHJlbmRlckhpc3RvcnkoaCk7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbXMnKS50ZXh0Q29udGVudD0nU2VudCB0byAnKyhkLnNlbnR8fDApKycgcGFyZW50cyBzdWNjZXNzZnVsbHkuJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtbycpLmNsYXNzTGlzdC5hZGQoJ3Nob3cnKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtdCcpLnZhbHVlPScnOwogICAgdXBkYXRlUHJldmlldygpOwogICAgbG9hZFN0YXRzKCk7CiAgfSkuY2F0Y2goZnVuY3Rpb24oKXsgYWxlcnQoJ0Vycm9yIHNlbmRpbmcuIFBsZWFzZSB0cnkgYWdhaW4uJyk7IH0pOwogIGJ0bi5kaXNhYmxlZD1mYWxzZTsgYnRuLnRleHRDb250ZW50PSfwn5OkIFNlbmQgdG8gUGFyZW50cyc7Cn0KCmZ1bmN0aW9uIGNsb3NlTW9kYWwoKXsgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21vJykuY2xhc3NMaXN0LnJlbW92ZSgnc2hvdycpOyB9CgovLyDilIDilIAgSGlzdG9yeSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gcmVuZGVySGlzdG9yeShoKXsKICB2YXIgbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdobCcpOwogIGlmKCFofHwhaC5sZW5ndGgpewogICAgbC5pbm5lckhUTUw9JzxkaXYgc3R5bGU9ImNvbG9yOiM5NGEzYjg7dGV4dC1hbGlnbjpjZW50ZXI7cGFkZGluZzoyMHB4IDAiPk5vIGFubm91bmNlbWVudHMgeWV0PC9kaXY+JzsKICAgIHJldHVybjsKICB9CiAgbC5pbm5lckhUTUwgPSBoLm1hcChmdW5jdGlvbih4KXsKICAgIHJldHVybiAnPGRpdiBjbGFzcz0iaGkiPicrCiAgICAgICc8ZGl2IHN0eWxlPSJ3aWR0aDo0MHB4O2hlaWdodDo0MHB4O2JvcmRlci1yYWRpdXM6MTBweDtiYWNrZ3JvdW5kOiNmMGZkZjQ7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtqdXN0aWZ5LWNvbnRlbnQ6Y2VudGVyO2ZvbnQtc2l6ZToxOHB4O2ZsZXgtc2hyaW5rOjAiPvCfk6M8L2Rpdj4nKwogICAgICAnPGRpdiBjbGFzcz0iaGljIj48ZGl2PicreC5tc2crJzwvZGl2PicrCiAgICAgICc8ZGl2IGNsYXNzPSJobSI+JysKICAgICAgICBuZXcgRGF0ZSh4LnQpLnRvTG9jYWxlU3RyaW5nKCdlbi1HQicse2RheTonbnVtZXJpYycsbW9udGg6J3Nob3J0Jyxob3VyOicyLWRpZ2l0JyxtaW51dGU6JzItZGlnaXQnfSkrCiAgICAgICAgJyDCtyAnKyh4Lmd8fFsnQWxsJ10pLmpvaW4oJywgJykrCiAgICAgICAgJyDCtyA8c3BhbiBjbGFzcz0iaGIiPuKchSAnKyh4LnN8fDApKycgc2VudDwvc3Bhbj4nKwogICAgICAnPC9kaXY+PC9kaXY+PC9kaXY+JzsKICB9KS5qb2luKCcnKTsKfQoKLy8g4pSA4pSAIFRhYnMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIHNob3dUYWIodCl7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RhYi1iJykuc3R5bGUuZGlzcGxheSA9IHQ9PT0nYic/J2Jsb2NrJzonbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RhYi1oJykuc3R5bGUuZGlzcGxheSA9IHQ9PT0naCc/J2Jsb2NrJzonbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RhYi1wJykuc3R5bGUuZGlzcGxheSA9IHQ9PT0ncCc/J2Jsb2NrJzonbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3NyJykuc3R5bGUuZGlzcGxheSAgICA9IHQ9PT0nYic/J2dyaWQnOidub25lJzsKICB2YXIgdHQ9e2I6Wyfwn5OjIFNlbmQgQW5ub3VuY2VtZW50JywnQnJvYWRjYXN0IGEgbWVzc2FnZSB0byBwYXJlbnRzIHZpYSBXaGF0c0FwcCddLAogICAgICAgICAgaDpbJ/Cfk4sgSGlzdG9yeScsJ0FsbCBhbm5vdW5jZW1lbnRzIHNlbnQgZnJvbSB0aGlzIGFjY291bnQnXSwKICAgICAgICAgIHA6Wyfwn5GlIFBhcmVudHMnLCdQYXJlbnRzIHJlZ2lzdGVyZWQgd2l0aGluIHlvdXIgZ3JhZGUgc2NvcGUnXX07CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3B0JykudGV4dENvbnRlbnQgPSB0dFt0XVswXTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncHMnKS50ZXh0Q29udGVudCA9IHR0W3RdWzFdOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoJy5uaScpLmZvckVhY2goZnVuY3Rpb24oZSxpKXsgZS5jbGFzc0xpc3QudG9nZ2xlKCdhY3RpdmUnLFsnYicsJ2gnLCdwJ11baV09PT10KTsgfSk7CiAgaWYodD09PSdwJykgbG9hZFBhcmVudHMoKTsKfQoKLy8g4pSA4pSAIExvZ291dCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gZG9Mb2dvdXQoKXsKICBDVVJSRU5UX1VTRVI9bnVsbDsgQUxMX1BBUkVOVFM9W107IFNFTF9HUkFERVM9WydBbGwnXTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXcnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbHcnKS5zdHlsZS5kaXNwbGF5PSdmbGV4JzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbHUnKS52YWx1ZT0nJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbHAnKS52YWx1ZT0nJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGUnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcucm9sZS1jYXJkJykuZm9yRWFjaChmdW5jdGlvbihjLGkpe2MuY2xhc3NMaXN0LnRvZ2dsZSgnYWN0aXZlJyxpPT09MCk7fSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3Njb3BlSGludCcpLmlubmVySFRNTCA9IFJPTEVfSElOVFNbJ2FkbWluJ107CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ210JykudmFsdWU9Jyc7Cn0KPC9zY3JpcHQ+CjxzY3JpcHQ+Ci8vIOKUgOKUgCBDcmVkZW50aWFscyAoY2hlY2tlZCBjbGllbnQtc2lkZSArIHNlcnZlciB2YWxpZGF0ZXMgb24gQVBJIGNhbGxzKSDilIDilIDilIDilIDilIAKLy8g4pSA4pSAIEF1dGggc3RhdGUg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACnZhciBDVVJSRU5UX1RFQUNIRVIgPSAiIjsKdmFyIENVUlJFTlRfRlVMTF9OQU1FID0gIiI7CgovLyDilIDilIAgTG9naW4g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmFzeW5jIGZ1bmN0aW9uIGRvVGVhY2hlckxvZ2luKCkgewogIGNvbnN0IHVzZXIgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tdXNlcicpLnZhbHVlLnRyaW0oKS50b0xvd2VyQ2FzZSgpOwogIGNvbnN0IHBhc3MgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tcGFzcycpLnZhbHVlOwogIGNvbnN0IGVyciAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tZXJyb3InKTsKICBjb25zdCBidG4gID0gZG9jdW1lbnQucXVlcnlTZWxlY3RvcignLmxvZ2luLWJ0bicpOwogIGlmICghdXNlciB8fCAhcGFzcykgewogICAgZXJyLnRleHRDb250ZW50ID0gJ1x1MjZhMFx1ZmUwZiBQbGVhc2UgZW50ZXIgdXNlcm5hbWUgYW5kIHBhc3N3b3JkJzsKICAgIGVyci5zdHlsZS5kaXNwbGF5ID0gJ2Jsb2NrJzsgcmV0dXJuOwogIH0KICBidG4udGV4dENvbnRlbnQgPSAnU2lnbmluZyBpbi4uLic7IGJ0bi5kaXNhYmxlZCA9IHRydWU7CiAgZXJyLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7CiAgdHJ5IHsKICAgIGNvbnN0IHJlcyA9IGF3YWl0IGZldGNoKCcvYXBpL3RlYWNoZXItbG9naW4nLCB7CiAgICAgIG1ldGhvZDogJ1BPU1QnLCBoZWFkZXJzOiB7J0NvbnRlbnQtVHlwZSc6J2FwcGxpY2F0aW9uL2pzb24nfSwKICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoe3VzZXJuYW1lOiB1c2VyLCBwYXNzd29yZDogcGFzc30pCiAgICB9KTsKICAgIGNvbnN0IGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgaWYgKGRhdGEub2spIHsKICAgICAgQ1VSUkVOVF9URUFDSEVSICAgPSB1c2VyOwogICAgICBDVVJSRU5UX0ZVTExfTkFNRSA9IGRhdGEuZnVsbF9uYW1lOwogICAgICBzZXNzaW9uU3RvcmFnZS5zZXRJdGVtKCd0ZWFjaGVyX3VzZXInLCB1c2VyKTsKICAgICAgc2Vzc2lvblN0b3JhZ2Uuc2V0SXRlbSgndGVhY2hlcl9uYW1lJywgZGF0YS5mdWxsX25hbWUpOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5ID0gJ2Jsb2NrJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RlYWNoZXItYmFkZ2UnKS50ZXh0Q29udGVudCA9ICfwn5GkICcgKyBkYXRhLmZ1bGxfbmFtZTsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RlYWNoZXInKS52YWx1ZSA9IGRhdGEuZnVsbF9uYW1lOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtdGVhY2hlcicpLnZhbHVlID0gZGF0YS5mdWxsX25hbWU7CiAgICAgIGxvY2FsU3RvcmFnZS5zZXRJdGVtKCdod190ZWFjaGVyJywgZGF0YS5mdWxsX25hbWUpOwogICAgfSBlbHNlIHsKICAgICAgZXJyLnRleHRDb250ZW50ID0gJ+KdjCAnICsgZGF0YS5lcnJvcjsKICAgICAgZXJyLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tcGFzcycpLnZhbHVlID0gJyc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi1wYXNzJykuZm9jdXMoKTsKICAgIH0KICB9IGNhdGNoKGUpIHsKICAgIGVyci50ZXh0Q29udGVudCA9ICfinYwgQ29ubmVjdGlvbiBlcnJvci4gUGxlYXNlIHRyeSBhZ2Fpbi4nOwogICAgZXJyLnN0eWxlLmRpc3BsYXkgPSAnYmxvY2snOwogIH0KICBidG4udGV4dENvbnRlbnQgPSAnU2lnbiBJbiDihpInOyBidG4uZGlzYWJsZWQgPSBmYWxzZTsKfQoKZnVuY3Rpb24gZG9Mb2dvdXQoKSB7CiAgc2Vzc2lvblN0b3JhZ2UucmVtb3ZlSXRlbSgndGVhY2hlcl91c2VyJyk7CiAgc2Vzc2lvblN0b3JhZ2UucmVtb3ZlSXRlbSgndGVhY2hlcl9uYW1lJyk7CiAgQ1VSUkVOVF9URUFDSEVSID0gIiI7IENVUlJFTlRfRlVMTF9OQU1FID0gIiI7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5ID0gJ25vbmUnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi1zY3JlZW4nKS5zdHlsZS5kaXNwbGF5ID0gJ2ZsZXgnOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi11c2VyJykudmFsdWUgPSAnJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tcGFzcycpLnZhbHVlID0gJyc7Cn0KCndpbmRvdy5vbmxvYWQgPSBmdW5jdGlvbigpIHsKICBjb25zdCB0bXIgPSBuZXcgRGF0ZSgpOyB0bXIuc2V0RGF0ZSh0bXIuZ2V0RGF0ZSgpKzEpOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkdWVfZGF0ZScpLnZhbHVlID0gdG1yLnRvSVNPU3RyaW5nKCkuc3BsaXQoJ1QnKVswXTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtZGF0ZScpLnZhbHVlID0gbmV3IERhdGUoKS50b0lTT1N0cmluZygpLnNwbGl0KCdUJylbMF07CiAgY29uc3QgcyA9IHNlc3Npb25TdG9yYWdlLmdldEl0ZW0oJ3RlYWNoZXJfdXNlcicpOwogIGNvbnN0IG4gPSBzZXNzaW9uU3RvcmFnZS5nZXRJdGVtKCd0ZWFjaGVyX25hbWUnKTsKICBpZiAocyAmJiBuKSB7CiAgICBDVVJSRU5UX1RFQUNIRVIgPSBzOyBDVVJSRU5UX0ZVTExfTkFNRSA9IG47CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdtYWluLXBhbmVsJykuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGVhY2hlci1iYWRnZScpLnRleHRDb250ZW50ID0gJ/CfkaQgJyArIG47CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGVhY2hlcicpLnZhbHVlID0gbjsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10ZWFjaGVyJykudmFsdWUgPSBuOwogIH0KICBjb25zdCBzYXZlZCA9IGxvY2FsU3RvcmFnZS5nZXRJdGVtKCdod190ZWFjaGVyJyk7CiAgaWYgKHNhdmVkICYmICFDVVJSRU5UX1RFQUNIRVIpIHsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0ZWFjaGVyJykudmFsdWUgPSBzYXZlZDsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10ZWFjaGVyJykudmFsdWUgPSBzYXZlZDsKICB9Cn07CgovLyDilIDilIAgVGFiIHN3aXRjaGluZyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gc3dpdGNoVGFiKHRhYiwgYnRuKSB7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnRhYi1wYW5lbCcpLmZvckVhY2gocCA9PiBwLmNsYXNzTGlzdC5yZW1vdmUoJ2FjdGl2ZScpKTsKICBkb2N1bWVudC5xdWVyeVNlbGVjdG9yQWxsKCcudGFiLWJ0bicpLmZvckVhY2goYiA9PiBiLmNsYXNzTGlzdC5yZW1vdmUoJ2FjdGl2ZScpKTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGFiLScgKyB0YWIpLmNsYXNzTGlzdC5hZGQoJ2FjdGl2ZScpOwogIGJ0bi5jbGFzc0xpc3QuYWRkKCdhY3RpdmUnKTsKfQoKLy8g4pSA4pSAIFN5bmMgdGVhY2hlciBuYW1lIGFjcm9zcyB0YWJzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCdET01Db250ZW50TG9hZGVkJywgZnVuY3Rpb24oKSB7CiAgWyd0ZWFjaGVyJywnZXgtdGVhY2hlciddLmZvckVhY2goZnVuY3Rpb24oaWQpIHsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKT8uYWRkRXZlbnRMaXN0ZW5lcignaW5wdXQnLCBmdW5jdGlvbigpIHsKICAgICAgY29uc3Qgb3RoZXIgPSBpZCA9PT0gJ3RlYWNoZXInID8gJ2V4LXRlYWNoZXInIDogJ3RlYWNoZXInOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZChvdGhlcikudmFsdWUgPSB0aGlzLnZhbHVlOwogICAgfSk7CiAgfSk7Cn0pOwoKLy8g4pSA4pSAIEhPTUVXT1JLIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApjb25zdCBod0hpc3RvcnkgPSBbXTsKYXN5bmMgZnVuY3Rpb24gc3VibWl0SG9tZXdvcmsoKSB7CiAgY29uc3QgYnRuICAgID0gZG9jdW1lbnQucXVlcnlTZWxlY3RvcignI3RhYi1ob21ld29yayAuc3VibWl0LWJ0bicpOwogIGNvbnN0IHN0YXR1cyA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdody1zdGF0dXMnKTsKICBjb25zdCB0ZWFjaGVyICAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RlYWNoZXInKS52YWx1ZS50cmltKCk7CiAgY29uc3QgZ3JhZGUgICAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdncmFkZScpLnZhbHVlOwogIGNvbnN0IHN1YmplY3QgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3ViamVjdCcpLnZhbHVlOwogIGNvbnN0IGFzc2lnbm1lbnQgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXNzaWdubWVudCcpLnZhbHVlLnRyaW0oKTsKICBjb25zdCBkdWVfZGF0ZSAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2R1ZV9kYXRlJykudmFsdWU7CiAgY29uc3QgdHlwZSAgICAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCd0eXBlJykudmFsdWU7CiAgY29uc3Qgbm90ZXMgICAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdub3RlcycpLnZhbHVlLnRyaW0oKTsKCiAgY29uc3QgbWlzc2luZyA9IFtdOwogIGlmICghdGVhY2hlcikgbWlzc2luZy5wdXNoKCdUZWFjaGVyIE5hbWUnKTsKICBpZiAoIWdyYWRlKSBtaXNzaW5nLnB1c2goJ0dyYWRlJyk7CiAgaWYgKCFzdWJqZWN0KSBtaXNzaW5nLnB1c2goJ1N1YmplY3QnKTsKICBpZiAoIWFzc2lnbm1lbnQpIG1pc3NpbmcucHVzaCgnQXNzaWdubWVudCcpOwogIGlmICghZHVlX2RhdGUpIG1pc3NpbmcucHVzaCgnRHVlIERhdGUnKTsKCiAgaWYgKG1pc3NpbmcubGVuZ3RoKSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pqg77iPIFBsZWFzZSBmaWxsIGluOiAnICsgbWlzc2luZy5qb2luKCcsICcpOwogICAgcmV0dXJuOwogIH0KCiAgYnRuLmRpc2FibGVkID0gdHJ1ZTsgYnRuLnRleHRDb250ZW50ID0gJ1NhdmluZ+KApic7CiAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwoKICB0cnkgewogICAgY29uc3QgcmVzID0gYXdhaXQgZmV0Y2goJy9hcGkvYWRkLWhvbWV3b3JrJywgewogICAgICBtZXRob2Q6ICdQT1NUJywgaGVhZGVyczogeydDb250ZW50LVR5cGUnOiAnYXBwbGljYXRpb24vanNvbid9LAogICAgICBib2R5OiBKU09OLnN0cmluZ2lmeSh7dGVhY2hlciwgZ3JhZGUsIHN1YmplY3QsIGFzc2lnbm1lbnQsIGR1ZV9kYXRlLCB0eXBlLCBub3Rlc30pCiAgICB9KTsKICAgIGNvbnN0IGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgaWYgKGRhdGEub2spIHsKICAgICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMgc3VjY2Vzcyc7CiAgICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICfinIUgJyArIGRhdGEubWVzc2FnZTsKICAgICAgbG9jYWxTdG9yYWdlLnNldEl0ZW0oJ2h3X3RlYWNoZXInLCB0ZWFjaGVyKTsKICAgICAgaHdIaXN0b3J5LnVuc2hpZnQoe3RlYWNoZXIsIGdyYWRlLCBzdWJqZWN0LCBhc3NpZ25tZW50LCBkdWVfZGF0ZSwgdHlwZX0pOwogICAgICByZW5kZXJId0hpc3RvcnkoKTsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N1YmplY3QnKS52YWx1ZSA9ICcnOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXNzaWdubWVudCcpLnZhbHVlID0gJyc7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdub3RlcycpLnZhbHVlID0gJyc7CiAgICAgIGNvbnN0IHRtciA9IG5ldyBEYXRlKCk7IHRtci5zZXREYXRlKHRtci5nZXREYXRlKCkrMSk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdkdWVfZGF0ZScpLnZhbHVlID0gdG1yLnRvSVNPU3RyaW5nKCkuc3BsaXQoJ1QnKVswXTsKICAgIH0gZWxzZSB7CiAgICAgIHN0YXR1cy5jbGFzc05hbWUgPSAnc3RhdHVzIGVycm9yJzsKICAgICAgc3RhdHVzLnRleHRDb250ZW50ID0gJ+KdjCAnICsgZGF0YS5lcnJvcjsKICAgIH0KICB9IGNhdGNoKGUpIHsKICAgIHN0YXR1cy5jbGFzc05hbWUgPSAnc3RhdHVzIGVycm9yJzsKICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICfinYwgTmV0d29yayBlcnJvci4gVHJ5IGFnYWluLic7CiAgfQogIGJ0bi5kaXNhYmxlZCA9IGZhbHNlOyBidG4udGV4dENvbnRlbnQgPSAn4p6VIEFERCBIT01FV09SSyc7Cn0KCmZ1bmN0aW9uIHJlbmRlckh3SGlzdG9yeSgpIHsKICBjb25zdCBlbCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdody1oaXN0b3J5Jyk7CiAgaWYgKCFod0hpc3RvcnkubGVuZ3RoKSB7IGVsLmlubmVySFRNTCA9ICc8ZGl2IGNsYXNzPSJuby1oaXN0Ij5Ob3RoaW5nIGFkZGVkIHlldC48L2Rpdj4nOyByZXR1cm47IH0KICBlbC5pbm5lckhUTUwgPSBod0hpc3Rvcnkuc2xpY2UoMCw1KS5tYXAoaCA9PgogICAgYDxkaXYgY2xhc3M9Imhpc3QtaXRlbSI+PHN0cm9uZz4ke2guZ3JhZGV9IOKAlCAke2guc3ViamVjdH08L3N0cm9uZz46ICR7aC5hc3NpZ25tZW50LnN1YnN0cmluZygwLDcwKX0ke2guYXNzaWdubWVudC5sZW5ndGg+NzA/J+KApic6Jyd9CiAgICAgPGRpdiBjbGFzcz0ibWV0YSI+8J+ThSAke2guZHVlX2RhdGV9IMK3IPCfkaQgJHtoLnRlYWNoZXJ9IMK3IPCfl4LvuI8gJHtoLnR5cGV9PC9kaXY+PC9kaXY+YAogICkuam9pbignJyk7Cn0KCi8vIOKUgOKUgCBFWEFNIFJFU1VMVFMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACnZhciBsb2FkZWRTdHVkZW50cyA9IFtdOwpjb25zdCBleEhpc3RvcnkgPSBbXTsKCmZ1bmN0aW9uIGNsZWFyU3R1ZGVudHMoKSB7CiAgbG9hZGVkU3R1ZGVudHMgPSBbXTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3R1ZGVudHMtc2VjdGlvbicpLnN0eWxlLmRpc3BsYXkgPSAnbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXN0YXR1cycpLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwp9Cgphc3luYyBmdW5jdGlvbiBsb2FkU3R1ZGVudHMoKSB7CiAgY29uc3QgZ3JhZGUgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtZ3JhZGUnKS52YWx1ZTsKICBpZiAoIWdyYWRlKSB7IGFsZXJ0KCdQbGVhc2Ugc2VsZWN0IGEgZ3JhZGUgZmlyc3QuJyk7IHJldHVybjsgfQogIGNvbnN0IGJ0biA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2FkQnRuJyk7CiAgY29uc3Qgc3RhdHVzID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXN0YXR1cycpOwogIGJ0bi5kaXNhYmxlZCA9IHRydWU7IGJ0bi50ZXh0Q29udGVudCA9ICfij7MgTG9hZGluZyBzdHVkZW50c+KApic7CiAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwogIHRyeSB7CiAgICBjb25zdCByZXMgPSBhd2FpdCBmZXRjaCgnL2FwaS9zdHVkZW50cy1ieS1ncmFkZT9ncmFkZT0nICsgZW5jb2RlVVJJQ29tcG9uZW50KGdyYWRlKSk7CiAgICBjb25zdCBkYXRhID0gYXdhaXQgcmVzLmpzb24oKTsKICAgIGlmICghZGF0YS5vayB8fCAhZGF0YS5zdHVkZW50cy5sZW5ndGgpIHsKICAgICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMgZXJyb3InOwogICAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4p2MIE5vIHN0dWRlbnRzIGZvdW5kIGZvciAnICsgZ3JhZGU7CiAgICAgIGJ0bi5kaXNhYmxlZCA9IGZhbHNlOyBidG4udGV4dENvbnRlbnQgPSAn8J+RpSBMb2FkIFN0dWRlbnRzIGZvciBUaGlzIEdyYWRlJzsKICAgICAgcmV0dXJuOwogICAgfQogICAgbG9hZGVkU3R1ZGVudHMgPSBkYXRhLnN0dWRlbnRzOwogICAgcmVuZGVyU3R1ZGVudFRhYmxlKGxvYWRlZFN0dWRlbnRzKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdHVkZW50cy1zZWN0aW9uJykuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3R1ZGVudC1jb3VudCcpLnRleHRDb250ZW50ID0KICAgICAgYPCfk4sgJHtkYXRhLmNvdW50fSBzdHVkZW50cyBpbiAke2dyYWRlfSDigJQgZW50ZXIgZWFjaCBzY29yZSBiZWxvdzpgOwogICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwogIH0gY2F0Y2goZSkgewogICAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMgZXJyb3InOwogICAgc3RhdHVzLnRleHRDb250ZW50ID0gJ+KdjCBFcnJvciBsb2FkaW5nIHN0dWRlbnRzOiAnICsgZS5tZXNzYWdlOwogIH0KICBidG4uZGlzYWJsZWQgPSBmYWxzZTsgYnRuLnRleHRDb250ZW50ID0gJ/CfkaUgTG9hZCBTdHVkZW50cyBmb3IgVGhpcyBHcmFkZSc7Cn0KCmZ1bmN0aW9uIHJlbmRlclN0dWRlbnRUYWJsZShzdHVkZW50cykgewogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdHVkZW50cy10Ym9keScpLmlubmVySFRNTCA9IHN0dWRlbnRzLm1hcCgocywgaSkgPT4gYAogICAgPHRyPgogICAgICA8dGQgc3R5bGU9ImNvbG9yOiM5NGEzYjg7Zm9udC1zaXplOjEycHg7d2lkdGg6MzBweCI+JHtpKzF9PC90ZD4KICAgICAgPHRkIHN0eWxlPSJmb250LXdlaWdodDo2MDA7Zm9udC1zaXplOjEzcHgiPiR7cy5uYW1lfTwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0iY29sb3I6Izk0YTNiODtmb250LXNpemU6MTFweCI+JHtzLmlkfTwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0id2lkdGg6ODBweCI+CiAgICAgICAgPGlucHV0IHR5cGU9Im51bWJlciIgaWQ9InNjb3JlLSR7aX0iIG1pbj0iMCIgcGxhY2Vob2xkZXI9IuKAlCIKICAgICAgICAgIG9uaW5wdXQ9ImF1dG9DYWxjKCR7aX0pIiBzdHlsZT0id2lkdGg6NzBweDt0ZXh0LWFsaWduOmNlbnRlciI+CiAgICAgIDwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0id2lkdGg6NjBweCI+PHNwYW4gaWQ9InBjdC0ke2l9IiBzdHlsZT0iZm9udC1zaXplOjEzcHg7Y29sb3I6IzY0NzQ4YiI+4oCUPC9zcGFuPjwvdGQ+CiAgICAgIDx0ZCBzdHlsZT0id2lkdGg6NTVweCI+PHNwYW4gY2xhc3M9ImdyYWRlLWJhZGdlIiBpZD0iZ2wtJHtpfSI+4oCUPC9zcGFuPjwvdGQ+CiAgICAgIDx0ZD48aW5wdXQgdHlwZT0idGV4dCIgaWQ9Im5vdGUtJHtpfSIgcGxhY2Vob2xkZXI9Im9wdGlvbmFsIiBzdHlsZT0iZm9udC1zaXplOjEycHgiPjwvdGQ+CiAgICA8L3RyPgogIGApLmpvaW4oJycpOwp9CgpmdW5jdGlvbiBhdXRvQ2FsYyhpKSB7CiAgY29uc3QgdG90YWwgPSBwYXJzZUZsb2F0KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10b3RhbCcpLnZhbHVlKSB8fCAxMDA7CiAgY29uc3Qgc2NvcmUgPSBwYXJzZUZsb2F0KGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzY29yZS0nK2kpLnZhbHVlKTsKICBpZiAoaXNOYU4oc2NvcmUpKSB7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncGN0LScraSkudGV4dENvbnRlbnQgPSAn4oCUJzsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdnbC0nK2kpLnRleHRDb250ZW50ID0gJ+KAlCc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZ2wtJytpKS5jbGFzc05hbWUgPSAnZ3JhZGUtYmFkZ2UnOwogICAgcmV0dXJuOwogIH0KICBjb25zdCBwY3QgPSBNYXRoLnJvdW5kKHNjb3JlIC8gdG90YWwgKiAxMDApOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwY3QtJytpKS50ZXh0Q29udGVudCA9IHBjdCArICclJzsKICBsZXQgZ2wgPSAnRicsIGNscyA9ICdGJzsKICBpZiAocGN0Pj05NSl7Z2w9J0ErJztjbHM9J0EnO30gZWxzZSBpZihwY3Q+PTkwKXtnbD0nQSc7Y2xzPSdBJzt9CiAgZWxzZSBpZihwY3Q+PTg1KXtnbD0nQisnO2Nscz0nQic7fSBlbHNlIGlmKHBjdD49ODApe2dsPSdCJztjbHM9J0InO30KICBlbHNlIGlmKHBjdD49NzUpe2dsPSdDKyc7Y2xzPSdDJzt9IGVsc2UgaWYocGN0Pj03MCl7Z2w9J0MnO2Nscz0nQyc7fQogIGVsc2UgaWYocGN0Pj02NSl7Z2w9J0QrJztjbHM9J0QnO30gZWxzZSBpZihwY3Q+PTYwKXtnbD0nRCc7Y2xzPSdEJzt9CiAgY29uc3QgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZ2wtJytpKTsKICBlbC50ZXh0Q29udGVudCA9IGdsOyBlbC5jbGFzc05hbWUgPSAnZ3JhZGUtYmFkZ2UgJytjbHM7Cn0KCmFzeW5jIGZ1bmN0aW9uIHN1Ym1pdEV4YW1SZXN1bHRzKCkgewogIGNvbnN0IHRlYWNoZXIgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXRlYWNoZXInKS52YWx1ZS50cmltKCk7CiAgY29uc3QgZ3JhZGUgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtZ3JhZGUnKS52YWx1ZTsKICBjb25zdCBzdWJqZWN0ICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC1zdWJqZWN0JykudmFsdWU7CiAgY29uc3QgdGVybSAgICAgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZXgtdGVybScpLnZhbHVlOwogIGNvbnN0IGV4YW1EYXRlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LWRhdGUnKS52YWx1ZTsKICBjb25zdCB0b3RhbCAgICA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdleC10b3RhbCcpLnZhbHVlIHx8ICcxMDAnOwogIGNvbnN0IHN0YXR1cyAgID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LXN0YXR1cycpOwoKICBpZiAoIXRlYWNoZXIgfHwgIWdyYWRlIHx8ICFzdWJqZWN0KSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pqg77iPIFBsZWFzZSBmaWxsIGluIFRlYWNoZXIsIEdyYWRlIGFuZCBTdWJqZWN0Lic7CiAgICByZXR1cm47CiAgfQoKICBjb25zdCByZXN1bHRzID0gbG9hZGVkU3R1ZGVudHMubWFwKChzLCBpKSA9PiAoewogICAgc3R1ZGVudF9pZDogcy5pZCwgc3R1ZGVudF9uYW1lOiBzLm5hbWUsIGdyYWRlOiBzLmdyYWRlLAogICAgc2NvcmU6IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzY29yZS0nK2kpPy52YWx1ZS50cmltKCkgfHwgJycsCiAgICB0b3RhbCwKICAgIHBlcmNlbnRhZ2U6IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwY3QtJytpKT8udGV4dENvbnRlbnQgfHwgJycsCiAgICBncmFkZV9sZXR0ZXI6IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdnbC0nK2kpPy50ZXh0Q29udGVudCB8fCAnJywKICAgIG5vdGVzOiBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbm90ZS0nK2kpPy52YWx1ZS50cmltKCkgfHwgJycKICB9KSkuZmlsdGVyKHIgPT4gci5zY29yZSAhPT0gJycpOwoKICBpZiAoIXJlc3VsdHMubGVuZ3RoKSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pqg77iPIE5vIHNjb3JlcyBlbnRlcmVkIHlldC4nOwogICAgcmV0dXJuOwogIH0KCiAgY29uc3QgYnRuID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4YW1TdWJtaXRCdG4nKTsKICBidG4uZGlzYWJsZWQgPSB0cnVlOyBidG4udGV4dENvbnRlbnQgPSAn4o+zIFNhdmluZyB0byBzaGVldOKApic7CiAgc3RhdHVzLmNsYXNzTmFtZSA9ICdzdGF0dXMnOwoKICB0cnkgewogICAgY29uc3QgcmVzID0gYXdhaXQgZmV0Y2goJy9hcGkvYWRkLWV4YW0tcmVzdWx0cycsIHsKICAgICAgbWV0aG9kOiAnUE9TVCcsIGhlYWRlcnM6IHsnQ29udGVudC1UeXBlJzogJ2FwcGxpY2F0aW9uL2pzb24nfSwKICAgICAgYm9keTogSlNPTi5zdHJpbmdpZnkoe3Jlc3VsdHMsIHN1YmplY3QsIHRlcm0sIHRlYWNoZXIsIGV4YW1fZGF0ZTogZXhhbURhdGV9KQogICAgfSk7CiAgICBjb25zdCBkYXRhID0gYXdhaXQgcmVzLmpzb24oKTsKICAgIGlmIChkYXRhLm9rKSB7CiAgICAgIHN0YXR1cy5jbGFzc05hbWUgPSAnc3RhdHVzIHN1Y2Nlc3MnOwogICAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4pyFICcgKyBkYXRhLm1lc3NhZ2U7CiAgICAgIGxvY2FsU3RvcmFnZS5zZXRJdGVtKCdod190ZWFjaGVyJywgdGVhY2hlcik7CiAgICAgIGV4SGlzdG9yeS51bnNoaWZ0KHtncmFkZSwgc3ViamVjdCwgdGVybSwgY291bnQ6IGRhdGEuc2F2ZWQsIHRlYWNoZXJ9KTsKICAgICAgcmVuZGVyRXhIaXN0b3J5KCk7CiAgICB9IGVsc2UgewogICAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICfinYwgJyArIGRhdGEuZXJyb3I7CiAgICB9CiAgfSBjYXRjaChlKSB7CiAgICBzdGF0dXMuY2xhc3NOYW1lID0gJ3N0YXR1cyBlcnJvcic7CiAgICBzdGF0dXMudGV4dENvbnRlbnQgPSAn4p2MIE5ldHdvcmsgZXJyb3I6ICcgKyBlLm1lc3NhZ2U7CiAgfQogIGJ0bi5kaXNhYmxlZCA9IGZhbHNlOyBidG4udGV4dENvbnRlbnQgPSAn8J+SviBTQVZFIEFMTCBSRVNVTFRTIFRPIFNIRUVUJzsKfQoKZnVuY3Rpb24gcmVuZGVyRXhIaXN0b3J5KCkgewogIGNvbnN0IGVsID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2V4LWhpc3RvcnknKTsKICBpZiAoIWV4SGlzdG9yeS5sZW5ndGgpIHsgZWwuaW5uZXJIVE1MID0gJzxkaXYgY2xhc3M9Im5vLWhpc3QiPk5vdGhpbmcgc2F2ZWQgeWV0LjwvZGl2Pic7IHJldHVybjsgfQogIGVsLmlubmVySFRNTCA9IGV4SGlzdG9yeS5zbGljZSgwLDUpLm1hcChoID0+CiAgICBgPGRpdiBjbGFzcz0iaGlzdC1pdGVtIj48c3Ryb25nPiR7aC5ncmFkZX0g4oCUICR7aC5zdWJqZWN0fTwvc3Ryb25nPiAoJHtoLnRlcm19KQogICAgIDxkaXYgY2xhc3M9Im1ldGEiPuKchSAke2guY291bnR9IHJlc3VsdHMgc2F2ZWQgwrcg8J+RpCAke2gudGVhY2hlcn08L2Rpdj48L2Rpdj5gCiAgKS5qb2luKCcnKTsKfQoKLy8gQ3RybCtFbnRlciB0byBzdWJtaXQgYWN0aXZlIHRhYgpkb2N1bWVudC5hZGRFdmVudExpc3RlbmVyKCdrZXlkb3duJywgZSA9PiB7CiAgaWYgKChlLmN0cmxLZXl8fGUubWV0YUtleSkgJiYgZS5rZXk9PT0nRW50ZXInKSB7CiAgICBpZiAoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3RhYi1ob21ld29yaycpLmNsYXNzTGlzdC5jb250YWlucygnYWN0aXZlJykpIHN1Ym1pdEhvbWV3b3JrKCk7CiAgICBlbHNlIHN1Ym1pdEV4YW1SZXN1bHRzKCk7CiAgfQp9KTsKPC9zY3JpcHQ+CjxzY3JpcHQ+CnZhciBGSU5BTkNFX1VTRVJTID0geyJmaW5hbmNlIjogImZpbmFuY2UyMDI2IiwgImZpbmFuY2VfYWRtaW4iOiAibW9kZXJuaW5maW5pdHkyMDI2In07CnZhciBhbGxTdHVkZW50cyA9IFtdOwp2YXIgY2hhbmdlcyA9IHt9OwoKZnVuY3Rpb24gZG9Mb2dpbigpewogIHZhciB1ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2Z1JykudmFsdWUudHJpbSgpOwogIHZhciBwID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2ZwJykudmFsdWUudHJpbSgpOwogIHZhciBlcnIgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZmUnKTsKICBpZighdXx8IXApe2Vyci50ZXh0Q29udGVudD0n4pqg77iPIEVudGVyIHVzZXJuYW1lIGFuZCBwYXNzd29yZCc7ZXJyLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJztyZXR1cm47fQogIGlmKEZJTkFOQ0VfVVNFUlNbdV0gJiYgRklOQU5DRV9VU0VSU1t1XT09PXApewogICAgZXJyLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2xvZ2luLXNjcmVlbicpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBsb2FkU3R1ZGVudHMoKTsKICB9IGVsc2UgewogICAgZXJyLnRleHRDb250ZW50PSfinYwgSW5jb3JyZWN0IHVzZXJuYW1lIG9yIHBhc3N3b3JkJzsKICAgIGVyci5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZnAnKS52YWx1ZT0nJzsKICB9Cn0KCmZ1bmN0aW9uIGRvTG9nb3V0KCl7CiAgYWxsU3R1ZGVudHM9W107IGNoYW5nZXM9e307CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tcGFuZWwnKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbG9naW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheT0nZmxleCc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2Z1JykudmFsdWU9Jyc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2ZwJykudmFsdWU9Jyc7Cn0KCmFzeW5jIGZ1bmN0aW9uIGxvYWRTdHVkZW50cygpewogIHZhciBidG4gPSBkb2N1bWVudC5xdWVyeVNlbGVjdG9yKCcubG9hZC1idG4nKTsKICBidG4udGV4dENvbnRlbnQ9J+KPsyBMb2FkaW5nLi4uJzsgYnRuLmRpc2FibGVkPXRydWU7CiAgY2hhbmdlcz17fTsKICB1cGRhdGVTYXZlQmFyKCk7CiAgdHJ5ewogICAgdmFyIGdyYWRlID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2dyYWRlRmlsdGVyJykudmFsdWU7CiAgICB2YXIgdXJsID0gJy9hcGkvZmluYW5jZS9zdHVkZW50cycgKyAoZ3JhZGUgPyAnP2dyYWRlPScrZW5jb2RlVVJJQ29tcG9uZW50KGdyYWRlKSA6ICcnKTsKICAgIHZhciByZXMgPSBhd2FpdCBmZXRjaCh1cmwpOwogICAgdmFyIGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgYWxsU3R1ZGVudHMgPSBkYXRhLnN0dWRlbnRzIHx8IFtdOwogICAgcmVuZGVyVGFibGUoYWxsU3R1ZGVudHMpOwogICAgdXBkYXRlU3RhdHMoYWxsU3R1ZGVudHMpOwogICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXR1c01zZycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIH0gY2F0Y2goZSl7CiAgICBzaG93U3RhdHVzKCfinYwgRXJyb3IgbG9hZGluZyBzdHVkZW50czogJytlLm1lc3NhZ2UsICdlcnJvcicpOwogIH0KICBidG4udGV4dENvbnRlbnQ9J/CflIQgTG9hZCBTdHVkZW50cyc7IGJ0bi5kaXNhYmxlZD1mYWxzZTsKfQoKZnVuY3Rpb24gcmVuZGVyVGFibGUoc3R1ZGVudHMpewogIHZhciBzdGF0dXMgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdHVzRmlsdGVyJykudmFsdWU7CiAgdmFyIHNlYXJjaCA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzZWFyY2hJbnB1dCcpLnZhbHVlLnRvTG93ZXJDYXNlKCk7CiAgdmFyIGZpbHRlcmVkID0gc3R1ZGVudHMuZmlsdGVyKGZ1bmN0aW9uKHMpewogICAgdmFyIG1hdGNoU3RhdHVzID0gIXN0YXR1cyB8fAogICAgICAoc3RhdHVzPT09J3BhaWQnICYmIChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKT09PSdwYWlkJykgfHwKICAgICAgKHN0YXR1cz09PSd1bnBhaWQnICYmIChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKSE9PSdwYWlkJyk7CiAgICB2YXIgbWF0Y2hTZWFyY2ggPSAhc2VhcmNoIHx8CiAgICAgIChzLm5hbWV8fCcnKS50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKHNlYXJjaCkgfHwKICAgICAgKHMuaWR8fCcnKS50b0xvd2VyQ2FzZSgpLmluY2x1ZGVzKHNlYXJjaCk7CiAgICByZXR1cm4gbWF0Y2hTdGF0dXMgJiYgbWF0Y2hTZWFyY2g7CiAgfSk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0dWRlbnRDb3VudCcpLnRleHRDb250ZW50ID0gZmlsdGVyZWQubGVuZ3RoICsgJyBzdHVkZW50cyBzaG93bic7CiAgaWYoIWZpbHRlcmVkLmxlbmd0aCl7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGFibGVDb250YWluZXInKS5pbm5lckhUTUw9JzxkaXYgY2xhc3M9ImVtcHR5Ij5ObyBzdHVkZW50cyBtYXRjaCB5b3VyIGZpbHRlcnM8L2Rpdj4nOwogICAgcmV0dXJuOwogIH0KICB2YXIgcm93cyA9IGZpbHRlcmVkLm1hcChmdW5jdGlvbihzLCBpKXsKICAgIHZhciBpc1BhaWQgPSAoY2hhbmdlc1tzLnJvd19pbmRleF0gIT09IHVuZGVmaW5lZCkKICAgICAgPyBjaGFuZ2VzW3Mucm93X2luZGV4XT09PSdwYWlkJwogICAgICA6IChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKT09PSdwYWlkJzsKICAgIHZhciBsYWJlbCA9IGlzUGFpZAogICAgICA/ICc8c3BhbiBjbGFzcz0idG9nZ2xlLWxhYmVsIHBhaWQiPuKchSBQYWlkPC9zcGFuPicKICAgICAgOiAnPHNwYW4gY2xhc3M9InRvZ2dsZS1sYWJlbCB1bnBhaWQiPuKdjCBOb3QgUGFpZDwvc3Bhbj4nOwogICAgcmV0dXJuICc8dHI+JysKICAgICAgJzx0ZCBzdHlsZT0iZm9udC13ZWlnaHQ6NjAwIj4nK3MubmFtZSsnPC90ZD4nKwogICAgICAnPHRkIHN0eWxlPSJjb2xvcjojNjQ3NDhiO2ZvbnQtc2l6ZToxMnB4Ij4nK3MuaWQrJzwvdGQ+JysKICAgICAgJzx0ZD48c3BhbiBjbGFzcz0iZ3JhZGUtYmFkZ2UiPicrcy5ncmFkZSsnPC9zcGFuPjwvdGQ+JysKICAgICAgJzx0ZD4nKwogICAgICAgICc8ZGl2IGNsYXNzPSJ0b2dnbGUtd3JhcCI+JysKICAgICAgICAgICc8bGFiZWwgY2xhc3M9InRvZ2dsZSI+JysKICAgICAgICAgICAgJzxpbnB1dCB0eXBlPSJjaGVja2JveCIgJysoaXNQYWlkPydjaGVja2VkJzonJykrJyBvbmNoYW5nZT0idG9nZ2xlUGF5bWVudCh0aGlzLCcrcy5yb3dfaW5kZXgrJykiPicrCiAgICAgICAgICAgICc8c3BhbiBjbGFzcz0ic2xpZGVyIj48L3NwYW4+JysKICAgICAgICAgICc8L2xhYmVsPicrCiAgICAgICAgICAnPHNwYW4gaWQ9ImxibC0nK3Mucm93X2luZGV4KyciPicrbGFiZWwrJzwvc3Bhbj4nKwogICAgICAgICc8L2Rpdj4nKwogICAgICAnPC90ZD4nKwogICAgJzwvdHI+JzsKICB9KS5qb2luKCcnKTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGFibGVDb250YWluZXInKS5pbm5lckhUTUwgPQogICAgJzx0YWJsZT48dGhlYWQ+PHRyPjx0aD5TdHVkZW50IE5hbWU8L3RoPjx0aD5JRDwvdGg+PHRoPkdyYWRlPC90aD48dGg+UGF5bWVudCBTdGF0dXM8L3RoPjwvdHI+PC90aGVhZD4nKwogICAgJzx0Ym9keT4nK3Jvd3MrJzwvdGJvZHk+PC90YWJsZT4nOwp9CgpmdW5jdGlvbiBmaWx0ZXJUYWJsZSgpeyByZW5kZXJUYWJsZShhbGxTdHVkZW50cyk7IH0KCmZ1bmN0aW9uIHRvZ2dsZVBheW1lbnQoY2hlY2tib3gsIHJvd0luZGV4KXsKICB2YXIgaXNQYWlkID0gY2hlY2tib3guY2hlY2tlZDsKICBjaGFuZ2VzW3Jvd0luZGV4XSA9IGlzUGFpZCA/ICdwYWlkJyA6ICd1bnBhaWQnOwogIHZhciBsYmwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnbGJsLScrcm93SW5kZXgpOwogIGlmKGxibCkgbGJsLmlubmVySFRNTCA9IGlzUGFpZAogICAgPyAnPHNwYW4gY2xhc3M9InRvZ2dsZS1sYWJlbCBwYWlkIj7inIUgUGFpZDwvc3Bhbj4nCiAgICA6ICc8c3BhbiBjbGFzcz0idG9nZ2xlLWxhYmVsIHVucGFpZCI+4p2MIE5vdCBQYWlkPC9zcGFuPic7CiAgdXBkYXRlU2F2ZUJhcigpOwogIHVwZGF0ZVN0YXRzKGFsbFN0dWRlbnRzKTsKfQoKZnVuY3Rpb24gdXBkYXRlU2F2ZUJhcigpewogIHZhciBjb3VudCA9IE9iamVjdC5rZXlzKGNoYW5nZXMpLmxlbmd0aDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnY2hhbmdlQ291bnQnKS50ZXh0Q29udGVudCA9IGNvdW50OwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYXZlQmFyJykuY2xhc3NOYW1lID0gY291bnQgPiAwID8gJ3NhdmUtYmFyIHNob3cnIDogJ3NhdmUtYmFyJzsKfQoKZnVuY3Rpb24gdXBkYXRlU3RhdHMoc3R1ZGVudHMpewogIHZhciB0b3RhbCA9IHN0dWRlbnRzLmxlbmd0aDsKICB2YXIgcGFpZCA9IHN0dWRlbnRzLmZpbHRlcihmdW5jdGlvbihzKXsKICAgIHZhciBzdGF0dXMgPSAoY2hhbmdlc1tzLnJvd19pbmRleF0gIT09IHVuZGVmaW5lZCkgPyBjaGFuZ2VzW3Mucm93X2luZGV4XSA6IChzLnBheW1lbnRfc3RhdHVzfHwnJykudG9Mb3dlckNhc2UoKTsKICAgIHJldHVybiBzdGF0dXMgPT09ICdwYWlkJzsKICB9KS5sZW5ndGg7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXRUb3RhbCcpLnRleHRDb250ZW50ID0gdG90YWw7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3N0YXRQYWlkJykudGV4dENvbnRlbnQgPSBwYWlkOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzdGF0VW5wYWlkJykudGV4dENvbnRlbnQgPSB0b3RhbCAtIHBhaWQ7Cn0KCmFzeW5jIGZ1bmN0aW9uIHNhdmVDaGFuZ2VzKCl7CiAgdmFyIGJ0biA9IGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdzYXZlQnRuJyk7CiAgYnRuLmRpc2FibGVkPXRydWU7IGJ0bi50ZXh0Q29udGVudD0n4o+zIFNhdmluZy4uLic7CiAgdHJ5ewogICAgdmFyIHJlcyA9IGF3YWl0IGZldGNoKCcvYXBpL2ZpbmFuY2UvdXBkYXRlLXBheW1lbnQnLCB7CiAgICAgIG1ldGhvZDonUE9TVCcsCiAgICAgIGhlYWRlcnM6eydDb250ZW50LVR5cGUnOidhcHBsaWNhdGlvbi9qc29uJ30sCiAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KHtjaGFuZ2VzOiBjaGFuZ2VzfSkKICAgIH0pOwogICAgdmFyIGRhdGEgPSBhd2FpdCByZXMuanNvbigpOwogICAgaWYoZGF0YS5vayl7CiAgICAgIHNob3dTdGF0dXMoJ+KchSBTYXZlZCBzdWNjZXNzZnVsbHkg4oCUICcrZGF0YS51cGRhdGVkKycgc3R1ZGVudHMgdXBkYXRlZCcsICdzdWNjZXNzJyk7CiAgICAgIC8vIFVwZGF0ZSBsb2NhbCBzdHVkZW50IGRhdGEKICAgICAgT2JqZWN0LmtleXMoY2hhbmdlcykuZm9yRWFjaChmdW5jdGlvbihyb3dJbmRleCl7CiAgICAgICAgdmFyIHMgPSBhbGxTdHVkZW50cy5maW5kKGZ1bmN0aW9uKHgpeyByZXR1cm4geC5yb3dfaW5kZXggPT0gcm93SW5kZXg7IH0pOwogICAgICAgIGlmKHMpIHMucGF5bWVudF9zdGF0dXMgPSBjaGFuZ2VzW3Jvd0luZGV4XTsKICAgICAgfSk7CiAgICAgIGNoYW5nZXMgPSB7fTsKICAgICAgdXBkYXRlU2F2ZUJhcigpOwogICAgICB1cGRhdGVTdGF0cyhhbGxTdHVkZW50cyk7CiAgICAgIHJlbmRlclRhYmxlKGFsbFN0dWRlbnRzKTsKICAgIH0gZWxzZSB7CiAgICAgIHNob3dTdGF0dXMoJ+KdjCBFcnJvcjogJytkYXRhLmVycm9yLCAnZXJyb3InKTsKICAgIH0KICB9IGNhdGNoKGUpewogICAgc2hvd1N0YXR1cygn4p2MIE5ldHdvcmsgZXJyb3I6ICcrZS5tZXNzYWdlLCAnZXJyb3InKTsKICB9CiAgYnRuLmRpc2FibGVkPWZhbHNlOyBidG4udGV4dENvbnRlbnQ9J/Cfkr4gU2F2ZSBBbGwgQ2hhbmdlcyc7Cn0KCmZ1bmN0aW9uIHNob3dTdGF0dXMobXNnLCB0eXBlKXsKICB2YXIgZWwgPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnc3RhdHVzTXNnJyk7CiAgZWwudGV4dENvbnRlbnQgPSBtc2c7CiAgZWwuY2xhc3NOYW1lID0gJ3N0YXR1cy1tc2cgJyt0eXBlOwogIHNldFRpbWVvdXQoZnVuY3Rpb24oKXsgZWwuc3R5bGUuZGlzcGxheT0nbm9uZSc7IH0sIDUwMDApOwp9Cjwvc2NyaXB0Pgo8c2NyaXB0Pgp2YXIgRklOQU5DRV9VU0VSUyA9IHsiZmluYW5jZSI6ImZpbmFuY2UyMDI2IiwiZmluYW5jZV9hZG1pbiI6Im1vZGVybmluZmluaXR5MjAyNiJ9Owphc3luYyBmdW5jdGlvbiBwTG9naW4oKXsKICB2YXIgdT1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncHUnKS52YWx1ZS50cmltKCk7CiAgdmFyIHA9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3BwJykudmFsdWUudHJpbSgpOwogIHZhciBlcnI9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3BlcnInKTsKICB2YXIgYnRuPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwc2lnbmluJyk7CiAgZXJyLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogIGlmKCF1fHwhcCl7ZXJyLnRleHRDb250ZW50PSdQbGVhc2UgZW50ZXIgdXNlcm5hbWUgYW5kIHBhc3N3b3JkJztlcnIuc3R5bGUuZGlzcGxheT0nYmxvY2snO3JldHVybjt9CiAgaWYoRklOQU5DRV9VU0VSU1t1XSYmRklOQU5DRV9VU0VSU1t1XT09PXApewogICAgb3BlblBhbmUoJ2ZpbmFuY2UnKTsKICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdmdScpLnZhbHVlPXU7CiAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnZnAnKS52YWx1ZT1wOwogICAgZG9Mb2dpbigpOwogICAgcmV0dXJuOwogIH0KICBidG4uZGlzYWJsZWQ9dHJ1ZTtidG4udGV4dENvbnRlbnQ9J1NpZ25pbmcgaW4uLi4nOwogIHRyeXsKICAgIHZhciByPWF3YWl0IGZldGNoKCcvYXBpL2xvZ2luJyx7bWV0aG9kOidQT1NUJyxoZWFkZXJzOnsnQ29udGVudC1UeXBlJzonYXBwbGljYXRpb24vanNvbid9LGJvZHk6SlNPTi5zdHJpbmdpZnkoe3VzZXJuYW1lOnUscGFzc3dvcmQ6cH0pfSk7CiAgICB2YXIgZD1hd2FpdCByLmpzb24oKTsKICAgIGlmKGQub2spewogICAgICBvcGVuUGFuZSgnYWRtaW4nKTsKICAgICAgQ1VSUkVOVF9VU0VSPWQ7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsdycpLnN0eWxlLmRpc3BsYXk9J25vbmUnOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYXcnKS5zdHlsZS5kaXNwbGF5PSdmbGV4JzsKICAgICAgc2V0dXBBZG1pblVJKCk7bG9hZFN0YXRzKCk7bG9hZFBhcmVudHMoKTsKICAgICAgYnRuLmRpc2FibGVkPWZhbHNlO2J0bi50ZXh0Q29udGVudD0nU2lnbiBJbic7CiAgICAgIHJldHVybjsKICAgIH0KICB9Y2F0Y2goZSl7fQogIHRyeXsKICAgIHZhciByMj1hd2FpdCBmZXRjaCgnL2FwaS90ZWFjaGVyLWxvZ2luJyx7bWV0aG9kOidQT1NUJyxoZWFkZXJzOnsnQ29udGVudC1UeXBlJzonYXBwbGljYXRpb24vanNvbid9LGJvZHk6SlNPTi5zdHJpbmdpZnkoe3VzZXJuYW1lOnUscGFzc3dvcmQ6cH0pfSk7CiAgICB2YXIgZDI9YXdhaXQgcjIuanNvbigpOwogICAgaWYoZDIub2spewogICAgICBvcGVuUGFuZSgnaHcnKTsKICAgICAgQ1VSUkVOVF9URUFDSEVSPXU7CiAgICAgIENVUlJFTlRfRlVMTF9OQU1FPWQyLm5hbWV8fGQyLmZ1bGxfbmFtZXx8dTsKICAgICAgc2Vzc2lvblN0b3JhZ2Uuc2V0SXRlbSgndGVhY2hlcl91c2VyJyx1KTsKICAgICAgc2Vzc2lvblN0b3JhZ2Uuc2V0SXRlbSgndGVhY2hlcl9uYW1lJyxDVVJSRU5UX0ZVTExfTkFNRSk7CiAgICAgIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdsb2dpbi1zY3JlZW4nKS5zdHlsZS5kaXNwbGF5PSdub25lJzsKICAgICAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ21haW4tc2NyZWVuJykuc3R5bGUuZGlzcGxheT0nYmxvY2snOwogICAgICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgndGVhY2hlci1uYW1lLWRpc3BsYXknKS50ZXh0Q29udGVudD1DVVJSRU5UX0ZVTExfTkFNRTsKICAgICAgc3dpdGNoVGFiKCdob21ld29yaycsZG9jdW1lbnQucXVlcnlTZWxlY3RvcignLnRhYi1idG4nKSk7CiAgICAgIGJ0bi5kaXNhYmxlZD1mYWxzZTtidG4udGV4dENvbnRlbnQ9J1NpZ24gSW4nOwogICAgICByZXR1cm47CiAgICB9CiAgfWNhdGNoKGUpe30KICBlcnIudGV4dENvbnRlbnQ9J0luY29ycmVjdCB1c2VybmFtZSBvciBwYXNzd29yZCc7CiAgZXJyLnN0eWxlLmRpc3BsYXk9J2Jsb2NrJzsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncHAnKS52YWx1ZT0nJzsKICBidG4uZGlzYWJsZWQ9ZmFsc2U7YnRuLnRleHRDb250ZW50PSdTaWduIEluJzsKfQpmdW5jdGlvbiBvcGVuUGFuZShuYW1lKXsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgncG9ydGFsLWxvZ2luJykuc3R5bGUuZGlzcGxheT0nbm9uZSc7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3BhbmVsLWhvc3QnKS5zdHlsZS5kaXNwbGF5PSdibG9jayc7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvckFsbCgnLnBhbmUnKS5mb3JFYWNoKGZ1bmN0aW9uKGVsKXtlbC5jbGFzc0xpc3QucmVtb3ZlKCdvbicpO30pOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdwYW5lLScrbmFtZSkuY2xhc3NMaXN0LmFkZCgnb24nKTsKfQo8L3NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+'
    ).decode('utf-8')
    return h, 200, [('Content-Type', 'text/html; charset=utf-8')]


@app.route('/portal/')
def unified_portal():
    """Unified school portal — Announcements + Teacher Panel + Finance."""
    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Modern Infinity School — Staff Portal</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;min-height:100vh}

/* ── LOGIN ── */
#login-screen{display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#0F1C2E 0%,#1a3a2a 100%)}
.login-box{background:#fff;border-radius:16px;padding:40px 36px;width:100%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.login-logo{text-align:center;margin-bottom:28px}
.login-logo .school-icon{font-size:48px}
.login-logo h1{font-size:22px;font-weight:700;color:#0F1C2E;margin-top:10px}
.login-logo p{font-size:13px;color:#64748b;margin-top:4px}
.field{margin-bottom:16px}
.field label{display:block;font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.field input,.field select{width:100%;padding:13px 14px;border:2px solid #e2e8f0;border-radius:9px;font-size:15px;outline:none;transition:border-color .2s;background:#f8fafc}
.field input:focus,.field select:focus{border-color:#00C8C8;background:#fff}
.login-btn{width:100%;padding:14px;background:linear-gradient(135deg,#0F1C2E,#1a3a5c);color:#fff;border:none;border-radius:9px;font-size:15px;font-weight:700;cursor:pointer;transition:opacity .2s}
.login-btn:hover{opacity:.9}
.login-error{background:#fee2e2;color:#dc2626;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:600;margin-top:14px;text-align:center;display:none}

/* ── MAIN LAYOUT ── */
#main-panel{display:none;min-height:100vh}
.layout{display:flex;min-height:100vh}

/* ── SIDEBAR ── */
.sidebar{width:240px;background:#0F1C2E;display:flex;flex-direction:column;flex-shrink:0}
.sidebar-logo{padding:24px 20px 16px;border-bottom:1px solid rgba(255,255,255,.08)}
.sidebar-logo .logo-icon{font-size:28px}
.sidebar-logo h2{color:#fff;font-size:14px;font-weight:700;margin-top:6px;line-height:1.3}
.sidebar-logo p{color:rgba(255,255,255,.4);font-size:11px;margin-top:2px}
.sidebar-user{padding:14px 20px;border-bottom:1px solid rgba(255,255,255,.08)}
.sidebar-user .user-name{color:#fff;font-size:13px;font-weight:600}
.sidebar-user .user-role{color:#00C8C8;font-size:11px;margin-top:2px}
.nav-section{padding:16px 12px 8px;flex:1}
.nav-label{color:rgba(255,255,255,.3);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;padding:0 8px;margin-bottom:6px}
.nav-item{display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:8px;cursor:pointer;margin-bottom:2px;transition:background .15s;color:rgba(255,255,255,.6);font-size:13px;font-weight:500;border:none;background:none;width:100%;text-align:left}
.nav-item:hover{background:rgba(255,255,255,.07);color:#fff}
.nav-item.active{background:rgba(0,200,200,.15);color:#00C8C8;font-weight:700}
.nav-item .nav-icon{font-size:16px;flex-shrink:0}
.nav-item .nav-badge{margin-left:auto;background:rgba(0,200,200,.2);color:#00C8C8;border-radius:10px;padding:2px 7px;font-size:10px;font-weight:700}
.sidebar-footer{padding:16px;border-top:1px solid rgba(255,255,255,.08)}
.logout-btn{width:100%;padding:10px;background:rgba(255,255,255,.06);color:rgba(255,255,255,.5);border:1px solid rgba(255,255,255,.1);border-radius:8px;font-size:12px;cursor:pointer;transition:all .15s}
.logout-btn:hover{background:rgba(255,59,48,.15);color:#ff6b6b;border-color:rgba(255,59,48,.2)}

/* ── CONTENT ── */
.content{flex:1;overflow-y:auto}
.panel{display:none;height:100%}
.panel.active{display:block}
.panel-header{background:#fff;border-bottom:1px solid #e2e8f0;padding:20px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.panel-title{font-size:18px;font-weight:700;color:#0F1C2E}
.panel-subtitle{font-size:12px;color:#64748b;margin-top:2px}
.panel-body{padding:24px 28px}

/* ── CARDS & STATS ── */
.stats-row{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
.stat-card{background:#fff;border-radius:12px;padding:20px;border:1px solid #e2e8f0}
.stat-label{font-size:12px;color:#64748b;font-weight:500;margin-bottom:4px}
.stat-value{font-size:26px;font-weight:700;color:#0F1C2E}
.stat-value.green{color:#16a34a}
.stat-value.red{color:#dc2626}
.stat-value.blue{color:#2563eb}

/* ── BUTTONS ── */
.btn{display:inline-flex;align-items:center;gap:6px;padding:10px 20px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .15s}
.btn-primary{background:#0F1C2E;color:#fff}
.btn-primary:hover{background:#1a3a5c}
.btn-teal{background:#00C8C8;color:#0F1C2E}
.btn-teal:hover{background:#00b0b0}
.btn-green{background:#16a34a;color:#fff}
.btn-green:hover{background:#15803d}
.btn-green:disabled{background:#86efac;cursor:not-allowed}
.btn-outline{background:#fff;color:#0F1C2E;border:1.5px solid #e2e8f0}
.btn-outline:hover{border-color:#0F1C2E}
.btn-red{background:#dc2626;color:#fff}
.btn-sm{padding:7px 14px;font-size:12px}

/* ── FORM ── */
.form-group{margin-bottom:16px}
.form-label{display:block;font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.form-input,.form-select,.form-textarea{width:100%;padding:11px 13px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none;background:#f8fafc;transition:border-color .15s;font-family:inherit}
.form-input:focus,.form-select:focus,.form-textarea:focus{border-color:#00C8C8;background:#fff}
.form-textarea{resize:vertical;min-height:100px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.char-counter{font-size:11px;color:#94a3b8;text-align:right;margin-top:4px}

/* ── TABLE ── */
.table-card{background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;margin-bottom:20px}
.table-card-header{padding:16px 20px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between}
.table-card-title{font-size:14px;font-weight:700;color:#0F1C2E}
.table-count{font-size:12px;color:#64748b}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:11px 16px;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;background:#f8fafc;border-bottom:1px solid #e2e8f0}
td{padding:11px 16px;border-bottom:1px solid #f1f5f9;font-size:13px;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafbfc}

/* ── TOGGLE ── */
.toggle{position:relative;width:48px;height:26px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#e2e8f0;border-radius:26px;transition:.25s}
.slider:before{content:'';position:absolute;height:18px;width:18px;left:4px;bottom:4px;background:#fff;border-radius:50%;transition:.25s;box-shadow:0 1px 3px rgba(0,0,0,.2)}
input:checked+.slider{background:#16a34a}
input:checked+.slider:before{transform:translateX(22px)}
.toggle-label{font-size:12px;font-weight:600}
.toggle-label.paid{color:#16a34a}
.toggle-label.unpaid{color:#dc2626}

/* ── ANNOUNCEMENT PREVIEW ── */
.preview-box{background:#075E54;border-radius:12px;padding:16px 20px;margin-top:12px}
.preview-header{color:rgba(255,255,255,.6);font-size:11px;margin-bottom:8px}
.preview-bubble{background:#202C33;border-radius:8px;padding:12px 14px;color:#e9edef;font-size:13px;line-height:1.5;white-space:pre-wrap;max-height:150px;overflow-y:auto}
.preview-meta{color:rgba(255,255,255,.4);font-size:10px;margin-top:6px;text-align:right}

/* ── SAVE BAR ── */
.save-bar{background:#fff;border-radius:12px;padding:14px 20px;margin-bottom:20px;border:1.5px solid #fbbf24;display:flex;align-items:center;justify-content:space-between;display:none}
.save-bar.show{display:flex}
.save-bar-info{font-size:13px;color:#374151}
.save-bar-info span{font-weight:700;color:#d97706}

/* ── ALERTS ── */
.alert{border-radius:8px;padding:12px 16px;font-size:13px;font-weight:600;margin-bottom:16px;display:none}
.alert.show{display:block}
.alert-success{background:#dcfce7;color:#15803d}
.alert-error{background:#fee2e2;color:#dc2626}
.alert-info{background:#dbeafe;color:#1d4ed8}

/* ── BADGE ── */
.badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.badge-green{background:#dcfce7;color:#16a34a}
.badge-red{background:#fee2e2;color:#dc2626}
.badge-blue{background:#dbeafe;color:#2563eb}
.badge-grey{background:#f1f5f9;color:#64748b}

/* ── FILTERS ── */
.filters{background:#fff;border-radius:12px;padding:16px 20px;margin-bottom:20px;border:1px solid #e2e8f0;display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.filter-group{display:flex;flex-direction:column;gap:5px}
.filter-group label{font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em}
.filter-group select,.filter-group input{padding:9px 12px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:13px;outline:none;background:#f8fafc}
.filter-group select:focus,.filter-group input:focus{border-color:#00C8C8}

/* ── RESPONSIVE ── */
@media(max-width:768px){
  .sidebar{width:64px}
  .sidebar-logo h2,.sidebar-logo p,.sidebar-user,.nav-label,.nav-item span:not(.nav-icon),.nav-badge,.logout-btn span{display:none}
  .nav-item{justify-content:center;padding:12px}
  .stats-row{grid-template-columns:1fr}
  .form-row{grid-template-columns:1fr}
  .panel-body{padding:16px}
}
</style>
</head>
<body>

<!-- LOGIN SCREEN -->
<div id="login-screen">
  <div class="login-box">
    <div class="login-logo">
      <div class="school-icon">🏫</div>
      <h1>Modern Infinity School</h1>
      <p>Staff Portal — Powered by Smarvex</p>
    </div>
    <div class="field">
      <label>Username</label>
      <input type="text" id="lu" placeholder="Enter your username" autocomplete="off">
    </div>
    <div class="field">
      <label>Password</label>
      <input type="password" id="lp" placeholder="Enter your password" onkeydown="if(event.key==='Enter')doLogin()">
    </div>
    <button class="login-btn" onclick="doLogin()">Sign In →</button>
    <div class="login-error" id="lerr">❌ Incorrect username or password</div>
  </div>
</div>

<!-- MAIN PANEL -->
<div id="main-panel">
  <div class="layout">

    <!-- SIDEBAR -->
    <div class="sidebar">
      <div class="sidebar-logo">
        <div class="logo-icon">🏫</div>
        <h2>Modern Infinity School</h2>
        <p>Staff Portal</p>
      </div>
      <div class="sidebar-user">
        <div class="user-name" id="sidebarUserName">—</div>
        <div class="user-role" id="sidebarUserRole">—</div>
      </div>
      <div class="nav-section">
        <div class="nav-label">Main Menu</div>
        <button class="nav-item active" id="nav-announce" onclick="showPanel('announce')" style="display:none">
          <span class="nav-icon">📣</span>
          <span>Announcements</span>
        </button>
        <button class="nav-item" id="nav-teacher" onclick="showPanel('teacher')" style="display:none">
          <span class="nav-icon">👩‍🏫</span>
          <span>Teacher Panel</span>
        </button>
        <button class="nav-item" id="nav-finance" onclick="showPanel('finance')" style="display:none">
          <span class="nav-icon">💳</span>
          <span>Finance Panel</span>
        </button>
      </div>
      <div class="sidebar-footer">
        <button class="logout-btn" onclick="doLogout()">🚪 <span>Sign out</span></button>
      </div>
    </div>

    <!-- CONTENT AREA -->
    <div class="content">

      <!-- ════════════════════════════════════════════════
           ANNOUNCEMENTS PANEL
      ════════════════════════════════════════════════ -->
      <div class="panel active" id="panel-announce">
        <div class="panel-header">
          <div>
            <div class="panel-title">📣 Announcements</div>
            <div class="panel-subtitle">Send broadcasts to parents via WhatsApp</div>
          </div>
          <div id="ann-status-top"></div>
        </div>
        <div class="panel-body">

          <div id="ann-alert" class="alert"></div>

          <!-- Compose -->
          <div class="table-card" style="margin-bottom:20px">
            <div class="table-card-header">
              <div class="table-card-title">✍️ New Announcement</div>
            </div>
            <div style="padding:20px">
              <div class="form-row">
                <div class="form-group">
                  <label class="form-label">Audience</label>
                  <select class="form-select" id="ann-audience">
                    <option value="all" id="ann-opt-all">📢 All Parents</option>
                    <option value="grade">📚 Specific Grade</option>
                  </select>
                </div>
                <div class="form-group" id="ann-grade-wrap" style="display:none">
                  <label class="form-label">Grade</label>
                  <select class="form-select" id="ann-grade">
                    <option>KG1</option><option>KG2</option>
                    <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
                    <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
                    <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
                    <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
                  </select>
                </div>
                <div class="form-group" id="ann-grade-restricted-wrap" style="display:none">
                  <label class="form-label">Grade</label>
                  <select class="form-select" id="ann-grade-restricted"></select>
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">Message</label>
                <textarea class="form-textarea" id="ann-msg" placeholder="Type your announcement here..." oninput="updatePreview()" rows="4"></textarea>
                <div class="char-counter"><span id="ann-chars">0</span> characters</div>
              </div>
              <div class="form-group">
                <label class="form-label">📎 Image (optional)</label>
                <input type="file" accept="image/*" id="ann-image" style="padding:8px;font-size:13px;border:1.5px dashed #e2e8f0;border-radius:8px;width:100%;background:#f8fafc">
              </div>
              <div class="preview-box" id="ann-preview" style="display:none">
                <div class="preview-header">👀 Preview — What parents will see</div>
                <div class="preview-bubble" id="ann-preview-text"></div>
                <div class="preview-meta">Modern Infinity School · now</div>
              </div>
              <div style="margin-top:16px;display:flex;gap:10px">
                <button class="btn btn-green" onclick="sendAnnouncement()" id="ann-send-btn">📤 Send to Parents</button>
                <button class="btn btn-outline" onclick="clearAnnouncement()">🗑️ Clear</button>
              </div>
            </div>
          </div>

          <!-- History -->
          <div class="table-card">
            <div class="table-card-header">
              <div class="table-card-title">📋 Recent Announcements</div>
              <button class="btn btn-outline btn-sm" onclick="loadHistory()">🔄 Refresh</button>
            </div>
            <div id="ann-history-body">
              <div style="padding:30px;text-align:center;color:#94a3b8;font-size:13px">Click Refresh to load history</div>
            </div>
          </div>

        </div>
      </div>

      <!-- ════════════════════════════════════════════════
           TEACHER PANEL
      ════════════════════════════════════════════════ -->
      <div class="panel" id="panel-teacher">
        <div class="panel-header">
          <div>
            <div class="panel-title">👩‍🏫 Teacher Panel</div>
            <div class="panel-subtitle">Manage homework and exam schedules</div>
          </div>
        </div>
        <div class="panel-body">
          <div id="teacher-alert" class="alert"></div>
          <div style="display:flex;gap:12px;margin-bottom:20px">
            <button class="btn btn-primary" id="tbtn-hw" onclick="showTeacherTab('hw')">📚 Homework</button>
            <button class="btn btn-outline" id="tbtn-exam" onclick="showTeacherTab('exam')">📝 Exams</button>
          </div>

          <!-- Homework Sub-tab -->
          <div id="teacher-hw">
            <div class="table-card" style="margin-bottom:20px">
              <div class="table-card-header">
                <div class="table-card-title">➕ Add Homework</div>
                <div style="font-size:12px;color:#00C8C8" id="hw-teacher-tag"></div>
              </div>
              <div style="padding:20px">
                <div class="form-row">
                  <div class="form-group">
                    <label class="form-label">Grade</label>
                    <select class="form-select" id="hw-grade">
                      <option>KG1</option><option>KG2</option>
                      <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
                      <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
                      <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
                      <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <label class="form-label">Subject</label>
                    <select class="form-select" id="hw-subject">
                      <option>Math</option><option>Arabic</option><option>English</option>
                      <option>Science</option><option>Social Studies</option><option>Islamic Studies</option>
                      <option>French</option><option>Art</option><option>Computer</option><option>PE</option>
                    </select>
                  </div>
                </div>
                <div class="form-group">
                  <label class="form-label">Assignment</label>
                  <textarea class="form-textarea" id="hw-assignment" placeholder="Describe the homework..." rows="3"></textarea>
                </div>
                <div class="form-row">
                  <div class="form-group">
                    <label class="form-label">Due Date</label>
                    <input type="date" class="form-input" id="hw-due">
                  </div>
                  <div class="form-group">
                    <label class="form-label">Notes (optional)</label>
                    <input type="text" class="form-input" id="hw-notes" placeholder="e.g. Handwritten only">
                  </div>
                </div>
                <button class="btn btn-green" onclick="addHomework()">➕ Add Homework</button>
              </div>
            </div>
            <div class="table-card">
              <div class="table-card-header">
                <div class="table-card-title">📚 Active Homework</div>
                <button class="btn btn-outline btn-sm" onclick="loadHomework()">🔄 Refresh</button>
              </div>
              <div id="hw-list-body">
                <div style="padding:30px;text-align:center;color:#94a3b8;font-size:13px">Click Refresh to load homework</div>
              </div>
            </div>
          </div>

          <!-- Exam Results Sub-tab -->
          <div id="teacher-exam" style="display:none">

            <!-- Step 1: Select Grade + Subject -->
            <div class="table-card" style="margin-bottom:20px" id="ex-step1">
              <div class="table-card-header"><div class="table-card-title">📝 Enter Exam Results</div></div>
              <div style="padding:20px">
                <div class="form-row">
                  <div class="form-group">
                    <label class="form-label">Grade</label>
                    <select class="form-select" id="ex-grade">
                      <option value="">Select grade...</option>
                      <option>KG1</option><option>KG2</option>
                      <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
                      <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
                      <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
                      <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <label class="form-label">Subject</label>
                    <select class="form-select" id="ex-subject">
                      <option>Math</option><option>Arabic</option><option>English</option>
                      <option>Science</option><option>Social Studies</option><option>Islamic Studies</option>
                      <option>French</option><option>Art</option><option>Computer</option><option>PE</option>
                    </select>
                  </div>
                </div>
                <div class="form-row">
                  <div class="form-group">
                    <label class="form-label">Term</label>
                    <select class="form-select" id="ex-term">
                      <option>Term 1</option><option>Term 2</option><option>Term 3</option><option>Final</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <label class="form-label">Total Marks</label>
                    <input type="number" class="form-input" id="ex-total" value="100" min="1">
                  </div>
                </div>
                <div class="form-row">
                  <div class="form-group">
                    <label class="form-label">Exam Date</label>
                    <input type="date" class="form-input" id="ex-date">
                  </div>
                </div>
                <button class="btn btn-primary" onclick="loadStudentsForExam()">📋 Load Students</button>
              </div>
            </div>

            <!-- Step 2: Student scores table (hidden until students loaded) -->
            <div id="ex-step2" style="display:none">
              <div class="table-card">
                <div class="table-card-header">
                  <div>
                    <div class="table-card-title" id="ex-table-title">Enter Scores</div>
                    <div style="font-size:12px;color:#64748b;margin-top:2px" id="ex-table-sub"></div>
                    <div style="font-size:11px;color:#00C8C8;margin-top:3px" id="ex-teacher-tag"></div>
                  </div>
                  <div style="display:flex;gap:8px">
                    <button class="btn btn-outline btn-sm" onclick="resetExamForm()">← Back</button>
                    <button class="btn btn-green" id="ex-save-btn" onclick="saveAllExamResults()">💾 Save All Results</button>
                  </div>
                </div>
                <div id="ex-students-body"></div>
              </div>
            </div>

          </div>
        </div>
      </div>

      <!-- ════════════════════════════════════════════════
           FINANCE PANEL
      ════════════════════════════════════════════════ -->
      <div class="panel" id="panel-finance">
        <div class="panel-header">
          <div>
            <div class="panel-title">💳 Finance Panel</div>
            <div class="panel-subtitle">Manage student payment status</div>
          </div>
        </div>
        <div class="panel-body">
          <div id="finance-alert" class="alert"></div>
          <div class="stats-row">
            <div class="stat-card"><div class="stat-label">Total Students</div><div class="stat-value" id="fin-total">—</div></div>
            <div class="stat-card"><div class="stat-label">Paid</div><div class="stat-value green" id="fin-paid">—</div></div>
            <div class="stat-card"><div class="stat-label">Not Paid</div><div class="stat-value red" id="fin-unpaid">—</div></div>
          </div>
          <div class="save-bar" id="fin-save-bar">
            <div class="save-bar-info">⚠️ You have <span id="fin-change-count">0</span> unsaved changes</div>
            <button class="btn btn-green" id="fin-save-btn" onclick="saveFinanceChanges()">💾 Save All Changes</button>
          </div>
          <div class="filters">
            <div class="filter-group">
              <label>Grade</label>
              <select id="fin-grade-filter">
                <option value="">All Grades</option>
                <option>KG1</option><option>KG2</option>
                <option>Grade 1</option><option>Grade 2</option><option>Grade 3</option>
                <option>Grade 4</option><option>Grade 5</option><option>Grade 6</option>
                <option>Grade 7</option><option>Grade 8</option><option>Grade 9</option>
                <option>Grade 10</option><option>Grade 11</option><option>Grade 12</option>
              </select>
            </div>
            <div class="filter-group">
              <label>Status</label>
              <select id="fin-status-filter" onchange="renderFinanceTable()">
                <option value="">All</option>
                <option value="paid">Paid Only</option>
                <option value="unpaid">Not Paid Only</option>
              </select>
            </div>
            <div class="filter-group">
              <label>Search</label>
              <input type="text" id="fin-search" placeholder="Name or ID..." oninput="renderFinanceTable()">
            </div>
            <button class="btn btn-primary btn-sm" onclick="loadFinanceStudents()">🔄 Load Students</button>
          </div>
          <div class="table-card">
            <div class="table-card-header">
              <div class="table-card-title">Students</div>
              <div class="table-count" id="fin-student-count">—</div>
            </div>
            <div id="fin-table-body">
              <div style="padding:30px;text-align:center;color:#94a3b8;font-size:13px">Click "Load Students" to begin</div>
            </div>
          </div>
        </div>
      </div>

    </div><!-- /content -->
  </div><!-- /layout -->
</div><!-- /main-panel -->

<script>
// ── USER ROLES & PERMISSIONS ──────────────────────────────────────────────────
var USERS = {
  "super_admin":  {pass:"admin2026",  name:"Super Admin",  role:"Super Administrator",  panels:["announce","teacher","finance"], grade_filter:null},
  "junior_admin": {pass:"junior2026", name:"Junior Admin", role:"Junior Administrator", panels:["announce"],                     grade_filter:"junior"},
  "senior_admin": {pass:"senior2026", name:"Senior Admin", role:"Senior Administrator", panels:["announce"],                     grade_filter:"senior"},
  "finance":      {pass:"finance2026",name:"Finance Team", role:"Finance Officer",      panels:["finance"],                      grade_filter:null}
};
var JUNIOR_GRADES = ["KG1","KG2","Nursery","Grade 1","Grade 2","Grade 3","Grade 4","Grade 5","Grade 6"];
var SENIOR_GRADES = ["Grade 7","Grade 8","Grade 9","Grade 10","Grade 11","Grade 12"];
var currentUser = null;
var financeStudents = [];
var financeChanges = {};

// ── LOGIN ─────────────────────────────────────────────────────────────────────
function doLogin(){
  var u = document.getElementById('lu').value.trim();
  var p = document.getElementById('lp').value.trim();
  var err = document.getElementById('lerr');
  var btn = document.querySelector('.login-btn');
  if(!u||!p){ err.textContent='⚠️ Enter username and password'; err.style.display='block'; return; }

  // Check static users first
  var user = USERS[u];
  if(user && user.pass === p){
    err.style.display='none';
    currentUser = {username:u, name:user.name, role:user.role, panels:user.panels, grade_filter:user.grade_filter};
    doLoginSuccess();
    return;
  }

  // Otherwise verify against Teachers sheet
  btn.disabled=true; btn.textContent='Signing in...';
  err.style.display='none';
  fetch('/api/teacher-login', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username:u, password:p})
  })
  .then(function(r){ return r.json(); })
  .then(function(data){
    if(data.ok){
      currentUser = {username:u, name:data.name, role:'Teacher', panels:['teacher'], grade_filter:null};
      doLoginSuccess();
    } else {
      err.textContent='❌ Incorrect username or password';
      err.style.display='block';
      document.getElementById('lp').value='';
    }
  })
  .catch(function(){
    err.textContent='❌ Server error, try again';
    err.style.display='block';
  })
  .finally(function(){
    btn.disabled=false; btn.textContent='Sign In →';
  });
}

function doLogout(){
  currentUser=null; financeStudents=[]; financeChanges={};
  ['announce','teacher','finance'].forEach(function(p){
    var nav=document.getElementById('nav-'+p);
    if(nav){ nav.style.display='none'; nav.classList.remove('active'); }
  });
  document.getElementById('main-panel').style.display='none';
  document.getElementById('login-screen').style.display='flex';
  document.getElementById('lu').value='';
  document.getElementById('lp').value='';
}

function doLoginSuccess(){
  var user = currentUser;
  document.getElementById('login-screen').style.display='none';
  document.getElementById('main-panel').style.display='block';
  document.getElementById('sidebarUserName').textContent = user.name;
  document.getElementById('sidebarUserRole').textContent = user.role;
  // Show only allowed panels
  user.panels.forEach(function(panel){
    var nav = document.getElementById('nav-'+panel);
    if(nav) nav.style.display='flex';
  });
  showPanel(user.panels[0]);
  // Set today's date on forms
  var today = new Date().toISOString().split('T')[0];
  ['hw-due','ex-date'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.value = today;
  });
  // Grade restrictions for junior/senior
  if(user.grade_filter === 'junior' || user.grade_filter === 'senior'){
    var optAll = document.getElementById('ann-opt-all');
    if(optAll) optAll.style.display='none';
    var audSel = document.getElementById('ann-audience');
    if(audSel) audSel.value='grade';
    document.getElementById('ann-grade-wrap').style.display='none';
    var grades = user.grade_filter==='junior' ? JUNIOR_GRADES : SENIOR_GRADES;
    var sel = document.getElementById('ann-grade-restricted');
    if(sel) sel.innerHTML = grades.map(function(g){return '<option>'+g+'</option>';}).join('');
    var wrap = document.getElementById('ann-grade-restricted-wrap');
    if(wrap) wrap.style.display='block';
  }
}

// ── PANEL SWITCHING ───────────────────────────────────────────────────────────
function showPanel(name){
  document.querySelectorAll('.panel').forEach(function(p){ p.classList.remove('active'); });
  document.querySelectorAll('.nav-item').forEach(function(n){ n.classList.remove('active'); });
  var panel = document.getElementById('panel-'+name);
  var nav   = document.getElementById('nav-'+name);
  if(panel) panel.classList.add('active');
  if(nav)   nav.classList.add('active');
  // Update teacher name tags immediately on login
  var hwTag = document.getElementById('hw-teacher-tag');
  if(hwTag) hwTag.textContent = user && user.name ? '👤 '+user.name : '';
}
var exStudents = [];

async function loadStudentsForExam(){
  var grade = document.getElementById('ex-grade').value;
  var subj  = document.getElementById('ex-subject').value;
  var total = document.getElementById('ex-total').value;
  var term  = document.getElementById('ex-term').value;
  var date  = document.getElementById('ex-date').value;
  if(!grade){ showAlert('teacher','Please select a grade first','error'); return; }
  var btn = document.querySelector('#ex-step1 .btn-primary');
  btn.disabled=true; btn.textContent='⏳ Loading...';
  try{
    var res = await fetch('/api/finance/students?grade='+encodeURIComponent(grade));
    var data = await res.json();
    exStudents = data.students || [];
    if(!exStudents.length){
      showAlert('teacher','No students found for '+grade,'error');
      btn.disabled=false; btn.textContent='📋 Load Students';
      return;
    }
    // Build scores table
    var tName = currentUser ? currentUser.name : '';
    document.getElementById('ex-table-title').textContent = grade+' — '+subj+' Results';
    document.getElementById('ex-table-sub').textContent = term+' · Total: '+total+' marks · Date: '+(date||'—');
    document.getElementById('ex-teacher-tag').textContent = tName ? '👤 Entered by: '+tName : '';
    var rows = exStudents.map(function(s,i){
      return '<tr>'+
        '<td style="font-weight:600;color:#0F1C2E">'+esc(s.name)+'</td>'+
        '<td style="font-size:12px;color:#64748b">'+esc(s.id)+'</td>'+
        '<td>'+
          '<div style="display:flex;align-items:center;gap:6px">'+
            '<input type="number" id="ex-score-'+i+'" min="0" '+
            'style="width:80px;padding:8px 10px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:14px;outline:none;text-align:center" '+
            'oninput="updateGradeBadge('+i+')" placeholder="—">'+
            '<span style="color:#64748b;font-size:13px;white-space:nowrap">/ <strong id="ex-out-'+i+'">'+total+'</strong></span>'+
            '<input type="number" id="ex-total-'+i+'" min="1" value="'+total+'" '+
            'style="width:60px;padding:8px 8px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:12px;outline:none;text-align:center;color:#64748b" '+
            'oninput="syncTotal('+i+')" placeholder="Max" title="Change total for this student">'+
          '</div>'+
        '</td>'+
        '<td id="ex-badge-'+i+'" style="min-width:80px">—</td>'+
        '<td><input type="text" id="ex-note-'+i+'" placeholder="Notes..." '+
          'style="width:150px;padding:7px 10px;border:1.5px solid #e2e8f0;border-radius:7px;font-size:12px;outline:none"></td>'+
      '</tr>';
    }).join('');
    document.getElementById('ex-students-body').innerHTML =
      '<table><thead><tr><th>Student Name</th><th>ID</th><th>Score &nbsp;/&nbsp; Out of</th><th>Grade</th><th>Notes</th></tr></thead>'+
      '<tbody>'+rows+'</tbody></table>';
    document.getElementById('ex-step1').style.display='none';
    document.getElementById('ex-step2').style.display='block';
  } catch(e){
    showAlert('teacher','❌ Error loading students: '+e.message,'error');
  }
  btn.disabled=false; btn.textContent='📋 Load Students';
}

function updateGradeBadge(i){
  var scoreEl = document.getElementById('ex-score-'+i);
  var totalEl = document.getElementById('ex-total-'+i);
  var badgeEl = document.getElementById('ex-badge-'+i);
  var score = parseFloat(scoreEl.value);
  var total = parseFloat(totalEl ? totalEl.value : 100);
  if(isNaN(score)||scoreEl.value===''||isNaN(total)||total<=0){badgeEl.innerHTML='—';return;}
  var pct = Math.round((score/total)*100);
  var gl = pct>=90?'A+':pct>=85?'A':pct>=80?'B+':pct>=75?'B':pct>=70?'C+':pct>=65?'C':pct>=60?'D':'F';
  var color = pct>=80?'#16a34a':pct>=60?'#2563eb':'#dc2626';
  badgeEl.innerHTML='<span style="font-weight:700;color:'+color+'">'+gl+'</span>'+
    '<span style="font-size:11px;color:#94a3b8;margin-left:4px">('+pct+'%)</span>';
}
function syncTotal(i){
  var totalEl = document.getElementById('ex-total-'+i);
  var outEl   = document.getElementById('ex-out-'+i);
  if(outEl && totalEl) outEl.textContent = totalEl.value||'?';
  updateGradeBadge(i);
}

function resetExamForm(){
  document.getElementById('ex-step1').style.display='block';
  document.getElementById('ex-step2').style.display='none';
  exStudents=[];
}

async function saveAllExamResults(){
  var grade = document.getElementById('ex-grade').value;
  var subj  = document.getElementById('ex-subject').value;
  var total = document.getElementById('ex-total').value;
  var term  = document.getElementById('ex-term').value;
  var date  = document.getElementById('ex-date').value;
  var btn   = document.getElementById('ex-save-btn');
  var teacherName = currentUser ? currentUser.name : 'Unknown';
  var results = [];
  exStudents.forEach(function(s, i){
    var scoreEl = document.getElementById('ex-score-'+i);
    var totalEl = document.getElementById('ex-total-'+i);
    var noteEl  = document.getElementById('ex-note-'+i);
    var score   = scoreEl ? scoreEl.value.trim() : '';
    var rowTotal= totalEl ? totalEl.value.trim() : total;
    if(!score) return;
    var pct = Math.round((parseFloat(score)/parseFloat(rowTotal))*100);
    var gl  = pct>=90?'A+':pct>=85?'A':pct>=80?'B+':pct>=75?'B':pct>=70?'C+':pct>=65?'C':pct>=60?'D':'F';
    results.push({
      student_id:   s.id,
      student_name: s.name,
      grade:        grade,
      subject:      subj,
      score:        score,
      total:        rowTotal,
      percentage:   pct+'%',
      grade_letter: gl,
      term:         term,
      exam_date:    date,
      entered_by:   teacherName,
      notes:        noteEl ? noteEl.value.trim() : ''
    });
  });
  if(!results.length){
    showAlert('teacher','Please enter at least one score before saving','error');
    return;
  }
  btn.disabled=true; btn.textContent='⏳ Saving...';
  try{
    var res = await fetch('/api/exam-results/bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({results})
    });
    var data = await res.json();
    if(data.ok){
      showAlert('teacher','✅ Saved '+data.saved+' results for '+grade+' — '+subj+' (by '+teacherName+')','success');
      resetExamForm();
    } else {
      showAlert('teacher','❌ '+(data.error||'Error saving'),'error');
    }
  } catch(e){
    showAlert('teacher','❌ Network error: '+e.message,'error');
  }
  btn.disabled=false; btn.textContent='💾 Save All Results';
}

// ── FINANCE PANEL ─────────────────────────────────────────────────────────────
async function loadFinanceStudents(){
  var grade=document.getElementById('fin-grade-filter').value;
  financeChanges={};
  updateFinanceSaveBar();
  try{
    var url='/api/finance/students'+(grade?'?grade='+encodeURIComponent(grade):'');
    var res=await fetch(url);
    var data=await res.json();
    financeStudents=data.students||[];
    renderFinanceTable();
    updateFinanceStats();
  } catch(e){showAlert('finance','❌ Error loading students','error');}
}
function renderFinanceTable(){
  var status=document.getElementById('fin-status-filter').value;
  var search=document.getElementById('fin-search').value.toLowerCase();
  var filtered=financeStudents.filter(function(s){
    var isPaid=(financeChanges[s.row_index]!==undefined)?financeChanges[s.row_index]==='paid':(s.payment_status||'').toLowerCase()==='paid';
    var matchStatus=!status||(status==='paid'&&isPaid)||(status==='unpaid'&&!isPaid);
    var matchSearch=!search||(s.name||'').toLowerCase().includes(search)||(s.id||'').toLowerCase().includes(search);
    return matchStatus&&matchSearch;
  });
  document.getElementById('fin-student-count').textContent=filtered.length+' students shown';
  if(!filtered.length){
    document.getElementById('fin-table-body').innerHTML='<div style="padding:30px;text-align:center;color:#94a3b8;font-size:13px">No students match your filters</div>';
    return;
  }
  var rows=filtered.map(function(s){
    var isPaid=(financeChanges[s.row_index]!==undefined)?financeChanges[s.row_index]==='paid':(s.payment_status||'').toLowerCase()==='paid';
    var label=isPaid?'<span class="toggle-label paid">✅ Paid</span>':'<span class="toggle-label unpaid">❌ Not Paid</span>';
    return '<tr><td style="font-weight:600">'+esc(s.name)+'</td>'+
           '<td style="color:#64748b;font-size:12px">'+esc(s.id)+'</td>'+
           '<td><span class="badge badge-blue">'+esc(s.grade)+'</span></td>'+
           '<td><div style="display:flex;align-items:center;gap:10px">'+
             '<label class="toggle"><input type="checkbox" '+(isPaid?'checked':'')+' onchange="togglePayment(this,'+s.row_index+')"><span class="slider"></span></label>'+
             '<span id="fin-lbl-'+s.row_index+'">'+label+'</span></div></td></tr>';
  }).join('');
  document.getElementById('fin-table-body').innerHTML=
    '<table><thead><tr><th>Name</th><th>ID</th><th>Grade</th><th>Payment Status</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
function togglePayment(cb,rowIndex){
  financeChanges[rowIndex]=cb.checked?'paid':'unpaid';
  var lbl=document.getElementById('fin-lbl-'+rowIndex);
  if(lbl) lbl.innerHTML=cb.checked?'<span class="toggle-label paid">✅ Paid</span>':'<span class="toggle-label unpaid">❌ Not Paid</span>';
  updateFinanceSaveBar();
  updateFinanceStats();
}
function updateFinanceSaveBar(){
  var count=Object.keys(financeChanges).length;
  document.getElementById('fin-change-count').textContent=count;
  document.getElementById('fin-save-bar').className='save-bar'+(count>0?' show':'');
}
function updateFinanceStats(){
  var total=financeStudents.length;
  var paid=financeStudents.filter(function(s){
    var status=(financeChanges[s.row_index]!==undefined)?financeChanges[s.row_index]:(s.payment_status||'').toLowerCase();
    return status==='paid';
  }).length;
  document.getElementById('fin-total').textContent=total||'—';
  document.getElementById('fin-paid').textContent=paid||'—';
  document.getElementById('fin-unpaid').textContent=total?(total-paid):'—';
}
async function saveFinanceChanges(){
  var btn=document.getElementById('fin-save-btn');
  btn.disabled=true; btn.textContent='⏳ Saving...';
  try{
    var res=await fetch('/api/finance/update-payment',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({changes:financeChanges})});
    var data=await res.json();
    if(data.ok){
      showAlert('finance','✅ Saved — '+data.updated+' students updated','success');
      financeStudents.forEach(function(s){
        if(financeChanges[s.row_index]!==undefined) s.payment_status=financeChanges[s.row_index];
      });
      financeChanges={};
      updateFinanceSaveBar();
      renderFinanceTable();
    } else {showAlert('finance','❌ '+(data.error||'Error'),'error');}
  } catch(e){showAlert('finance','❌ Network error','error');}
  btn.disabled=false; btn.textContent='💾 Save All Changes';
}

// ── HELPERS ───────────────────────────────────────────────────────────────────

// ── ANNOUNCEMENTS ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function(){
  var audSel = document.getElementById('ann-audience');
  if(audSel) audSel.addEventListener('change', function(){
    document.getElementById('ann-grade-wrap').style.display = this.value==='grade' ? 'block' : 'none';
  });
});
function updatePreview(){
  var msg = document.getElementById('ann-msg').value;
  document.getElementById('ann-chars').textContent = msg.length;
  var prev = document.getElementById('ann-preview');
  if(msg.trim()){
    prev.style.display='block';
    document.getElementById('ann-preview-text').textContent = msg;
  } else { prev.style.display='none'; }
}
function clearAnnouncement(){
  document.getElementById('ann-msg').value='';
  document.getElementById('ann-image').value='';
  document.getElementById('ann-preview').style.display='none';
  document.getElementById('ann-chars').textContent='0';
}
async function sendAnnouncement(){
  var msg = document.getElementById('ann-msg').value.trim();
  if(!msg){ showAlert('ann','Please write a message first','error'); return; }
  var audience = document.getElementById('ann-audience').value;
  var grade = '';
  var gf = currentUser ? currentUser.grade_filter : null;
  if(audience==='grade'){
    if(gf==='junior'||gf==='senior'){
      grade = document.getElementById('ann-grade-restricted').value;
    } else {
      grade = document.getElementById('ann-grade').value;
    }
  }
  var imageFile = document.getElementById('ann-image').files[0];
  var btn = document.getElementById('ann-send-btn');
  btn.disabled=true; btn.textContent='⏳ Sending...';
  showAlert('ann','Sending announcement...','info');
  try{
    var fd = new FormData();
    fd.append('message', msg);
    fd.append('audience', audience);
    if(grade) fd.append('grade', grade);
    if(imageFile) fd.append('image', imageFile);
    var res = await fetch('/api/broadcast', {method:'POST', body:fd});
    var data = await res.json();
    if(data.ok){
      showAlert('ann','✅ Sent to '+data.sent+' parents successfully!','success');
      clearAnnouncement();
      loadHistory();
    } else { showAlert('ann','❌ Error: '+(data.error||'Unknown error'),'error'); }
  } catch(e){ showAlert('ann','❌ Network error: '+e.message,'error'); }
  btn.disabled=false; btn.textContent='📤 Send to Parents';
}
async function loadHistory(){
  var body = document.getElementById('ann-history-body');
  body.innerHTML='<div style="padding:20px;text-align:center;color:#94a3b8">Loading...</div>';
  try{
    var res = await fetch('/api/announcements');
    var data = await res.json();
    var rows = data.announcements || [];
    if(!rows.length){ body.innerHTML='<div style="padding:30px;text-align:center;color:#94a3b8;font-size:13px">No announcements yet</div>'; return; }
    body.innerHTML='<table><thead><tr><th>Title</th><th>Message</th><th>Date</th><th>Status</th></tr></thead><tbody>'+
      rows.slice(0,20).map(function(r){
        return '<tr><td style="font-weight:600">'+esc(r.Title||'')+'</td>'+
               '<td style="max-width:280px;color:#64748b">'+esc((r.Message||'').substring(0,80)+((r.Message||'').length>80?'...':''))+'</td>'+
               '<td style="white-space:nowrap;color:#64748b">'+esc(r.Date||'')+'</td>'+
               '<td><span class="badge '+(r.Status==='Sent'?'badge-green':'badge-grey')+'">'+esc(r.Status||'')+'</span></td></tr>';
      }).join('')+'</tbody></table>';
  } catch(e){ body.innerHTML='<div style="padding:20px;text-align:center;color:#dc2626">Error loading history</div>'; }
}

// ── TEACHER PANEL ─────────────────────────────────────────────────────────────
function showTeacherTab(tab){
  document.getElementById('teacher-hw').style.display   = tab==='hw'   ? 'block' : 'none';
  document.getElementById('teacher-exam').style.display = tab==='exam' ? 'block' : 'none';
  document.getElementById('tbtn-hw').className   = 'btn '+(tab==='hw'  ?'btn-primary':'btn-outline');
  document.getElementById('tbtn-exam').className = 'btn '+(tab==='exam'?'btn-primary':'btn-outline');
}
async function addHomework(){
  var grade       = document.getElementById('hw-grade').value;
  var subject     = document.getElementById('hw-subject').value;
  var assignment  = document.getElementById('hw-assignment').value.trim();
  var due         = document.getElementById('hw-due').value;
  var notes       = document.getElementById('hw-notes').value.trim();
  var teacherName = currentUser ? currentUser.name : 'Unknown';
  if(!assignment||!due){ showAlert('teacher','Please fill in the assignment and due date','error'); return; }
  var btn = document.querySelector('#teacher-hw .btn-green');
  if(btn){ btn.disabled=true; btn.textContent='⏳ Saving...'; }
  try{
    var res = await fetch('/api/homework', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({grade, subject, assignment, due_date:due, notes, teacher:teacherName})
    });
    var data = await res.json();
    if(data.ok){
      showAlert('teacher','✅ Homework added by '+teacherName+'!','success');
      document.getElementById('hw-assignment').value='';
      document.getElementById('hw-notes').value='';
      loadHomework();
    } else { showAlert('teacher','❌ '+(data.error||'Error'),'error'); }
  } catch(e){ showAlert('teacher','❌ Network error: '+e.message,'error'); }
  if(btn){ btn.disabled=false; btn.textContent='➕ Add Homework'; }
}
async function loadHomework(){
  var body = document.getElementById('hw-list-body');
  body.innerHTML='<div style="padding:20px;text-align:center;color:#94a3b8">Loading...</div>';
  try{
    var res = await fetch('/api/homework');
    var data = await res.json();
    var rows = data.homework || [];
    if(!rows.length){ body.innerHTML='<div style="padding:30px;text-align:center;color:#94a3b8;font-size:13px">No active homework</div>'; return; }
    body.innerHTML='<table><thead><tr><th>Grade</th><th>Subject</th><th>Assignment</th><th>Due Date</th><th>Teacher</th></tr></thead><tbody>'+
      rows.map(function(r){
        return '<tr>'+
          '<td><span class="badge badge-blue">'+esc(r.Grade||r.grade||'')+'</span></td>'+
          '<td>'+esc(r.Subject||r.subject||'')+'</td>'+
          '<td style="max-width:260px">'+esc(r.Assignment||r.assignment||'')+'</td>'+
          '<td style="white-space:nowrap">'+esc(r["Due Date"]||r.due_date||'')+'</td>'+
          '<td style="color:#64748b">'+esc(r.Teacher||r.teacher||'')+'</td>'+
        '</tr>';
      }).join('')+'</tbody></table>';
  } catch(e){ body.innerHTML='<div style="padding:20px;text-align:center;color:#dc2626">Error loading homework</div>'; }
}

function showAlert(panel,msg,type){
  var el=document.getElementById(panel+'-alert');
  if(!el) return;
  el.textContent=msg;
  el.className='alert show alert-'+type;
  setTimeout(function(){el.style.display='none';},5000);
}
function esc(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}




@app.route('/api/exam-results', methods=['GET'])
def api_get_exam_results():
    """Return exam results from the exam tab."""
    try:
        grade = request.args.get('grade', '').strip()
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet("exam")
        rows = ws.get_all_records()
        if grade:
            rows = [r for r in rows if str(r.get('Grade', r.get('grade', ''))).strip().lower() == grade.lower()]
        # Return most recent first (last 100)
        rows = rows[-100:][::-1]
        return jsonify({"results": rows})
    except Exception as e:
        logger.error(f"[api/exam-results GET] {e}")
        return jsonify({"results": [], "error": str(e)}), 500


@app.route('/api/exam-results', methods=['POST'])
def api_post_exam_result():
    """Save a single exam result to the exam tab."""
    data = request.get_json(force=True, silent=True) or {}
    required = ['student_id', 'student_name', 'grade', 'subject', 'score', 'total']
    missing = [f for f in required if not str(data.get(f, '')).strip()]
    if missing:
        return jsonify({"ok": False, "error": f"Missing: {', '.join(missing)}"}), 400
    try:
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet("exam")
        headers = ws.row_values(1) if ws.row_count > 0 else []
        # Ensure headers exist
        expected = ['Student ID', 'Student Name', 'Grade', 'Subject', 'Score', 'Total', 'Percentage', 'Grade Letter', 'Term', 'Exam Date', 'Notes']
        if not headers:
            ws.update('A1', [expected])
            headers = expected
        row = [
            str(data.get('student_id', '')).strip(),
            str(data.get('student_name', '')).strip(),
            str(data.get('grade', '')).strip(),
            str(data.get('subject', '')).strip(),
            str(data.get('score', '')).strip(),
            str(data.get('total', '')).strip(),
            str(data.get('percentage', '')).strip(),
            str(data.get('grade_letter', '')).strip(),
            str(data.get('term', '')).strip(),
            str(data.get('exam_date', '')).strip(),
            str(data.get('notes', '')).strip(),
        ]
        ws.append_row(row, value_input_option='USER_ENTERED')
        logger.info(f"[exam-results] Saved result for {data.get('student_id')} — {data.get('subject')}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[api/exam-results POST] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route('/api/exam-results/bulk', methods=['POST'])
def api_post_exam_results_bulk():
    """Save multiple exam results at once."""
    data = request.get_json(force=True, silent=True) or {}
    results = data.get('results', [])
    if not results:
        return jsonify({"ok": False, "error": "No results provided"}), 400
    try:
        wb = get_client().open_by_key(SHEET_ID)
        ws = wb.worksheet("exam")
        headers = ws.row_values(1) if ws.row_count > 0 else []
        expected = ['Student ID', 'Student Name', 'Grade', 'Subject', 'Score', 'Total', 'Percentage', 'Grade Letter', 'Term', 'Exam Date', 'Notes']
        if not headers:
            ws.update('A1', [expected])
        rows_to_append = []
        for r in results:
            rows_to_append.append([
                str(r.get('student_id', '')),
                str(r.get('student_name', '')),
                str(r.get('grade', '')),
                str(r.get('subject', '')),
                str(r.get('score', '')),
                str(r.get('total', '')),
                str(r.get('percentage', '')),
                str(r.get('grade_letter', '')),
                str(r.get('term', '')),
                str(r.get('exam_date', '')),
                str(r.get('notes', '')),
            ])
        if rows_to_append:
            ws.append_rows(rows_to_append, value_input_option='USER_ENTERED')
        logger.info(f"[exam-results/bulk] Saved {len(rows_to_append)} results")
        return jsonify({"ok": True, "saved": len(rows_to_append)})
    except Exception as e:
        logger.error(f"[exam-results/bulk] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route('/api/teacher-login', methods=['POST'])
def api_teacher_login():
    """Verify teacher credentials against Teachers sheet."""
    data = request.get_json(force=True, silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "Missing credentials"}), 400
    try:
        rows = read_tab("Teachers")
        for row in rows:
            u = str(row.get('Username', '')).strip()
            p = str(row.get('Password', '')).strip()
            active = str(row.get('Active', 'yes')).strip().lower()
            if u.lower() == username.lower() and p == password and active == 'yes':
                name = str(row.get('Full Name', username)).strip()
                logger.info(f"[teacher-login] ✅ {username} logged in")
                return jsonify({"ok": True, "name": name, "username": username, "role": "Teacher"})
        logger.warning(f"[teacher-login] ❌ Failed login for: {username}")
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401
    except Exception as e:
        logger.error(f"[teacher-login] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")