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
    return send_from_directory('.', 'admin_panel.html')


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

    sent = 0
    failed = 0
    for parent in all_parents:
        phone = str(parent.get("Phone", "")).strip()
        if not phone:
            continue
        if not phone.startswith("20") and not phone.startswith("+"):
            phone = "20" + phone.lstrip("0")
        phone = phone.lstrip("+")
        if send_whatsapp(phone, broadcast_msg):
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
        ws.append_row([grade, subject, assignment, due_date, teacher, hw_type, notes, "Active"],
                      value_input_option="USER_ENTERED")
        logger.info(f"[homework] Added: {grade} {subject} by {teacher}")
        return jsonify({"ok": True, "message": f"Added: {grade} — {subject}: {assignment[:50]}"})
    except Exception as e:
        logger.error(f"[homework] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")