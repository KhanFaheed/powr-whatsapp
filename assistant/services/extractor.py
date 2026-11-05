import os
import logging
from typing import Optional
from openai import OpenAI, APIError
from pydantic import ValidationError

from .schemas import ExtractedRequest  # validated in one place (services/schemas.py)

logger = logging.getLogger(__name__)

# FIX 1: base_url MUST include /api/v1
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],   # FIX 2: fail loud if missing
    timeout=15.0,                              # FIX 3: OpenRouter adds latency
    default_headers={
        "HTTP-Referer": "https://powr-bot.rankforgeacademy.in",
        "X-Title": "POWR WhatsApp Bot",
    },
)

# ExtractedRequest is re-exported for backwards compatibility
__all__ = ["ExtractedRequest", "extract", "PROMPT", "MODELS"]


PROMPT = """You are a data extraction engine for POWR (an influencer marketing agency in Saudi Arabia).
Extract structured advertising partnership requests from WhatsApp messages.

Return ONLY a JSON object matching this schema:
{
  "brand": string | null,
  "platform": "youtube" | "instagram" | "tiktok" | "snapchat" | "x" | null,
  "format": "integration" | "dedicated" | "story" | "reel" | "video" | "post" | null,
  "creator_tier": "A" | "B" | "C" | null,
  "requested_days": integer | null,
  "budget_sar": number | null,
  "requested_discount_pct": number | null,
  "language": "ar" | "en" | "mixed",
  "contact_name": string | null,
  "email": string | null
}

LANGUAGE RULE (critical):
- "language" describes the language the USER wrote THIS message in.
- If the message is written in English letters/English words → "en".
- If the message is written in Arabic script → "ar".
- Only use "mixed" if the same message truly mixes both Arabic script AND English sentences.

PLATFORM RULES (accept spelling variants and typos):
- "tiktok" ← tiktok, tik tok, tic tok, tt, تيك توك, تيكتوك
- "instagram" ← instagram, insta, ig, إنستقرام, انستقرام, انستا
- "snapchat" ← snapchat, snap, سناب, سناب شات
- "youtube" ← youtube, yt, يوتيوب
- "x" ← x, twitter, تويتر, إكس

FORMAT RULES (map the user's intent, do not invent):
- "video" ← video, فيديو, "dedicated video" when the platform is TikTok/Snapchat
  (TikTok and Snapchat content is a single video — map to "video")
- "dedicated" ← dedicated, "dedicated video" / فيديو مخصص when the platform is YouTube
- "integration" ← integration, mention, "دمج", تكامل
- "reel" ← reel, reels, ريل, ريلز (Instagram short video)
- "story" ← story, stories, ستوري
- "post" ← post, tweet, منشور, تغريدة

OTHER RULES:
- brand: the company/advertiser name ("my company X", "we are X", "نحن علامة X")
- creator_tier: ONLY if explicitly stated (Tier A/B/C, "big creators", "small creators")
- requested_days: 1 week=7, 2 weeks=14, 1 month=30, 2 months=60, 3 months=90
- budget_sar: number in SAR only (ريال, SAR, ر.س → number)
- requested_discount_pct: number only ("30%" → 30)
- contact_name: a person's name if given ("I am Ahmed", "أنا أحمد")
- email: an email address if present

Only extract what the CURRENT message states. Use the conversation context only to interpret
follow-ups (e.g. "yes", "for tiktok", "reel") — never to invent new values.
Return null for anything not stated.

EXAMPLES:
Input: "Hi I want an ad for My company Tenhal for tik tok what is cost"
Output: {"brand":"Tenhal","platform":"tiktok","format":null,"creator_tier":null,"requested_days":null,"budget_sar":null,"requested_discount_pct":null,"language":"en","contact_name":null,"email":null}

Input: "We are looking for a dedicated video"  (context: platform=tiktok, brand=Tenhal)
Output: {"brand":null,"platform":null,"format":"video","creator_tier":null,"requested_days":null,"budget_sar":null,"requested_discount_pct":null,"language":"en","contact_name":null,"email":null}

Input: "For TikTok reel"
Output: {"brand":null,"platform":"tiktok","format":"reel","creator_tier":null,"requested_days":null,"budget_sar":null,"requested_discount_pct":null,"language":"en","contact_name":null,"email":null}

Input: "مرحباً، نحن علامة نون. نرغب في حملة إعلانية على إنستقرام (ريل) لمدة شهر، بميزانية 20,000 ريال."
Output: {"brand":"نون","platform":"instagram","format":"reel","creator_tier":null,"requested_days":30,"budget_sar":20000,"requested_discount_pct":null,"language":"ar","contact_name":null,"email":null}
"""


MODELS = [
    "openai/gpt-4o-mini",           # strongest structured-output support
    "google/gemini-2.5-flash",      # fallback
    "anthropic/claude-3.5-haiku",   # second fallback
]


def extract(text: str, context: Optional[dict] = None) -> ExtractedRequest:
    """Extract a structured request from a message.

    Args:
        text: The user's latest message.
        context: Previously known values (brand/platform/format/etc.) so the model
                 can interpret short follow-ups like "yes" or "for tiktok".
    """
    last_exception = None

    user_content = text
    if context:
        # Skip internal bookkeeping keys (e.g. "_stage") and empty values
        known = {
            k: v for k, v in context.items()
            if not k.startswith("_") and v not in (None, "", {})
        }
        if known:
            user_content = (
                "Conversation context (already known, do not repeat unless restated):\n"
                + "\n".join(f"- {k}: {v}" for k, v in known.items())
                + f"\n\nNew user message: {text}"
            )

    for model_name in MODELS:
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = resp.choices[0].message.content
            return ExtractedRequest.model_validate_json(content)
        except (APIError, ValidationError) as e:
            logger.warning("Model %s failed: %s", model_name, e)
            last_exception = e
            continue

    raise RuntimeError(f"All extraction models failed: {last_exception}") from last_exception