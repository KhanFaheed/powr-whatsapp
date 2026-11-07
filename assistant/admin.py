from django.contrib import admin
from .models import ConversationState, InboundMessage, IntegrationSettings, LeadRecord

admin.site.site_header = "POWR Concierge administration"
admin.site.site_title = "POWR Concierge"
admin.site.index_title = "Desk data"

@admin.register(InboundMessage)
class InboundMessageAdmin(admin.ModelAdmin):
    list_display = ("twilio_sid", "phone", "status", "received_at")
    list_filter = ("status",)
    search_fields = ("phone", "body")

@admin.register(ConversationState)
class ConversationStateAdmin(admin.ModelAdmin):
    list_display = ("phone", "last_message_at")

@admin.register(LeadRecord)
class LeadRecordAdmin(admin.ModelAdmin):
    list_display = ("brand", "platform", "tier", "final_quote_sar", "escalated", "created_at")
    list_filter = ("escalated", "platform")


@admin.register(IntegrationSettings)
class IntegrationSettingsAdmin(admin.ModelAdmin):
    """Single-row console.

    The desk UI at ``/settings/`` is the primary editor; this is the fallback
    for someone who already lives in the Django admin.
    """

    fieldsets = (
        ("OpenRouter", {"fields": ("openrouter_api_key",)}),
        ("Odoo", {"fields": (
            "odoo_url", "odoo_db", "odoo_username", "odoo_api_key", "dev_mode",
        )}),
        ("Twilio", {"fields": (
            "twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from",
        )}),
        ("Audit", {"fields": ("updated_at", "updated_by")}),
    )
    readonly_fields = ("updated_at", "updated_by")

    def has_add_permission(self, request):
        return not IntegrationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)