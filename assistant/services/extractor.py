import os
import logging
from typing import Optional
from openai import OpenAI, APIError
from pydantic import BaseModel, ValidationError

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


class ExtractedRequest(BaseModel):
    brand: Optional[str] = None
    platform: Optional[str] = None
    format: Optional[str] = None
    creator_tier: Optional[str] = None
    requested_days: Optional[int] = None
    budget_sar: Optional[float] = None
    requested_discount_pct: Optional[float] = None
    language: str = "ar"


PROMPT = """Extract structured ad partnership requests from WhatsApp messages (Arabic/English).

Return ONLY a JSON object matching this schema:
{
  "brand": string | null,
  "platform": "youtube" | "instagram" | "tiktok" | "snapchat" | "x" | null,
  "format": "integration" | "dedicated" | "story" | "reel" | "video" | "post" | null,
  "creator_tier": "A" | "B" | "C" | null,
  "requested_days": integer | null,
  "budget_sar": number | null,
  "requested_discount_pct": number | null,
  "language": "ar" | "en" | "mixed"
}

RULES:
- platform: youtube|instagram|tiktok|snapchat|x (يوتيوب/إنستقرام/تيك توك/سناب/إكس)
- format: integration|dedicated|story|reel|video|post
- creator_tier: only if explicitly stated (Tier A/B/C, "big creators", "small creators")
- requested_days: 1 week=7, 2 weeks=14, 1 month=30, 2 months=60, 3 months=90
- budget_sar: number only
- requested_discount_pct: number only ("30%" → 30)

DO NOT invent values. Return null for anything not explicitly stated.

Example input: "مرحباً، نحن علامة نون. نرغب في حملة إعلانية على إنستقرام (ريل) لمدة شهر، بميزانية 20,000 ريال."
Example output: {"brand":"نون","platform":"instagram","format":"reel","creator_tier":null,"requested_days":30,"budget_sar":20000,"requested_discount_pct":null,"language":"ar"}
"""


MODELS = [
    "openai/gpt-4o-mini",           # strongest structured-output support
    "google/gemini-2.5-flash",      # fallback
    "anthropic/claude-3.5-haiku",   # second fallback
]


def extract(text: str) -> ExtractedRequest:
    last_exception = None

    for model_name in MODELS:
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": text},
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