"""
School AI Assistant - Debug version for Railway
This version prints detailed startup info to help diagnose crashes
"""
import os
import sys
import json
import traceback

print("=" * 50)
print("STARTUP DIAGNOSTIC")
print("=" * 50)
print(f"Python: {sys.version}")
print(f"PORT: {os.environ.get('PORT', 'NOT SET')}")
print(f"SHEET_ID set: {bool(os.environ.get('SHEET_ID'))}")
print(f"ACCESS_TOKEN set: {bool(os.environ.get('ACCESS_TOKEN'))}")
print(f"GOOGLE_CREDENTIALS set: {bool(os.environ.get('GOOGLE_CREDENTIALS'))}")

# Test GOOGLE_CREDENTIALS parsing
creds_str = os.environ.get("GOOGLE_CREDENTIALS", "")
if creds_str:
    print(f"GOOGLE_CREDENTIALS length: {len(creds_str)}")
    print(f"First 30 chars: {creds_str[:30]}")
    try:
        creds_data = json.loads(creds_str)
        # Write to file
        with open("credentials.json", "w") as f:
            json.dump(creds_data, f)
        print("✅ GOOGLE_CREDENTIALS parsed and written OK")
        print(f"Keys: {list(creds_data.keys())[:4]}")
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse error: {e}")
        print(f"Error at position: {e.pos}")
        print(f"Near: {creds_str[max(0,e.pos-20):e.pos+20]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)
else:
    print("⚠️ GOOGLE_CREDENTIALS not set - using local credentials.json")
    if not os.path.exists("credentials.json"):
        print("❌ No credentials.json found!")
        sys.exit(1)
    print("✅ credentials.json found locally")

print("=" * 50)
print("Starting Flask app...")
print("=" * 50)

# Now import and run Flask
from flask import Flask, request, jsonify, send_from_directory
import gspread
from google.oauth2.service_account import Credentials
import requests
from datetime import datetime

app = Flask(__name__)

CREDENTIALS_FILE = "credentials.json"
SHEET_ID = os.environ.get("SHEET_ID", "1EqhlDPwQB_L7Ho_MN6lbeE_OrLsVmUKpfZTRUEw3ePE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1358537447338280")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "schoolai2026")
META_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

SCHOOL = {
    "name": "Modern Infinity Language School",
    "phone": "02-3796-9155",
    "whatsapp": "01066253331",
}

def read_tab(tab_name):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(SHEET_ID).worksheet(tab_name).get_all_records()
    except Exception as e:
        print(f"Sheet error ({tab_name}): {e}")
        return []

@app.route("/health")
def health():
    return jsonify({
        "status": "running",
        "school": SCHOOL["name"],
        "whatsapp_configured": bool(ACCESS_TOKEN),
        "sheet_id": SHEET_ID[:20] + "..."
    })

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
        response = process_message(text)
        send_whatsapp(phone, response)
        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "OK", 200

def send_whatsapp(to_phone, message):
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
               "to": to_phone, "type": "text", "text": {"body": message}}
    try:
        res = requests.post(META_API_URL, headers=headers, json=payload)
        print(f"Sent to {to_phone}: {res.status_code}")
    except Exception as e:
        print(f"Send error: {e}")

def process_message(msg):
    m = msg.lower().strip()
    is_arabic = any('\u0600' <= c <= '\u06FF' for c in msg)
    
    if any(w in m for w in ['homework','assignment','واجب']):
        grade = None
        for g in ['12','11','10','9','8','7','6','5','4','3','2','1']:
            if f'grade {g}' in m or f'الصف {g}' in m:
                grade = f'Grade {g}'; break
        if not grade:
            return "حدد الصف\nمثال: واجب الصف السابع" if is_arabic else "Specify grade\nExample: Homework for Grade 7"
        rows = read_tab("Homework")
        hw = [r for r in rows if grade.lower() in r.get("Grade","").lower() and r.get("Assignment","").strip()]
        if not hw: return f"No homework for {grade}\n📞 {SCHOOL['phone']}"
        r = f"📚 {grade} Homework\n\n"
        for h in hw:
            r += f"• {h.get('Subject','')}: {h.get('Assignment','')}\n  Due: {h.get('Due Date','')}\n\n"
        return r.strip()

    if any(w in m for w in ['fee','fees','رسوم','مصاريف']):
        rows = read_tab("Admissions")
        info = {r.get("Item",""): r.get("Value","") for r in rows}
        return (f"💰 Modern Infinity Fees\n\n"
                f"KG: {info.get('KG1 Fees','42,000 EGP')}\n"
                f"Grade 1-3: {info.get('Grade 1-3 Fees','48,000 EGP')}\n"
                f"Grade 4-6: {info.get('Grade 4-6 Fees','55,000 EGP')}\n"
                f"Grade 7-9: {info.get('Grade 7-9 Fees','62,000 EGP')}\n\n"
                f"📞 {SCHOOL['phone']}")

    return (f"Welcome to Modern Infinity! 👋\n\n"
            f"📚 Homework — Homework for Grade 7\n"
            f"💰 Fees — How much are fees?\n"
            f"📞 {SCHOOL['phone']}")

@app.route("/")
def index():
    return send_from_directory(".", "live_demo.html")

@app.route("/api/homework")
def homework():
    grade = request.args.get("grade","")
    rows = read_tab("Homework")
    result = [r for r in rows if grade.lower() in r.get("Grade","").lower() and r.get("Assignment","").strip()]
    return jsonify({"homework": result, "count": len(result)})

@app.route("/api/fees")
def fees():
    rows = read_tab("Admissions")
    info = {r.get("Item",""): r.get("Value","") for r in rows}
    return jsonify({"info": info})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")
