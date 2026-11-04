from django.contrib import admin
from .models import InboundMessage, ConversationState, LeadRecord

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