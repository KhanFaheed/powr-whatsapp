#!/usr/bin/env python
"""
Test script to verify Twilio WhatsApp Sandbox configuration
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

import requests

ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')

print("=" * 50)
print("TWILIO WHATSAPP SANDBOX VERIFICATION")
print("=" * 50)

# 1. Check credentials
print("\n1. Checking Twilio credentials...")
url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}.json"
resp = requests.get(url, auth=(ACCOUNT_SID, AUTH_TOKEN))
if resp.status_code == 200:
    data = resp.json()
    print(f"   ✅ Account: {data.get('friendly_name')}")
    print(f"   ✅ Status: {data.get('status')}")
    print(f"   ✅ Type: {data.get('type')}")
else:
    print(f"   ❌ Failed: {resp.status_code}")
    sys.exit(1)

# 2. Check WhatsApp Sandbox config
print("\n2. Checking WhatsApp Sandbox...")
print("   ℹ️  Sandbox number: whatsapp:+14155238886")

# 3. Instructions
print("\n" + "=" * 50)
print("SETUP INSTRUCTIONS")
print("=" * 50)
print("""
1. Go to: https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn

2. Click 'Sandbox settings' tab

3. Set 'WHEN A MESSAGE COMES IN' to:
   https://micha-nonparabolic-lovie.ngrok-free.dev/webhook/
   
   Method: POST (HTTP POST)

4. Click 'Save'

5. Make sure your phone has joined the sandbox:
   - Send 'join <code>' to +1 415 523 8886
   - The code is shown in the Twilio console

6. Send a test message in Arabic to the sandbox number

IMPORTANT: The webhook URL must be EXACTLY as shown above,
ending with /webhook/ (including the trailing slash)
""")

print("=" * 50)
print("CURRENT WEBHOOK URL TO USE:")
print("https://micha-nonparabolic-lovie.ngrok-free.dev/webhook/")
print("=" * 50)
