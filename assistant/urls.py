from django.urls import path
from . import views

urlpatterns = [
    path("webhook/", views.whatsapp_webhook, name="webhook"),
    path("health/", views.health, name="health"),
]