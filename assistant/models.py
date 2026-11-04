import uuid
from django.db import models


class InboundMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    twilio_sid = models.CharField(max_length=64, unique=True, db_index=True)
    phone = models.CharField(max_length=32, db_index=True)
    body = models.TextField()
    status = models.CharField(max_length=20, default="RECEIVED")
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

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