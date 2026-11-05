from django.urls import path
from . import views

urlpatterns = [
    path("webhook/", views.whatsapp_webhook, name="webhook"),
    path("health/", views.health, name="health"),
    # Analytics Dashboard (Homepage) & Web Chat Simulator
    path("", views.dashboard_view, name="dashboard"),
    path("chat/", views.chat_simulator_view, name="chat"),
    path("api/chat/", views.chat_api_view, name="chat_api"),
    # Chat History
    path("history/", views.chat_history_view, name="chat_history"),
    path("history/<path:phone>/", views.chat_detail_view, name="chat_detail"),
    # Leads Management
    path("leads/", views.leads_view, name="leads"),
    path("leads/<uuid:lead_id>/", views.lead_detail_view, name="lead_detail"),
]