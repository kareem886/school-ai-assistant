"""
School AI Assistant - Modern Infinity Language School
Uses Google Sheets REST API directly - no file writing needed
"""
import os, json, re, requests, time
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime
import google.auth
from google.oauth2 import service_account
from google.auth.transport.requests import Request

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
SHEET_ID = "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE"
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

# Google Service Account credentials
SA_CREDS = {
    "type": "service_account",
    "project_id": "school-moderninfinity-ai",
    "private_key_id": "447959bf85cee2f3879d109ae4d3287a2ce01550",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDc0NJ5IgA8uC6M\nVfMoy0kJcG5fJgeDRCeleED3no2OAR6YB72kT8sdwF90OWzcTFDtN5DoJ4iGUOeE\njU87xf5jd1ZHQQDipyw4jsmfEUuXqrRSX2x8+M1Ai2zG0yyr87MoYlKbWq6tvVAa\n+Y8fLh+tt1SLxsu2LuSXxjOSQoFsDNPDVtxXxvbij46Epnot7s0809idJ2wKyD90\nEiLswhImPoByqUnIRr4fetoU7pQkKrzteU1Sb4EoWRSm/qD3VjqZPb3P3tq2X6IF\nZgT+8dNLX36Oxsl1Q9hFmUVAUPsWdDdgu2NJ4DNoRqOR0l3NfBvEi9OCvIfezXwC\nhR5bUXV1AgMBAAECggEAEbo5vNaqtl/irCAQrzH7eVnwy8vnDFYJ/nS5dy0KuBTl\nbUxTyy+fwtHz/TCeCXqeqvNhe2MvHfoRTYOLEfqq2gvq3eS9VYpDwypNdqsstS6p\nHuLWeBsBCWvtbfHmsyBTvgvUuMWujTgMCLpdJTeZUfjhp/4Wp9flwLySsoI11zlz\nOEcJDy4LAzyM4ZN0IiHKM+LGAxE7sq42J7XaYWYjIs/x2bM7+cNhiVC8N7GlmrB6\nKm7NSxK/HnGZYOsZRTHJNh9I7K3eJutRPItl9LR2ADxt6qUtGU90rTTfE7SOTdC6\n3zDoWiPk8MSLuCZq0jJV+DR51Nfh8N6gYPheLj7XWQKBgQDzN2C/OnselmMuBCZl\nzksKwhijC7e1/a+nlCyVpLNYRHRN7Vct3KwFRyw5vMZITxGJySnOmqJDP9izzrMX\nNj2zEQXD8qm+uhhAifejrR33+I9iYMvyn1BIudVSJWP2PAoT2bJ+RYoEK1GiurFL\n2A8NDkq6ISgHz0FV9bqCNYYTnQKBgQDobAfTCpEZ/4Emgokiuh6TYklsgB+Gb+/t\nxtSN5+JCxjntm8y/DmV7VH0VTQSIGdh1LTVDLVYyAa/GIDKd89uMMgU/8VpjWqL8\nCnk59LU4xSVfWYr1dyV6fHb9goIa1v+5DCX14MTr/+oiIcHGR9/OBpWIK/qhvBUv\nPs1Nkx6duQKBgCud7cq9iSDmJWk2M1Ckm06Vmmd7DXokwaCS8R/xBny44garnqvJ\n3EuiBOth0EldbK7CFa5Iivr2cz1jvzhVcOExF1CZrxlWNE02sON4g1xaBhTFeS3M\nnplA0i24M6I1bHQ+MRfdhLywqPJyrUGpil+hmfL4+ffhQkc4BoG4DfUT9AoGAL1zM\nX2I43W5muB1DqtL4phoSUkztn6yx3Od2qxBE2Eyiw1vLZmedoHtAHhYaxU1XAdHb\nl7vmY7xaQGqRRgTKiZAr57LcM4Dl06yitX+7aj0qd3q8yXalKYexi8mLj8KeS+xA\n1BEgr+LvqFLutOQypD5NPHmR0mGMg5stpRBE3ekCgYEAy581horE7dywo3by4W3J\nf+MIwHd4nbmI1iqYh2nJ4jdyPVS/OiziuYEMPvAvqdDSZGNWt2RvcE3noeQFy9G3\nNZCYXBCGk8UeJGW7/5HrSBbR3l6ZXXzVMmOjjeQx4g9XzMP0efWz+uK0d27y6vGZ\ndf6/Umo3s4H9MEMz0a57hl8=\n-----END PRIVATE KEY-----\n",
    "client_email": "school-ai-reader@school-moderninfinity-ai.iam.gserviceaccount.com",
    "client_id": "113880446030698300156",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/school-ai-reader%40school-moderninfinity-ai.iam.gserviceaccount.com",
    "universe_domain": "googleapis.com"
}

# ============================================================
# GOOGLE SHEETS VIA REST API - no file writing needed
# ============================================================
_token_cache = {"token": None, "expires": 0}

def get_google_token():
    """Get OAuth token using service account credentials"""
    if _token_cache["token"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["token"]
    try:
        creds = service_account.Credentials.from_service_account_info(
            SA_CREDS,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        creds.refresh(Request())
        _token_cache["token"] = creds.token
        _token_cache["expires"] = creds.expiry.timestamp() if creds.expiry else time.time() + 3600
        return creds.token
    except Exception as e:
        print(f"Token error: {e}")
        return None

def read_tab(tab_name):
    """Read a Google Sheet tab using REST API"""
    try:
        token = get_google_token()
        if not token:
            print("No token available")
            return []
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{tab_name}"
        headers = {"Authorization": f"Bearer {token}"}
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print(f"Sheets API error: {res.status_code} - {res.text[:200]}")
            return []
        data = res.json()
        values = data.get("values", [])
        if not values:
            return []
        headers_row = values[0]
        rows = []
        for row in values[1:]:
            row_dict = {}
            for i, header in enumerate(headers_row):
                row_dict[header] = row[i] if i < len(row) else ""
            rows.append(row_dict)
        print(f"Read {len(rows)} rows from {tab_name}")
        return rows
    except Exception as e:
        print(f"read_tab error ({tab_name}): {e}")
        return []

def append_row(tab_name, values):
    """Append a row to a Google Sheet tab"""
    try:
        token = get_google_token()
        if not token:
            return False
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{tab_name}:append?valueInputOption=USER_ENTERED"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        creds_write = service_account.Credentials.from_service_account_info(
            SA_CREDS,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        creds_write.refresh(Request())
        headers["Authorization"] = f"Bearer {creds_write.token}"
        payload = {"values": [values]}
        res = requests.post(url, headers=headers, json=payload)
        return res.status_code == 200
    except Exception as e:
        print(f"append_row error: {e}")
        return False

# ============================================================
# WHATSAPP
# ============================================================
def send_whatsapp(to_phone, message):
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}",
               "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
               "to": to_phone, "type": "text", "text": {"body": message}}
    try:
        res = requests.post(META_API_URL, headers=headers, json=payload)
        print(f"WhatsApp to {to_phone}: {res.status_code}")
    except Exception as e:
        print(f"Send error: {e}")

# ============================================================
# AI RESPONSE ENGINE
# ============================================================
def process_message(msg):
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    # HOMEWORK
    if any(w in m for w in ['homework','assignment','واجب','تكليف','hw']):
        grade = None
        for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
            if f'grade {g}' in m or f'الصف {g}' in m or f'صف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف\nمثال: واجب الصف السابع" if is_arabic \
                   else "Please specify the grade\nExample: Homework for Grade 7"
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower()
              and str(r.get("Assignment","")).strip()]
        if not hw:
            return f"لا يوجد واجب لـ{grade} حالياً\n📞 {SCHOOL['phone']}" if is_arabic \
                   else f"No homework found for {grade}\n📞 {SCHOOL['phone']}"
        if is_arabic:
            r = f"📚 واجبات {grade}\n\n"
            for h in hw:
                r += f"• {h.get('Subject','')}: {h.get('Assignment','')}\n"
                r += f"  التسليم: {h.get('Due Date','')}\n\n"
        else:
            r = f"📚 {grade} Homework\n\n"
            for h in hw:
                r += f"• {h.get('Subject','')}: {h.get('Assignment','')}\n"
                r += f"  Due: {h.get('Due Date','')} | {h.get('Teacher','')}\n\n"
        return r.strip()

    # SCHEDULE
    if any(w in m for w in ['schedule','timetable','جدول','حصص']):
        grade = None
        for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
            if f'grade {g}' in m or f'الصف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف" if is_arabic else "Specify the grade\nExample: Grade 7 schedule"
        day_map = {'الأحد':'Sunday','الاثنين':'Monday','الثلاثاء':'Tuesday',
                   'الأربعاء':'Wednesday','الخميس':'Thursday'}
        day = None
        for ar,en in day_map.items():
            if ar in msg: day=en; break
        for en in ['sunday','monday','tuesday','wednesday','thursday']:
            if en in m: day=en.capitalize(); break
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower()]
        if day:
            sched = [r for r in sched if day.lower() in str(r.get("Day","")).lower()]
        if not sched:
            return f"لا يوجد جدول لـ{grade}" if is_arabic else f"No schedule for {grade}"
        if is_arabic:
            r = f"📅 جدول {grade}\n\n"
            for s in sched:
                ps = [str(s.get(f'Period {i}','')) for i in range(1,6) if s.get(f'Period {i}','')]
                r += f"{s.get('Day','')}: {' ← '.join(ps)}\n"
        else:
            r = f"📅 {grade} Schedule\n\n"
            for s in sched:
                ps = [str(s.get(f'Period {i}','')) for i in range(1,6) if s.get(f'Period {i}','')]
                r += f"{s.get('Day','')}: {' → '.join(ps)}\n"
        return r.strip()

    # STUDENT ID
    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID","")).upper()==sid), None)
        if not s:
            return f"رقم {sid} غير موجود\n📞 {SCHOOL['phone']}" if is_arabic \
                   else f"ID {sid} not found\n📞 {SCHOOL['phone']}"
        name=s.get('Student Name',''); grade=s.get('Grade','')
        if any(w in m for w in ['attendance','absent','حضور','غياب']):
            present=int(s.get('Days Present',0)); total=int(s.get('Total School Days',0))
            pct=round((present/total*100) if total>0 else 0,1)
            if is_arabic:
                return f"👤 {name} ({grade})\n📊 حضر: {present}/{total}\nغياب: {total-present}\nنسبة: {pct}%"
            return f"👤 {name} ({grade})\n📊 Present: {present}/{total}\nAbsent: {total-present}\nRate: {pct}%"
        total=int(s.get('Total Fees',0)); paid=int(s.get('Amount Paid',0))
        remaining=int(s.get('Remaining',0)); status=s.get('Payment Status','')
        due=s.get('Next Payment Due','')
        if is_arabic:
            return f"👤 {name} ({grade})\n💰 الإجمالي: {total:,} جنيه\nالمدفوع: {paid:,} جنيه ✅\nالمتبقي: {remaining:,} جنيه\nالحالة: {status}\nالقسط القادم: {due}\n📞 {SCHOOL['phone']}"
        return f"👤 {name} ({grade})\n💰 Total: {total:,} EGP\nPaid: {paid:,} EGP ✅\nRemaining: {remaining:,} EGP\nStatus: {status}\nNext Due: {due}\n📞 {SCHOOL['phone']}"

    # FEES
    if any(w in m for w in ['fee','fees','cost','how much','رسوم','مصاريف','كام','بكام']):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        if is_arabic:
            return (f"💰 رسوم مودرن إنفينيتي 2025/2026\n\n"
                    f"🔸 KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                    f"🔸 الصف 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                    f"🔸 الصف 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                    f"🔸 الصف 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n"
                    f"🔸 الصف 10-12: {info.get('Grade 10-12 Fees','70,000 EGP')}\n\n"
                    f"📅 3 أقساط\n📞 {SCHOOL['phone']}")
        return (f"💰 Modern Infinity Fees 2025/2026\n\n"
                f"🔸 KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                f"🔸 Grade 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                f"🔸 Grade 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                f"🔸 Grade 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n"
                f"🔸 Grade 10-12: {info.get('Grade 10-12 Fees','70,000 EGP')}\n\n"
                f"📅 3 installments\n📞 {SCHOOL['phone']}")

    # ANNOUNCEMENTS
    if any(w in m for w in ['announcement','news','holiday','exam','event',
                             'إعلان','أخبار','امتحان','إجازة']):
        rows = read_tab("Announcements")
        active = [r for r in rows if str(r.get("Status","")).lower()=="active"]
        if not active:
            return "لا توجد إعلانات حالياً" if is_arabic else "No announcements right now"
        if is_arabic:
            r = "📢 إعلانات المدرسة\n\n"
            for a in active:
                r += f"🔔 {a.get('Title','')}\n{a.get('Message','')}\n📅 {a.get('Date','')}\n\n"
        else:
            r = "📢 School Announcements\n\n"
            for a in active:
                r += f"🔔 {a.get('Title','')}\n{a.get('Message','')}\n📅 {a.get('Date','')}\n\n"
        return r.strip()

    # ADMISSIONS
    if any(w in m for w in ['admission','enroll','register','apply','قبول','تسجيل','التحاق']):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        if is_arabic:
            return (f"📋 التسجيل في مودرن إنفينيتي\n\n"
                    f"✅ {info.get('Registration Status','مفتوح')}\n"
                    f"📅 آخر موعد: {info.get('Application Deadline','')}\n\n"
                    f"الصفوف المتاحة:\n{info.get('Available Grades','')}\n\n"
                    f"📞 {SCHOOL['phone']}\n💬 {SCHOOL['whatsapp']}")
        return (f"📋 Admissions at Modern Infinity\n\n"
                f"✅ {info.get('Registration Status','Open')}\n"
                f"📅 Deadline: {info.get('Application Deadline','')}\n\n"
                f"Available:\n{info.get('Available Grades','')}\n\n"
                f"📞 {SCHOOL['phone']}\n💬 {SCHOOL['whatsapp']}")

    # LOCATION
    if any(w in m for w in ['where','location','address','فين','عنوان','موقع']):
        if is_arabic:
            return f"📍 مودرن إنفينيتي\n🏫 {SCHOOL['address']}\n📞 {SCHOOL['phone']}\n⏰ {SCHOOL['admin_hours']}"
        return f"📍 Modern Infinity\n🏫 {SCHOOL['address']}\n📞 {SCHOOL['phone']}\n⏰ {SCHOOL['admin_hours']}"

    # DEFAULT
    if is_arabic:
        return (f"أهلاً بك في مودرن إنفينيتي! 👋\n\n"
                f"📚 الواجبات — واجب الصف السابع\n"
                f"📅 الجداول — جدول الصف الثامن\n"
                f"💰 المصاريف — رسوم الصف الخامس\n"
                f"💳 رصيد الطالب — رصيد STU001\n"
                f"📢 الإعلانات — في إعلانات؟\n"
                f"📋 التسجيل — التسجيل مفتوح؟\n\n"
                f"📞 {SCHOOL['phone']}")
    return (f"Welcome to Modern Infinity! 👋\n\n"
            f"📚 Homework — Homework for Grade 7\n"
            f"📅 Schedule — Grade 8 schedule\n"
            f"💰 Fees — Fees for Grade 5\n"
            f"💳 Fee status — Balance STU001\n"
            f"📢 Announcements — Any news?\n"
            f"📋 Admissions — Is registration open?\n\n"
            f"📞 {SCHOOL['phone']}")

# ============================================================
# WEBHOOK
# ============================================================
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
        print(f"📱 {phone}: {text}")
        if not text or not phone: return "OK", 200
        response = process_message(text)
        send_whatsapp(phone, response)
        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "OK", 200

# ============================================================
# API ROUTES
# ============================================================
@app.route("/api/homework")
def homework_api():
    grade = request.args.get("grade","")
    rows = read_tab("Homework")
    result = [r for r in rows if grade.lower() in str(r.get("Grade","")).lower()
              and str(r.get("Assignment","")).strip()]
    return jsonify({"homework": result, "count": len(result)})

@app.route("/api/announcements")
def announcements_api():
    rows = read_tab("Announcements")
    active = [r for r in rows if str(r.get("Status","")).lower()=="active"]
    return jsonify({"announcements": active, "count": len(active)})

@app.route("/api/fees")
def fees_api():
    rows = read_tab("Admissions")
    info = {r.get("Item",""): r.get("Value","") for r in rows}
    return jsonify({"info": info})

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
        "homework_rows": len(rows)
    })

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")
