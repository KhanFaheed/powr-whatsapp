import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
FROM = os.environ["TWILIO_WHATSAPP_FROM"]


def send_whatsapp(to, body):
    """Send WhatsApp message via Twilio REST API directly
    
    Using direct REST API call to avoid SDK issues with sandbox
    """
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"
        
        data = {
            "From": FROM,
            "To": to,
            "Body": body
        }
        
        response = requests.post(
            url,
            data=data,
            auth=(ACCOUNT_SID, AUTH_TOKEN)
        )
        
        if response.status_code in [200, 201]:
            result = response.json()
            logger.info(f"WhatsApp sent to {to}: {result.get('sid')}")
            return result.get('sid')
        else:
            logger.error(f"Twilio error: {response.status_code} - {response.text}")
            raise Exception(f"Twilio API error: {response.status_code} - {response.text}")
            
    except Exception as e:
        logger.exception(f"Failed to send WhatsApp to {to}: {e}")
        raise