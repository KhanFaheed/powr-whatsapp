import os
import logging
from functools import wraps
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.conf import settings
from twilio.request_validator import RequestValidator

from assistant.models import InboundMessage, LeadRecord
from assistant.services.agent import handle_message
from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone
import json

logger = logging.getLogger(__name__)
validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])

# Use synchronous mode (TwiML response) or async mode (Celery + API)
SYNC_MODE = os.environ.get("WHATSAPP_SYNC_MODE", "true").lower() == "true"


def validate_twilio_request(f):
    @wraps(f)
    def wrapper(request, *args, **kwargs):
        # Skip validation in DEBUG mode for easier local testing
        if settings.DEBUG:
            logger.debug("Skipping Twilio signature validation (DEBUG mode)")
            return f(request, *args, **kwargs)
        
        sig = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        proto = request.META.get("HTTP_X_FORWARDED_PROTO", request.scheme)
        host = request.META.get("HTTP_X_FORWARDED_HOST", request.get_host())
        url = f"{proto}://{host}{request.get_full_path()}"
        if not validator.validate(url, request.POST, sig):
            logger.warning(f"Invalid Twilio signature for URL: {url}")
            return HttpResponseForbidden("Invalid signature")
        return f(request, *args, **kwargs)
    return wrapper


def make_twiml_response(message):
    """Create TwiML response for WhatsApp"""
    from xml.sax.saxutils import escape
    safe_message = escape(message)
    # Return compact XML without extra whitespace
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe_message}</Message></Response>'


@csrf_exempt
@validate_twilio_request
def whatsapp_webhook(request):
    # Handle GET requests (Twilio validation)
    if request.method == "GET":
        return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="text/xml; charset=utf-8")
    
    sid = request.POST.get("MessageSid")
    phone = request.POST.get("From", "")
    body = request.POST.get("Body", "")
    
    logger.info(f"Received WhatsApp from {phone}: {body[:50]}...")
    
    if not sid:
        return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="text/xml; charset=utf-8")
    
    # Check for duplicate
    inbound, created = InboundMessage.objects.get_or_create(
        twilio_sid=sid,
        defaults={"phone": phone, "body": body, "status": "PROCESSING"},
    )
    
    if not created:
        # Already processed
        return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="text/xml; charset=utf-8")
    
    if SYNC_MODE:
        # Synchronous mode - respond directly with TwiML
        try:
            reply = handle_message(phone, body)
            inbound.status = "PROCESSED"
            inbound.save(update_fields=["status"])
            logger.info(f"Reply to {phone}: {reply[:50]}...")
            return HttpResponse(make_twiml_response(reply), content_type="text/xml; charset=utf-8")
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            inbound.status = "FAILED"
            inbound.error = str(e)
            inbound.save(update_fields=["status", "error"])
            error_msg = "عذراً، حدث خطأ. يرجى المحاولة مرة أخرى."
            return HttpResponse(make_twiml_response(error_msg), content_type="text/xml; charset=utf-8")
    else:
        # Async mode - use Celery (requires Content Templates for replies)
        from assistant.tasks.process_message import process_message
        process_message.delay(str(inbound.id))
        return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="text/xml; charset=utf-8")


@require_GET
def health(request):
    return HttpResponse("ok")


# ==================== NEW VIEWS: Analytics Dashboard & Web Chat Simulator ====================

def dashboard_view(request):
    """Analytics Dashboard - shows message stats and recent activity."""
    recent_messages = InboundMessage.objects.all()[:30]
    total_messages = InboundMessage.objects.count()
    total_leads = LeadRecord.objects.count()
    escalated_leads = LeadRecord.objects.filter(escalated=True).count()
    
    context = {
        "recent_messages": recent_messages,
        "total_messages": total_messages,
        "total_leads": total_leads,
        "escalated_leads": escalated_leads,
    }
    return render(request, "assistant/dashboard.html", context)


def chat_simulator_view(request):
    """Web Chat Simulator - WhatsApp-style chat interface for testing."""
    return render(request, "assistant/chat.html")


@csrf_exempt
def chat_api_view(request):
    """API endpoint for the chat simulator."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    try:
        data = json.loads(request.body)
        text = data.get("text", "").strip()
        
        if not text:
            return JsonResponse({"error": "No text provided"}, status=400)
        
        # Use hardcoded dummy phone number for web simulator
        phone = "whatsapp:+19999999999"
        
        # Call existing AI logic
        reply = handle_message(phone, text)
        
        # Save the interaction to the database
        InboundMessage.objects.create(
            twilio_sid=f"web-{timezone.now().timestamp()}",
            phone=phone,
            body=text,
            outbound_reply=reply,
            source="Web Simulator",
            status="PROCESSED"
        )
        
        return JsonResponse({"reply": reply})
    
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception(f"Error in chat_api_view: {e}")
        return JsonResponse({"error": "An error occurred"}, status=500)