import os
import json
import logging
from functools import wraps
from urllib.parse import urlparse

from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import translation
from django.utils.translation import gettext, gettext_lazy as _
from django.views.i18n import set_language
from twilio.request_validator import RequestValidator

from assistant.auth import desk_login_required, is_allowed_user
from assistant.forms import IntegrationSettingsForm
from assistant.models import (
    ConversationState,
    InboundMessage,
    IntegrationSettings,
    LeadRecord,
)
from assistant.services.agent import handle_message
from assistant.services.runtime_settings import (
    SECRET_FIELDS,
    SOURCE_DATABASE,
    dev_mode,
    integration_value,
    mask,
    settings_row,
    source_of,
    twilio_config,
)
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q

logger = logging.getLogger(__name__)

# Use synchronous mode (TwiML response) or async mode (Celery + API)
SYNC_MODE = os.environ.get("WHATSAPP_SYNC_MODE", "true").lower() == "true"


def validate_twilio_request(f):
    @wraps(f)
    def wrapper(request, *args, **kwargs):
        # Skip validation in DEBUG mode for easier local testing
        if settings.DEBUG:
            logger.debug("Skipping Twilio signature validation (DEBUG mode)")
            return f(request, *args, **kwargs)

        auth_token = twilio_config()["auth_token"]
        if not auth_token:
            logger.error("Twilio auth token is not configured; rejecting webhook")
            return HttpResponseForbidden("Webhook not configured")

        validator = RequestValidator(auth_token)
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
            inbound.outbound_reply = reply  # Save the bot's reply for chat history
            inbound.save(update_fields=["status", "outbound_reply"])
            logger.info(f"Reply to {phone}: {reply[:50]}...")
            return HttpResponse(make_twiml_response(reply), content_type="text/xml; charset=utf-8")
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            inbound.status = "FAILED"
            inbound.error = str(e)
            error_msg = "عذراً، حدث خطأ. يرجى المحاولة مرة أخرى."
            inbound.outbound_reply = error_msg  # Save error reply too
            inbound.save(update_fields=["status", "error", "outbound_reply"])
            return HttpResponse(make_twiml_response(error_msg), content_type="text/xml; charset=utf-8")
    else:
        # Async mode - use Celery (requires Content Templates for replies)
        from assistant.tasks.process_message import process_message
        process_message.delay(str(inbound.id))
        return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="text/xml; charset=utf-8")


@require_GET
def health(request):
    return HttpResponse("ok")


@desk_login_required
@require_POST
def set_language_switch(request):
    """Switch the interface language and confirm it with the messages framework.

    The cookie handling / redirect is delegated to Django's own ``set_language``
    view; we only add the flash confirmation on top.
    """
    language = request.POST.get("language", "")
    response = set_language(request)

    if language in dict(settings.LANGUAGES):
        # Resolve the flash text in the *new* language before storing it.
        with translation.override(language):
            name = translation.get_language_info(language)["name_local"]
            messages.success(
                request,
                gettext("Interface language set to %(language)s.") % {"language": name},
            )

    return response


# ==================== Integration settings (Waleed's test console) ====================

# Groups rendered as cards on /settings/. Each item is a model field name.
INTEGRATION_FIELD_GROUPS = [
    {
        "id": "openrouter",
        "eyebrow": "LLM",
        "title": "OpenRouter",
        "description": "Turns each WhatsApp message into a validated, structured request.",
        "fields": ["openrouter_api_key"],
        "icon": "spark",
    },
    {
        "id": "odoo",
        "eyebrow": "CRM",
        "title": "Odoo",
        "description": "Where a confirmed deal becomes a crm.lead opportunity.",
        "fields": ["odoo_url", "odoo_db", "odoo_username", "odoo_api_key", "dev_mode"],
        "icon": "cloud",
    },
    {
        "id": "twilio",
        "eyebrow": "Channel",
        "title": "Twilio WhatsApp",
        "description": "Sends the replies and signs the inbound webhook.",
        "fields": ["twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from"],
        "icon": "chat",
    },
]


def _integration_cards(form):
    """Attach source badges / masked previews to each bound field."""
    cards = []
    for group in INTEGRATION_FIELD_GROUPS:
        items = []
        for name in group["fields"]:
            items.append({
                "name": name,
                "field": form[name],
                "source": source_of(name),
                "from_database": source_of(name) == SOURCE_DATABASE,
                "is_secret": name in SECRET_FIELDS,
                "clear_field": form[f"clear_{name}"] if name in SECRET_FIELDS else None,
                "effective": (
                    mask(integration_value(name))
                    if name in SECRET_FIELDS and integration_value(name)
                    else ""
                ),
            })
        cards.append({**group, "items": items})
    return cards


@desk_login_required
def integration_settings_view(request):
    """Let the allow-listed seat point the assistant at test integrations.

    Values saved here win over ``.env``; anything left blank keeps using the
    environment, so production never needs this page.
    """
    row = settings_row() or IntegrationSettings()

    if request.method == "POST":
        # Secrets are write-only inputs. They are always present in the browser
        # payload, but an API/test POST may omit them — materialise the keys so
        # ModelForm applies the "keep / clear" decision from the form instead of
        # silently skipping the column.
        data = request.POST.copy()
        for name in SECRET_FIELDS:
            data.setdefault(name, "")

        form = IntegrationSettingsForm(data, instance=row)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.updated_by = request.user
            saved.save()
            messages.success(request, gettext("Integration settings saved."))
            return redirect("settings")
        messages.error(request, gettext("Please review the highlighted fields."))
    else:
        form = IntegrationSettingsForm(instance=row)

    context = {
        "cards": _integration_cards(form),
        "has_errors": bool(form.errors),
        "dev_mode_effective": dev_mode(),
        "updated_at": row.updated_at if row.pk else None,
        "updated_by": row.updated_by if row.pk else None,
        "openrouter_ready": bool(integration_value("openrouter_api_key")),
        "odoo_ready": all(
            integration_value(name) for name in ("odoo_url", "odoo_db", "odoo_username", "odoo_api_key")
        ),
        "twilio_ready": all(
            integration_value(name)
            for name in ("twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from")
        ),
    }
    return render(request, "assistant/settings.html", context)


# ==================== Desk access (single allow-listed seat) ====================

class DeskAuthenticationForm(AuthenticationForm):
    """Sign-in form for the desk: styled widgets + an allow-list check.

    The backend already refuses other usernames; ``confirm_login_allowed`` adds
    a second, user-facing gate so a valid password for a non-listed account can
    never open the desk (and its OpenRouter spend).
    """

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Those credentials do not match the desk account."),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({
            "class": "input",
            "autofocus": True,
            "autocomplete": "username",
            "placeholder": _("Username"),
        })
        self.fields["password"].widget.attrs.update({
            "class": "input",
            "autocomplete": "current-password",
            "placeholder": _("Password"),
        })

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not is_allowed_user(user):
            raise ValidationError(
                _("This desk is restricted to an authorised account."),
                code="not_allowed",
            )


class DeskLoginView(auth_views.LoginView):
    """The one sign-in page: username + password for the allow-listed seat."""

    template_name = "assistant/login.html"
    authentication_form = DeskAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        # Arabic welcome for Waleed, shown as a toast on the dashboard.
        messages.success(self.request, "أهلاً وسهلاً يا وليد 👋")
        return response


class DeskLogoutView(auth_views.LogoutView):
    """POST-only sign-out (Django 5 removed GET logout)."""

    next_page = reverse_lazy("login")

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        # logout() flushes the old session; this message lands in the new one.
        messages.success(request, _("You have been signed out."))
        return response


# ==================== Analytics Dashboard & Concierge Chatbot ====================

@desk_login_required
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


@desk_login_required
def chatbot_view(request):
    """POWR Concierge - WhatsApp-style chatbot desk (previously the simulator)."""
    return render(request, "assistant/chat.html")


@csrf_exempt
@desk_login_required
def chat_api_view(request):
    """API endpoint for the Concierge chatbot.

    Signed-in seat only (see ``assistant/auth.py``) so nobody can burn the
    OpenRouter budget. The view is CSRF-exempt because the browser posts JSON
    without a token, so a same-origin guard replaces the CSRF check.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.get_host():
        logger.warning("Rejected cross-origin chat API call from %s", origin)
        return JsonResponse({"error": "Cross-origin request rejected"}, status=403)

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


# ==================== Chat History & Lead Management Views ====================

@desk_login_required
def chat_history_view(request):
    """Chat history list with search and filter.

    The queryset is exposed as ``inbound_messages`` — *not* ``messages`` — so it
    can never shadow the messages framework's ``messages``/``flash_messages``
    context and leak into the toast stack.
    """
    search = request.GET.get("q", "").strip()
    source_filter = request.GET.get("source", "")
    
    inbound_messages = InboundMessage.objects.all()
    
    if search:
        inbound_messages = inbound_messages.filter(
            Q(body__icontains=search) | 
            Q(outbound_reply__icontains=search) |
            Q(phone__icontains=search)
        )
    
    if source_filter:
        inbound_messages = inbound_messages.filter(source=source_filter)
    
    # Group messages by phone for conversation view
    context = {
        "inbound_messages": inbound_messages[:100],
        "search": search,
        "source_filter": source_filter,
        "total_count": inbound_messages.count(),
    }
    return render(request, "assistant/chat_history.html", context)


@desk_login_required
def chat_detail_view(request, phone):
    """Detailed chat history for a specific phone number."""
    inbound_messages = InboundMessage.objects.filter(phone=phone).order_by("received_at")
    conversation = ConversationState.objects.filter(phone=phone).first()
    
    context = {
        "phone": phone,
        "inbound_messages": inbound_messages,
        "conversation": conversation,
        "message_count": inbound_messages.count(),
    }
    return render(request, "assistant/chat_detail.html", context)


@desk_login_required
def leads_view(request):
    """Leads list with search and filter."""
    search = request.GET.get("q", "").strip()
    stage_filter = request.GET.get("stage", "")
    escalated_filter = request.GET.get("escalated", "")
    
    leads = LeadRecord.objects.all()
    
    if search:
        leads = leads.filter(
            Q(brand__icontains=search) |
            Q(company_name__icontains=search) |
            Q(contact_name__icontains=search) |
            Q(email__icontains=search) |
            Q(phone__icontains=search) |
            Q(platform__icontains=search)
        )
    
    if stage_filter:
        leads = leads.filter(stage=stage_filter)
    
    if escalated_filter == "yes":
        leads = leads.filter(escalated=True)
    elif escalated_filter == "no":
        leads = leads.filter(escalated=False)
    
    # Get unique stages for filter dropdown
    stages = LeadRecord.objects.values_list("stage", flat=True).distinct()
    
    context = {
        "leads": leads[:100],
        "search": search,
        "stage_filter": stage_filter,
        "escalated_filter": escalated_filter,
        "stages": list(stages),
        "total_count": leads.count(),
    }
    return render(request, "assistant/leads.html", context)


@desk_login_required
def lead_detail_view(request, lead_id):
    """Detailed view of a single lead."""
    lead = get_object_or_404(LeadRecord, id=lead_id)
    
    context = {
        "lead": lead,
    }
    return render(request, "assistant/lead_detail.html", context)