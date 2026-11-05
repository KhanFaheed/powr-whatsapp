# POWR WhatsApp Bot

A WhatsApp assistant for advertising partnership requests. Receives Arabic messages, extracts requirements, generates quotes from a price sheet, creates Odoo CRM opportunities, and handles discount escalations.

## Quick Start (Dev Mode)

```bash
# 1. Clone and setup
cd powr-whatsapp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy env and configure
cp .env.example .env
# Edit .env with your OpenRouter API key

# 3. Run migrations
python manage.py migrate

# 4. Test all three cases
python dev_test.py --test-all --no-pause
```

## Test Cases

### A) Complete Request
```
User: مرحبا، أنا من شركة نون. نريد حملة إعلانية على يوتيوب (تكامل) لمدة شهرين. الميزانية 100,000 ريال.
Bot: [Quote with Odoo reference]
```

### B) Incomplete Request
```
User: السلام عليكم، نحن من شركة STC نريد إعلان
Bot: [Asks for platform and format]
User: نريده على إنستقرام ريل
Bot: [Quote with Odoo reference]
```

### C) Excessive Discount
```
User: مرحبا، أنا من شركة أمازون. نريد حملة يوتيوب تكامل Tier A لمدة شهر. نحتاج خصم 30%.
Bot: [Escalated to sales team, no price promised]
```

## Interactive Testing

```bash
python dev_test.py
# Then type messages in Arabic/English
# Type 'reset' to clear state, 'quit' to exit
```

## Production Setup

### 1. Odoo Trial
1. Go to https://www.odoo.com/trial
2. Select CRM app
3. Note your URL (e.g., `https://yourcompany.odoo.com`)
4. Go to Settings → Users → Your User → API Keys
5. Generate an API key

### 2. Update .env
```bash
DEV_MODE=false
ODOO_URL=https://yourcompany.odoo.com
ODOO_DB=yourcompany
ODOO_USERNAME=your-email@example.com
ODOO_API_KEY=your-api-key
```

### 3. Run with Celery
```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery worker
celery -A config worker -l info

# Terminal 3: Django server
python manage.py runserver

# Terminal 4: Expose webhook (for WhatsApp)
ngrok http 8000
```

### 4. Configure Twilio
1. Go to Twilio Console → Messaging → WhatsApp Sandbox
2. Set webhook URL: `https://your-ngrok-url.ngrok.io/webhook/`
3. Send test message to sandbox number

## Project Structure

```
powr-whatsapp/
├── assistant/
│   ├── services/
│   │   ├── agent.py         # Main orchestration
│   │   ├── extractor.py     # AI extraction (OpenRouter)
│   │   ├── price_sheet.py   # Quote logic
│   │   ├── odoo_client.py   # CRM integration
│   │   └── twilio_client.py # WhatsApp sending
│   ├── tasks/
│   │   └── process_message.py  # Celery task
│   ├── models.py            # DB models
│   └── views.py             # Webhook endpoint
├── config/
│   ├── settings.py
│   └── celery.py
├── dev_test.py              # CLI testing tool
├── DESIGN.md                # Architecture doc
└── .env.example             # Environment template
```

## License

MIT
