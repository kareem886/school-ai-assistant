"""
School AI Assistant - Modern Infinity Language School
======================================================
Complete server with WhatsApp Cloud API via Meta — ALL CREDENTIALS FILLED IN
All credentials pre-filled — just replace YOUR_FULL_ACCESS_TOKEN

WHAT TO DO:
1. Replace YOUR_FULL_ACCESS_TOKEN below with your real token
   (copy it from Meta dashboard — it's longer than what you see on screen)
2. Put this file in your school-ai folder
3. Run: python app_qualid.py
4. Open browser: http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory
import gspread
from google.oauth2.service_account import Credentials
import requests
import json
import os
from datetime import datetime

app = Flask(__name__)

# ============================================================
# CONFIGURATION — Already filled in for Modern Infinity
# ============================================================

# Google Sheets
if os.environ.get("GOOGLE_CREDENTIALS"):
    creds_data = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    with open("credentials.json", "w") as f:
        json.dump(creds_data, f)

CREDENTIALS_FILE = "credentials.json"
SHEET_ID = os.environ.get(
    "SHEET_ID",
    "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE"
)

# School info
SCHOOL = {
    "name": "Modern Infinity Language School",
    "phone": "02-3796-9155 / 02-3796-9166",
    "whatsapp": "01066253331",
    "address": "El Yasmeen Compound, Entrance 1, El Sheikh Zayed, 6th of October City",
    "admin_hours": "Sunday to Thursday, 8:00 AM to 5:00 PM",
}

# ============================================================
# META WHATSAPP CLOUD API CREDENTIALS
# These are YOUR credentials from Meta Developer dashboard
# ============================================================

PHONE_NUMBER_ID = os.environ.get(
    "PHONE_NUMBER_ID",
    "1358537447338280"           # ✅ Your Phone Number ID
)

ACCESS_TOKEN = os.environ.get(
    "ACCESS_TOKEN",
    "EAAXhZABz45F0BSUZC9dYxsnZATJyie0MFBm675rPiaimUZAuOBaz2ZC6AgISJJmEtqtN45j5Sv5UbUulJV6yBHuNaU1MwM94SqTPyTy9ZAR0tZASMXKZBtLD4ZCWfKE77ZCDviSGoaGxnWBG1wRfWZAswBmzSsiqxngbuEgloPOHpKmLFuNSdYEd2VyZBaYAJvPL0skLc89stsVbZAaLWNKXzlhuIP46y3qZA9UZAOygOoiQhqiHXWg8iKJ01iD8i8e8IwIyguLfPzVYHPtIQSnZAEi1sbVZCO1DR"
)

TEST_PHONE = "201118900880"  # Kareem personal WhatsApp for testing

VERIFY_TOKEN = os.environ.get(
    "VERIFY_TOKEN",
    "schoolai2026"               # ✅ Your webhook verify token
)

META_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# ============================================================
# GOOGLE SHEETS
# ============================================================

def read_tab(tab_name):
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=scopes
        )
        client = gspread.authorize(creds)
        wb = client.open_by_key(SHEET_ID)
        return wb.worksheet(tab_name).get_all_records()
    except Exception as e:
        print(f"Sheet error ({tab_name}): {e}")
        return []

def get_sheet(tab_name):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=scopes
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(tab_name)

# ============================================================
# SEND WHATSAPP MESSAGE
# ============================================================

def send_whatsapp(to_phone: str, message: str):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"body": message}
    }
    try:
        res = requests.post(META_API_URL, headers=headers, json=payload)
        if res.status_code == 200:
            print(f"✅ Sent to {to_phone}")
        else:
            print(f"❌ Failed: {res.status_code} — {res.text}")
    except Exception as e:
        print(f"❌ Error: {e}")

# ============================================================
# AI RESPONSE ENGINE — reads live from Google Sheets
# ============================================================

def process_message(msg: str) -> str:
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)

    # HOMEWORK
    if any(w in m for w in ['homework','assignment','واجب','تكليف']):
        grade = None
        for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
            if f'grade {g}' in m or f'الصف {g}' in m or f'صف {g}' in m:
                grade = f'Grade {g}'
                break
        if not grade:
            return "من فضلك حدد الصف\nمثال: واجب الصف السابع" if is_arabic \
                   else "Please specify the grade\nExample: Homework for Grade 7"
        rows = read_tab("Homework")
        hw = [r for r in rows
              if grade.lower() in r.get("Grade","").lower()
              and r.get("Assignment","").strip()]
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
                grade = f'Grade {g}'
                break
        if not grade:
            return "حدد الصف\nمثال: جدول الصف السابع" if is_arabic \
                   else "Specify the grade\nExample: Grade 7 schedule"
        day_map = {'الأحد':'Sunday','الاثنين':'Monday','الثلاثاء':'Tuesday',
                   'الأربعاء':'Wednesday','الخميس':'Thursday'}
        day = None
        for ar,en in day_map.items():
            if ar in msg: day=en; break
        for en in ['sunday','monday','tuesday','wednesday','thursday']:
            if en in m: day=en.capitalize(); break
        rows = read_tab("Schedule")
        sched = [r for r in rows if grade.lower() in r.get("Grade","").lower()]
        if day:
            sched = [r for r in sched if day.lower() in r.get("Day","").lower()]
        if not sched:
            return f"لا يوجد جدول لـ{grade}" if is_arabic else f"No schedule for {grade}"
        if is_arabic:
            r = f"📅 جدول {grade}\n\n"
            for s in sched:
                ps = [s.get(f'Period {i}','') for i in range(1,6) if s.get(f'Period {i}','')]
                r += f"{s.get('Day','')}: {' ← '.join(ps)}\n"
        else:
            r = f"📅 {grade} Schedule\n\n"
            for s in sched:
                ps = [s.get(f'Period {i}','') for i in range(1,6) if s.get(f'Period {i}','')]
                r += f"{s.get('Day','')}: {' → '.join(ps)}\n"
        return r.strip()

    # STUDENT ID
    import re
    id_match = re.search(r'STU\d+', msg.upper())
    if id_match:
        sid = id_match.group()
        rows = read_tab("Students")
        s = next((r for r in rows if str(r.get("Student ID","")).upper()==sid), None)
        if not s:
            return f"رقم الطالب {sid} غير موجود\n📞 {SCHOOL['phone']}" if is_arabic \
                   else f"Student ID {sid} not found\n📞 {SCHOOL['phone']}"
        name = s.get('Student Name','')
        grade = s.get('Grade','')
        if any(w in m for w in ['attendance','absent','حضور','غياب']):
            present = s.get('Days Present',0)
            total = s.get('Total School Days',0)
            pct = round((present/total*100) if total>0 else 0,1)
            st = "ممتاز 🌟" if pct>=95 else "جيد ✅" if pct>=85 else "يحتاج انتباه ⚠️" if pct>=75 else "حرج 🚨"
            if is_arabic:
                return f"👤 {name} ({grade})\n\n📊 الحضور:\n• حضر: {present}/{total}\n• غياب: {total-present}\n• النسبة: {pct}%\n• التقييم: {st}"
            st_en = "Excellent 🌟" if pct>=95 else "Good ✅" if pct>=85 else "Needs Attention ⚠️" if pct>=75 else "Critical 🚨"
            return f"👤 {name} ({grade})\n\n📊 Attendance:\n• Present: {present}/{total}\n• Absent: {total-present}\n• Rate: {pct}%\n• Status: {st_en}"
        total=s.get('Total Fees',0); paid=s.get('Amount Paid',0)
        remaining=s.get('Remaining',0); status=s.get('Payment Status','')
        due=s.get('Next Payment Due','')
        if is_arabic:
            return (f"👤 {name} ({grade})\n\n💰 المصاريف:\n"
                    f"• الإجمالي: {int(total):,} جنيه\n• المدفوع: {int(paid):,} جنيه ✅\n"
                    f"• المتبقي: {int(remaining):,} جنيه\n• الحالة: {status}\n• القسط القادم: {due}\n\n📞 {SCHOOL['phone']}")
        return (f"👤 {name} ({grade})\n\n💰 Fees:\n"
                f"• Total: {int(total):,} EGP\n• Paid: {int(paid):,} EGP ✅\n"
                f"• Remaining: {int(remaining):,} EGP\n• Status: {status}\n• Next Due: {due}\n\n📞 {SCHOOL['phone']}")

    # FEES (general)
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
                    f"📅 3 أقساط\n🏦 مقدم: {info.get('Registration Deposit','5,000 EGP')}\n📞 {SCHOOL['phone']}")
        return (f"💰 Modern Infinity Fees 2025/2026\n\n"
                f"🔸 KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                f"🔸 Grade 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                f"🔸 Grade 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                f"🔸 Grade 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n"
                f"🔸 Grade 10-12: {info.get('Grade 10-12 Fees','70,000 EGP')}\n\n"
                f"📅 3 installments\n🏦 Deposit: {info.get('Registration Deposit','5,000 EGP')}\n📞 {SCHOOL['phone']}")

    # ANNOUNCEMENTS
    if any(w in m for w in ['announcement','news','holiday','exam','event',
                             'إعلان','أخبار','امتحان','إجازة']):
        rows = read_tab("Announcements")
        active = [r for r in rows if r.get("Status","").lower()=="active"]
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
                    f"المستندات:\n{info.get('Required Documents','')}\n\n"
                    f"📞 {SCHOOL['phone']}\n💬 {SCHOOL['whatsapp']}")
        return (f"📋 Admissions at Modern Infinity\n\n"
                f"✅ {info.get('Registration Status','Open')}\n"
                f"📅 Deadline: {info.get('Application Deadline','')}\n\n"
                f"Available:\n{info.get('Available Grades','')}\n\n"
                f"Documents needed:\n{info.get('Required Documents','')}\n\n"
                f"📞 {SCHOOL['phone']}\n💬 {SCHOOL['whatsapp']}")

    # LOCATION
    if any(w in m for w in ['where','location','address','contact','phone',
                             'فين','عنوان','موقع','تليفون']):
        if is_arabic:
            return (f"📍 مدرسة مودرن إنفينيتي\n\n🏫 {SCHOOL['address']}\n\n"
                    f"📞 {SCHOOL['phone']}\n💬 {SCHOOL['whatsapp']}\n\n⏰ {SCHOOL['admin_hours']}")
        return (f"📍 Modern Infinity Language School\n\n🏫 {SCHOOL['address']}\n\n"
                f"📞 {SCHOOL['phone']}\n💬 {SCHOOL['whatsapp']}\n\n⏰ {SCHOOL['admin_hours']}")

    # DEFAULT
    if is_arabic:
        return (f"أهلاً بك في مودرن إنفينيتي! 👋\n\n"
                f"📚 الواجبات — واجب الصف السابع\n"
                f"📅 الجداول — جدول الصف الثامن الأحد\n"
                f"💰 المصاريف — رسوم الصف الخامس\n"
                f"💳 رصيد — رصيد STU001\n"
                f"✅ الحضور — حضور STU001\n"
                f"📢 الإعلانات — في إعلانات؟\n"
                f"📋 التسجيل — التسجيل مفتوح؟\n\n📞 {SCHOOL['phone']}")
    return (f"Welcome to Modern Infinity! 👋\n\n"
            f"📚 Homework — Homework for Grade 7\n"
            f"📅 Schedule — Grade 8 schedule Sunday\n"
            f"💰 Fees — Fees for Grade 5\n"
            f"💳 Fee status — Balance STU001\n"
            f"✅ Attendance — Attendance STU001\n"
            f"📢 Announcements — Any news?\n"
            f"📋 Admissions — Is registration open?\n\n📞 {SCHOOL['phone']}")

# ============================================================
# WHATSAPP WEBHOOK — Meta sends messages here
# ============================================================

@app.route("/webhook", methods=["GET"])
def verify():
    """Meta verifies your webhook URL with this GET request."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive():
    """Receive WhatsApp messages and send AI responses."""
    try:
        data = request.json

        # Navigate Meta's JSON structure
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

        # Generate and send AI response
        response = process_message(text)
        send_whatsapp(phone, response)

        # Log enrollment inquiries
        if any(w in text.lower() for w in ['enroll','register','تسجيل','قبول']):
            try:
                sheet = get_sheet("Inquiries")
                sheet.append_row([
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "WhatsApp", phone, "Unknown", "Via WhatsApp", text,
                    "New — From WhatsApp"
                ])
            except: pass

        return "OK", 200
    except Exception as e:
        print(f"Error: {e}")
        return "OK", 200

# ============================================================
# BROWSER DEMO ROUTES
# ============================================================

@app.route("/api/homework")
def homework():
    grade = request.args.get("grade","")
    rows = read_tab("Homework")
    result = [r for r in rows
              if grade.lower() in r.get("Grade","").lower()
              and r.get("Assignment","").strip()]
    return jsonify({"homework": result, "count": len(result)})

@app.route("/api/schedule")
def schedule():
    grade = request.args.get("grade","")
    day = request.args.get("day","all")
    rows = read_tab("Schedule")
    result = [r for r in rows if grade.lower() in r.get("Grade","").lower()]
    if day.lower() != "all":
        result = [r for r in result if day.lower() in r.get("Day","").lower()]
    return jsonify({"schedule": result})

@app.route("/api/fees")
def fees():
    sid = request.args.get("id","")
    rows = read_tab("Students")
    s = next((r for r in rows
              if str(r.get("Student ID","")).upper()==sid.upper()), None)
    if not s: return jsonify({"error": f"Student ID {sid} not found"})
    return jsonify({"name": s.get("Student Name",""), "grade": s.get("Grade",""),
                   "total": s.get("Total Fees",0), "paid": s.get("Amount Paid",0),
                   "remaining": s.get("Remaining",0), "status": s.get("Payment Status",""),
                   "next_due": s.get("Next Payment Due","")})

@app.route("/api/attendance")
def attendance():
    sid = request.args.get("id","")
    rows = read_tab("Students")
    s = next((r for r in rows
              if str(r.get("Student ID","")).upper()==sid.upper()), None)
    if not s: return jsonify({"error": f"Student ID {sid} not found"})
    present=s.get("Days Present",0); total=s.get("Total School Days",0)
    pct=round((present/total*100) if total>0 else 0,1)
    return jsonify({"name": s.get("Student Name",""), "grade": s.get("Grade",""),
                   "present": present, "total": total,
                   "absent": total-present, "percentage": pct})

@app.route("/api/announcements")
def announcements():
    rows = read_tab("Announcements")
    active = [r for r in rows if r.get("Status","").lower()=="active"]
    return jsonify({"announcements": active, "count": len(active)})

@app.route("/api/admissions")
def admissions():
    rows = read_tab("Admissions")
    info = {r.get("Item",""): r.get("Value","") for r in rows}
    return jsonify({"info": info})

@app.route("/api/inquiry", methods=["POST"])
def inquiry():
    try:
        data = request.json
        sheet = get_sheet("Inquiries")
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            data.get("parent_name",""), data.get("phone",""),
            data.get("child_name",""), data.get("grade",""),
            data.get("message",""), "New — Pending Contact"
        ])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/")
def index():
    return send_from_directory(".", "live_demo.html")

@app.route("/health")
def health():
    configured = ACCESS_TOKEN != "YOUR_FULL_ACCESS_TOKEN"
    return jsonify({
        "status": "running",
        "school": SCHOOL["name"],
        "whatsapp_ready": configured,
        "phone_number_id": PHONE_NUMBER_ID,
        "sheet_id": SHEET_ID
    })

# ============================================================
# RUN
# ============================================================



# ============================================================
# QUICK TEST — sends a WhatsApp message to YOUR phone
# Visit: http://localhost:5000/test
# ============================================================

@app.route("/test")
def test_whatsapp():
    msg = "School AI Assistant is working! Connected to Google Sheets and WhatsApp API. System is ready!"
    send_whatsapp(TEST_PHONE, msg)
    return jsonify({"status": "Test message sent!", "sent_to": TEST_PHONE})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print()
    print("=" * 55)
    print("School AI Assistant — Modern Infinity Language School")
    print("=" * 55)
    print(f"Phone Number ID : {PHONE_NUMBER_ID}")
    print(f"Sheet ID        : {SHEET_ID}")
    print(f"Verify Token    : {VERIFY_TOKEN}")
    print()
    if ACCESS_TOKEN == "YOUR_FULL_ACCESS_TOKEN":
        print("⚠️  Paste your ACCESS_TOKEN from Meta dashboard")
        print("   Browser demo still works without it")
    else:
        print("✅ WhatsApp configured and ready")
    print()
    print(f"Browser demo : http://localhost:{port}")
    print(f"Webhook URL  : http://localhost:{port}/webhook")
    print(f"Health check : http://localhost:{port}/health")
    print()
    print("Starting... Press Ctrl+C to stop")
    print("=" * 55)
    app.run(debug=False, port=port, host="0.0.0.0")


# ============================================================
# QUICK TEST — sends a WhatsApp message to YOUR phone
# Visit: http://localhost:5000/test
# ============================================================

