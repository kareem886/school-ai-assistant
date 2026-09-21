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
    try:
        wb = get_client().open_by_key(SHEET_ID)
        rows = wb.worksheet(tab_name).get_all_records()
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
    """Return list of student IDs linked to this parent phone number."""
    try:
        rows = read_tab("Parents")
        norm = from_phone.lstrip('+').strip()
        matched = []
        for r in rows:
            p = str(r.get("Phone", "")).lstrip('+').strip()
            if p and p == norm:
                sid = str(r.get("Student ID", "")).strip().upper()
                if sid:
                    matched.append(sid)
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
        name = s.get('Student Name','')
        grade = s.get('Grade','')

        # EXAM RESULTS — SECURED BY PARENT PHONE NUMBER
        result_kw = ['result','results','exam','score','mark','marks','\u0646\u062a\u064a\u062c\u0629','\u0646\u062a\u0627\u0626\u062c','\u0627\u0645\u062a\u062d\u0627\u0646','\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a','\u062f\u0631\u062c\u0629','\u062f\u0631\u062c\u0627\u062a']
        if any(w in m for w in result_kw):
            authorized = get_parent_students(from_phone)
            if sid not in authorized:
                return (f"🔒 \u0639\u0630\u0631\u0627\u064b\u060c \u064a\u0645\u0643\u0646\u0643 \u0641\u0642\u0637 \u0627\u0644\u0627\u0637\u0644\u0627\u0639 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c \u0623\u0628\u0646\u0627\u0626\u0643 \u0627\u0644\u0645\u0633\u062c\u0644\u064a\u0646 \u0628\u0631\u0642\u0645\u0643.\n"
                        f"📞 {SCHOOL['phone']}") if is_arabic else                        (f"🔒 Sorry, you can only access results for students registered under your phone number.\n"
                        f"📞 {SCHOOL['phone']}")
            result_rows = read_tab("Results")
            student_results = [r for r in result_rows if str(r.get("Student ID","")).upper() == sid]
            if not student_results:
                return (f"📝 \u0644\u0627 \u062a\u0648\u062c\u062f \u0646\u062a\u0627\u0626\u062c \u0644\u0640 {name} \u062d\u0627\u0644\u064a\u0627\u064b\n📞 {SCHOOL['phone']}") if is_arabic else                        (f"📝 No results found for {name} yet\n📞 {SCHOOL['phone']}")
            if is_arabic:
                r = f"📝 \u0646\u062a\u0627\u0626\u062c \u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a {name} ({grade})\n\n"
                for res in student_results:
                    r += f"\u2022 {res.get('Subject','')}: {res.get('Score','')} \u2014 {res.get('Grade','')}\n"
                    if res.get('Rank',''): r += f"  🏆 \u0627\u0644\u062a\u0631\u062a\u064a\u0628: {res.get('Rank','')}\n"
            else:
                r = f"📝 Exam Results \u2014 {name} ({grade})\n\n"
                for res in student_results:
                    r += f"\u2022 {res.get('Subject','')}: {res.get('Score','')} \u2014 {res.get('Grade','')}\n"
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
    """Load parents from Google Sheet, optionally filtered by admin grade scope."""
    try:
        grades_param = request.args.get("grades", "")          # comma-separated or empty
        allowed = [g.strip() for g in grades_param.split(",") if g.strip()]
        rows = read_tab("Parents")
        parents = []
        for r in rows:
            if not (str(r.get("Active", "yes")).lower() == "yes" and r.get("Phone", "")):
                continue
            grade = r.get("Grade", "")
            if allowed:
                if not any(a.lower() in grade.lower() for a in allowed):
                    continue
            parents.append({
                "name":  r.get("Name", ""),
                "phone": str(r.get("Phone", "")),
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
        rows = read_tab("Parents")
        all_parents = [
            r for r in rows
            if str(r.get("Active", "yes")).lower() == "yes" and r.get("Phone", "")
        ]
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


# ── ONE-TIME SHEET REBUILD ENDPOINT ─────────────────────────────────────────
@app.route('/rebuild-sheet-x7k2', methods=['GET'])
def rebuild_sheet():
    """One-time endpoint to rebuild all sheet tabs. DELETE AFTER USE."""
    try:
        gc = get_gspread()
        sh = gc.open_by_key(SHEET_ID)

        C = {"dk":"#0F1C2E","gr":"#25D366","lgr":"#DCFCE7","amb":"#FEF3C7",
             "bl":"#DBEAFE","rd":"#FEE2E2","pu":"#F3E8FF","wh":"#FFFFFF",
             "gy":"#F8FAFC","brd":"#E2E8F0"}

        def tab(name):
            try: s=sh.worksheet(name)
            except: s=sh.add_worksheet(name,500,30)
            s.clear()
            return s

        def hdr(s,n,col):
            import gspread
            s.format(f"A1:{chr(64+n)}1",{"backgroundColor":{"red":int(col[1:3],16)/255,"green":int(col[3:5],16)/255,"blue":int(col[5:7],16)/255},"textFormat":{"bold":True,"foregroundColor":{"red":1,"green":1,"blue":1},"fontSize":11},"verticalAlignment":"MIDDLE"})
            s.freeze(rows=1)

        def color_range(s, r1, c1, r2, c2, bg, fg="#000000", bold=False):
            import gspread
            cell_range = f"{chr(64+c1)}{r1}:{chr(64+c2)}{r2}"
            fmt = {"backgroundColor":{"red":int(bg[1:3],16)/255,"green":int(bg[3:5],16)/255,"blue":int(bg[5:7],16)/255},"textFormat":{"foregroundColor":{"red":int(fg[1:3],16)/255,"green":int(fg[3:5],16)/255,"blue":int(fg[5:7],16)/255},"bold":bold}}
            s.format(cell_range, fmt)

        results = []

        # ── 1. HOMEWORK ──────────────────────────────────────────────────────
        s = tab("Homework")
        h = [["Grade","Subject","Assignment","Due Date","Teacher","Type","Notes","Status"]]
        d = [
            ["KG1","Activities","Color the butterfly worksheet","2026-09-23","Ms. Noha","Coloring","Use crayons","Active"],
            ["KG1","Arabic","Trace letters: ا ب ت ث — page 5","2026-09-24","Ms. Fatima","Tracing","Pencil only","Active"],
            ["KG2","English","Write your name 3 times on dotted lines","2026-09-23","Ms. Sara","Writing","","Active"],
            ["KG2","Math","Count and circle groups of 5 — worksheet page 8","2026-09-24","Ms. Noha","Worksheet","","Active"],
            ["Grade 1","Math","Addition up to 20 — workbook page 14-15","2026-09-23","Ms. Hana","Workbook","","Active"],
            ["Grade 1","Arabic","Read lesson 2 and answer questions 1-3","2026-09-24","Mr. Tarek","Reading","Handwritten","Active"],
            ["Grade 1","English","Write 5 sentences: cat, dog, sun, run, big","2026-09-25","Ms. Sara","Writing","","Active"],
            ["Grade 2","Math","Subtraction worksheet page 22 questions 1-10","2026-09-23","Ms. Hana","Worksheet","","Active"],
            ["Grade 2","Arabic","Memorize poem on page 18 — 4 lines","2026-09-24","Mr. Tarek","Memorization","","Active"],
            ["Grade 2","Science","Draw and label: sun, cloud, rain, wind","2026-09-25","Ms. Hana","Drawing","Use colors","Active"],
            ["Grade 3","Math","Multiplication tables 3 and 4 — page 30","2026-09-23","Ms. Hana","Tables","","Active"],
            ["Grade 3","Arabic","Essay: My School — 5 sentences minimum","2026-09-24","Mr. Tarek","Essay","Handwritten","Active"],
            ["Grade 3","English","Read unit 3 story and answer comprehension Qs 1-5","2026-09-25","Ms. Sara","Reading","","Active"],
            ["Grade 4","Math","Long division exercises page 45 Qs 1-8","2026-09-23","Ms. Hana","Exercises","Show work","Active"],
            ["Grade 4","Science","Research: 3 facts about the solar system","2026-09-24","Mr. Omar","Research","Typed or handwritten","Active"],
            ["Grade 4","Arabic","Grammar: identify nouns in 10 sentences page 52","2026-09-25","Ms. Fatima","Grammar","","Active"],
            ["Grade 5","Math","Fractions practice workbook page 52-53","2026-09-23","Ms. Hana","Workbook","","Active"],
            ["Grade 5","Arabic","Read and summarize lesson 4 in your own words","2026-09-24","Mr. Tarek","Summary","Handwritten","Active"],
            ["Grade 5","English","Vocabulary list 20 words — memorize for test Thursday","2026-09-25","Ms. Sara","Memorization","","Active"],
            ["Grade 5","Science","Draw and label parts of a plant","2026-09-26","Ms. Hana","Drawing","Use colors","Active"],
            ["Grade 6","Math","Ratio and proportion — page 61 Qs 1-12","2026-09-23","Mr. Khaled","Exercises","Show work","Active"],
            ["Grade 6","Arabic","Essay: The importance of reading — 150 words","2026-09-24","Ms. Fatima","Essay","Handwritten","Active"],
            ["Grade 6","English","Read chapter 4 of the reader — answer Qs 1-6","2026-09-25","Ms. Sara","Reading","","Active"],
            ["Grade 6","Science","Research report: Water cycle — 1 page with diagram","2026-09-26","Mr. Omar","Report","Include diagram","Active"],
            ["Grade 7","Math","Chapter 4 exercises page 67 questions 1-15","2026-09-23","Mr. Ahmed","Exercises","Bring calculator","Active"],
            ["Grade 7","Arabic","Essay on importance of education — 200 words","2026-09-24","Ms. Fatima","Essay","Handwritten","Active"],
            ["Grade 7","English","Read unit 5 pages 34-40 answer comprehension questions","2026-09-25","Ms. Sara","Reading","","Active"],
            ["Grade 7","Science","Research report on digestive system — 1 page","2026-09-26","Mr. Omar","Report","Typed or handwritten","Active"],
            ["Grade 7","Social Studies","Study chapter 6 for quiz on Thursday","2026-09-25","Ms. Nadia","Study","","Active"],
            ["Grade 7","French","Learn vocabulary list unit 2 — 15 words","2026-09-24","Ms. Claire","Memorization","","Active"],
            ["Grade 8","Math","Algebra worksheet — equations and inequalities page 44","2026-09-23","Mr. Khaled","Worksheet","Show all steps","Active"],
            ["Grade 8","Physics","Lab report on electricity experiment","2026-09-24","Mr. Hassan","Lab Report","Include diagrams","Active"],
            ["Grade 8","English","Finish reading chapter 7 — 10 lines summary","2026-09-25","Ms. Sara","Reading","","Active"],
            ["Grade 8","Chemistry","Study periodic table groups 1-3 — quiz Sunday","2026-09-28","Mr. Hassan","Study","","Active"],
            ["Grade 8","Biology","Draw and label the cell: animal and plant","2026-09-26","Mr. Omar","Drawing","Use colors + labels","Active"],
            ["Grade 9","Math","Quadratic equations worksheet page 78 Qs 1-10","2026-09-23","Mr. Ahmed","Worksheet","Show all steps","Active"],
            ["Grade 9","Physics","Newton's laws — summarize and give 2 examples each","2026-09-24","Mr. Hassan","Summary","","Active"],
            ["Grade 9","Arabic","Literary analysis of poem — page 90 (2 paragraphs)","2026-09-25","Ms. Fatima","Analysis","Handwritten","Active"],
            ["Grade 9","Chemistry","Balancing chemical equations — worksheet page 55","2026-09-26","Mr. Hassan","Worksheet","Show work","Active"],
            ["Grade 10","Math","Trigonometry — sin/cos/tan exercises page 92 Qs 1-8","2026-09-23","Mr. Ahmed","Exercises","Use calculator","Active"],
            ["Grade 10","Physics","Momentum and energy — problems page 110 Qs 1-5","2026-09-24","Mr. Hassan","Problems","Show full solution","Active"],
            ["Grade 10","English","300-word argumentative essay: Social media pros/cons","2026-09-25","Ms. Sara","Essay","Typed preferred","Active"],
            ["Grade 11","Math","Calculus — differentiation exercises chapter 3","2026-09-23","Mr. Ahmed","Exercises","Show all steps","Active"],
            ["Grade 11","Chemistry","Organic chemistry — naming compounds worksheet","2026-09-24","Mr. Hassan","Worksheet","","Active"],
            ["Grade 11","Arabic","Research: An Egyptian literary figure — 2 pages","2026-09-25","Ms. Fatima","Research","Handwritten","Active"],
            ["Grade 12","Math","Past exam paper 2025 — full paper attempt","2026-09-23","Mr. Ahmed","Exam Practice","Timed: 3 hours","Active"],
            ["Grade 12","Physics","Revision: Electricity chapter summary notes","2026-09-24","Mr. Hassan","Revision","","Active"],
            ["Grade 12","English","University application essay — topic of your choice","2026-09-25","Ms. Sara","Essay","500 words","Active"],
        ]
        s.update([h[0]] + d, value_input_option="USER_ENTERED")
        hdr(s, 8, C["dk"])
        gc_map = {"KG1":C["pu"],"KG2":C["pu"],"Grade 1":C["bl"],"Grade 2":C["bl"],"Grade 3":C["bl"],
                  "Grade 4":C["lgr"],"Grade 5":C["lgr"],"Grade 6":C["lgr"],
                  "Grade 7":C["amb"],"Grade 8":C["amb"],"Grade 9":C["amb"],
                  "Grade 10":"#FED7AA","Grade 11":"#FED7AA","Grade 12":"#FED7AA"}
        for i,row in enumerate(d,2):
            bg = gc_map.get(row[0], C["wh"])
            color_range(s,i,1,i,1,bg,"#000000",True)
            if row[7]=="Active": color_range(s,i,8,i,8,C["lgr"],"#15803D",True)
        s.set_basic_filter()
        results.append("Homework: 48 rows")

        # ── 2. STUDENTS ──────────────────────────────────────────────────────
        s = tab("Students")
        h2 = ["Student ID","Full Name","Grade","Section","Gender","Parent Name","Parent Phone",
              "Total Fees","Paid","Remaining","Payment Status","Next Due",
              "Days Present","Days Absent","Attendance%","Bus Route","Active"]
        rows2 = [
            ["STU001","Lina Hany El-Shafei","KG1","A","F","Hany El-Shafei","201506667788",42000,14000,28000,"On Track","2026-03-01",17,3,"85%","Route 5","yes"],
            ["STU002","Adam Sherif Mansour","KG1","A","M","Sherif Mansour","201607001122",42000,42000,0,"Paid in Full","N/A",19,1,"95%","Route 6","yes"],
            ["STU003","Kareem Adel Nasser","KG2","A","M","Adel Nasser","201118900880",42000,21000,21000,"On Track","2026-03-01",16,4,"80%","Route 8","yes"],
            ["STU004","Nada Wael Ibrahim","KG2","B","F","Wael Ibrahim","201203110044",42000,42000,0,"Paid in Full","N/A",20,0,"100%","Route 3","yes"],
            ["STU005","Youssef Bassem Reda","Grade 1","A","M","Bassem Reda","201223334455",48000,16000,32000,"On Track","2026-03-01",18,2,"90%","Route 1","yes"],
            ["STU006","Salma Ibrahim Mostafa","Grade 1","A","F","Ibrahim Mostafa","201556667788",48000,48000,0,"Paid in Full","N/A",20,0,"100%","Route 4","yes"],
            ["STU007","Fares Magdy Helal","Grade 2","A","M","Magdy Helal","201203334455",48000,48000,0,"Paid in Full","N/A",20,0,"100%","Route 2","yes"],
            ["STU008","Rana Osama Shawky","Grade 2","B","F","Osama Shawky","201990001122",48000,16000,32000,"Overdue","2025-12-15",12,8,"60%","Route 7","yes"],
            ["STU009","Adam Sherif Gouda","Grade 3","A","M","Sherif Gouda","201405556677",48000,32000,16000,"On Track","2026-03-01",19,1,"95%","Route 5","yes"],
            ["STU010","Noura Khaled Abdallah","Grade 3","A","F","Khaled Abdallah","201778889900",48000,16000,32000,"Overdue","2025-11-01",13,7,"65%","Route 1","yes"],
            ["STU011","Salma Tarek Samir","Grade 4","A","F","Tarek Samir","201112223344",55000,55000,0,"Paid in Full","N/A",20,0,"100%","Route 3","yes"],
            ["STU012","Karim Ehab Mansour","Grade 4","B","M","Ehab Mansour","201102223334",55000,36667,18333,"Payment Due Soon","2026-02-10",16,4,"80%","Route 6","yes"],
            ["STU013","Esraa Sayed Hassan","Grade 5","A","F","Sayed Hassan","201035551012",55000,36667,18333,"On Track","2026-03-01",19,1,"95%","Route 4","yes"],
            ["STU014","Ziad Amr El-Sayed","Grade 5","A","M","Amr El-Sayed","201445556677",55000,36667,18333,"Payment Due Soon","2026-02-01",17,3,"85%","Route 2","yes"],
            ["STU015","Mariam Hassan Farouk","Grade 5","B","F","Hassan Farouk","201334445566",55000,55000,0,"Paid in Full","N/A",20,0,"100%","Route 7","yes"],
            ["STU016","Dina Nader El-Masry","Grade 6","A","F","Nader El-Masry","201001112233",55000,36667,18333,"On Track","2026-03-01",18,2,"90%","Route 8","yes"],
            ["STU017","Hassan Taher Barakat","Grade 6","A","M","Taher Barakat","201607778899",55000,55000,0,"Paid in Full","N/A",20,0,"100%","Route 5","yes"],
            ["STU018","Ahmed Mohamed Hassan","Grade 7","A","M","Mohamed Hassan","201118900880",62000,41334,20666,"On Track","2026-03-01",18,2,"90%","Route 1","yes"],
            ["STU019","Hana Walid Farouk","Grade 7","A","F","Walid Farouk","201334440066",62000,41334,20666,"On Track","2026-03-01",19,1,"95%","Route 3","yes"],
            ["STU020","Karim Nader Soliman","Grade 7","B","M","Nader Soliman","201001112234",62000,41334,20666,"On Track","2026-03-01",20,0,"100%","Route 6","yes"],
            ["STU021","Omar Youssef Ali","Grade 7","B","M","Youssef Ali","201007778888",62000,62000,0,"Paid in Full","N/A",20,0,"100%","Route 2","yes"],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","A","F","Ahmed Ibrahim","201234567890",62000,41334,20666,"Payment Due Soon","2026-02-15",20,0,"100%","Route 4","yes"],
            ["STU023","Mahmoud Sameh Fathy","Grade 8","A","M","Sameh Fathy","201889990011",62000,62000,0,"Paid in Full","N/A",20,0,"100%","Route 7","yes"],
            ["STU024","Layla Khaled Mahmoud","Grade 8","B","F","Khaled Mahmoud","201035551013",62000,20667,41333,"Overdue","2025-12-01",14,6,"70%","Route 1","yes"],
            ["STU025","Ali Hassan El-Gohary","Grade 9","A","M","Hassan El-Gohary","201667778899",62000,41334,20666,"On Track","2026-03-01",18,2,"90%","Route 3","yes"],
            ["STU026","Sara Mostafa El-Khatib","Grade 9","A","F","Mostafa El-Khatib","201304445566",62000,20667,41333,"Overdue","2025-11-15",11,9,"55%","Route 8","yes"],
            ["STU027","Marwa Tarek Helmy","Grade 10","A","F","Tarek Helmy","201503334455",70000,46667,23333,"On Track","2026-03-01",19,1,"95%","Route 5","yes"],
            ["STU028","Badr Amr Khalifa","Grade 10","A","M","Amr Khalifa","201604445566",70000,70000,0,"Paid in Full","N/A",20,0,"100%","Route 2","yes"],
            ["STU029","Nadia Sherif El-Wakil","Grade 11","A","F","Sherif El-Wakil","201705556677",70000,46667,23333,"Payment Due Soon","2026-02-20",17,3,"85%","Route 4","yes"],
            ["STU030","Karim Hassan Sabry","Grade 11","A","M","Hassan Sabry","201806667788",70000,70000,0,"Paid in Full","N/A",20,0,"100%","Route 6","yes"],
            ["STU031","Farah Mahmoud Zaki","Grade 12","A","F","Mahmoud Zaki","201907778899",70000,70000,0,"Paid in Full","N/A",20,0,"100%","Route 1","yes"],
            ["STU032","Omar Khaled El-Sayed","Grade 12","A","M","Khaled El-Sayed","201008889900",70000,23334,46666,"Overdue","2025-10-01",15,5,"75%","Route 3","yes"],
        ]
        s.update([h2] + rows2, value_input_option="USER_ENTERED")
        hdr(s, len(h2), "#14532D")
        sc_colors = {"Paid in Full":(C["lgr"],"#14532D"),"On Track":(C["bl"],"#1E3A8A"),
                     "Payment Due Soon":(C["amb"],"#78350F"),"Overdue":(C["rd"],"#7F1D1D")}
        for i,row in enumerate(rows2,2):
            s_val = row[10]; c = sc_colors.get(s_val)
            if c: color_range(s,i,11,i,11,c[0],c[1],True)
            att = int(str(row[14]).replace('%','') or 0)
            if att>=90: color_range(s,i,15,i,15,C["lgr"],"#14532D",True)
            elif att>=75: color_range(s,i,15,i,15,C["amb"],"#78350F")
            else: color_range(s,i,15,i,15,C["rd"],"#7F1D1D")
            g = row[2]
            bg = C["pu"] if "KG" in g else (C["bl"] if int(''.join(filter(str.isdigit,g)) or '0')<=6 else C["amb"])
            color_range(s,i,3,i,3,bg,"#000000",True)
        s.set_basic_filter()
        results.append("Students: 32 rows")

        # ── 3. PARENTS ───────────────────────────────────────────────────────
        s = tab("Parents")
        h3 = ["Name","Phone","Grade","Active","Student ID","Student Name","Relationship"]
        rows3 = [
            ["Hany El-Shafei","201506667788","KG1","yes","STU001","Lina Hany El-Shafei","Father"],
            ["Sherif Mansour","201607001122","KG1","yes","STU002","Adam Sherif Mansour","Father"],
            ["Adel Nasser","201118900880","KG2","yes","STU003","Kareem Adel Nasser","Father"],
            ["Wael Ibrahim","201203110044","KG2","yes","STU004","Nada Wael Ibrahim","Father"],
            ["Bassem Reda","201223334455","Grade 1","yes","STU005","Youssef Bassem Reda","Father"],
            ["Ibrahim Mostafa","201556667788","Grade 1","yes","STU006","Salma Ibrahim Mostafa","Father"],
            ["Magdy Helal","201203334455","Grade 2","yes","STU007","Fares Magdy Helal","Father"],
            ["Osama Shawky","201990001122","Grade 2","yes","STU008","Rana Osama Shawky","Father"],
            ["Sherif Gouda","201405556677","Grade 3","yes","STU009","Adam Sherif Gouda","Father"],
            ["Khaled Abdallah","201778889900","Grade 3","yes","STU010","Noura Khaled Abdallah","Father"],
            ["Tarek Samir","201112223344","Grade 4","yes","STU011","Salma Tarek Samir","Father"],
            ["Ehab Mansour","201102223334","Grade 4","yes","STU012","Karim Ehab Mansour","Father"],
            ["Sayed Hassan","201035551012","Grade 5","yes","STU013","Esraa Sayed Hassan","Father"],
            ["Amr El-Sayed","201445556677","Grade 5","yes","STU014","Ziad Amr El-Sayed","Father"],
            ["Hassan Farouk","201334445566","Grade 5","yes","STU015","Mariam Hassan Farouk","Father"],
            ["Nader El-Masry","201001112233","Grade 6","yes","STU016","Dina Nader El-Masry","Father"],
            ["Taher Barakat","201607778899","Grade 6","yes","STU017","Hassan Taher Barakat","Father"],
            ["Mohamed Hassan","201118900880","Grade 7","yes","STU018","Ahmed Mohamed Hassan","Father"],
            ["Walid Farouk","201334440066","Grade 7","yes","STU019","Hana Walid Farouk","Father"],
            ["Youssef Ali","201007778888","Grade 7","yes","STU021","Omar Youssef Ali","Father"],
            ["Ahmed Ibrahim","201234567890","Grade 8","yes","STU022","Nour Ahmed Ibrahim","Father"],
            ["Sameh Fathy","201889990011","Grade 8","yes","STU023","Mahmoud Sameh Fathy","Father"],
            ["Hassan El-Gohary","201667778899","Grade 9","yes","STU025","Ali Hassan El-Gohary","Father"],
            ["Tarek Helmy","201503334455","Grade 10","yes","STU027","Marwa Tarek Helmy","Father"],
            ["Sherif El-Wakil","201705556677","Grade 11","yes","STU029","Nadia Sherif El-Wakil","Father"],
            ["Mahmoud Zaki","201907778899","Grade 12","yes","STU031","Farah Mahmoud Zaki","Father"],
            ["Ahmed Mohamed (Test)","201118900880","Grade 7","yes","STU018","Ahmed Mohamed Hassan","Father"],
            ["Sara Khaled (Test)","201234567890","Grade 8","yes","STU022","Nour Ahmed Ibrahim","Mother"],
            ["Test Parent","201035551012","Grade 5","yes","STU013","Esraa Sayed Hassan","Father"],
        ]
        s.update([h3]+rows3, value_input_option="USER_ENTERED")
        hdr(s,7,"#1D4ED8")
        for i,row in enumerate(rows3,2):
            g=row[2]
            bg=C["pu"] if "KG" in g else (C["bl"] if int(''.join(filter(str.isdigit,g)) or '0')<=6 else C["amb"])
            color_range(s,i,3,i,3,bg,"#000000",True)
            color_range(s,i,4,i,4,C["lgr"] if row[3]=="yes" else C["rd"],"#000000",True)
        results.append("Parents: 29 rows")

        # ── 4. EXAM RESULTS ──────────────────────────────────────────────────
        s = tab("exam")
        h4 = ["Student ID","Student Name","Grade","Subject","Score","Total","Percentage","Grade Letter","Rank","Exam Date","Term","Teacher Notes"]
        rows4 = [
            ["STU018","Ahmed Mohamed Hassan","Grade 7","Math",95,100,"95%","A","1st","2026-09-15","Term 1","Excellent performance"],
            ["STU018","Ahmed Mohamed Hassan","Grade 7","Arabic",88,100,"88%","B+","3rd","2026-09-15","Term 1","Good essay writing"],
            ["STU018","Ahmed Mohamed Hassan","Grade 7","English",92,100,"92%","A-","2nd","2026-09-15","Term 1","Strong comprehension"],
            ["STU018","Ahmed Mohamed Hassan","Grade 7","Science",85,100,"85%","B","4th","2026-09-15","Term 1","Needs more lab practice"],
            ["STU018","Ahmed Mohamed Hassan","Grade 7","Social Studies",79,100,"79%","C+","6th","2026-09-15","Term 1","Study maps more"],
            ["STU021","Omar Youssef Ali","Grade 7","Math",98,100,"98%","A+","1st","2026-09-15","Term 1","Top of class"],
            ["STU021","Omar Youssef Ali","Grade 7","Arabic",94,100,"94%","A","1st","2026-09-15","Term 1","Exceptional"],
            ["STU021","Omar Youssef Ali","Grade 7","English",96,100,"96%","A","1st","2026-09-15","Term 1","Exceptional writing"],
            ["STU021","Omar Youssef Ali","Grade 7","Science",91,100,"91%","A-","1st","2026-09-15","Term 1","Strong analytical skills"],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","Math",78,100,"78%","C+","8th","2026-09-15","Term 1","Needs algebra practice"],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","Arabic",91,100,"91%","A-","2nd","2026-09-15","Term 1","Excellent writing"],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","English",84,100,"84%","B","5th","2026-09-15","Term 1","Good reader"],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","Physics",76,100,"76%","C+","9th","2026-09-15","Term 1","Review electricity chapter"],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","Chemistry",82,100,"82%","B-","6th","2026-09-15","Term 1","Periodic table needs work"],
            ["STU023","Mahmoud Sameh Fathy","Grade 8","Math",96,100,"96%","A","1st","2026-09-15","Term 1","Top of class"],
            ["STU023","Mahmoud Sameh Fathy","Grade 8","Physics",90,100,"90%","A-","2nd","2026-09-15","Term 1","Excellent lab skills"],
            ["STU023","Mahmoud Sameh Fathy","Grade 8","English",93,100,"93%","A","1st","2026-09-15","Term 1","Strong writing"],
            ["STU013","Esraa Sayed Hassan","Grade 5","Math",90,100,"90%","A-","2nd","2026-09-15","Term 1","Great improvement"],
            ["STU013","Esraa Sayed Hassan","Grade 5","Arabic",83,100,"83%","B","4th","2026-09-15","Term 1","Good handwriting"],
            ["STU013","Esraa Sayed Hassan","Grade 5","English",88,100,"88%","B+","3rd","2026-09-15","Term 1","Excellent vocabulary"],
            ["STU013","Esraa Sayed Hassan","Grade 5","Science",77,100,"77%","C+","7th","2026-09-15","Term 1","Review plant unit"],
            ["STU025","Ali Hassan El-Gohary","Grade 9","Math",88,100,"88%","B+","3rd","2026-09-15","Term 1","Good problem solver"],
            ["STU025","Ali Hassan El-Gohary","Grade 9","Physics",79,100,"79%","C+","7th","2026-09-15","Term 1","Review Newton's laws"],
            ["STU025","Ali Hassan El-Gohary","Grade 9","English",95,100,"95%","A","1st","2026-09-15","Term 1","Best in class English"],
            ["STU025","Ali Hassan El-Gohary","Grade 9","Arabic",92,100,"92%","A-","2nd","2026-09-15","Term 1","Strong literature skills"],
            ["STU025","Ali Hassan El-Gohary","Grade 9","Chemistry",84,100,"84%","B","4th","2026-09-15","Term 1","Good lab work"],
            ["STU027","Marwa Tarek Helmy","Grade 10","Math",91,100,"91%","A-","2nd","2026-09-15","Term 1","Strong in trigonometry"],
            ["STU027","Marwa Tarek Helmy","Grade 10","English",94,100,"94%","A","1st","2026-09-15","Term 1","Excellent essay writer"],
            ["STU027","Marwa Tarek Helmy","Grade 10","Physics",82,100,"82%","B-","5th","2026-09-15","Term 1","Work on momentum unit"],
        ]
        s.update([h4]+rows4, value_input_option="USER_ENTERED")
        hdr(s,12,"#065F46")
        gc_map2={"A+":"#DCFCE7","A":"#DCFCE7","A-":"#D1FAE5","B+":"#DBEAFE","B":"#DBEAFE","B-":"#E0F2FE","C+":"#FEF3C7","C":"#FEF3C7","C-":"#FEE2E2","D":"#FEE2E2","F":"#FCA5A5"}
        fc_map2={"A+":"#14532D","A":"#14532D","A-":"#065F46","B+":"#1E3A8A","B":"#1E3A8A","B-":"#0C4A6E","C+":"#78350F","C":"#78350F","C-":"#7F1D1D","D":"#7F1D1D","F":"#991B1B"}
        for i,row in enumerate(rows4,2):
            lg=row[7]; bg2=gc_map2.get(lg); fc2=fc_map2.get(lg)
            if bg2: color_range(s,i,8,i,8,bg2,fc2,True)
        results.append("Exam Results: 29 rows")

        # ── 5. FEES (bot-compatible) ──────────────────────────────────────────
        s = tab("Fees")
        h5=["Student ID","Student Name","Grade","Parent Name","Parent Phone","Total Fees","Amount Paid","Remaining","Payment Status","Next Payment Due","Last Payment Date","Days Present","Total School Days"]
        rows5=[
            ["STU001","Lina Hany El-Shafei","KG1","Hany El-Shafei","201506667788",42000,14000,28000,"On Track","2026-03-01","2025-11-01",17,20],
            ["STU002","Adam Sherif Mansour","KG1","Sherif Mansour","201607001122",42000,42000,0,"Paid in Full","N/A","2025-09-01",19,20],
            ["STU003","Kareem Adel Nasser","KG2","Adel Nasser","201118900880",42000,21000,21000,"On Track","2026-03-01","2025-11-01",16,20],
            ["STU004","Nada Wael Ibrahim","KG2","Wael Ibrahim","201203110044",42000,42000,0,"Paid in Full","N/A","2025-09-01",20,20],
            ["STU005","Youssef Bassem Reda","Grade 1","Bassem Reda","201223334455",48000,16000,32000,"On Track","2026-03-01","2025-11-01",18,20],
            ["STU006","Salma Ibrahim Mostafa","Grade 1","Ibrahim Mostafa","201556667788",48000,48000,0,"Paid in Full","N/A","2025-09-20",20,20],
            ["STU007","Fares Magdy Helal","Grade 2","Magdy Helal","201203334455",48000,48000,0,"Paid in Full","N/A","2025-09-25",20,20],
            ["STU008","Rana Osama Shawky","Grade 2","Osama Shawky","201990001122",48000,16000,32000,"Overdue","2025-12-15","2025-09-15",12,20],
            ["STU009","Adam Sherif Gouda","Grade 3","Sherif Gouda","201405556677",48000,32000,16000,"On Track","2026-03-01","2025-11-01",19,20],
            ["STU010","Noura Khaled Abdallah","Grade 3","Khaled Abdallah","201778889900",48000,16000,32000,"Overdue","2025-11-01","2025-09-01",13,20],
            ["STU011","Salma Tarek Samir","Grade 4","Tarek Samir","201112223344",55000,55000,0,"Paid in Full","N/A","2025-09-15",20,20],
            ["STU012","Karim Ehab Mansour","Grade 4","Ehab Mansour","201102223334",55000,36667,18333,"Payment Due Soon","2026-02-10","2025-10-10",16,20],
            ["STU013","Esraa Sayed Hassan","Grade 5","Sayed Hassan","201035551012",55000,36667,18333,"On Track","2026-03-01","2025-11-01",19,20],
            ["STU014","Ziad Amr El-Sayed","Grade 5","Amr El-Sayed","201445556677",55000,36667,18333,"Payment Due Soon","2026-02-01","2025-10-20",17,20],
            ["STU015","Mariam Hassan Farouk","Grade 5","Hassan Farouk","201334445566",55000,55000,0,"Paid in Full","N/A","2025-09-15",20,20],
            ["STU016","Dina Nader El-Masry","Grade 6","Nader El-Masry","201001112233",55000,36667,18333,"On Track","2026-03-01","2025-11-01",18,20],
            ["STU017","Hassan Taher Barakat","Grade 6","Taher Barakat","201607778899",55000,55000,0,"Paid in Full","N/A","2025-09-01",20,20],
            ["STU018","Ahmed Mohamed Hassan","Grade 7","Mohamed Hassan","201118900880",62000,41334,20666,"On Track","2026-03-01","2025-11-01",18,20],
            ["STU019","Hana Walid Farouk","Grade 7","Walid Farouk","201334440066",62000,41334,20666,"On Track","2026-03-01","2025-11-01",19,20],
            ["STU020","Karim Nader Soliman","Grade 7","Nader Soliman","201001112234",62000,41334,20666,"On Track","2026-03-01","2025-11-01",20,20],
            ["STU021","Omar Youssef Ali","Grade 7","Youssef Ali","201007778888",62000,62000,0,"Paid in Full","N/A","2025-10-01",20,20],
            ["STU022","Nour Ahmed Ibrahim","Grade 8","Ahmed Ibrahim","201234567890",62000,41334,20666,"Payment Due Soon","2026-02-15","2025-10-15",20,20],
            ["STU023","Mahmoud Sameh Fathy","Grade 8","Sameh Fathy","201889990011",62000,62000,0,"Paid in Full","N/A","2025-10-05",20,20],
            ["STU024","Layla Khaled Mahmoud","Grade 8","Khaled Mahmoud","201035551013",62000,20667,41333,"Overdue","2025-12-01","2025-09-01",14,20],
            ["STU025","Ali Hassan El-Gohary","Grade 9","Hassan El-Gohary","201667778899",62000,41334,20666,"On Track","2026-03-01","2025-11-01",18,20],
            ["STU026","Sara Mostafa El-Khatib","Grade 9","Mostafa El-Khatib","201304445566",62000,20667,41333,"Overdue","2025-11-15","2025-09-10",11,20],
            ["STU027","Marwa Tarek Helmy","Grade 10","Tarek Helmy","201503334455",70000,46667,23333,"On Track","2026-03-01","2025-11-01",19,20],
            ["STU028","Badr Amr Khalifa","Grade 10","Amr Khalifa","201604445566",70000,70000,0,"Paid in Full","N/A","2025-09-01",20,20],
            ["STU029","Nadia Sherif El-Wakil","Grade 11","Sherif El-Wakil","201705556677",70000,46667,23333,"Payment Due Soon","2026-02-20","2025-10-20",17,20],
            ["STU030","Karim Hassan Sabry","Grade 11","Hassan Sabry","201806667788",70000,70000,0,"Paid in Full","N/A","2025-09-01",20,20],
            ["STU031","Farah Mahmoud Zaki","Grade 12","Mahmoud Zaki","201907778899",70000,70000,0,"Paid in Full","N/A","2025-09-01",20,20],
            ["STU032","Omar Khaled El-Sayed","Grade 12","Khaled El-Sayed","201008889900",70000,23334,46666,"Overdue","2025-10-01","2025-09-01",15,20],
        ]
        s.update([h5]+rows5, value_input_option="USER_ENTERED")
        hdr(s,13,"#92400E")
        for i,row in enumerate(rows5,2):
            c=sc_colors.get(row[8])
            if c: color_range(s,i,9,i,9,c[0],c[1],True)
        results.append("Fees: 32 rows")

        # ── 6. BUS ROUTES ────────────────────────────────────────────────────
        s = tab("BusRoutes")
        h6=["Route","Area / Stops","Morning Pickup","Arrives School","Departs School","Home Dropoff","Driver Name","Driver Phone","Capacity","Registered","Active"]
        rows6=[
            ["Route 1","Maadi - Degla - Zahraa - School","6:45 AM","7:30 AM","2:15 PM","3:00 PM","Ahmed Saber","01012345678",45,12,"yes"],
            ["Route 2","Heliopolis - Roxy - Sheraton - School","6:50 AM","7:30 AM","2:15 PM","3:00 PM","Hassan Nour","01098765432",45,18,"yes"],
            ["Route 3","Zamalek - Mohandessin - Agouza - School","7:00 AM","7:35 AM","2:15 PM","3:10 PM","Mohamed Saad","01123456789",40,10,"yes"],
            ["Route 4","Nasr City - Abbas El Akkad - School","6:40 AM","7:25 AM","2:15 PM","3:00 PM","Khaled Omar","01234567890",45,15,"yes"],
            ["Route 5","6th October - Palm Hills - School","7:15 AM","7:35 AM","2:15 PM","2:35 PM","Samer Fouad","01345678901",50,22,"yes"],
            ["Route 6","Dokki - Giza Square - Sheikh Zayed - School","6:55 AM","7:30 AM","2:15 PM","2:50 PM","Tarek Moussa","01456789012",45,14,"yes"],
            ["Route 7","New Cairo - 5th Settlement - School","6:30 AM","7:25 AM","2:15 PM","3:10 PM","Ayman Rashad","01567890123",45,9,"yes"],
            ["Route 8","Sheikh Zayed - Beverly Hills - School","7:10 AM","7:30 AM","2:15 PM","2:40 PM","Walid Kamal","01678901234",50,20,"yes"],
        ]
        s.update([h6]+rows6, value_input_option="USER_ENTERED")
        hdr(s,11,"#1E40AF")
        bcolors=["#DBEAFE","#DCFCE7","#FEF3C7","#F3E8FF","#FEE2E2","#E0F2FE","#FFF7ED","#FDF4FF"]
        for i,row in enumerate(rows6,2):
            color_range(s,i,1,i,1,bcolors[(i-2)%8],"#000000",True)
        results.append("BusRoutes: 8 rows")

        # ── 7. CANTEEN ───────────────────────────────────────────────────────
        s = tab("Canteen")
        h7=["Category","Item","Description","Price (EGP)","Available Days","Allergens","Halal"]
        rows7=[
            ["Main Meal","Koshary","Classic Egyptian koshary with lentils, rice, pasta",20,"Mon & Wed","Gluten","Yes"],
            ["Main Meal","Macaroni Bechamel","Baked pasta with bechamel sauce and minced meat",22,"Tuesday","Gluten, Dairy","Yes"],
            ["Main Meal","Molokhia & Rice","Green molokhia soup with white rice",20,"Wednesday","None","Yes"],
            ["Main Meal","Grilled Kofta","Grilled beef kofta with bread and salad",30,"Thursday","None","Yes"],
            ["Main Meal","Grilled Chicken","Quarter grilled chicken with rice or bread",35,"Daily","None","Yes"],
            ["Snacks","Cheese Sandwich","Baladi bread with white cheese and tomato",12,"Daily","Gluten, Dairy","Yes"],
            ["Snacks","Falafel Wrap","Falafel in bread with tahini and veggies",12,"Daily","Gluten, Sesame","Yes"],
            ["Snacks","Pizza Slice","Cheese pizza - tomato sauce and mozzarella",20,"Daily","Gluten, Dairy","Yes"],
            ["Snacks","Pasta Box","Pasta with tomato sauce",18,"Daily","Gluten","Yes"],
            ["Drinks","Water Bottle","500ml chilled water",5,"Daily","None","Yes"],
            ["Drinks","Fresh Juice","Orange or mango fresh juice",15,"Daily","None","Yes"],
            ["Drinks","Chocolate Milk","200ml chocolate flavored milk",12,"Daily","Dairy","Yes"],
            ["Drinks","Yogurt Drink","Activia yogurt drink",10,"Daily","Dairy","Yes"],
            ["Dessert","Fruit Cup","Seasonal fresh fruit mix",15,"Daily","None","Yes"],
            ["Dessert","Cake Slice","Homemade sponge cake - changes daily",18,"Daily","Gluten, Dairy, Eggs","Yes"],
            ["Dessert","Chocolate Bar","Kit Kat or similar wafer bar",10,"Daily","Gluten, Dairy, Nuts","Yes"],
        ]
        s.update([h7]+rows7, value_input_option="USER_ENTERED")
        hdr(s,7,"#B45309")
        cat_col={"Main Meal":C["amb"],"Snacks":C["lgr"],"Drinks":C["bl"],"Dessert":C["pu"]}
        for i,row in enumerate(rows7,2):
            bg=cat_col.get(row[0],C["wh"])
            color_range(s,i,1,i,1,bg,"#000000",True)
        results.append("Canteen: 16 rows")

        # ── 8. LIBRARY ───────────────────────────────────────────────────────
        s = tab("Library")
        h8=["Book ID","Book Title","Author","Category","Grade Level","Language","Copies","Available","Status","Borrower ID","Due Date","Shelf"]
        rows8=[
            ["LIB001","Harry Potter and the Philosopher's Stone","J.K. Rowling","Fantasy","Grade 4-8","English",2,2,"Available","—","—","Shelf A1"],
            ["LIB002","The Alchemist","Paulo Coelho","Fiction","Grade 9-12","English",1,1,"Available","—","—","Shelf A2"],
            ["LIB003","Sapiens","Yuval Noah Harari","Non-Fiction","Grade 10-12","English",1,0,"Borrowed","STU025","2026-10-01","Shelf B1"],
            ["LIB004","The Little Prince","Antoine de St-Exupery","Classic","Grade 5-9","English",2,2,"Available","—","—","Shelf A3"],
            ["LIB005","Diary of a Wimpy Kid","Jeff Kinney","Children","Grade 3-6","English",2,1,"Borrowed","STU013","2026-09-28","Shelf C1"],
            ["LIB006","Wonder","R.J. Palacio","Children","Grade 4-7","English",1,1,"Available","—","—","Shelf C2"],
            ["LIB007","Percy Jackson: Lightning Thief","Rick Riordan","Fantasy","Grade 4-8","English",2,1,"Borrowed","STU021","2026-10-05","Shelf A4"],
            ["LIB008","Animal Farm","George Orwell","Classic","Grade 8-12","English",2,2,"Available","—","—","Shelf B2"],
            ["LIB009","Matilda","Roald Dahl","Children","Grade 3-6","English",2,2,"Available","—","—","Shelf C3"],
            ["LIB010","A Brief History of Time","Stephen Hawking","Science","Grade 10-12","English",1,1,"Available","—","—","Shelf D1"],
            ["LIB011","Rich Dad Poor Dad","Robert Kiyosaki","Finance","Grade 10-12","English",1,1,"Available","—","—","Shelf D2"],
            ["LIB012","Rehlet Ibn Battuta","Ibn Battuta","History","Grade 6-12","Arabic",2,2,"Available","—","—","Shelf E1"],
            ["LIB013","Kalila wa Dimna","Ibn Al-Muqaffa","Classic","Grade 5-9","Arabic",2,2,"Available","—","—","Shelf E2"],
            ["LIB014","Al-Ayyam","Taha Hussein","Literature","Grade 9-12","Arabic",1,1,"Available","—","—","Shelf E3"],
            ["LIB015","Zuqaq Al-Middaq","Naguib Mahfouz","Literature","Grade 10-12","Arabic",1,0,"Borrowed","STU027","2026-10-10","Shelf E4"],
        ]
        s.update([h8]+rows8, value_input_option="USER_ENTERED")
        hdr(s,12,"#5B21B6")
        for i,row in enumerate(rows8,2):
            if row[8]=="Available": color_range(s,i,9,i,9,C["lgr"],"#14532D",True)
            else: color_range(s,i,9,i,9,C["rd"],"#7F1D1D",True)
        results.append("Library: 15 rows")

        # ── 9. ADMISSIONS ────────────────────────────────────────────────────
        s = tab("Admissions")
        rows9=[
            ["Registration Status","Open for 2025/2026"],
            ["Application Deadline","March 31, 2026"],
            ["Available Grades","KG1, KG2, Grade 1, Grade 4, Grade 7"],
            ["Waitlist Grades","Grade 3, Grade 6, Grade 10"],
            ["Full Grades","Grade 5, Grade 8, Grade 11, Grade 12"],
            ["",""],
            ["-- FEES STRUCTURE --",""],
            ["KG1 & KG2 Fees","42,000 EGP per year"],
            ["Grade 1-3 Fees","48,000 EGP per year"],
            ["Grade 4-6 Fees","55,000 EGP per year"],
            ["Grade 7-9 Fees","62,000 EGP per year"],
            ["Grade 10-12 Fees","70,000 EGP per year"],
            ["Registration Deposit","5,000 EGP (non-refundable)"],
            ["Payment Plan","3 installments per year"],
            ["",""],
            ["-- REQUIRED DOCUMENTS --",""],
            ["Required Documents","Birth certificate, previous report card, parent ID, 4 photos, medical certificate"],
            ["Application Process","Apply online - submit docs - assessment - management meeting - decision in 5 days"],
            ["",""],
            ["-- CONTACT & LOCATION --",""],
            ["School Location","El Yasmeen Compound, Entrance 1, El Sheikh Zayed, 6th of October City"],
            ["School Phone","02-3796-9155 / 02-3796-9166"],
            ["School WhatsApp","01066253331"],
            ["School Hours","Sunday to Thursday, 7:30 AM to 2:30 PM"],
        ]
        s.update([["Item","Value"]]+rows9, value_input_option="USER_ENTERED")
        hdr(s,2,"#047857")
        for i,row in enumerate(rows9,2):
            if "--" in str(row[0]):
                color_range(s,i,1,i,2,C["dk"],"#FFFFFF",True)
            else:
                color_range(s,i,1,i,1,C["wh"],"#000000",True)
        results.append("Admissions: 24 rows")

        # ── 10. HOW TO USE ───────────────────────────────────────────────────
        s = tab("HOW TO USE")
        guide=[
            ["TAB","PURPOSE","WHO UPDATES IT","HOW BOT USES IT","NOTE"],
            ["Homework","Daily homework per grade","Class Teachers","Parents ask: homework grade 7","Update daily"],
            ["Students","Full student roster","Admin Office","All student data","Source of truth"],
            ["Parents","Parent WhatsApp numbers","Admin Office","WhatsApp broadcasts","Never delete rows"],
            ["Schedule","Weekly timetable","Academic Coordinator","Parents ask: schedule grade 5","Update each term"],
            ["exam","Exam results per student","Subject Teachers","Parents ask: STU018 results","Keep exact tab name"],
            ["Fees","Fee & attendance data","Accounts Dept","Parents ask: fees status","BOT TAB - do not rename"],
            ["Announcements","School announcements","Admin Panel only","Auto WhatsApp broadcast","Via admin panel only"],
            ["BusRoutes","Bus routes & times","Transport Dept","Parents ask: bus route","BOT TAB - do not rename"],
            ["Canteen","Daily menu and prices","Canteen Manager","Parents ask: canteen menu","BOT TAB - do not rename"],
            ["Library","Book catalog","Librarian","Parents ask: library books","BOT TAB - do not rename"],
            ["Admissions","Enrollment info & fees","Admissions Office","Parents ask: admissions","Update each year"],
            ["","","","",""],
            ["IMPORTANT RULES - READ CAREFULLY","","","",""],
            ["Never change column headers","Bot reads exact names - changes break the bot","","","CRITICAL"],
            ["Student IDs","Must be: STU001, STU002 ... STU999","","","CRITICAL"],
            ["Grade names","Exactly: KG1, KG2, Grade 1, Grade 2 ... Grade 12","","","CRITICAL"],
            ["Date format","Always: YYYY-MM-DD (example: 2026-10-15)","","","CRITICAL"],
            ["Active column","Must be: yes or no (lowercase only)","","","CRITICAL"],
            ["Payment Status","Paid in Full / On Track / Payment Due Soon / Overdue","","","CRITICAL"],
            ["Phone numbers","Include country code: 201118900880 (no + sign)","","","CRITICAL"],
        ]
        s.update(guide, value_input_option="USER_ENTERED")
        s.format("A1:E1",{"backgroundColor":{"red":0.118,"green":0.227,"blue":0.545},"textFormat":{"bold":True,"foregroundColor":{"red":1,"green":1,"blue":1},"fontSize":11}})
        s.freeze(rows=1)
        color_range(s,14,1,14,5,"#DC2626","#FFFFFF",True)
        for i in range(15,22):
            color_range(s,i,1,i,1,C["amb"],"#000000",True)
        results.append("HOW TO USE: guide written")

        return jsonify({"status":"✅ DONE","results":results,"tabs_updated":len(results)})

    except Exception as e:
        import traceback
        return jsonify({"status":"ERROR","error":str(e),"trace":traceback.format_exc()}), 500
