"""
School AI Assistant - Modern Infinity Language School
Includes: WhatsApp webhook + Admin Panel + Broadcast announcements
"""
import os, json, re, base64, requests, logging, time, hmac, hashlib
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, render_template_string, session, redirect
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "school-bot-secret-2026")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

# ── Config ─────────────────────────────────────────────────────────────────────────────
SHEET_ID        = os.environ.get("SHEET_ID", "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1358537447338280")
ACCESS_TOKEN    = os.environ.get("ACCESS_TOKEN", "")
VERIFY_TOKEN    = os.environ.get("VERIFY_TOKEN", "schoolai2026")
APP_SECRET      = os.environ.get("APP_SECRET", "")
ADMIN_USER      = "admin"
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

def process_message(msg):
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    greet_kw = ['\u0645\u0631\u062d\u0628\u0627','\u0627\u0647\u0644\u0627','\u0623\u0647\u0644\u0627','\u0647\u0644\u0627','\u0627\u0644\u0633\u0644\u0627\u0645','\u0635\u0628\u0627\u062d','\u0645\u0633\u0627\u0621','hi','hello','hey','\u0633\u0644\u0627\u0645']
    if any(w in m for w in greet_kw):
        if is_arabic:
            return (f"\u0623\u0647\u0644\u0627\u064b \u0648\u0633\u0647\u0644\u0627\u064b \u0641\u064a \u0645\u062f\u0631\u0633\u0629 Modern Infinity \U0001f44b\n\n"
                    f"\u0627\u062e\u062a\u0631 \u0645\u0646 \u0627\u0644\u062e\u062f\u0645\u0627\u062a \u0627\u0644\u062a\u0627\u0644\u064a\u0629:\n\n"
                    f"\U0001f4da *\u0627\u0644\u0648\u0627\u062c\u0628\u0627\u062a* - \u0645\u062b\u0627\u0644: \u0648\u0627\u062c\u0628 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u0628\u0639\n"
                    f"\U0001f4c5 *\u0627\u0644\u062c\u062f\u0648\u0644 \u0627\u0644\u062f\u0631\u0627\u0633\u064a* - \u0645\u062b\u0627\u0644: \u062c\u062f\u0648\u0644 \u0627\u0644\u0635\u0641 \u0627\u0644\u062e\u0627\u0645\u0633\n"
                    f"\U0001f4b0 *\u0627\u0644\u0631\u0633\u0648\u0645 \u0627\u0644\u062f\u0631\u0627\u0633\u064a\u0629*\n"
                    f"\U0001f4b3 *\u0631\u0635\u064a\u062f \u0627\u0644\u0637\u0627\u0644\u0628* - \u0645\u062b\u0627\u0644: STU001\n"
                    f"\U0001f4e2 *\u0627\u0644\u0625\u0639\u0644\u0627\u0646\u0627\u062a*\n"
                    f"\U0001f4cb *\u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0648\u0627\u0644\u0642\u0628\u0648\u0644*\n"
                    f"\U0001f4cd *\u0645\u0648\u0642\u0639 \u0627\u0644\u0645\u062f\u0631\u0633\u0629*\n\n"
                    f"\U0001f4de {SCHOOL['phone']}")
        return (f"Welcome to Modern Infinity Language School! \U0001f44b\n\n"
                f"How can I help you?\n\n"
                f"\U0001f4da Homework - e.g. Homework for Grade 7\n"
                f"\U0001f4c5 Schedule - e.g. Grade 5 schedule\n"
                f"\U0001f4b0 Fees\n"
                f"\U0001f4b3 Balance - e.g. STU001\n"
                f"\U0001f4e2 Announcements\n"
                f"\U0001f4cb Admissions\n"
                f"\U0001f4cd Location\n\n"
                f"\U0001f4de {SCHOOL['phone']}")

    hw_kw = ['homework','assignment','hw','\u0648\u0627\u062c\u0628','\u062a\u0643\u0644\u064a\u0641','\u0648\u0627\u062c\u0628\u0627\u062a','\u0627\u0644\u0648\u0627\u062c\u0628','\u0627\u0644\u0648\u0627\u062c\u0628\u0627\u062a','\u062a\u0643\u0644\u064a\u0641\u0627\u062a']
    if any(w in m for w in hw_kw):
        grade = extract_grade(m)
        if not grade:
            return ("\U0001f4da \u062d\u062f\u062f \u0627\u0644\u0635\u0641 \u0645\u0646 \u0641\u0636\u0644\u0643\n"
                    "\u0645\u062b\u0627\u0644: *\u0648\u0627\u062c\u0628 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u0628\u0639*\n\n"
                    "\u0627\u0644\u0635\u0641\u0648\u0641 \u0627\u0644\u0645\u062a\u0627\u062d\u0629: 1 \u0625\u0644\u0649 12") if is_arabic else \
                   ("\U0001f4da Please specify the grade\n"
                    "Example: *Homework for Grade 7*\n\n"
                    "Available: Grade 1 to 12")
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower() and str(r.get("Assignment","")).strip()]
        if not hw:
            return (f"\U0001f4da \u0644\u0627 \u064a\u0648\u062c\u062f \u0648\u0627\u062c\u0628 \u0645\u0633\u062c\u0651\u0644 \u0644\u0640 {grade} \u062d\u0627\u0644\u064a\u0627\u064b\n"
                    f"\U0001f4de \u062a\u0648\u0627\u0635\u0644 \u0645\u0639 \u0627\u0644\u0645\u062f\u0631\u0633\u0629: {SCHOOL['phone']}") if is_arabic else \
                   (f"\U0001f4da No homework found for {grade}\n"
                    f"\U0001f4de Contact school: {SCHOOL['phone']}")
        if is_arabic:
            r = f"\U0001f4da \u0648\u0627\u062c\u0628\u0627\u062a {grade}\n\n"
            for h in hw:
                r += f"\u2022 {h.get('Subject','')}: {h.get('Assignment','')}\n"
                if h.get('Due Date',''):
                    r += f"  \U0001f4c5 \u0645\u0648\u0639\u062f \u0627\u0644\u062a\u0633\u0644\u064a\u0645: {h.get('Due Date','')}\n"
                r += "\n"
        else:
            r = f"\U0001f4da {grade} Homework\n\n"
            for h in hw:
                r += f"\u2022 {h.get('Subject','')}: {h.get('Assignment','')}\n"
                if h.get('Due Date',''):
                    r += f"  \U0001f4c5 Due: {h.get('Due Date','')}\n"
                r += "\n"
        return r.strip()

    sched_kw = ['schedule','timetable','\u062c\u062f\u0648\u0644','\u062d\u0635\u0635','\u0627\u0644\u062c\u062f\u0648\u0644','\u0627\u0644\u0645\u0648\u0627\u062f','\u062d\u0635\u0629']
    if any(w in m for w in sched_kw):
        grade = extract_grade(m)
        if not grade:
            return ("\U0001f4c5 \u062d\u062f\u062f \u0627\u0644\u0635\u0641 \u0645\u0646 \u0641\u0636\u0644\u0643\n"
                    "\u0645\u062b\u0627\u0644: *\u062c\u062f\u0648\u0644 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u062f\u0633*") if is_arabic else \
                   ("\U0001f4c5 Please specify the grade\n"
                    "Example: *Grade 6 schedule*")
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower()]
        if not sched:
            return (f"\U0001f4c5 \u0644\u0627 \u064a\u0648\u062c\u062f \u062c\u062f\u0648\u0644 \u0644\u0640 {grade}\n\U0001f4de {SCHOOL['phone']}") if is_arabic else \
                   (f"\U0001f4c5 No schedule found for {grade}\n\U0001f4de {SCHOOL['phone']}")
        title = f"\U0001f4c5 \u062c\u062f\u0648\u0644 {grade}\n\n" if is_arabic else f"\U0001f4c5 {grade} Schedule\n\n"
        r = title
        for s in sched:
            ps = [str(s.get(f'Period {i}','')) for i in range(1,6) if s.get(f'Period {i}','')]
            arrow = ' → '
            r += f"{s.get('Day','')}: {arrow.join(ps)}\n"
        return r.strip()

    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID","")).upper() == sid), None)
        if not s:
            return (f"\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0627\u0644\u0637\u0627\u0644\u0628 {sid}\n\U0001f4de {SCHOOL['phone']}") if is_arabic else \
                   (f"\u274c Student {sid} not found\n\U0001f4de {SCHOOL['phone']}")
        name = s.get('Student Name','')
        grade = s.get('Grade','')
        if any(w in m for w in ['attendance','absent','\u062d\u0636\u0648\u0631','\u063a\u064a\u0627\u0628','\u0627\u0644\u063a\u064a\u0627\u0628','\u0627\u0644\u062d\u0636\u0648\u0631']):
            present = int(s.get('Days Present',0) or 0)
            total = int(s.get('Total School Days',0) or 0)
            pct = round((present/total*100) if total>0 else 0,1)
            if is_arabic:
                return f"\U0001f464 {name} ({grade})\n\u2705 \u062d\u0627\u0636\u0631: {present} \u064a\u0648\u0645\n\U0001f4c5 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a: {total} \u064a\u0648\u0645\n\U0001f4ca \u0646\u0633\u0628\u0629 \u0627\u0644\u062d\u0636\u0648\u0631: {pct}%"
            return f"\U0001f464 {name} ({grade})\n\u2705 Present: {present} days\n\U0001f4c5 Total: {total} days\n\U0001f4ca Rate: {pct}%"
        total_fees = int(s.get('Total Fees',0) or 0)
        paid = int(s.get('Amount Paid',0) or 0)
        remaining = int(s.get('Remaining',0) or 0)
        if is_arabic:
            return (f"\U0001f464 {name} ({grade})\n"
                    f"\U0001f4b0 \u0625\u062c\u0645\u0627\u0644\u064a \u0627\u0644\u0631\u0633\u0648\u0645: {total_fees:,} \u062c\u0646\u064a\u0647\n"
                    f"\u2705 \u0627\u0644\u0645\u062f\u0641\u0648\u0639: {paid:,} \u062c\u0646\u064a\u0647\n"
                    f"\u23f3 \u0627\u0644\u0645\u062a\u0628\u0642\u064a: {remaining:,} \u062c\u0646\u064a\u0647\n"
                    f"\U0001f4de {SCHOOL['phone']}")
        return (f"\U0001f464 {name} ({grade})\n"
                f"\U0001f4b0 Total: {total_fees:,} EGP\n"
                f"\u2705 Paid: {paid:,} EGP\n"
                f"\u23f3 Remaining: {remaining:,} EGP\n"
                f"\U0001f4de {SCHOOL['phone']}")

    fees_kw = ['fee','fees','cost','how much','price',
               '\u0631\u0633\u0648\u0645','\u0645\u0635\u0627\u0631\u064a\u0641','\u0643\u0627\u0645','\u0633\u0639\u0631','\u062a\u0643\u0644\u0641\u0629','\u0627\u0644\u0631\u0633\u0648\u0645','\u0627\u0644\u0645\u0635\u0627\u0631\u064a\u0641',
               '\u0628\u0643\u0627\u0645','\u0628\u0642\u062f \u0627\u064a\u0647','\u0642\u062f\u064a\u0647','\u0642\u062f \u0627\u064a\u0647']
    if any(w in m for w in fees_kw):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        if is_arabic:
            _kg_d = '42,000 \u062c\u0646\u064a\u0647'
            _g13_d = '48,000 \u062c\u0646\u064a\u0647'
            _g46_d = '55,000 \u062c\u0646\u064a\u0647'
            _g79_d = '62,000 \u062c\u0646\u064a\u0647'
            _g1012_d = '70,000 \u062c\u0646\u064a\u0647'
            return (f"\U0001f4b0 \u0631\u0633\u0648\u0645 \u0645\u062f\u0631\u0633\u0629 Modern Infinity 2025/2026\n\n"
                    f"\U0001f538 KG: {info.get('KG1 Fees',_kg_d)}\n"
                    f"\U0001f538 \u0627\u0644\u0635\u0641 1-3: {info.get('Grade 1-3 Fees',_g13_d)}\n"
                    f"\U0001f538 \u0627\u0644\u0635\u0641 4-6: {info.get('Grade 4-6 Fees',_g46_d)}\n"
                    f"\U0001f538 \u0627\u0644\u0635\u0641 7-9: {info.get('Grade 7-9 Fees',_g79_d)}\n"
                    f"\U0001f538 \u0627\u0644\u0635\u0641 10-12: {info.get('Grade 10-12 Fees',_g1012_d)}\n\n"
                    f"\U0001f4c5 \u062a\u0642\u0633\u064a\u0645 \u0639\u0644\u0649 3 \u0623\u0642\u0633\u0627\u0637\n"
                    f"\U0001f4de \u0644\u0644\u0627\u0633\u062a\u0641\u0633\u0627\u0631: {SCHOOL['phone']}")
        return (f"\U0001f4b0 Modern Infinity Fees 2025/2026\n\n"
                f"\U0001f538 KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                f"\U0001f538 Grade 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                f"\U0001f538 Grade 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                f"\U0001f538 Grade 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n"
                f"\U0001f538 Grade 10-12: {info.get('Grade 10-12 Fees','70,000 EGP')}\n\n"
                f"\U0001f4c5 3 installments available\n"
                f"\U0001f4de {SCHOOL['phone']}")

    ann_kw = ['announcement','news','holiday','exam','\u0625\u0639\u0644\u0627\u0646','\u0627\u0645\u062a\u062d\u0627\u0646',
              '\u0625\u0639\u0644\u0627\u0646\u0627\u062a','\u0627\u0639\u0644\u0627\u0646\u0627\u062a','\u0627\u062e\u0628\u0627\u0631','\u0623\u062e\u0628\u0627\u0631','\u0627\u0645\u062a\u062d\u0627\u0646\u0627\u062a','\u0627\u062c\u0627\u0632\u0629','\u0625\u062c\u0627\u0632\u0629','\u0645\u0648\u0639\u062f','\u062c\u062f\u064a\u062f']
    if any(w in m for w in ann_kw):
        rows = read_tab("Announcements")
        active = [r for r in rows if str(r.get("Status","")).lower() == "active"]
        if not active:
            return "\U0001f4e2 \u0644\u0627 \u062a\u0648\u062c\u062f \u0625\u0639\u0644\u0627\u0646\u0627\u062a \u062d\u0627\u0644\u064a\u0627\u064b" if is_arabic else "\U0001f4e2 No announcements at this time"
        r = "\U0001f4e2 \u0625\u0639\u0644\u0627\u0646\u0627\u062a \u0627\u0644\u0645\u062f\u0631\u0633\u0629\n\n" if is_arabic else "\U0001f4e2 School Announcements\n\n"
        for a in active:
            r += f"\U0001f514 {a.get('Title','')}\n{a.get('Message','')}\n\U0001f4c5 {a.get('Date','')}\n\n"
        return r.strip()

    admit_kw = ['admission','enroll','register','apply',
                '\u0642\u0628\u0648\u0644','\u062a\u0633\u062c\u064a\u0644','\u0627\u0644\u062a\u0633\u062c\u064a\u0644','\u0627\u0644\u0642\u0628\u0648\u0644','\u0627\u0633\u062c\u0644',
                '\u062a\u0642\u062f\u064a\u0645','\u0627\u0644\u0627\u0644\u062a\u062d\u0627\u0642','\u0627\u0628\u0646\u064a','\u0628\u0646\u062a\u064a']
    if any(w in m for w in admit_kw):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        if is_arabic:
            _open_d = '\u0645\u0641\u062a\u0648\u062d'
            return (f"\U0001f4cb \u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0641\u064a Modern Infinity\n\n"
                    f"\u2705 \u0627\u0644\u062d\u0627\u0644\u0629: {info.get('Registration Status',_open_d)}\n"
                    f"\U0001f4c5 \u0622\u062e\u0631 \u0645\u0648\u0639\u062f: {info.get('Application Deadline','')}\n\n"
                    f"\U0001f4de \u0644\u0644\u062a\u0648\u0627\u0635\u0644: {SCHOOL['phone']}\n"
                    f"\U0001f4cd \u0627\u0644\u0639\u0646\u0648\u0627\u0646: {SCHOOL['address']}")
        return (f"\U0001f4cb Admissions at Modern Infinity\n\n"
                f"\u2705 Status: {info.get('Registration Status','Open')}\n"
                f"\U0001f4c5 Deadline: {info.get('Application Deadline','')}\n\n"
                f"\U0001f4de {SCHOOL['phone']}\n"
                f"\U0001f4cd {SCHOOL['address']}")

    loc_kw = ['location','address','where','map','directions',
              '\u0639\u0646\u0648\u0627\u0646','\u0641\u064a\u0646','\u0645\u0648\u0642\u0639','\u0627\u0644\u0639\u0646\u0648\u0627\u0646','\u0627\u0644\u0645\u0648\u0642\u0639','\u0643\u064a\u0641 \u0627\u0648\u0635\u0644','\u0645\u0643\u0627\u0646\u0643\u0645']
    if any(w in m for w in loc_kw):
        if is_arabic:
            return (f"\U0001f4cd *\u0639\u0646\u0648\u0627\u0646 \u0627\u0644\u0645\u062f\u0631\u0633\u0629*\n{SCHOOL['address']}\n\n"
                    f"\U0001f4de {SCHOOL['phone']}\n"
                    f"\u23f0 \u0645\u0648\u0627\u0639\u064a\u062f \u0627\u0644\u0639\u0645\u0644: {SCHOOL['admin_hours']}")
        return (f"\U0001f4cd *School Location*\n{SCHOOL['address']}\n\n"
                f"\U0001f4de {SCHOOL['phone']}\n"
                f"\u23f0 Hours: {SCHOOL['admin_hours']}")

    if is_arabic:
        return (f"\u0623\u0647\u0644\u0627\u064b! \U0001f44b \u0643\u064a\u0641 \u0623\u0642\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643\u061f\n\n"
                f"\U0001f4da *\u0627\u0644\u0648\u0627\u062c\u0628\u0627\u062a* \u2014 \u0645\u062b\u0627\u0644: \u0648\u0627\u062c\u0628 \u0627\u0644\u0635\u0641 \u0627\u0644\u0633\u0627\u0628\u0639\n"
                f"\U0001f4c5 *\u0627\u0644\u062c\u062f\u0648\u0644* \u2014 \u0645\u062b\u0627\u0644: \u062c\u062f\u0648\u0644 \u0627\u0644\u0635\u0641 \u0627\u0644\u062e\u0627\u0645\u0633\n"
                f"\U0001f4b0 *\u0627\u0644\u0631\u0633\u0648\u0645 \u0627\u0644\u062f\u0631\u0627\u0633\u064a\u0629*\n"
                f"\U0001f4b3 *\u0631\u0635\u064a\u062f \u0627\u0644\u0637\u0627\u0644\u0628* \u2014 \u0645\u062b\u0627\u0644: STU001\n"
                f"\U0001f4e2 *\u0627\u0644\u0625\u0639\u0644\u0627\u0646\u0627\u062a*\n"
                f"\U0001f4cb *\u0627\u0644\u062a\u0633\u062c\u064a\u0644 \u0648\u0627\u0644\u0642\u0628\u0648\u0644*\n"
                f"\U0001f4cd *\u0645\u0648\u0642\u0639 \u0627\u0644\u0645\u062f\u0631\u0633\u0629*\n\n"
                f"\U0001f4de {SCHOOL['phone']}")
    return (f"Welcome to Modern Infinity! \U0001f44b\n\n"
            f"\U0001f4da *Homework* \u2014 e.g. Homework for Grade 7\n"
            f"\U0001f4c5 *Schedule* \u2014 e.g. Grade 5 schedule\n"
            f"\U0001f4b0 *Fees*\n"
            f"\U0001f4b3 *Balance* \u2014 e.g. STU001\n"
            f"\U0001f4e2 *Announcements*\n"
            f"\U0001f4cb *Admissions*\n"
            f"\U0001f4cd *Location*\n\n"
            f"\U0001f4de {SCHOOL['phone']}")

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
            reply = process_message(body)
            send_whatsapp(from_num, reply)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return 'OK', 200

def send_whatsapp(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4096]}}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False

@app.route('/admin', methods=['GET','POST'])
def admin():
    error = None
    success = None
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'login':
            u = request.form.get('username','')
            p = request.form.get('password','')
            if u == ADMIN_USER and hmac.compare_digest(p, ADMIN_PASSWORD):
                session['admin'] = True
                return redirect('/admin')
            error = 'Invalid credentials'
        elif not session.get('admin'):
            return redirect('/admin')
        elif action == 'logout':
            session.pop('admin', None)
            return redirect('/admin')
        elif action == 'broadcast':
            msg = request.form.get('message','').strip()
            grade_filter = request.form.get('grade_filter','all')
            if msg:
                rows = read_tab('Parents')
                targets = rows if grade_filter == 'all' else [r for r in rows if r.get('Grade','') == grade_filter]
                sent = sum(1 for r in targets if send_whatsapp((r.get('WhatsApp','') or r.get('Phone','')).strip(), msg) if r.get('WhatsApp','') or r.get('Phone',''))
                success = f"Sent! Sent to {sent} parents successfully"
    if not session.get('admin'):
        return render_template_string("""<!DOCTYPE html><html><head><title>Admin</title>
<style>body{font-family:Arial;max-width:400px;margin:80px auto;padding:20px}input{width:100%;padding:8px;margin:5px 0;box-sizing:border-box}button{background:#25D366;color:white;border:none;padding:10px;width:100%;cursor:pointer}.err{color:red}</style></head>
<body><h2>&#x1F916; School Bot Admin</h2>{% if error %}<p class="err">{{error}}</p>{% endif %}
<form method="POST"><input type="hidden" name="action" value="login">
<input name="username" placeholder="Username" required><input type="password" name="password" placeholder="Password" required>
<button>Login</button></form></body></html>""", error=error)
    rows = read_tab('Parents')
    grades = sorted(set(r.get('Grade','') for r in rows if r.get('Grade','')))
    return render_template_string("""<!DOCTYPE html><html><head><title>Bot Admin</title>
<style>body{font-family:Arial;max-width:600px;margin:30px auto;padding:20px}textarea{width:100%;height:100px;padding:8px;box-sizing:border-box}
.btn{background:#25D366;color:white;border:none;padding:10px 20px;cursor:pointer;border-radius:4px}.btn2{background:#e74c3c}
.ok{color:green}.info{background:#f5f5f5;padding:10px;border-radius:4px;margin:10px 0}</style></head>
<body><h2>&#x1F916; Modern Infinity Admin</h2>
<div class="info">&#x1F4CA; Parents: {{total}} | &#x1F3EB; Sent Today: {{sent_today}}</div>
{% if success %}<p class="ok">&#x2705; {{success}}</p>{% endif %}
<form method="POST"><input type="hidden" name="action" value="broadcast">
<b>&#x1F4E2; Broadcast</b><br>
Grade: <select name="grade_filter"><option value="all">All</option>{% for g in grades %}<option>{{g}}</option>{% endfor %}</select><br><br>
<textarea name="message" placeholder="Message..." required></textarea><br>
<button class="btn">Send to WhatsApp</button></form>
<form method="POST" style="margin-top:15px"><input type="hidden" name="action" value="logout">
<button class="btn btn2">Logout</button></form></body></html>""",
        total=len(rows), grades=grades, success=success, sent_today=app.config.get('SENT_TODAY',0))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
