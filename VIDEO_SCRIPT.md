# Video script — POWR practical task (target 5:30)

Read this off-screen while recording. Narration is word-for-word; **[SCREEN]** marks what to do.

---

## Pre-flight (do this ~10 min before recording)

```bash
cd ~/Desktop/pakiza/powr-whatsapp
source .venv/bin/activate

# 1. Clean slate so the numbers on camera are small and tidy
cp db.sqlite3 "db.sqlite3.bak.$(date +%Y%m%d-%H%M%S)"
python manage.py shell -c "
from assistant.models import InboundMessage, LeadRecord, ConversationState
InboundMessage.objects.all().delete()
ConversationState.objects.all().delete()
LeadRecord.objects.all().delete()
print('db cleaned')"

# 2. Real Odoo, so the opportunity is actually visible
grep '^DEV_MODE' .env          # must show DEV_MODE=false

# 3. Sanity check before you hit record
python manage.py check
python dev_test.py --test-all  # all three must say PASSED

# 4. Run the server
python manage.py runserver 8000
```

- [ ] Odoo trial open in a tab: **CRM → Pipeline**, emptied of old test leads
- [ ] Tabs in this order: `localhost:8000` · `localhost:8000/chat/` · Odoo · `DESIGN.md`
- [ ] Browser zoom 110 %, bookmarks bar hidden, notifications off
- [ ] Terminal font ≥ 16 px; never show `.env` **values**, only key names
- [ ] Mic test; record in 2 takes (0:00–3:20, then 3:20–end) and stitch
- [ ] Delete yesterday's Odoo leads first — a tidy pipeline looks far more professional

---

## 0:00 – 0:20 · Intro
**[SCREEN]** `localhost:8000` — the dashboard, dark mode.

> "Hi Waleed, this is Faheed. Here's my submission for the POWR AI Developer practical task:
> a WhatsApp assistant that receives an Arabic advertising request, reads the price sheet,
> creates the opportunity in Odoo, and replies with a quote. I'll run the three test cases,
> then walk you through the design."

---

## 0:20 – 1:00 · Architecture and security
**[SCREEN]** Open `DESIGN.md`. Then a terminal: `cat .env.example` — scroll slowly, names only.

> "The stack is Django behind a Twilio WhatsApp webhook, an LLM extraction layer on OpenRouter,
> a deterministic price-sheet engine in Python, and Odoo CRM over XML-RPC.
>
> Two deliberate decisions. First, the model only *extracts* — it never invents a number.
> Every price, duration surcharge and discount cap is computed in Python, so a quote can never
> disagree with the sheet. Second, every secret comes from environment variables — no keys in
> the code — and the webhook validates Twilio's request signature."

---

## 1:00 – 2:20 · Case A — clear request
**[SCREEN]** Go to `localhost:8000/chat/`. Type each message, pause ~1 s after each reply.

**1.** `مرحبا، أنا من شركة نون. نريد حملة إعلانية على يوتيوب (تكامل) لمدة شهرين. الميزانية 100,000 ريال.`

> "Case A: brand, platform, format, duration and budget — all in one Arabic message.
> The extractor reads noon, YouTube, integration, sixty days, and a hundred-thousand-riyal budget.
>
> It shows an estimate first: YouTube integration, Tier B — forty-five thousand for the first
> thirty days, plus twenty percent for the second month — fifty-four thousand. That is the
> YT-INT-B row of your sheet."

**2.** `اسمي نون ops@noon.com`

> "Because the budget was already stated it doesn't ask again — and it does **not** ask about a
> discount either: fifty-four thousand already fits the hundred-thousand budget, so the quote
> stays at full price. It just captures contact details — the WhatsApp number is already known."

**3.** `1`

> "And here's the important part: it asks for confirmation before writing anything to the CRM,
> so the pipeline only ever contains agreed, qualified conversations. On confirm it creates the
> opportunity and replies with the reference number."

**[SCREEN]** Switch to Odoo → **CRM → Pipeline** → open the new lead (scroll once, slowly).

> "Here it is in the Odoo trial: the brand, expected revenue fifty-four thousand, the phone field,
> the email, and the description with the full quote breakdown and the price-sheet code."

**[SCREEN]** Cut to `assistant/services/price_sheet.py`, highlight the `YT-INT-B` line.

> "Same code, same base price, same duration surcharge — the quote matches the sheet exactly,
> and the code is on screen."

---

## 2:20 – 3:20 · Case B — incomplete request
**[SCREEN]** Back to `/chat/`.

**1.** `السلام عليكم، نحن من شركة STC نريد إعلان`

> "Case B: no platform and no budget. It does not guess. It repeats what it understood —
> brand STC — and asks only for what's missing: platform and format."

**2.** `نريده على إنستقرام ريل`

> "I answer 'Instagram reel'. Now it asks for the budget."

**3.** `تخطي`  *(skip)*

> "And when I skip, it simply carries on — the budget stays empty. It doesn't become zero, it
> doesn't become a default, and the quote isn't distorted. That's the 'no invented values'
> requirement. With no budget there is nothing to fill a discount gap, so it doesn't ask about
> a discount at all."

**4.** `تخطي`  *(skip contact)*

> "It collects what it can, quotes the Instagram reel row — eighteen thousand — and only creates
> the lead once I confirm."

**5.** `1`

> "Eighteen thousand: that's the Instagram reel, Tier B, thirty days, no discount — straight from
> the sheet."

---

## 3:20 – 4:20 · Case C — discount above the limit
**[SCREEN]** Still in `/chat/`.

**1.** `مرحبا، أنا من شركة أمازون. نريد حملة يوتيوب تكامل Tier A لمدة شهر. نحتاج خصم 30%.`

> "Case C is the one that matters most. Amazon asks for YouTube integration, Tier A, and a
> thirty percent discount — but the sheet caps Tier A at ten percent.
>
> Notice it doesn't even ask for a budget: there's no price to negotiate, so it goes straight to
> collecting contact details for a human."

**2.** `اسمي أمازون sales@amazon.sa`

> "The summary states the cap — maximum ten percent — and says the request is going to the sales
> team. It never states a price."

**3.** `1`

> "On confirmation it creates the opportunity flagged as escalated and replies with a handover
> message and a reference — not a quote. That's the expected behaviour for case C."

**[SCREEN]** Odoo → open the Amazon lead → scroll to the description.

> "In Odoo the description reads 'escalated — thirty percent is above the ten percent cap', so the
> salesperson has the context without having to ask."

**[SCREEN]** `localhost:8000/leads/?escalated=yes`

> "And in the local analytics view it's listed as escalated, at five percent probability."

---

## 4:20 – 5:05 · Extras (only if time allows)
**[SCREEN]** `/` dashboard → click *Total messages* → open a conversation.

> "Beyond the task I added a small operator layer: an analytics dashboard, a searchable chat
> history that stores the bot's replies as well as the customer's, and a lead view mirroring the
> Odoo fields.
>
> There's a second handover rule too: if even the maximum discount can't reach the customer's
> budget, it escalates instead of promising a price it can't honour.
>
> And it's bilingual — it answers in the language the customer used — with dark and light themes."

---

## 5:05 – 5:30 · Close
**[SCREEN]** Repo root with `README.md` visible.

> "To run it: copy `.env.example`, add an OpenRouter key and your Odoo credentials, migrate, and
> start the server. `DEV_MODE=true` mocks Odoo so you can test without a CRM.
>
> The repo has the README with setup and the design write-up, the CLI harness for the three cases,
> and the full source. Thanks for your time — happy to walk through any part of it."

---

## If something goes wrong on camera

| Symptom | What to do |
|---|---|
| Odoo call fails (trial expired) | Keep going — the reply still shows a reference; say the CRM call is mocked in `DEV_MODE`, then re-record that shot with `DEV_MODE=true`. |
| LLM returns a wrong value | Correct it naturally by typing the right detail. The bot merges it and asks the next question — that *is* the design (context merging). Say so. |
| Wrong language in a reply | Point at `_detect_language()`: ambiguous inputs keep the conversation's language. |
| Rate limit / timeout | `python dev_test.py --test-all` prints the same transcripts — show the terminal instead. |

---

## Copy-paste block (all demo inputs, in order)

```
# Case A
مرحبا، أنا من شركة نون. نريد حملة إعلانية على يوتيوب (تكامل) لمدة شهرين. الميزانية 100,000 ريال.
اسمي نون ops@noon.com
1

# Case B
السلام عليكم، نحن من شركة STC نريد إعلان
نريده على إنستقرام ريل
تخطي
تخطي
1

# Case C
مرحبا، أنا من شركة أمازون. نريد حملة يوتيوب تكامل Tier A لمدة شهر. نحتاج خصم 30%.
اسمي أمازون sales@amazon.sa
1
```

