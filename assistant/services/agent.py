import logging
from assistant.models import ConversationState
from .extractor import extract
from .price_sheet import build_quote, lookup_row, DEFAULT_TIER
from .odoo_client import create_lead, OdooError

logger = logging.getLogger(__name__)
REQUIRED = ["brand", "platform", "format"]


def handle_message(phone, text):
    state, _ = ConversationState.objects.get_or_create(phone=phone)
    partial = state.partial_request or {}
    #partial->old information bot already knows about the user, text->new information user just sent to bot
    try:
        extracted = extract(text).model_dump()
    except Exception as e:
        logger.exception("extract failed: %s", e)
        return "عذراً، حدث خطأ مؤقت. يرجى المحاولة مرة أخرى."

    merged = {**partial}
    for k, v in extracted.items():
        if v is not None and k != "language":
            merged[k] = v

    missing = [f for f in REQUIRED if not merged.get(f)]
    if missing:
        state.partial_request = merged
        state.save(update_fields=["partial_request", "last_message_at"])
        return _ask_clarifying(missing)

    tier = merged.get("creator_tier") or DEFAULT_TIER
    days = merged.get("requested_days") or 30
    discount = merged.get("requested_discount_pct") or 0.0

    quote = build_quote(merged["platform"], merged["format"], tier, days, discount)

    if quote["status"] == "UNKNOWN":
        state.partial_request = merged
        state.save(update_fields=["partial_request", "last_message_at"])
        return "عذراً، الباقة غير متوفرة. هل ترغب في منصة أو صيغة مختلفة؟"

    try:
        if quote["status"] == "ESCALATE":
            lead = create_lead(merged, quote, note=f"ESCALATED — {quote['reason']}")
            state.delete()
            return (
                f"شكراً لاهتمامك بـ POWR.\n\n"
                f"بخصوص طلب الخصم ({quote['requested_discount_pct']:.0f}%)، "
                f"الحد الأقصى لهذه الباقة {quote['max_discount_pct']}%.\n\n"
                f"تم تحويلك لفريق المبيعات (مرجع: {lead.odoo_id}) وسيتواصلون خلال 24 ساعة."
            )
        lead = create_lead(merged, quote)
        state.delete()
        return _format_quote(merged, quote, lead.odoo_id)
    except OdooError:
        logger.exception("Odoo failed")
        return "عذراً، لم نتمكن من تسجيل الطلب. يرجى المحاولة بعد دقائق."


def _ask_clarifying(missing):
    q = {
        "brand": "ما اسم العلامة التجارية؟",
        "platform": "ما المنصة (يوتيوب، إنستقرام، تيك توك، سناب، إكس)؟",
        "format": "ما نوع المحتوى (تكامل، فيديو مخصص، ستوري، ريل، فيديو، منشور)؟",
    }
    body = "\n".join(f"• {q[m]}" for m in missing)
    return f"شكراً لتواصلك مع POWR. لتحضير عرض السعر:\n\n{body}"


def _format_quote(req, quote, lead_id):
    lines = [
        f"شكراً لاهتمامك بـ POWR، {req['brand']}.\n",
        f"عرض السعر ({quote['code']}):",
        f"• المنصة: {req['platform']}",
        f"• الصيغة: {req['format']}",
        f"• الفئة: Tier {quote['tier_used']}" + (" (افتراضي)" if quote.get("tier_defaulted") else ""),
        f"• المدة: {req.get('requested_days', 30)} يوم",
    ]
    if quote["extra_blocks"]:
        lines.append(f"• فترات إضافية: {quote['extra_blocks']} × 30 يوم (+{quote['extra_blocks']*20}%)")
    lines.append(f"• السعر الأساسي: {quote['base']:,.0f} SAR")
    if quote["extra_blocks"]:
        lines.append(f"• بعد المدة: {quote['gross']:,.0f} SAR")
    if quote["discount_pct"]:
        lines.append(f"• الخصم: {quote['discount_pct']:.0f}%")
    lines.append(f"\n*الإجمالي: {quote['final']:,.0f} SAR*")

    budget = req.get("budget_sar")
    if budget:
        if quote["final"] <= budget:
            lines.append(f"\n✓ داخل الميزانية ({budget:,.0f} SAR).")
        else:
            row = lookup_row(req["platform"], req["format"], quote["tier_used"])
            max_disc = row["max_discount_pct"] if row else 0
            gap = quote["final"] - budget
            lines.append(f"\n⚠ الميزانية أقل بـ {gap:,.0f} SAR. أقصى خصم {max_disc}%.")

    lines.append(f"\nمرجع: {lead_id}")
    return "\n".join(lines)