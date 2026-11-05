import logging
import re
from assistant.models import ConversationState, LeadRecord
from .extractor import extract
from .price_sheet import build_quote, lookup_row, DEFAULT_TIER, describe_row
from .odoo_client import create_lead, OdooError
from .schemas import ContactInfo, QuoteRequest, is_valid_email
from pydantic import ValidationError

logger = logging.getLogger(__name__)
REQUIRED = ["brand", "platform", "format"]

# Conversation stages (stored in ConversationState.partial_request["_stage"])
STAGE_COLLECT = "collect"    # gathering brand / platform / format
STAGE_BUDGET = "budget"      # asking budget + duration
STAGE_DISCOUNT = "discount"  # asking about discount
STAGE_CONTACT = "contact"    # asking contact name / email
STAGE_CONFIRM = "confirm"    # showing summary, waiting for yes/no

# How many times we re-ask before accepting the customer really doesn't want to answer
_MAX_DISCOUNT_ASKS = 1       # "yes" with no number -> one extra question
_MAX_CONTACT_ASKS = 2        # name-only answer -> ask for the email up to twice

# A deal is handed over to the sales team when even the maximum allowed
# discount cannot bring the price within this tolerance of the budget.
BUDGET_ESCALATION_TOLERANCE_PCT = 0.0


# Platform format mapping - what formats are available for each platform
PLATFORM_FORMATS = {
    "youtube": ["integration", "dedicated"],
    "instagram": ["reel"],
    "tiktok": ["video"],
    "snapchat": ["story"],
    "x": ["post"],
}

# Format aliases - map common terms to our standard format names
FORMAT_ALIASES = {
    "dedicated video": "dedicated",
    "dedicated": "dedicated",
    "integration": "integration",
    "reel": "reel",
    "reels": "reel",
    "video": "video",
    "story": "story",
    "stories": "story",
    "post": "post",
}


def _save_lead_record(lead, merged, quote, phone, escalated=False):
    """Save lead to Django database for dashboard tracking."""
    try:
        clean_phone = phone.replace("whatsapp:", "") if phone else ""
        LeadRecord.objects.create(
            odoo_lead_id=lead.odoo_id,
            brand=merged.get("brand", ""),
            platform=merged.get("platform", ""),
            format=merged.get("format", ""),
            tier=quote.get("tier_used", "B"),
            final_quote_sar=quote.get("final"),
            escalated=escalated,
            phone=clean_phone,
            email=merged.get("email", ""),
            contact_name=merged.get("contact_name", ""),
            company_name=merged.get("brand", ""),
            expected_revenue=quote.get("final"),
            probability=5 if escalated else 20,
            stage="Escalated" if escalated else "New",
            description=lead.payload.get("description", ""),
        )
    except Exception as e:
        logger.warning("Failed to save LeadRecord: %s", e)


# Display names for nicer output
PLATFORM_DISPLAY = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "snapchat": "Snapchat",
    "x": "X",
}

FORMAT_DISPLAY = {
    "integration": "Integration",
    "dedicated": "Dedicated Video",
    "reel": "Reel",
    "video": "Video",
    "story": "Story",
    "post": "Post",
}

FORMAT_DISPLAY_AR = {
    "integration": "تكامل",
    "dedicated": "فيديو مخصص",
    "reel": "ريل",
    "video": "فيديو",
    "story": "ستوري",
    "post": "منشور",
}

TIER_DISPLAY = "Tier"


def _display_platform(platform):
    """Human friendly platform name."""
    if not platform:
        return "—"
    return PLATFORM_DISPLAY.get(platform.lower(), platform.title())


def _display_format(fmt, lang="en"):
    """Human friendly format name."""
    if not fmt:
        return "—"
    key = fmt.lower()
    if lang == "ar":
        return FORMAT_DISPLAY_AR.get(key, fmt.title())
    return FORMAT_DISPLAY.get(key, fmt.title())


def _get_available_formats(platform):
    """Get available formats for a platform."""
    return PLATFORM_FORMATS.get(platform, [])


def _script_counts(text):
    """Count Arabic-script and Latin characters in a message."""
    ar = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
    lat = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    return ar, lat


def _detect_language(text):
    """Detect the language of a message from its script.

    Returns 'ar', 'en' or None when the message is undecidable / mixed.
    Returning None lets the caller keep the language already used in the
    conversation, which keeps replies consistent (e.g. for "1", "60 days",
    or "نون ناصر nooon@n.com").
    """
    if not text:
        return None
    ar, lat = _script_counts(text)
    if ar == 0 and lat == 0:
        return None                      # numbers / emoji only
    if ar == 0:
        return "en" if lat > 0 else None
    if lat == 0:
        return "ar" if ar > 0 else None
    # Mixed message: only switch when one script clearly dominates
    if ar >= 3 and lat <= max(1, ar // 4):
        return "ar"
    if lat >= 3 and ar <= 1:
        return "en"
    return None


# ---- Confirmation detection (yes / no / numbered menu choices) ----
_YES_TOKENS = {
    "yes", "y", "yea", "yeah", "yep", "yup", "ok", "okay", "okey", "sure",
    "confirm", "confirmed", "proceed", "correct", "approve", "approved",
    "go", "ahead", "book", "bookit", "done", "accept", "accepted", "1",
    "نعم", "أي", "اي", "أكيد", "اكيد", "تمام", "موافق", "اوكي", "أوكي",
    "اوك", "أوك", "يلا", "ابدأ", "ابدا", "احجز", "تأكيد", "تاكيد", "صحيح",
    "وافق", "١", "١)تم", "تم",
}
_NO_TOKENS = {
    "no", "n", "nope", "nah", "cancel", "stop", "later", "never", "decline",
    "3",
    "لا", "لأ", "ليس", "الغاء", "إلغاء", "الغي", "ألغي", "بعدين", "توقف",
    "٣",
}
_YES_PHRASES = (
    "go ahead", "sounds good", "do it", "yes please", "book it", "i confirm",
    "confirm the booking", "yes i agree", "that works",
    "نعم من فضلك", "أوافق", "اوافق", "تم التأكيد", "تم تاكيد",
)
_NO_PHRASES = (
    "no thanks", "not now", "no discount", "don't", "dont", "not yet",
    "لا شكرا", "لا شكراً", "ليس الان", "ليس الآن", "بدون خصم", "ما اريد",
)


def _detect_confirmation(text):
    """Detect an affirmative / negative answer. Returns True, False or None."""
    if not text:
        return None
    norm = re.sub(r"[^\w\u0600-\u06FF\s']", " ", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return None
    tokens = set(norm.split())
    if tokens & _YES_TOKENS:
        return True
    if tokens & _NO_TOKENS:
        return False
    for phrase in _YES_PHRASES:
        if phrase in norm:
            return True
    for phrase in _NO_PHRASES:
        if phrase in norm:
            return False
    return None


def _parse_number(text):
    """Extract the first number in a message (supports 20,000 / 20k / ٢٠٠٠٠)."""
    if not text:
        return None
    clean = text.replace(",", "").replace("٬", "").replace(" ", "")
    # Arabic-Indic digits -> ASCII
    trans = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
    clean = clean.translate(trans)
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK]", clean)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)", clean)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


_BUDGET_WORDS = ("budget", "ميزانية", "ميزانيتي", "sar", "ريال", "ريالا", "ر.س", "ر س")
_DISCOUNT_WORDS = ("discount", "خصم", "offer", "عرض", "تخفيض")
_DURATION_WORDS = (
    "day", "days", "week", "weeks", "month", "months",
    "يوم", "يومين", "ايام", "أيام", "شهر", "شهرين", "اشهر", "أشهر", "اسبوع", "أسبوع", "اسابيع",
)
_SKIP_TOKENS = {"skip", "none", "nothing", "n/a", "na", "later", "تخطي", "تخطى", "لايوجد", "بدون"}


def _only_number(text):
    """Return the number if the whole message is basically just a number."""
    if not text:
        return None
    stripped = re.sub(r"[^\d.,٫٬٠-٩kK\s]", "", text).strip()
    if not stripped or not re.search(r"[\d٠-٩]", stripped):
        return None
    return _parse_number(stripped)


def _budget_from_text(text):
    """Extract a budget only when it is clearly a budget (keyword or standalone number)."""
    if not text:
        return None
    low = text.lower()
    if any(w in low for w in ("skip", "تخطي", "تخطى")):
        return None
    has_budget_kw = any(w in low for w in _BUDGET_WORDS)
    # "60 days" answers the duration question, not the budget question
    if not has_budget_kw and any(w in low for w in _DURATION_WORDS):
        return None
    if has_budget_kw:
        return _parse_number(text)
    return _only_number(text)


def _discount_from_text(text):
    """Extract a discount percentage only when clearly a discount."""
    if not text:
        return None
    low = text.lower()
    if any(w in low for w in ("no discount", "بدون خصم", "none", "skip", "تخطي")):
        return 0.0
    has_kw = any(w in low for w in _DISCOUNT_WORDS) or "%" in low or "٪" in low
    value = _parse_number(text) if has_kw else _only_number(text)
    if value is None:
        return None
    if 0 <= value <= 100:
        return value
    return None


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_.]*\.[A-Za-z]{2,}$")
_NAME_PREFIXES = ("my name is", "i am", "i'm", "im", "this is", "اسمي", "أنا", "انا", "معك")
_SKIP_WORDS = ("skip", "later", "none", "no email", "تخطي", "تخطى", "بدون", "بعدين")


def _email_from_text(text):
    """Return the first email address found in the message."""
    if not text:
        return None
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


def _name_from_text(text):
    """Return a plausible contact name from the message."""
    if not text:
        return None
    low = text.strip().lower()
    if any(w in low for w in _SKIP_WORDS):
        return None
    # Never let an email address leak into the name ("ali@x.com" -> "ali")
    text = _EMAIL_RE.sub(" ", text).strip()
    if not text:
        return None
    # "my name is X" / "أنا X"
    for prefix in _NAME_PREFIXES:
        if low.startswith(prefix):
            rest = text.strip()[len(prefix):].strip(" .,:-")
            rest = _EMAIL_RE.sub("", rest).strip(" .,:-")
            if rest:
                return rest
    # A message that is only a short name (no email, no digits)
    cleaned = re.sub(r"[^A-Za-z\u0600-\u06FF\s.'\-]", "", text).strip(" .,-")
    words = cleaned.split()
    if 1 <= len(words) <= 4 and len(cleaned) <= 50:
        return cleaned
    return None


def _brand_from_text(text):
    """Fallback: treat a bare name / domain as the brand when we asked for it."""
    if not text:
        return None
    t = text.strip().strip(" .,!،؟?")
    if not t or len(t) > 60:
        return None
    if _DOMAIN_RE.match(t):
        return t
    # Single short token like "Tenhal" (no platform / intent keywords)
    tokens = t.split()
    if 1 <= len(tokens) <= 3:
        low = t.lower()
        blocked = set(PLATFORM_FORMATS) | set(FORMAT_ALIASES) | {
            "ad", "ads", "advert", "campaign", "video", "post", "want", "hi", "hello",
            "إعلان", "حملة", "مرحبا", "أريد", "اريد",
        }
        if not any(tok.strip(".,").lower() in blocked for tok in tokens):
            return t
    return None


def _max_discounted_price(req, quote):
    """Lowest price achievable with the package's maximum allowed discount."""
    row = lookup_row(req["platform"], req["format"], quote.get("tier_used", DEFAULT_TIER))
    if not row:
        return None
    gross = quote.get("gross") or quote.get("final") or 0
    return round(gross * (1 - row["max_discount_pct"] / 100), 2)


def _needs_human_escalation(req, quote):
    """Return (bool, reason) when a human (sales) must handle the deal.

    Escalate when:
      * the requested discount is above the package cap, or
      * even the maximum allowed discount cannot fit the stated budget.
    """
    if quote.get("status") == "ESCALATE":
        return True, "discount_above_cap"

    budget = req.get("budget_sar")
    best = _max_discounted_price(req, quote)
    if budget and best is not None:
        limit = budget * (1 + BUDGET_ESCALATION_TOLERANCE_PCT / 100)
        if best > limit:
            return True, "budget_too_tight"
    return False, ""


def _normalize_format(format_str, platform=None):
    """Normalize format string and auto-correct for platform."""
    if not format_str:
        return None
    
    format_lower = format_str.lower().strip()
    normalized = FORMAT_ALIASES.get(format_lower, format_lower)
    
    # If platform is known, auto-correct format if needed
    if platform and normalized:
        available = _get_available_formats(platform)
        if normalized not in available and len(available) == 1:
            # If only one format available for this platform, use it
            return available[0]
        elif normalized not in available:
            # Try to find a close match
            if normalized in ["dedicated", "dedicated video", "video"] and "video" in available:
                return "video"
            if normalized in ["reel", "reels", "video"] and "reel" in available:
                return "reel"
    
    return normalized


def _save_state(state, data):
    """Persist the conversation state."""
    state.partial_request = data
    state.save(update_fields=["partial_request", "last_message_at"])


def handle_message(phone, text):
    """Main entry point.

    Flow: collect details -> show estimate & ask budget -> ask discount
          -> show summary -> ask confirmation -> only THEN create the lead.
    """
    state, _ = ConversationState.objects.get_or_create(phone=phone)
    partial = dict(state.partial_request or {})
    stage = partial.get("_stage") or STAGE_COLLECT

    try:
        extracted = extract(text, context=partial).model_dump()
    except Exception as e:
        logger.exception("extract failed: %s", e)
        return ("Sorry, a temporary error occurred. Please try again. "
                "| عذراً، حدث خطأ مؤقت. يرجى المحاولة مرة أخرى.")

    # Language: script detection wins, then the language already used in this
    # conversation, then the model's guess (important for inputs like "1").
    lang = _detect_language(text) or partial.get("language") or extracted.get("language") or "en"
    if lang not in ("ar", "en"):
        lang = "en"

    answer = _detect_confirmation(text)
    choice = re.sub(r"[^\w\u0600-\u06FF]", "", text or "")

    # ---------------- CONFIRM stage: numbered menu 1 / 2 / 3 ----------------
    if stage == STAGE_CONFIRM:
        if choice in ("1", "١") or answer is True:
            return _create_lead_and_reply(phone, state, partial, lang)
        if choice in ("2", "٢"):
            partial["_stage"] = STAGE_DISCOUNT
            partial.pop("requested_discount_pct", None)  # ask fresh
            _save_state(state, partial)
            return _ask_discount(partial, lang)
        if choice in ("3", "٣") or answer is False:
            state.delete()
            return _cancelled(lang)
        # Anything else = the user is changing something -> re-show the summary

    merged = {**partial, "language": lang}
    for k, v in extracted.items():
        if k in ("language", "_stage") or v is None:
            continue
        merged[k] = v

    # The model sometimes misses plain numbers -> parse from the raw text
    if stage == STAGE_BUDGET and merged.get("budget_sar") is None:
        budget = _budget_from_text(text)
        if budget:
            merged["budget_sar"] = budget
    if stage == STAGE_DISCOUNT:
        disc = _discount_from_text(text)
        if disc is not None:
            merged["requested_discount_pct"] = disc
        elif merged.get("requested_discount_pct") is None:
            if _is_skip(text) or answer is False:
                merged["requested_discount_pct"] = 0.0
            elif merged.get("_discount_asks", 0) < _MAX_DISCOUNT_ASKS:
                # "yes" without a number -> ask once for the percentage
                merged["_discount_asks"] = merged.get("_discount_asks", 0) + 1
                merged["_stage"] = STAGE_DISCOUNT
                _save_state(state, merged)
                return _ask_discount_pct(merged, lang)

    # Fallback: a bare brand name typed at the "what is your company?" step
    if stage == STAGE_COLLECT and not merged.get("brand"):
        brand_guess = _brand_from_text(text)
        if brand_guess:
            merged["brand"] = brand_guess

    # Contact details (name / email address) — validated by pydantic
    if stage == STAGE_CONTACT:
        info = ContactInfo(
            full_name=extracted.get("contact_name") or _name_from_text(text),
            email=extracted.get("email") or _email_from_text(text),
        )
        if info.email:
            merged["email"] = info.email
        # Never overwrite a name we already validated (e.g. when the follow-up
        # message only carries the email address).
        if info.full_name and not merged.get("contact_name"):
            merged["contact_name"] = info.full_name

    # Normalize / auto-correct the format for the chosen platform
    original_format = merged.get("format")
    if merged.get("format"):
        merged["format"] = _normalize_format(merged["format"], merged.get("platform"))
    format_substituted = bool(
        original_format and merged.get("format") and original_format != merged["format"]
    )

    # ---------------- still collecting required fields ----------------
    missing = [f for f in REQUIRED if not merged.get(f)]
    if missing:
        merged["_stage"] = STAGE_COLLECT
        _save_state(state, merged)
        return _ask_clarifying(missing, lang, merged)

    tier = merged.get("creator_tier") or DEFAULT_TIER
    days = merged.get("requested_days") or 30
    discount = merged.get("requested_discount_pct") or 0.0
    quote = build_quote(merged["platform"], merged["format"], tier, days, discount)

    if quote["status"] == "UNKNOWN":
        merged["_stage"] = STAGE_COLLECT
        _save_state(state, merged)
        return _suggest_alternatives(merged, lang)

    # ---------------- stage routing (NO lead creation here) ----------------
    if stage == STAGE_CONFIRM:
        merged["_stage"] = STAGE_CONFIRM
        _save_state(state, merged)
        return _quote_summary_and_confirm(merged, lang, format_substituted)

    # Discount already above the package cap -> there is no price to negotiate,
    # so skip the budget / discount questions and go straight to the handover.
    discount_above_cap = quote.get("status") == "ESCALATE"

    if stage == STAGE_COLLECT and not discount_above_cap and merged.get("budget_sar") is None:
        merged["_stage"] = STAGE_BUDGET
        _save_state(state, merged)
        return _ask_budget(merged, quote, lang)

    if (stage in (STAGE_COLLECT, STAGE_BUDGET) and not discount_above_cap
            and merged.get("requested_discount_pct") is None):
        merged["_stage"] = STAGE_DISCOUNT
        _save_state(state, merged)
        return _ask_discount(merged, lang)

    # Asked for a discount but got no number -> assume no discount
    if merged.get("requested_discount_pct") is None and not discount_above_cap:
        merged["requested_discount_pct"] = 0.0

    # ---------------- contact details (name / email) ----------------
    if stage == STAGE_CONTACT:
        info = ContactInfo(
            full_name=merged.get("contact_name"),
            email=merged.get("email"),
        )
        if info.full_name:
            merged["contact_name"] = info.full_name
        if info.email:
            merged["email"] = info.email

        # An email is required unless the customer explicitly skipped it.
        if not info.email and not merged.get("_email_skipped"):
            if _is_skip(text):
                merged["_email_skipped"] = True
            elif merged.get("_contact_asks", 0) < _MAX_CONTACT_ASKS:
                merged["_contact_asks"] = merged.get("_contact_asks", 0) + 1
                merged["_stage"] = STAGE_CONTACT
                _save_state(state, merged)
                return _ask_contact(merged, lang, only_missing=True)
            else:
                merged["_email_skipped"] = True  # asked twice already, move on

        merged["_contact_done"] = True
        merged["_stage"] = STAGE_CONFIRM
        _save_state(state, merged)
        return _quote_summary_and_confirm(merged, lang, format_substituted)

    if stage in (STAGE_COLLECT, STAGE_BUDGET, STAGE_DISCOUNT) and not merged.get("_contact_done"):
        merged["_stage"] = STAGE_CONTACT
        _save_state(state, merged)
        return _ask_contact(merged, lang)

    merged["_stage"] = STAGE_CONFIRM
    _save_state(state, merged)
    return _quote_summary_and_confirm(merged, lang, format_substituted)


def _create_lead_and_reply(phone, state, req, lang):
    """Create the Odoo lead + local record. Only called after confirmation."""
    req = {k: v for k, v in req.items() if not k.startswith("_")}

    # Last gate: everything that reaches the price sheet / Odoo is validated.
    try:
        validated = QuoteRequest(
            brand=req.get("brand"),
            platform=req.get("platform"),
            format=req.get("format"),
            creator_tier=req.get("creator_tier") or DEFAULT_TIER,
            requested_days=req.get("requested_days") or 30,
            discount_pct=req.get("requested_discount_pct") or 0,
            budget_sar=req.get("budget_sar"),
        )
    except ValidationError as exc:
        logger.warning("QuoteRequest rejected: %s", exc)
        state.delete()
        if lang == "ar":
            return ("عذراً، بعض التفاصيل غير صحيحة. لنبدأ من جديد — "
                    "ما اسم شركتك والمنصة والصيغة المطلوبة؟")
        return ("Sorry, some of those details don't look valid. Let's restart — "
                "what's your company name, and which platform and format do you need?")

    req.update({
        "brand": validated.brand,
        "platform": validated.platform,
        "format": validated.format,
        "creator_tier": validated.creator_tier,
        "requested_days": validated.requested_days,
        "requested_discount_pct": validated.discount_pct,
        "budget_sar": validated.budget_sar,
    })

    tier = validated.creator_tier
    days = validated.requested_days
    discount = validated.discount_pct
    quote = build_quote(validated.platform, validated.format, tier, days, discount)

    if quote["status"] == "UNKNOWN":
        state.delete()
        return _suggest_alternatives(req, lang)

    escalate, reason = _needs_human_escalation(req, quote)

    try:
        if escalate:
            if reason == "budget_too_tight":
                note = (
                    "ESCALATED — budget too tight. Budget "
                    f"{req.get('budget_sar', 0):,.0f} SAR vs best possible "
                    f"{_max_discounted_price(req, quote):,.0f} SAR "
                    f"(max discount {quote.get('max_discount_pct')}%)"
                )
            else:
                note = f"ESCALATED — {quote.get('reason', 'discount above cap')}"
            lead = create_lead(req, quote, note=note, phone=phone)
            _save_lead_record(lead, req, quote, phone, escalated=True)
            state.delete()
            return _format_escalation(req, quote, lead.odoo_id, lang, reason)

        lead = create_lead(req, quote, phone=phone)
        _save_lead_record(lead, req, quote, phone, escalated=False)
        state.delete()
        return _format_quote(req, quote, lead.odoo_id, lang)
    except OdooError:
        logger.exception("Odoo failed")
        if lang == "ar":
            return "عذراً، لم نتمكن من تسجيل الطلب. يرجى المحاولة بعد دقائق."
        return "Sorry, we couldn't register your request. Please try again in a few minutes."


def _cancelled(lang="en"):
    if lang == "ar":
        return "تم الإلغاء، لا مشكلة! 👋 إذا احتجت أي شيء آخر أنا هنا."
    return "Cancelled — no problem! 👋 If you need anything else, I'm here."


def _ask_clarifying(missing, lang="en", merged=None):
    """Ask clarifying questions in the user's language."""
    merged = merged or {}
    
    if lang == "ar":
        q = {"brand": "ما اسم شركتك؟", "platform": "ما المنصة؟ (يوتيوب، إنستقرام، تيك توك، سناب، إكس)", "format": "ما نوع المحتوى؟"}
        greeting, intro = "شكراً لتواصلك مع POWR! 👋", "لتحضير عرض السعر:"
    else:
        q = {"brand": "What is your company name?", "platform": "Which platform? (YouTube, Instagram, TikTok, Snapchat, X)", "format": "What type of content?"}
        greeting, intro = "Thanks for contacting POWR! 👋", "To prepare your quote:"
    
    # Customize format question based on platform
    if "format" in missing and merged.get("platform"):
        available = _get_available_formats(merged["platform"])
        if available:
            names = [_display_format(f, lang) for f in available]
            plat = _display_platform(merged["platform"])
            if lang == "ar":
                q["format"] = f"ما نوع المحتوى على {plat}؟ ({'، '.join(names)})"
            else:
                q["format"] = f"What format on {plat}? ({', '.join(names)})"

    # Show what we already know
    known = []
    if merged.get("brand") and "brand" not in missing:
        known.append(f"Brand: {merged['brand']}" if lang != "ar" else f"العلامة: {merged['brand']}")
    if merged.get("platform") and "platform" not in missing:
        known.append(f"Platform: {_display_platform(merged['platform'])}" if lang != "ar"
                     else f"المنصة: {_display_platform(merged['platform'])}")
    if merged.get("format") and "format" not in missing:
        known.append(f"Format: {_display_format(merged['format'], lang)}" if lang != "ar"
                     else f"الصيغة: {_display_format(merged['format'], lang)}")

    # Greet only on the first turn; follow-ups get a short nudge instead of
    # repeating the whole welcome message.
    if known:
        if lang == "ar":
            lines = ["تمام 👍 نحتاج فقط:"] + [f"• {q[m]}" for m in missing]
        else:
            lines = ["Got it 👍 I just need:"] + [f"• {q[m]}" for m in missing]
        lines.append("\n✓ " + " · ".join(known))
    else:
        lines = [greeting, intro] + [f"• {q[m]}" for m in missing]

    return "\n".join(lines)


def _ask_budget(req, quote, lang="en"):
    """Show the estimate and ask for budget + duration."""
    plat = _display_platform(req.get("platform"))
    fmt = _display_format(req.get("format"), lang)
    days = req.get("requested_days") or 30
    if lang == "ar":
        return (
            f"ممتاز! 👌 هذا العرض التقديري:\n\n"
            f"📊 {plat} — {fmt} · {TIER_DISPLAY} {quote['tier_used']}\n"
            f"• المدة: {days} يوم\n"
            f"• السعر التقديري: {quote['final']:,.0f} SAR\n\n"
            f"الآن، ما ميزانيتك التقريبية بالريال؟ 💰\n"
            f"وهل المدة مناسبة أم تريد مدة أخرى (مثلاً 60 أو 90 يوم)؟\n\n"
            f"ℹ️ إذا لم تحدد ميزانية اكتب *تخطي*."
        )
    return (
        f"Great! 👌 Here's the estimate:\n\n"
        f"📊 {plat} — {fmt} · {TIER_DISPLAY} {quote['tier_used']}\n"
        f"• Duration: {days} days\n"
        f"• Estimated price: {quote['final']:,.0f} SAR\n\n"
        f"Now, what's your approximate budget in SAR? 💰\n"
        f"And is this duration okay, or do you need a different one (e.g. 60 or 90 days)?\n\n"
        f"ℹ️ Type *skip* if you don't have a budget in mind."
    )


def _ask_discount(req, lang="en"):
    """Ask whether the user wants a discount."""
    budget = req.get("budget_sar")
    if lang == "ar":
        head = "👍 تمام."
        if budget:
            head += f"\n💰 الميزانية: {budget:,.0f} SAR"
        return (
            f"{head}\n\n"
            f"هل تريد طلب خصم؟ 🏷️\n"
            f"إذا نعم، اذكر النسبة (مثلاً 10%).\n"
            f"إذا لا، اكتب *بدون خصم* أو *0*."
        )
    head = "👍 Noted."
    if budget:
        head += f"\n💰 Budget: {budget:,.0f} SAR"
    return (
        f"{head}\n\n"
        f"Would you like to request a discount? 🏷️\n"
        f"If yes, tell me the percentage (e.g. 10%).\n"
        f"If not, type *no discount* or *0*."
    )


def _quote_lines(req, quote, lang="en", format_substituted=False):
    """Shared quote body (no reference / CTA)."""
    plat = _display_platform(req.get("platform"))
    fmt = _display_format(req.get("format"), lang)
    days = req.get("requested_days") or 30
    budget = req.get("budget_sar")
    row = lookup_row(req["platform"], req["format"], quote["tier_used"])
    max_disc = row["max_discount_pct"] if row else 0

    if lang == "ar":
        lines = [
            f"• المنصة: {plat}",
            f"• الصيغة: {fmt}",
            f"• الفئة: {TIER_DISPLAY} {quote['tier_used']}"
            + (" (افتراضي)" if quote.get("tier_defaulted") else ""),
            f"• المدة: {days} يوم",
        ]
        if format_substituted:
            lines.append(f"ℹ️ {plat} متوفر بصيغة {fmt} فقط")
        if quote.get("extra_blocks"):
            lines.append(f"• مدة إضافية: +{quote['extra_blocks'] * 20}%")
        if quote.get("discount_pct"):
            lines.append(f"• الخصم: {quote['discount_pct']:.0f}%")
        lines.append(f"\n💰 *الإجمالي: {quote['final']:,.0f} SAR*")
    else:
        lines = [
            f"• Platform: {plat}",
            f"• Format: {fmt}",
            f"• Tier: {quote['tier_used']}" + (" (default)" if quote.get("tier_defaulted") else ""),
            f"• Duration: {days} days",
        ]
        if format_substituted:
            lines.append(f"ℹ️ {plat} is available as {fmt} only")
        if quote.get("extra_blocks"):
            lines.append(f"• Extra duration: +{quote['extra_blocks'] * 20}%")
        if quote.get("discount_pct"):
            lines.append(f"• Discount: {quote['discount_pct']:.0f}%")
        lines.append(f"\n💰 *Total: {quote['final']:,.0f} SAR*")

    # Budget check
    if budget:
        if quote["final"] <= budget:
            lines.append(
                f"✅ ضمن ميزانيتك ({budget:,.0f} SAR)" if lang == "ar"
                else f"✅ Within your budget ({budget:,.0f} SAR)"
            )
        else:
            gap = quote["final"] - budget
            if lang == "ar":
                lines.append(f"⚠️ أعلى من ميزانيتك بـ {gap:,.0f} SAR (أقصى خصم متاح {max_disc}%)")
            else:
                lines.append(
                    f"⚠️ Exceeds your budget by {gap:,.0f} SAR "
                    f"(max discount available {max_disc}%)"
                )
    return lines


def _quote_summary_and_confirm(req, lang="en", format_substituted=False):
    """Full summary + numbered confirmation menu (no lead created yet)."""
    tier = req.get("creator_tier") or DEFAULT_TIER
    days = req.get("requested_days") or 30
    discount = req.get("requested_discount_pct") or 0.0
    quote = build_quote(req["platform"], req["format"], tier, days, discount)

    if quote["status"] == "UNKNOWN":
        return _suggest_alternatives(req, lang)

    plat = _display_platform(req["platform"])
    fmt = _display_format(req["format"], lang)
    escalate, esc_reason = _needs_human_escalation(req, quote)
    best = _max_discounted_price(req, quote)

    if lang == "ar":
        lines = [f"شكراً {req['brand']}! 🎉", "", "📋 *ملخص العرض* — يرجى المراجعة:"]
    else:
        lines = [f"Thank you {req['brand']}! 🎉", "", "📋 *Quote summary* — please review:"]

    if quote["status"] == "ESCALATE":
        if lang == "ar":
            lines += [
                f"• المنصة: {plat}",
                f"• الصيغة: {fmt}",
                f"• المدة: {days} يوم",
                f"• الخصم المطلوب: {discount:.0f}%",
                f"\n⚠️ الحد الأقصى للخصم على هذه الباقة هو {quote['max_discount_pct']}%.",
                "سيتم تحويل طلبك لفريق المبيعات لمراجعة الخصم.",
            ]
        else:
            lines += [
                f"• Platform: {plat}",
                f"• Format: {fmt}",
                f"• Duration: {days} days",
                f"• Requested discount: {discount:.0f}%",
                f"\n⚠️ The maximum discount for this package is {quote['max_discount_pct']}%.",
                "Your request will be forwarded to our sales team for review.",
            ]
    else:
        lines.extend(_quote_lines(req, quote, lang, format_substituted))
        if esc_reason == "budget_too_tight" and best is not None:
            if lang == "ar":
                lines.append(f"\n🤝 ميزانيتك أقل من الحد الأدنى لهذه الباقة "
                             f"(أفضل سعر تلقائي {best:,.0f} SAR).")
                lines.append("سنحوّل طلبك لمختص مبيعات لترتيب عرض خاص.")
            else:
                lines.append(f"\n🤝 Your budget is below the minimum for this package "
                             f"(best automatic price {best:,.0f} SAR).")
                lines.append("We'll hand your request to a sales specialist for a custom offer.")

    # Contact details collected in the previous step
    contact = []
    if req.get("contact_name"):
        contact.append(("الاسم", "Name", req["contact_name"]))
    if req.get("email"):
        contact.append(("البريد", "Email", req["email"]))
    if contact:
        lines.append("")
        lines.append("📇 *بيانات التواصل*:" if lang == "ar" else "📇 *Contact details*:")
        for ar_label, en_label, value in contact:
            lines.append(f"• {ar_label if lang == 'ar' else en_label}: {value}")

    lines.append("")
    if lang == "ar":
        lines += [
            "هل تريد تأكيد الطلب؟",
            "1️⃣ نعم — أنشئ الطلب",
            "2️⃣ أريد خصم / تعديل الميزانية",
            "3️⃣ إلغاء",
            "",
            "أرسل *1* أو *2* أو *3* (أو اكتب نعم / لا).",
        ]
    else:
        lines += [
            "Would you like to confirm this booking?",
            "1️⃣ Yes — create my booking",
            "2️⃣ I'd like a discount / adjust budget",
            "3️⃣ Cancel",
            "",
            "Reply *1*, *2* or *3* (or just say yes / no).",
        ]

    return "\n".join(lines)


def _is_skip(text):
    """True when the customer is explicitly declining to answer."""
    if not text:
        return False
    low = text.strip().lower()
    return any(word in low for word in (
        "skip", "no email", "without email", "none", "nothing", "later",
        "تخطي", "تخطى", "بدون", "بدون بريد", "لا املك", "لا أملك", "بعدين",
    ))


def _ask_discount_pct(req, lang="en"):
    """Re-ask only for the discount percentage (after a bare 'yes')."""
    if lang == "ar":
        return (
            "تمام! ما نسبة الخصم المطلوبة؟ 🏷️\n\n"
            "اكتب الرقم فقط، مثلاً: 10\n"
            "أو اكتب *بدون خصم* إذا لم تكن تريد خصماً."
        )
    return (
        "Sure — what discount percentage would you like? 🏷️\n\n"
        "Just send the number, e.g. 10\n"
        "Or type *no discount* if you don't need one."
    )


def _ask_contact(req, lang="en", only_missing=False):
    """Ask for the contact name + email before confirming."""
    plat = _display_platform(req.get("platform"))
    fmt = _display_format(req.get("format"), lang)
    has_name = bool(req.get("contact_name"))

    if only_missing:
        # Follow-up: we already have one of the two -> ask for the email only
        if lang == "ar":
            name_bit = f"شكراً {req['contact_name']}! " if has_name else ""
            return (
                f"{name_bit}ناقص شي واحد فقط 📧\n\n"
                f"ما بريدك الإلكتروني لإرسال العرض عليه؟\n"
                f"إذا لا تريد إضافته اكتب *تخطي*."
            )
        name_bit = f"Thanks {req['contact_name']}! " if has_name else ""
        return (
            f"{name_bit}One thing missing 📧\n\n"
            f"What's your email address for the proposal?\n"
            f"Type *skip* if you'd rather not share it."
        )

    if lang == "ar":
        return (
            "📇 قربنا نخلص! نحتاج بيانات التواصل لإرسال العرض:\n\n"
            "• الاسم الكامل\n"
            "• البريد الإلكتروني\n\n"
            "ℹ️ رقم واتساب مسجّل معنا تلقائياً.\n"
            "إذا لا تريد إضافة بريد، اكتب *تخطي*.\n\n"
            f"({plat} — {fmt})"
        )
    return (
        "📇 Almost done! I need your contact details for the proposal:\n\n"
        "• Full name\n"
        "• Email address\n\n"
        "ℹ️ Your WhatsApp number is already captured.\n"
        "Type *skip* if you'd rather not share an email.\n\n"
        f"({plat} — {fmt})"
    )


def _suggest_alternatives(merged, lang="en"):
    """Suggest available alternatives when package not found."""
    platform = merged.get("platform", "")
    plat = _display_platform(platform)
    available = _get_available_formats(platform)
    requested = _display_format(merged.get("format"), lang)
    if lang == "ar":
        if available:
            names = "، ".join(_display_format(f, lang) for f in available)
            return (f"عذراً، صيغة {requested} غير متوفرة على {plat}.\n\n"
                    f"الصيغ المتوفرة: {names}\n\nأي واحدة تفضل؟")
        return "عذراً، هذه الباقة غير متوفرة. ما المنصة والصيغة التي تفضلها؟"
    if available:
        names = ", ".join(_display_format(f, lang) for f in available)
        return (f"Sorry, {requested} isn't available on {plat}.\n\n"
                f"Available formats: {names}\n\nWhich would you prefer?")
    return "Sorry, that package isn't available. Which platform and format would you prefer?"


def _format_escalation(req, quote, lead_id, lang="en", reason=""):
    """Escalation message — a human sales agent takes over the deal."""
    budget = req.get("budget_sar")
    best = _max_discounted_price(req, quote)
    plat = _display_platform(req.get("platform"))
    fmt = _display_format(req.get("format"), lang)
    days = req.get("requested_days") or 30
    disc = req.get("requested_discount_pct") or 0.0
    cap = quote.get("max_discount_pct")

    if lang == "ar":
        lines = [
            "🤝 تم تحويل طلبك إلى فريق المبيعات (دعم بشري)",
            "",
            "📋 ملخص الطلب:",
            f"• المنصة: {plat}",
            f"• الصيغة: {fmt}",
            f"• المدة: {days} يوم",
        ]
        if budget:
            lines.append(f"• ميزانيتك: {budget:,.0f} SAR")
        if reason == "budget_too_tight" and best is not None:
            lines.append(f"• أفضل سعر متاح تلقائياً: {best:,.0f} SAR (أقصى خصم {cap}%)")
            lines.append("\n⚠️ ميزانيتك أقل من الحد الأدنى لهذه الباقة.")
            lines.append("سيتواصل معك أحد مختصي المبيعات لترتيب عرض خاص يناسب ميزانيتك.")
        else:
            lines.append(f"• الخصم المطلوب: {disc:.0f}% (الحد الأقصى {cap}%)")
            lines.append(f"\n⚠️ الخصم المطلوب أعلى من الحد المسموح ({cap}%).")
            lines.append("سيقوم أحد مختصي المبيعات بمراجعة الخصم والتواصل معك.")
        lines.append(f"\n📝 رقم المرجع: #{lead_id}")
        lines.append("⏱️ عادةً نتواصل خلال 24 ساعة.")
        lines.append("شكراً لتفهمك! 🌟")
        return "\n".join(lines)

    lines = [
        "🤝 Connecting you with our sales team (human support)",
        "",
        "📋 Request summary:",
        f"• Platform: {plat}",
        f"• Format: {fmt}",
        f"• Duration: {days} days",
    ]
    if budget:
        lines.append(f"• Your budget: {budget:,.0f} SAR")
    if reason == "budget_too_tight" and best is not None:
        lines.append(f"• Best automated price: {best:,.0f} SAR (max discount {cap}%)")
        lines.append("\n⚠️ Your budget is below the minimum for this package.")
        lines.append("One of our sales specialists will contact you to arrange a custom offer that fits.")
    else:
        lines.append(f"• Requested discount: {disc:.0f}% (maximum {cap}%)")
        lines.append(f"\n⚠️ The requested discount is above the maximum of {cap}%.")
        lines.append("A sales specialist will review your case and get back to you.")
    lines.append(f"\n📝 Reference: #{lead_id}")
    lines.append("⏱️ We usually respond within 24 hours.")
    lines.append("Thanks for your understanding! 🌟")
    return "\n".join(lines)


def _format_quote(req, quote, lead_id, lang="en", format_substituted=False):
    """Format quote in user's language."""
    brand = req['brand']
    if lang == "ar":
        lines = [f"شكراً {brand}! 🎉", f"📋 عرض السعر ({quote['code']}):"]
        lines.extend(_quote_lines(req, quote, lang, format_substituted))
        lines.append(f"\n📝 مرجع: #{lead_id}")
        lines.append("سيتواصل معك فريقنا قريباً! 🚀")
    else:
        lines = [f"Thank you {brand}! 🎉", f"📋 Your Quote ({quote['code']}):"]
        lines.extend(_quote_lines(req, quote, lang, format_substituted))
        lines.append(f"\n📝 Reference: #{lead_id}")
        lines.append("Our team will contact you soon! 🚀")

    return "\n".join(lines)