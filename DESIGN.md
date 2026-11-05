# POWR WhatsApp Bot - Design Document

## Overview

A WhatsApp assistant that processes advertising partnership requests in Arabic, generates price quotes from a price sheet, creates opportunities in Odoo CRM, and handles discount escalations.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  WhatsApp   │────▶│   Twilio    │────▶│   Django    │────▶│   Celery    │
│   User      │◀────│   Webhook   │◀────│   Views     │◀────│   Worker    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    │                          │                          │
                    ▼                          ▼                          ▼
            ┌─────────────┐            ┌─────────────┐            ┌─────────────┐
            │  OpenRouter │            │ Price Sheet │            │  Odoo CRM   │
            │ (GPT-4o)    │            │   Logic     │            │  (XML-RPC)  │
            └─────────────┘            └─────────────┘            └─────────────┘
```

## Core Components

### 1. Message Extraction (`extractor.py`)
- Uses OpenRouter API with GPT-4o-mini for Arabic NLU
- Extracts: brand, platform, format, tier, duration, budget, discount
- Falls back to Gemini/Claude if primary model fails
- **Never invents values** - returns null for unstated fields

### 2. Price Sheet (`price_sheet.py`)
- In-memory price data with codes (YT-INT-A, IG-REEL-B, etc.)
- Calculates duration surcharges (+20% per 30 days beyond included)
- Enforces max discount limits per package
- Returns ESCALATE status when discount exceeds limit

### 3. Conversation State (`models.py`)
- Persists partial requests between messages
- Merges new information with existing context
- Clears state after successful quote generation

### 4. Agent Logic (`agent.py`)
- Orchestrates extraction → quote → CRM flow
- Asks clarifying questions for missing required fields
- Handles three outcomes: OK, UNKNOWN, ESCALATE

### 5. Odoo Integration (`odoo_client.py`)
- XML-RPC API for Odoo 17+ compatibility
- Creates CRM opportunities with full details
- DEV_MODE for testing without actual Odoo calls

## Request Flow

```
1. User sends WhatsApp message
2. Twilio webhook receives → Django view
3. Message queued in DB → Celery task
4. Agent extracts fields via AI
5. Merge with conversation state
6. If missing required fields → ask questions
7. Build quote from price sheet
8. If discount > limit → ESCALATE to sales
9. Create Odoo opportunity
10. Send response via Twilio
```

## Test Cases

| Case | Input | Expected Output |
|------|-------|-----------------|
| A | Complete request (brand, platform, duration, budget) | Quote + Odoo ref |
| B | Incomplete (no platform/budget) | Clarifying questions |
| C | 30% discount on YT-INT-A (max 10%) | Escalate to sales |

## Security

- All API keys in environment variables
- Twilio signature validation on webhook
- CSRF protection enabled
- No secrets in code or logs

## Dev Testing

```bash
# Run all tests
python dev_test.py --test-all --no-pause

# Interactive mode
python dev_test.py

# Single test
python dev_test.py --test A
```

## Production Deployment

1. Set `DEV_MODE=false`
2. Configure Odoo trial credentials
3. Set up Twilio WhatsApp sandbox
4. Deploy with Celery + Redis
5. Expose webhook via HTTPS (ngrok for local)
