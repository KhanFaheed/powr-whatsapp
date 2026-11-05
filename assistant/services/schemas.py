"""Pydantic validation for LLM output and for the request that reaches pricing.

Why this module exists
----------------------
The LLM is non-deterministic: it can return "Youtube" instead of "youtube",
"Tier A (10M+)" instead of "A", a budget of -5, a discount of "30%", or a
placeholder like "null"/"غير محدد". Nothing unvalidated is allowed to reach
`price_sheet` or Odoo, so every value is either coerced into a canonical form
or dropped to None (which the agent then asks about).

No third-party dependency: email validation is a regex, so `email-validator`
is not required.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------- constants

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

PLATFORMS = ("youtube", "instagram", "tiktok", "snapchat", "x")
PLATFORM_ALIASES = {
    "yt": "youtube", "you tube": "youtube", "youtube shorts": "youtube",
    "yotube": "youtube", "youtub": "youtube", "you tube": "youtube", "يوتيوب": "youtube",
    "insta": "instagram", "ig": "instagram", "instagram reels": "instagram",
    "instgram": "instagram", "انستقرام": "instagram", "إنستقرام": "instagram",
    "tik tok": "tiktok", "tik-tok": "tiktok", "tic tok": "tiktok", "tt": "tiktok",
    "tiktk": "tiktok", "تيك توك": "tiktok", "تيكتوك": "tiktok",
    "snap": "snapchat", "snap chat": "snapchat", "sc": "snapchat",
    "snapcht": "snapchat", "سناب": "snapchat",
    "twitter": "x", "x.com": "x", "tweet": "x", "تويتر": "x", "إكس": "x",
}

FORMATS = ("integration", "dedicated", "reel", "story", "video", "post")
FORMAT_ALIASES = {
    "integration": "integration", "integrated": "integration", "mention": "integration",
    "dedicated": "dedicated", "dedicated video": "dedicated", "dedicated_video": "dedicated",
    "reel": "reel", "reels": "reel", "instagram reel": "reel",
    "story": "story", "stories": "story", "snap": "story", "snaps": "story",
    "video": "video", "tiktok video": "video", "short video": "video",
    "post": "post", "tweet": "post", "static post": "post",
}

TIERS = ("A", "B", "C")
_TIER_RE = re.compile(r"\b([abc])\b", re.IGNORECASE)

# Values an LLM loves to emit instead of null
PLACEHOLDERS = {
    "", "null", "none", "n/a", "na", "unknown", "not specified", "not stated",
    "undefined", "nan", "-", "—", "?", "غير محدد", "غير مذكور", "لا يوجد", "لا شيء",
}


# ---------------------------------------------------------------- helpers

def clean_text(value, max_len: int = 200) -> Optional[str]:
    """Collapse whitespace, trim punctuation and reject placeholder strings."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = re.sub(r"\s+", " ", value).strip(" .,-—|:؛")
    if value.lower() in PLACEHOLDERS:
        return None
    return value[:max_len] or None


def clean_number(value) -> Optional[float]:
    """Accept 20000, '20,000', '20k', '٢٠٠٠٠' or '30%' → float (or None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("٬", "")
    text = text.translate({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    if "k" in text.lower() and number < 1000:
        number *= 1000
    return number


def normalize_platform(value) -> Optional[str]:
    text = clean_text(value, 40)
    if not text:
        return None
    key = text.lower().replace("_", " ").strip()
    if key in PLATFORMS:
        return key
    return PLATFORM_ALIASES.get(key)


def normalize_format(value) -> Optional[str]:
    text = clean_text(value, 60)
    if not text:
        return None
    key = text.lower().replace("_", " ").strip()
    if key in FORMATS:
        return key
    if key in FORMAT_ALIASES:
        return FORMAT_ALIASES[key]
    # "Integration (60-90 sec inside a video)" → integration
    for canonical in FORMATS:
        if key.startswith(canonical):
            return canonical
    return None


def normalize_tier(value) -> Optional[str]:
    text = clean_text(value, 40)
    if not text:
        return None
    upper = text.upper().replace("TIER", "").strip(" :()-")
    if upper in TIERS:
        return upper
    match = _TIER_RE.search(text)          # "Tier A (10M+ subscribers)" → A
    return match.group(1).upper() if match else None


def is_valid_email(value) -> bool:
    text = clean_text(value, 254)
    return bool(text and EMAIL_RE.match(text))


def clean_email(value) -> Optional[str]:
    text = clean_text(value, 254)
    if not text:
        return None
    text = text.strip("<>()[],;")
    return text.lower() if is_valid_email(text) else None


def clean_person_name(value) -> Optional[str]:
    """Keep letters/spaces only; reject anything containing digits or '@'."""
    text = clean_text(value, 80)
    if not text or "@" in text:
        return None
    if any(ch.isdigit() for ch in text):
        return None
    text = re.sub(r"[^\w\u0600-\u06FF\s.'\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    if len(text) < 2 or text.lower() in PLACEHOLDERS:
        return None
    return text[:80]


# ---------------------------------------------------------------- models

class ExtractedRequest(BaseModel):
    """Strict shape for what the LLM is allowed to return.

    Every field is optional (null = "the customer did not say"), and each value
    is normalised, so a sloppy extraction degrades into `None` (which the agent
    asks about) instead of poisoning the quote.
    """

    model_config = ConfigDict(extra="ignore")

    brand: Optional[str] = None
    platform: Optional[str] = None
    format: Optional[str] = None
    creator_tier: Optional[str] = None
    requested_days: Optional[int] = None
    budget_sar: Optional[float] = None
    requested_discount_pct: Optional[float] = None
    language: str = "ar"
    contact_name: Optional[str] = None
    email: Optional[str] = None

    @field_validator("brand", mode="before")
    @classmethod
    def _brand(cls, value):
        return clean_text(value, 200)

    @field_validator("platform", mode="before")
    @classmethod
    def _platform(cls, value):
        return normalize_platform(value)

    @field_validator("format", mode="before")
    @classmethod
    def _format(cls, value):
        return normalize_format(value)

    @field_validator("creator_tier", mode="before")
    @classmethod
    def _tier(cls, value):
        return normalize_tier(value)

    @field_validator("requested_days", mode="before")
    @classmethod
    def _days(cls, value):
        number = clean_number(value)
        if number is None:
            return None
        number = int(round(number))
        return number if 1 <= number <= 365 else None

    @field_validator("budget_sar", mode="before")
    @classmethod
    def _budget(cls, value):
        number = clean_number(value)
        return number if number and number > 0 else None

    @field_validator("requested_discount_pct", mode="before")
    @classmethod
    def _discount(cls, value):
        number = clean_number(value)
        return number if number is not None and 0 <= number <= 100 else None

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, value):
        text = (clean_text(value, 10) or "").lower()
        return text if text in ("ar", "en", "mixed") else "ar"

    @field_validator("contact_name", mode="before")
    @classmethod
    def _contact_name(cls, value):
        return clean_person_name(value)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value):
        return clean_email(value)


class ContactInfo(BaseModel):
    """Validated contact details captured before the handover/booking."""

    model_config = ConfigDict(extra="ignore")

    full_name: Optional[str] = None
    email: Optional[str] = None

    @field_validator("full_name", mode="before")
    @classmethod
    def _name(cls, value):
        return clean_person_name(value)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value):
        return clean_email(value)


class QuoteRequest(BaseModel):
    """The exact values `price_sheet.build_quote` is allowed to see.

    This is the last gate before money is calculated and before Odoo is called:
    if a value is impossible (unknown platform, 0 days, 140% discount) the model
    raises and the agent asks the customer instead of quoting nonsense.
    """

    model_config = ConfigDict(extra="ignore")

    brand: str = Field(min_length=1, max_length=200)
    platform: str
    format: str
    creator_tier: str = "B"
    requested_days: int = Field(default=30, ge=1, le=365)
    discount_pct: float = Field(default=0.0, ge=0, le=100)
    budget_sar: Optional[float] = Field(default=None, gt=0)

    @field_validator("brand", mode="before")
    @classmethod
    def _brand(cls, value):
        cleaned = clean_text(value, 200)
        if not cleaned:
            raise ValueError("brand is required")
        return cleaned

    @field_validator("platform", mode="before")
    @classmethod
    def _platform(cls, value):
        cleaned = normalize_platform(value)
        if not cleaned:
            raise ValueError("unsupported platform")
        return cleaned

    @field_validator("format", mode="before")
    @classmethod
    def _format(cls, value):
        cleaned = normalize_format(value)
        if not cleaned:
            raise ValueError("unsupported format")
        return cleaned

    @field_validator("creator_tier", mode="before")
    @classmethod
    def _tier(cls, value):
        return normalize_tier(value) or "B"

    @field_validator("requested_days", mode="before")
    @classmethod
    def _days(cls, value):
        number = clean_number(value)
        return int(round(number)) if number and number > 0 else 30

    @field_validator("discount_pct", mode="before")
    @classmethod
    def _discount(cls, value):
        number = clean_number(value)
        if number is None:
            return 0.0
        return min(max(number, 0.0), 100.0)

    @field_validator("budget_sar", mode="before")
    @classmethod
    def _budget(cls, value):
        number = clean_number(value)
        return number if number and number > 0 else None

