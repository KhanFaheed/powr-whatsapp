from django.urls import path
from . import views

urlpatterns = [
    path("webhook/", views.whatsapp_webhook, name="webhook"),
    path("health/", views.health, name="health"),
    # Desk access — one allow-listed seat (see assistant/auth.py)
    path("login/", views.DeskLoginView.as_view(), name="login"),
    path("logout/", views.DeskLogoutView.as_view(), name="logout"),
    # Language switch (EN / AR) — flips the locale and flashes a confirmation
    path("language/", views.set_language_switch, name="set_language"),
    # Analytics Dashboard (Homepage) & POWR Concierge chatbot
    path("", views.dashboard_view, name="dashboard"),
    path("chat/", views.chatbot_view, name="chat"),
    path("api/chat/", views.chat_api_view, name="chat_api"),
    # Chat History
    path("history/", views.chat_history_view, name="chat_history"),
    path("history/<path:phone>/", views.chat_detail_view, name="chat_detail"),
    # Leads Management
    path("leads/", views.leads_view, name="leads"),
    path("leads/<uuid:lead_id>/", views.lead_detail_view, name="lead_detail"),
    # Integration settings — the tester's console (DB values override .env)
    path("settings/", views.integration_settings_view, name="settings"),
]