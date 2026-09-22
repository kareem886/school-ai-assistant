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

        # EXAM RESULTS — SECURED BY PARENT PHONE NUMBER
        result_kw = ['result','results','exam','score','mark','marks','\u0646\u062a\u064a\u062c\u0629','\u0646\u062a\u0627\u0626\u062c','\u0627\u0645\u062a\u062d\u0627\u0646','\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a','\u062f\u0631\u062c\u0629','\u062f\u0631\u062c\u0627\u062a']
        if any(w in m for w in result_kw):
            authorized = get_parent_students(from_phone)
            if sid not in authorized:
                return (f"🔒 \u0639\u0630\u0631\u0627\u064b\u060c \u064a\u0645\u0643\u0646\u0643 \u0641\u0642\u0637 \u0627\u0644\u0627\u0637\u0644\u0627\u0639 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c \u0623\u0628\u0646\u0627\u0626\u0643 \u0627\u0644\u0645\u0633\u062c\u0644\u064a\u0646 \u0628\u0631\u0642\u0645\u0643.\n"
                        f"📞 {SCHOOL['phone']}") if is_arabic else                        (f"🔒 Sorry, you can only access results for students registered under your phone number.\n"
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
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        if is_arabic:
            return (f"💰 \u0631\u0633\u0648\u0645 Modern Infinity 2025/2026\n\n"
                    f"🔹 KG: {info.get('KG1 Fees','42,000 جنيه')}\n"
                    f"🔹 \u0627\u0644\u0635\u0641 1-3: {info.get('Grade 1-3 Fees','48,000 جنيه')}\n"
                    f"🔹 \u0627\u0644\u0635\u0641 4-6: {info.get('Grade 4-6 Fees','55,000 جنيه')}\n"
                    f"🔹 \u0627\u0644\u0635\u0641 7-9: {info.get('Grade 7-9 Fees','62,000 جنيه')}\n"
                    f"🔹 \u0627\u0644\u0635\u0641 10-12: {info.get('Grade 10-12 Fees','70,000 جنيه')}\n\n"
                    f"📅 \u062a\u0642\u0633\u064a\u0645 \u0639\u0644\u0649 3 \u0623\u0642\u0633\u0627\u0637\n"
                    f"📞 {SCHOOL['phone']}")
        return (f"💰 Modern Infinity Fees 2025/2026\n\n"
                f"🔹 KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                f"🔹 Grade 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                f"🔹 Grade 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                f"🔹 Grade 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n"
                f"🔹 Grade 10-12: {info.get('Grade 10-12 Fees','70,000 EGP')}\n\n"
                f"📅 3 installments available\n"
                f"📞 {SCHOOL['phone']}")

    # Announcements
    ann_kw = ['announcement','news','holiday','\u0625\u0639\u0644\u0627\u0646','\u0627\u0645\u062a\u062d\u0627\u0646','\u0625\u0639\u0644\u0627\u0646\u0627\u062a','\u0627\u0639\u0644\u0627\u0646\u0627\u062a','\u0627\u062e\u0628\u0627\u0631','\u0623\u062e\u0628\u0627\u0631','\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a','\u0627\u062c\u0627\u0632\u0629','\u0625\u062c\u0627\u0632\u0629','\u0645\u0648\u0639\u062f','\u062c\u062f\u064a\u062f']
    if any(w in m for w in ann_kw):
        rows = read_tab("Announcements")
        active = [r for r in rows if str(r.get("Status","")).lower() == "active"]
        if not active:
            return "📢 \u0644\u0627 \u062a\u0648\u062c\u062f \u0625\u0639\u0644\u0627\u0646\u0627\u062a \u062d\u0627\u0644\u064a\u0627\u064b" if is_arabic else "📢 No announcements at this time"
        r = "📢 \u0625\u0639\u0644\u0627\u0646\u0627\u062a \u0627\u0644\u0645\u062f\u0631\u0633\u0629\n\n" if is_arabic else "📢 School Announcements\n\n"
        for a in active:
            r += f"🔔 {a.get('Title','')}\n{a.get('Message','')}\n📅 {a.get('Date','')}\n\n"
        return r.strip()

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

async function uploadPhoto(){
  var file = document.getElementById('photoInput').files[0];
  if(!file) return null;
  var st = document.getElementById('uploadStatus');
  st.style.display='block'; st.textContent='⏳ Uploading photo...';
  var fd = new FormData();
  fd.append('image', file);
  try{
    var res = await fetch('/api/upload-image',{method:'POST',body:fd});
    var data = await res.json();
    if(data.ok){
      st.textContent='✅ Photo ready to send';
      return data.media_id;
    } else {
      st.textContent='❌ Upload failed: '+data.error;
      return null;
    }
  } catch(e){
    st.textContent='❌ Upload error';
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
    for parent in all_parents:
        phone = str(parent.get("Phone", "")).strip()
        if not phone:
            continue
        if not phone.startswith("20") and not phone.startswith("+"):
            phone = "20" + phone.lstrip("0")
        phone = phone.lstrip("+")
        if media_id:
            # Send image with caption using WhatsApp media_id
            ok = send_whatsapp_image(phone, media_id, caption=broadcast_msg)
        elif image_url:
            # Fallback: link-based image (less reliable)
            ok = send_whatsapp_image(phone, image_url, caption=broadcast_msg)
        else:
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
<title>Add Homework — Modern Infinity School</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Segoe UI', Arial, sans-serif;
    background: #f0f4f8;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 24px 16px;
  }

  .header {
    background: #0F1C2E;
    color: #fff;
    width: 100%;
    max-width: 580px;
    border-radius: 14px 14px 0 0;
    padding: 22px 28px;
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .header-icon { font-size: 32px; }
  .header h1 { font-size: 18px; font-weight: 700; line-height: 1.3; }
  .header p  { font-size: 12px; opacity: 0.65; margin-top: 2px; }

  .card {
    background: #fff;
    width: 100%;
    max-width: 580px;
    border-radius: 0 0 14px 14px;
    padding: 28px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  }

  .field { margin-bottom: 18px; }
  label {
    display: block;
    font-size: 12px;
    font-weight: 700;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 6px;
  }
  label span { color: #e53e3e; }

  input, select, textarea {
    width: 100%;
    padding: 12px 14px;
    border: 2px solid #e2e8f0;
    border-radius: 8px;
    font-size: 15px;
    color: #1e293b;
    background: #f8fafc;
    transition: border-color 0.2s, box-shadow 0.2s;
    outline: none;
  }
  input:focus, select:focus, textarea:focus {
    border-color: #1D4ED8;
    background: #fff;
    box-shadow: 0 0 0 3px rgba(29,78,216,0.1);
  }
  textarea { resize: vertical; min-height: 80px; }

  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

  .submit-btn {
    width: 100%;
    padding: 16px;
    background: #16a34a;
    color: #fff;
    border: none;
    border-radius: 10px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    letter-spacing: 0.03em;
    transition: background 0.2s, transform 0.1s;
    margin-top: 8px;
  }
  .submit-btn:hover  { background: #15803d; }
  .submit-btn:active { transform: scale(0.98); }
  .submit-btn:disabled { background: #86efac; cursor: not-allowed; }

  .status {
    margin-top: 16px;
    padding: 14px 16px;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    text-align: center;
    display: none;
  }
  .status.success { background: #dcfce7; color: #15803d; display: block; }
  .status.error   { background: #fee2e2; color: #dc2626; display: block; }

  .history { margin-top: 28px; }
  .history h2 {
    font-size: 13px;
    font-weight: 700;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 12px;
  }
  .history-item {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 4px solid #1D4ED8;
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 8px;
    font-size: 13px;
    color: #334155;
  }
  .history-item strong { color: #0F1C2E; }
  .history-item .meta { color: #94a3b8; font-size: 11px; margin-top: 2px; }
  .no-history { color: #94a3b8; font-size: 13px; text-align: center; padding: 12px; }
</style>
</head>
<body>

<div class="header">
  <div class="header-icon">📚</div>
  <div>
    <h1>Add Homework</h1>
    <p>Modern Infinity Language School</p>
  </div>
</div>

<div class="card">
  <div class="field">
    <label>👤 Teacher Name <span>*</span></label>
    <input type="text" id="teacher" placeholder="e.g. Ms. Sara" autocomplete="off">
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

  <button class="submit-btn" id="submitBtn" onclick="submitHomework()">
    ➕ ADD HOMEWORK
  </button>

  <div class="status" id="status"></div>

  <div class="history">
    <h2>📋 Recently Added (this session)</h2>
    <div id="historyList"><div class="no-history">Nothing added yet this session.</div></div>
  </div>
</div>

<script>
  // Set default due date to tomorrow
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  document.getElementById('due_date').value = tomorrow.toISOString().split('T')[0];

  // Load teacher name from localStorage
  const savedTeacher = localStorage.getItem('hw_teacher');
  if (savedTeacher) document.getElementById('teacher').value = savedTeacher;

  const sessionHistory = [];

  async function submitHomework() {
    const btn    = document.getElementById('submitBtn');
    const status = document.getElementById('status');

    const teacher    = document.getElementById('teacher').value.trim();
    const grade      = document.getElementById('grade').value;
    const subject    = document.getElementById('subject').value;
    const assignment = document.getElementById('assignment').value.trim();
    const due_date   = document.getElementById('due_date').value;
    const type       = document.getElementById('type').value;
    const notes      = document.getElementById('notes').value.trim();

    // Validate
    const missing = [];
    if (!teacher)    missing.push('Teacher Name');
    if (!grade)      missing.push('Grade');
    if (!subject)    missing.push('Subject');
    if (!assignment) missing.push('Assignment');
    if (!due_date)   missing.push('Due Date');

    if (missing.length) {
      status.className = 'status error';
      status.textContent = '⚠️ Please fill in: ' + missing.join(', ');
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Saving…';
    status.className = 'status';

    try {
      const res = await fetch('/api/add-homework', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ teacher, grade, subject, assignment, due_date, type, notes })
      });
      const data = await res.json();

      if (data.ok) {
        status.className = 'status success';
        status.textContent = '✅ ' + data.message;

        // Save teacher name
        localStorage.setItem('hw_teacher', teacher);

        // Add to session history
        sessionHistory.unshift({ teacher, grade, subject, assignment, due_date, type });
        renderHistory();

        // Clear fields (keep teacher, grade, type, set tomorrow)
        document.getElementById('subject').value = '';
        document.getElementById('assignment').value = '';
        document.getElementById('notes').value = '';
        const tmr = new Date();
        tmr.setDate(tmr.getDate() + 1);
        document.getElementById('due_date').value = tmr.toISOString().split('T')[0];
        document.getElementById('subject').focus();
      } else {
        status.className = 'status error';
        status.textContent = '❌ ' + data.error;
      }
    } catch (e) {
      status.className = 'status error';
      status.textContent = '❌ Network error — please try again.';
    }

    btn.disabled = false;
    btn.textContent = '➕ ADD HOMEWORK';
  }

  function renderHistory() {
    const el = document.getElementById('historyList');
    if (!sessionHistory.length) {
      el.innerHTML = '<div class="no-history">Nothing added yet this session.</div>';
      return;
    }
    el.innerHTML = sessionHistory.slice(0, 5).map(h => `
      <div class="history-item">
        <strong>${h.grade} — ${h.subject}</strong>: ${h.assignment.substring(0,70)}${h.assignment.length>70?'…':''}
        <div class="meta">📅 ${h.due_date} · 👤 ${h.teacher} · 🗂️ ${h.type}</div>
      </div>
    `).join('');
  }

  // Allow Ctrl+Enter to submit
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submitHomework();
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
    """Upload image to WhatsApp Media API and return media_id."""
    if 'image' not in request.files:
        return jsonify({"ok": False, "error": "No image file provided"}), 400
    file = request.files['image']
    if not file.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400
    if not ACCESS_TOKEN:
        return jsonify({"ok": False, "error": "WhatsApp not configured"}), 500
    try:
        file_bytes = file.read()
        mime = file.content_type or "image/jpeg"
        # Upload to WhatsApp Media API
        upload_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
        res = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            files={"file": (file.filename, file_bytes, mime)},
            data={"messaging_product": "whatsapp"},
            timeout=30
        )
        if res.status_code == 200:
            media_id = res.json().get("id")
            logger.info(f"[upload] WhatsApp media_id: {media_id}")
            return jsonify({"ok": True, "media_id": media_id})
        logger.error(f"[upload] WA upload failed: {res.status_code} {res.text[:300]}")
        return jsonify({"ok": False, "error": f"Upload failed ({res.status_code}): {res.text[:200]}"}), 500
    except Exception as e:
        logger.error(f"[upload] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")