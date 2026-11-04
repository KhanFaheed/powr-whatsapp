import os
import json
from twilio.rest import Client

_client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
FROM = os.environ["TWILIO_WHATSAPP_FROM"]

# PASTE YOUR HX... SID HERE (From the Twilio Console)
TEMPLATE_SID = "HX9e7b4d3b92219a4a0f0b129756f64482"

def send_whatsapp(to, body):
    msg = _client.messages.create(
        from_=FROM,
        to=to,
        content_sid=TEMPLATE_SID,
        content_variables=json.dumps({"1": body})
    )
    return msg.sid