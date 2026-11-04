import os
from twilio.rest import Client

_client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
FROM = os.environ["TWILIO_WHATSAPP_FROM"]


def send_whatsapp(to, body):
    msg = _client.messages.create(from_=FROM, to=to, body=body)
    return msg.sid