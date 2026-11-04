import os
import logging
import requests
from dataclasses import dataclass

logger = logging.getLogger(__name__)
ODOO_URL = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB")
ODOO_API_KEY = os.environ["ODOO_API_KEY"]


class OdooError(Exception):
    pass


@dataclass
class OdooLead:
    odoo_id: int
    payload: dict


def create_lead(req, quote, note=""):
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

    headers = {
        "Authorization": f"bearer {ODOO_API_KEY}",
        "Content-Type": "application/json",
    }
    if ODOO_DB:
        headers["X-Odoo-Database"] = ODOO_DB

    try:
        r = requests.post(f"{ODOO_URL}/json/2/crm.lead/create",
                          headers=headers, json={"vals_list": [payload]}, timeout=10)
        r.raise_for_status()
        result = r.json()
        lead_id = result[0] if isinstance(result, list) else result["result"][0]
        logger.info("Odoo lead id=%s", lead_id)
        return OdooLead(odoo_id=lead_id, payload=payload)
    except requests.RequestException as e:
        logger.exception("Odoo failed")
        raise OdooError(str(e)) from e