import os
from functools import wraps
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from twilio.request_validator import RequestValidator

from assistant.models import InboundMessage
from assistant.tasks.process_message import process_message

validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])


def validate_twilio_request(f):
    @wraps(f)
    def wrapper(request, *args, **kwargs):
        sig = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        proto = request.META.get("HTTP_X_FORWARDED_PROTO", request.scheme)
        host = request.META.get("HTTP_X_FORWARDED_HOST", request.get_host())
        url = f"{proto}://{host}{request.get_full_path()}"
        if not validator.validate(url, request.POST, sig):
            return HttpResponseForbidden("Invalid signature")
        return f(request, *args, **kwargs)
    return wrapper


@csrf_exempt
@require_POST
@validate_twilio_request
def whatsapp_webhook(request):
    sid = request.POST.get("MessageSid")
    phone = request.POST.get("From", "")
    body = request.POST.get("Body", "")
    if not sid:
        return HttpResponse("<Response></Response>", content_type="application/xml")
    inbound, created = InboundMessage.objects.get_or_create(
        twilio_sid=sid,
        defaults={"phone": phone, "body": body, "status": "QUEUED"},
    )
    if created:
        process_message.delay(str(inbound.id))
    return HttpResponse("<Response></Response>", content_type="application/xml")


@require_GET
def health(request):
    return HttpResponse("ok")