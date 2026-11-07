import logging
import requests

from .runtime_settings import twilio_config

logger = logging.getLogger(__name__)


def send_whatsapp(to, body):
    """Send a WhatsApp message via the Twilio REST API.

    Credentials are resolved per call (desk settings first, then ``.env``) so a
    tester can switch accounts from /settings/ without a restart.
    """
    config = twilio_config()
    if not all(config.values()):
        raise RuntimeError(
            "Twilio is not configured — add the account SID, auth token and sender "
            "in Settings, or set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM."
        )

    account_sid = config["account_sid"]
    auth_token = config["auth_token"]
    from_number = config["from_number"]

    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

        data = {
            "From": from_number,
            "To": to,
            "Body": body
        }

        response = requests.post(
            url,
            data=data,
            auth=(account_sid, auth_token)
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