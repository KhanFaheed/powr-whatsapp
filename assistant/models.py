import uuid
from django.conf import settings
from django.db import models


class InboundMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    twilio_sid = models.CharField(max_length=64, unique=True, db_index=True)
    phone = models.CharField(max_length=32, db_index=True)
    body = models.TextField()
    status = models.CharField(max_length=20, default="RECEIVED")
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    outbound_reply = models.TextField(blank=True, null=True)
    source = models.CharField(max_length=20, default="WhatsApp")

    class Meta:
        ordering = ["-received_at"]


class ConversationState(models.Model):
    phone = models.CharField(max_length=32, unique=True, db_index=True)
    partial_request = models.JSONField(default=dict)
    last_message_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.phone} — {self.partial_request}"


class LeadRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    odoo_lead_id = models.IntegerField(null=True, blank=True)
    brand = models.CharField(max_length=200)
    platform = models.CharField(max_length=40)
    format = models.CharField(max_length=40)
    tier = models.CharField(max_length=4)
    final_quote_sar = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    escalated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Additional Odoo CRM fields
    phone = models.CharField(max_length=32, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    contact_name = models.CharField(max_length=200, blank=True, default="")
    company_name = models.CharField(max_length=200, blank=True, default="")
    expected_revenue = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    probability = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    stage = models.CharField(max_length=50, default="New")
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.brand} — {self.platform} {self.format} · Tier {self.tier}"


class IntegrationSettings(models.Model):
    """Integration credentials entered by the tester from the desk UI.

    Exactly one row lives in this table (``pk=1``). Every field is optional:
    a blank value falls back to the matching environment variable, so
    production keeps running on ``.env`` while Waleed can point the assistant at
    his own OpenRouter / Odoo / Twilio accounts from ``/settings/`` without
    touching the server. Resolution order lives in
    ``assistant/services/runtime_settings.py``.
    """

    SINGLETON_PK = 1

    openrouter_api_key = models.CharField(
        "OpenRouter API key",
        max_length=255,
        blank=True,
        default="",
        help_text="Used for LLM extraction. Blank → OPENROUTER_API_KEY from the environment.",
    )

    odoo_url = models.CharField(
        "Odoo URL",
        max_length=255,
        blank=True,
        default="",
        help_text="e.g. https://acme.odoo.com — blank → ODOO_URL.",
    )
    odoo_db = models.CharField(
        "Odoo database", max_length=120, blank=True, default="",
        help_text="Blank → ODOO_DB.",
    )
    odoo_username = models.CharField(
        "Odoo username", max_length=190, blank=True, default="",
        help_text="Login e-mail of the Odoo user. Blank → ODOO_USERNAME.",
    )
    odoo_api_key = models.CharField(
        "Odoo API key", max_length=255, blank=True, default="",
        help_text="Revocable API key (not the password). Blank → ODOO_API_KEY.",
    )
    dev_mode = models.BooleanField(
        "Odoo DEV mode",
        null=True,
        blank=True,
        default=None,
        help_text="Unknown = use DEV_MODE from the environment. Yes = mock every CRM call.",
    )

    twilio_account_sid = models.CharField(
        "Twilio account SID", max_length=64, blank=True, default="",
        help_text="Blank → TWILIO_ACCOUNT_SID.",
    )
    twilio_auth_token = models.CharField(
        "Twilio auth token", max_length=64, blank=True, default="",
        help_text="Also validates webhook signatures. Blank → TWILIO_AUTH_TOKEN.",
    )
    twilio_whatsapp_from = models.CharField(
        "Twilio WhatsApp sender", max_length=40, blank=True, default="",
        help_text="e.g. whatsapp:+14155238886 — blank → TWILIO_WHATSAPP_FROM.",
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "Integration settings"
        verbose_name_plural = "Integration settings"

    def __str__(self):
        return "Integration settings"

    def save(self, *args, **kwargs):
        # Collapse everything into a single row no matter who saves it. Dropping
        # force_insert lets `objects.create()` update row #1 instead of blowing
        # up with an IntegrityError on the second save.
        self.pk = self.SINGLETON_PK
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Return the singleton row, or ``None`` when it was never saved."""
        return cls.objects.filter(pk=cls.SINGLETON_PK).first()