import logging
import xmlrpc.client
from dataclasses import dataclass

from .runtime_settings import dev_mode as _dev_mode, odoo_config

logger = logging.getLogger(__name__)


class OdooError(Exception):
    pass


@dataclass
class OdooLead:
    odoo_id: int
    payload: dict


def _config():
    """Resolve Odoo credentials: settings saved in the desk, else ``.env``."""
    config = odoo_config()
    if not all(config.values()):
        raise OdooError(
            "Odoo is not configured — add the URL, database, username and API key "
            "in Settings, or set ODOO_URL / ODOO_DB / ODOO_USERNAME / ODOO_API_KEY."
        )
    return config


def _get_uid(config):
    """Authenticate and get user ID"""
    common = xmlrpc.client.ServerProxy(f'{config["url"]}/xmlrpc/2/common')
    uid = common.authenticate(config["db"], config["username"], config["api_key"], {})
    if not uid:
        raise OdooError("Authentication failed")
    return uid


def create_lead(req, quote, note="", phone=""):
    """Create a CRM lead/opportunity in Odoo"""
    tier = quote.get("tier_used", "B")
    tier_note = " (defaulted)" if quote.get("tier_defaulted") else ""
    name = f"{req['brand']} — {req['platform'].title()} {req['format'].title()} · Tier {tier}"

    desc_lines = [
        f"Code: {quote.get('code', '—')}",
        f"Brand: {req['brand']}",
        f"Platform: {req['platform']}",
        f"Format: {req['format']}",
        f"Tier: {tier}{tier_note}",
        f"Requested days: {req.get('requested_days', 30)}",
        f"Base price: {quote.get('base', 0):,.0f} SAR",
        f"Gross: {quote.get('gross', 0):,.0f} SAR",
        f"Discount requested: {req.get('requested_discount_pct', 0)}%",
        f"Max discount: {quote.get('max_discount_pct', '—')}%",
        ("Final: NOT ISSUED (escalated)" if not quote.get("final")
         else f"Final: {quote['final']:,.0f} SAR"),
        f"Budget: {req.get('budget_sar', 'not stated')} SAR",
        "",
        note,
    ]

    # Clean phone number (remove whatsapp: prefix if present)
    clean_phone = phone.replace("whatsapp:", "") if phone else ""

    payload = {
        "name": name,
        "partner_name": req["brand"],
        "type": "opportunity",
        "expected_revenue": quote.get("final") or 0,
        "description": "\n".join(l for l in desc_lines if l is not None),
    }
    
    # Add contact info if available
    if clean_phone:
        payload["phone"] = clean_phone
    if req.get("email"):
        payload["email_from"] = req["email"]
    if req.get("contact_name"):
        payload["contact_name"] = req["contact_name"]

    # Dev mode - return mock data without calling Odoo
    if _dev_mode():
        import random
        mock_id = random.randint(1000, 9999)
        logger.info("DEV_MODE: Mock Odoo lead id=%s", mock_id)
        return OdooLead(odoo_id=mock_id, payload=payload)

    try:
        config = _config()
        uid = _get_uid(config)
        models = xmlrpc.client.ServerProxy(f'{config["url"]}/xmlrpc/2/object')

        lead_id = models.execute_kw(
            config["db"], uid, config["api_key"],
            'crm.lead', 'create',
            [payload]
        )
        
        logger.info("Odoo lead id=%s", lead_id)
        return OdooLead(odoo_id=lead_id, payload=payload)
    except Exception as e:
        logger.exception("Odoo failed: %s", e)
        raise OdooError(str(e)) from e