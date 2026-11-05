# Design — POWR WhatsApp Ad-Partnership Assistant

*One-page write-up. Full setup and test-case transcripts: `README.md`.*

## 1. Goal

Turn an Arabic/English WhatsApp message into a **correct, sheet-accurate quote** and — when the
deal is valid — an **Odoo CRM opportunity**, without ever inventing data or promising a price the
price sheet doesn't allow.

## 2. Architecture

```
WhatsApp ──► Twilio ──► POST /webhook/  (Django view)
                            │ signature check · MessageSid idempotency
                            ▼
                      InboundMessage (DB)
                            ▼
                   handle_message(phone, text)          assistant/services/agent.py
                            │
              ┌─────────────┼──────────────────┬─────────────────┐
              ▼             ▼                  ▼                 ▼
        extractor.py   ConversationState   price_sheet.py    odoo_client.py
        (LLM → strict  (per-phone memory   (money maths,     (crm.lead via
         JSON schema)   in DB)              discount caps)    XML-RPC)
              └─────────────┴──────────────────┴─────────────────┘
                            ▼
                     TwiML / JSON reply  ──►  WhatsApp
```

## 3. Conversation state machine

```
collect ──► budget ──► discount ──► contact ──► confirm ──► create opportunity
   │           │           │            │          │
   │           │           │            │          └─ 1 yes · 2 discount · 3 cancel
   │           │           │            └─ name + email (phone known from WhatsApp)
   │           │           └─ "%" · "no discount" · "0" · skip
   │           └─ budget in SAR (+ optional duration change)
   └─ brand · platform · format — asks only what is missing

short-circuit: discount already above the package cap
               → skip budget/discount → contact → summary → handover
```

State lives in one JSON column (`ConversationState.partial_request`), so the bot survives restarts
and Twilio retries; it is deleted once the opportunity is created or the user cancels.

## 4. Key decisions

| Decision | Why |
|---|---|
| **LLM extracts, Python calculates** | A quote can never disagree with the sheet; money logic is testable offline |
| **Nullable schema, never guess** | Unstated fields stay `null`; the bot asks instead of defaulting to `0` |
| **Ask only what's missing** | One short question per message, echoing what was understood |
| **Confirm before CRM write** | The pipeline only holds agreed conversations; a natural insertion point for handover |
| **Two escalation rules** | (a) discount above cap, (b) budget below the cheapest achievable price → human with a summary |
| **Language = conversation property** | Ambiguous inputs (`1`, `60 days`, mixed AR+email) keep the established language |
| **Deterministic plumbing** | Platform/format normalisation + brand fallback live in code, not in the prompt |
| **Validation as a hard gate** | `schemas.py` (Pydantic) coerces or rejects every value before it reaches pricing or Odoo |

## 4b. Validation layer (`assistant/services/schemas.py`)

Three Pydantic models guard the pipeline: `ExtractedRequest` (what the LLM may return),
`ContactInfo` (name/email captured before handover) and `QuoteRequest` (the last gate before
`build_quote()` and the Odoo call). Values are normalised, never trusted:
`"Yotube"`→`youtube`, `"Tier A (10M+)"`→`A`, `"100,000 SAR"`→`100000`, `"30%"`→`30`,
`900 days`→`None`, `"null"`/`"غير محدد"`→`None`, `"ali@x"`→`None` (so the bot asks again).
Covered by 23 tests: `python manage.py test assistant`.

## 5. Money rules (`price_sheet.py`)

```
multiplier = 1 + ceil(max(0, days - 30) / 30) × 0.20
gross      = base × multiplier
final      = gross × (1 - discount_pct / 100)

discount_pct > row.max_discount_pct        → ESCALATE (no price quoted)
no row for (platform, format, tier)        → UNKNOWN  (asks for an alternative)
gross × (1 - max_discount/100) > budget    → handover (budget below minimum)
```

Tier defaults to **B** when unstated, and the quote labels it `(default)`.

## 6. Test-case mapping

| Case | Input | Behaviour | Proven by |
|---|---|---|---|
| **A** | brand + platform + duration + budget | Estimate → discount → contact → confirm → Odoo opportunity; `54,000 = 45,000 × 1.2` (YT-INT-B, 60 days) | `dev_test.py --test A` |
| **B** | brand only | Asks platform/format, then budget; `skip` leaves budget empty — nothing invented | `dev_test.py --test B` |
| **C** | 30 % discount on Tier A (cap 10 %) | Skips budget, no price stated, summary + handover, lead flagged `escalated=True` | `dev_test.py --test C` |

## 7. Security

- Secrets only from environment variables (`.env` git-ignored, `.env.example` = names only).
- Twilio webhook signature validation (`RequestValidator`); skipped only when `DEBUG=True`.
- `MessageSid` unique constraint → idempotent under Twilio retries.
- CSRF on for all pages; exempt only for the signed webhook and the JSON chat endpoint.
- Odoo API key (revocable) instead of a password; only `crm.lead:create` is called.

## 8. What I'd do next

Approved WhatsApp content templates for outbound after the 24-hour window · Arabic dialect
golden-set evaluation · auth in front of the operator UI · Postgres + Celery for scale ·
price sheet served from a versioned DB table instead of a module constant.
