import math
from typing import Optional

PRICE_SHEET = [
    {"code": "YT-INT-A",  "platform": "youtube",   "format": "integration", "tier": "A", "price_sar": 90000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 10},
    {"code": "YT-INT-B",  "platform": "youtube",   "format": "integration", "tier": "B", "price_sar": 45000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 15},
    {"code": "YT-INT-C",  "platform": "youtube",   "format": "integration", "tier": "C", "price_sar": 20000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 15},
    {"code": "YT-DED-A",  "platform": "youtube",   "format": "dedicated",   "tier": "A", "price_sar": 200000, "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 10},
    {"code": "YT-DED-B",  "platform": "youtube",   "format": "dedicated",   "tier": "B", "price_sar": 100000, "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 10},
    {"code": "SC-STR-A",  "platform": "snapchat",  "format": "story",       "tier": "A", "price_sar": 30000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 15},
    {"code": "SC-STR-B",  "platform": "snapchat",  "format": "story",       "tier": "B", "price_sar": 15000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 20},
    {"code": "IG-REEL-A","platform": "instagram", "format": "reel",        "tier": "A", "price_sar": 35000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 15},
    {"code": "IG-REEL-B","platform": "instagram", "format": "reel",        "tier": "B", "price_sar": 18000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 20},
    {"code": "TT-VID-A",  "platform": "tiktok",    "format": "video",       "tier": "A", "price_sar": 30000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 15},
    {"code": "TT-VID-B",  "platform": "tiktok",    "format": "video",       "tier": "B", "price_sar": 15000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 20},
    {"code": "X-POST-A",  "platform": "x",         "format": "post",        "tier": "A", "price_sar": 10000,  "usage_days_included": 30, "extra_30_days_pct": 20, "max_discount_pct": 20},
]

DEFAULT_TIER = "B"


def lookup_row(platform, format_, tier):
    for row in PRICE_SHEET:
        if row["platform"] == platform and row["format"] == format_ and row["tier"] == tier:
            return row
    matches = [r for r in PRICE_SHEET if r["platform"] == platform and r["format"] == format_]
    if len(matches) == 1:
        return matches[0]
    return None


def compute_duration_multiplier(row, requested_days):
    included = row["usage_days_included"]
    per_block = row["extra_30_days_pct"]
    if requested_days <= included:
        return 1.0, 0
    blocks = math.ceil((requested_days - included) / 30)
    return 1 + (blocks * per_block / 100), blocks


def build_quote(platform, format_, tier=DEFAULT_TIER, requested_days=30, requested_discount_pct=0.0):
    row = lookup_row(platform, format_, tier)
    if not row:
        return {"status": "UNKNOWN", "reason": f"No price for {platform}/{format_}/Tier {tier}"}
    tier_defaulted = (row["tier"] != tier)
    multiplier, extra_blocks = compute_duration_multiplier(row, requested_days)
    gross = round(row["price_sar"] * multiplier, 2)
    if requested_discount_pct > row["max_discount_pct"]:
        return {
            "status": "ESCALATE", "code": row["code"], "tier_used": row["tier"],
            "tier_defaulted": tier_defaulted, "base": row["price_sar"],
            "extra_blocks": extra_blocks, "gross": gross,
            "max_discount_pct": row["max_discount_pct"],
            "requested_discount_pct": requested_discount_pct,
            "reason": f"{requested_discount_pct}% > cap {row['max_discount_pct']}%",
        }
    final = round(gross * (1 - requested_discount_pct / 100), 2)
    return {
        "status": "OK", "code": row["code"], "tier_used": row["tier"],
        "tier_defaulted": tier_defaulted, "base": row["price_sar"],
        "extra_blocks": extra_blocks, "gross": gross,
        "discount_pct": requested_discount_pct, "final": final,
        "max_discount_pct": row["max_discount_pct"], "currency": "SAR",
    }