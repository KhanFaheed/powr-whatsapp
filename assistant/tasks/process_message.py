import logging
from celery import shared_task
from assistant.models import InboundMessage
from assistant.services.agent import handle_message
from assistant.services.twilio_client import send_whatsapp

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_message(self, inbound_id):
    inbound = InboundMessage.objects.get(id=inbound_id)
    if inbound.status == "PROCESSED":
        return
    try:
        reply = handle_message(inbound.phone, inbound.body)
        send_whatsapp(inbound.phone, reply)
        inbound.status = "PROCESSED"
        inbound.save(update_fields=["status"])
    except Exception as exc:
        inbound.status = "FAILED"
        inbound.error = str(exc)
        inbound.save(update_fields=["status", "error"])
        logger.exception("process_message failed")
        raise self.retry(exc=exc)