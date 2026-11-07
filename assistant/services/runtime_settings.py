"""Integration settings resolver — database first, environment second.

Every integration value (OpenRouter, Odoo, Twilio, DEV mode) is resolved the
same way:

    1. the single ``IntegrationSettings`` row, when that field is non-blank
       (set by Waleed from ``/settings/`` while testing),
    2. otherwise the matching environment variable (production ``.env``),
    3. otherwise an empty default.

Nothing here caches across requests, so saving the settings form takes effect
on the very next message — no restart, no cache invalidation dance.
"""
import logging
import os

from django.db import DatabaseError

from assistant.models import IntegrationSettings

logger = logging.getLogger(__name__)

# model field -> environment variable
ENV_NAMES = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "odoo_url": "ODOO_URL",
    "odoo_db": "ODOO_DB",
    "odoo_username": "ODOO_USERNAME",
    "odoo_api_key": "ODOO_API_KEY",
    "twilio_account_sid": "TWILIO_ACCOUNT_SID",
    "twilio_auth_token": "TWILIO_AUTH_TOKEN",
    "twilio_whatsapp_from": "TWILIO_WHATSAPP_FROM",
    "dev_mode": "DEV_MODE",
}

# Values that must never be printed in full on the settings page.
SECRET_FIELDS = ("openrouter_api_key", "odoo_api_key", "twilio_auth_token")

SOURCE_DATABASE = "database"
SOURCE_ENVIRONMENT = "environment"
SOURCE_MISSING = "missing"


def settings_row():
    """The singleton row, or ``None`` when it was never saved.

    Also tolerates the table not existing yet (fresh checkout before ``migrate``)
    so the webhook cannot crash just because the settings app is new.
    """
    try:
        return IntegrationSettings.load()
    except DatabaseError:  # pragma: no cover - only before the first migrate
        logger.debug("IntegrationSettings table unavailable", exc_info=True)
        return None


def is_blank(value):
    """``None`` or a whitespace-only string counts as 'not set'.

    ``False`` is deliberately *not* blank: DEV mode "off" is a real choice.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def integration_value(field_name, default=""):
    """Resolve one integration value using the documented precedence."""
    row = settings_row()
    if row is not None:
        value = getattr(row, field_name, None)
        if not is_blank(value):
            return value

    env_name = ENV_NAMES.get(field_name)
    if env_name:
        value = os.environ.get(env_name)
        if not is_blank(value):
            return value.strip()

    return default


def source_of(field_name):
    """Where the effective value comes from: database / environment / missing."""
    row = settings_row()
    if row is not None and not is_blank(getattr(row, field_name, None)):
        return SOURCE_DATABASE
    env_name = ENV_NAMES.get(field_name)
    if env_name and not is_blank(os.environ.get(env_name)):
        return SOURCE_ENVIRONMENT
    return SOURCE_MISSING


# ---------------------------------------------------------------- OpenRouter

def openrouter_api_key():
    return str(integration_value("openrouter_api_key")).strip()


# ---------------------------------------------------------------------- Odoo

def odoo_url():
    return str(integration_value("odoo_url")).strip().rstrip("/")


def odoo_config():
    return {
        "url": odoo_url(),
        "db": str(integration_value("odoo_db")).strip(),
        "username": str(integration_value("odoo_username")).strip(),
        "api_key": str(integration_value("odoo_api_key")).strip(),
    }


def odoo_configured():
    return all(odoo_config().values())


def dev_mode():
    """Odoo DEV mode: the row's tri-state wins, then ``DEV_MODE`` from the env."""
    row = settings_row()
    if row is not None and row.dev_mode is not None:
        return bool(row.dev_mode)
    return str(os.environ.get("DEV_MODE", "false")).strip().lower() in {"1", "true", "yes", "on"}


# -------------------------------------------------------------------- Twilio

def twilio_config():
    return {
        "account_sid": str(integration_value("twilio_account_sid")).strip(),
        "auth_token": str(integration_value("twilio_auth_token")).strip(),
        "from_number": str(integration_value("twilio_whatsapp_from")).strip(),
    }


def twilio_configured():
    return all(twilio_config().values())


# ------------------------------------------------------------------ Display

def mask(value, keep=4):
    """``••••••1a2b`` — never render a secret in full on a page."""
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * max(4, len(value))
    return "•" * max(6, len(value) - keep) + value[-keep:]
