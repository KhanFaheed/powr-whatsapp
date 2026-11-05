import os
import logging
import xmlrpc.client
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Odoo configuration - use XML-RPC which is standard for Odoo
ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USERNAME = os.environ.get("ODOO_USERNAME", "")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")

# Dev mode flag - if True, skip actual Odoo calls and return mock data
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true"


class OdooError(Exception):
    pass


@dataclass
class OdooLead:
    odoo_id: int
    payload: dict


def _get_uid():
    """Authenticate and get user ID"""
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, {})
    if not uid:
        raise OdooError("Authentication failed")
    return uid


def create_lead(req, quote, note=""):
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

    payload = {
        "name": name,
        "partner_name": req["brand"],
        "type": "opportunity",
        "expected_revenue": quote.get("final") or 0,
        "description": "\n".join(l for l in desc_lines if l is not None),
    }

    # Dev mode - return mock data without calling Odoo
    if DEV_MODE:
        import random
        mock_id = random.randint(1000, 9999)
        logger.info("DEV_MODE: Mock Odoo lead id=%s", mock_id)
        return OdooLead(odoo_id=mock_id, payload=payload)

    try:
        uid = _get_uid()
        models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
        
        lead_id = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'crm.lead', 'create',
            [payload]
        )
        
        logger.info("Odoo lead id=%s", lead_id)
        return OdooLead(odoo_id=lead_id, payload=payload)
    except Exception as e:
        logger.exception("Odoo failed: %s", e)
        raise OdooError(str(e)) from e