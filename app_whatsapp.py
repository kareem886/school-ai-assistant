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


@app.route('/api/homework')
def homework_api():
    grade = request.args.get("grade", "")
    rows = read_tab("Homework")
    result = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower() and str(r.get("Assignment","")).strip()]
    return jsonify({"homework": result, "count": len(result)})

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
  // Update teacher name tags
  if(currentUser){
    var hwTag = document.getElementById('hw-teacher-tag');
    if(hwTag) hwTag.textContent = '👤 '+currentUser.name;
  }
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
                return jsonify({"ok": True, "name": name, "username": username})
        logger.warning(f"[teacher-login] ❌ Failed login for: {username}")
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401
    except Exception as e:
        logger.error(f"[teacher-login] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")