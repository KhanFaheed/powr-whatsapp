# POWR — WhatsApp AI Assistant for Ad Partnership Requests

**POWR AI Developer Practical Task — submission by Faheed**

A WhatsApp assistant that receives an advertising partnership request (Arabic or English),
reads the POWR price sheet, qualifies the lead, creates the opportunity in Odoo CRM, and
replies with a quote that matches the sheet exactly — or hands the deal over to a human when
it falls outside the rules.

---

## نبذة بالعربية

مساعد واتساب ذكي يستقبل طلبات الشراكات الإعلانية بالعربية، يقرأ شيت الأسعار، يسأل عن الناقص
فقط (بدون تخمين أي قيمة)، ينشئ الفرصة في Odoo CRM، ويرد بعرض سعر مطابق للشيت تماماً.
وإذا كان الخصم المطلوب أعلى من الحد المسموح — أو الميزانية أقل من الحد الأدنى للباقة —
لا يَعِد بسعر، بل يحوّل الطلب إلى موظف مبيعات مع ملخص كامل.

قرارات التصميم الأساسية:
1. **الذكاء الاصطناعي يستخرج فقط** — كل الحسابات والأسعار وحدود الخصم في كود بايثون، فلا يمكن للعرض أن يختلف عن الشيت.
2. **لا يخترع قيماً** — الحقول غير المذكورة تبقى `null` ويسأل عنها.
3. **التأكيد قبل إنشاء الفرصة** — لا يتم إنشاء Lead في Odoo إلا بعد موافقة العميل.
4. **قاعدتان للتحويل البشري** — خصم فوق الحد، أو ميزانية أقل من أقل سعر ممكن.

---

## 1. What it does

| Capability | Detail |
|---|---|
| **Understands Arabic + English** | LLM extraction (OpenRouter) with a strict schema; replies in the customer's language |
| **Never invents values** | Unstated fields stay `null` and are asked for, one short question at a time |
| **Deterministic pricing** | Price sheet, duration surcharge and discount caps are computed in Python (`price_sheet.py`) |
| **Qualifies before quoting** | Asks budget → discount → contact details → shows a summary → waits for confirmation |
| **Creates the CRM record once** | Odoo opportunity created only after the customer confirms (no pipeline pollution) |
| **Two human-handover rules** | (a) discount above the package cap, (b) budget below the cheapest achievable price |
| **Operator layer** | Analytics dashboard, searchable chat history (with bot replies), lead browser mirroring the Odoo fields |

---

## 2. Deliverables (per the task email)

| Deliverable | Where |
|---|---|
| **Video** running the three test cases | `VIDEO_SCRIPT.md` (shot-by-shot script used to record it) |
| **One-page write-up of the design** | Section [5. Design decisions](#5-design-decisions--trade-offs) below, and `DESIGN.md` |
| **Code / flow file** | This repository (`assistant/` app) |

---

## 3. The three test cases — reproduce in order

All three can be run in the browser at **`http://localhost:8000/chat/`** (no Twilio needed),
or against the real WhatsApp webhook at `/webhook/`.

### Case A — clear request (brand + platform + duration + budget)

```
You: مرحبا، أنا من شركة نون. نريد حملة إعلانية على يوتيوب (تكامل) لمدة شهرين. الميزانية 100,000 ريال.
Bot: 📊 YouTube — Integration · Tier B
     • Duration: 60 days
     • Estimated price: 54,000 SAR          ← YT-INT-B: 45,000 + 20% for the 2nd month
     Now, what's your approximate budget in SAR? … ✓ (already provided → skipped)
You: اسمي نون ops@noon.com
Bot: 📋 Quote summary … 💰 Total: 54,000 SAR  ✅ Within your budget (100,000 SAR)
     1️⃣ Yes  2️⃣ Discount  3️⃣ Cancel  4️⃣ Change package
You: 1
Bot: 📋 Your Quote (YT-INT-B) … 📝 Reference: #<odoo lead id>
```

**No discount is ever offered here** — the price already fits the budget (see
[discount policy](#4-design-decisions--trade-offs)). The customer can still ask for one, or pick *2*.

**Expected:** one opportunity in Odoo with brand, expected revenue `54,000`, phone, email and the
full quote breakdown in the description; the reply carries the reference number.

### Case B — incomplete request (no platform, no budget)

```
You: السلام عليكم، نحن من شركة STC نريد إعلان
Bot: • Which platform? (YouTube, Instagram, TikTok, Snapchat, X)
     • What type of content?
     ✓ Brand: STC                        ← asks ONLY for what is missing
You: نريده على إنستقرام ريل
Bot: 📊 Instagram — Reel · Tier B … (estimated 18,000 SAR) … what's your budget?
You: تخطي                               ← skipping the budget is allowed
Bot: 📇 contact details…                 ← no budget, so no discount question either
You: تخطي
Bot: 📋 Quote summary … Total: 18,000 SAR (no budget line, because none was given)
```

**Expected:** it asks short questions and carries on. **Nothing is invented** — no budget, no
duration, and no tier price are fabricated; the missing values stay empty.

### Case C — discount above the limit

```
You: مرحبا، أنا من شركة أمازون. نريد حملة يوتيوب تكامل Tier A لمدة شهر. نحتاج خصم 30%.
Bot: 📇 Almost done! I need your contact details…     ← no price is quoted at all
You: اسمي أمازون sales@amazon.sa
Bot: 📋 Quote summary
     • Requested discount: 30%
     ⚠️ The maximum discount for this package is 10%.
     Your request will be forwarded to our sales team for review.
You: 1
Bot: 🤝 Connecting you with our sales team (human support)
     • Platform: YouTube   • Format: Integration   • Duration: 30 days
     • Requested discount: 30% (maximum 10%)
     ⚠️ The requested discount is above the maximum of 10%.
     📝 Reference: #<id>   ⏱️ We usually respond within 24 hours.
```

**Expected:** no price is promised; the customer is handed over to a person with a full summary.
The Odoo lead is created with **`ESCALATED — 30.0% > cap 10%`** written into the description,
and appears on `/leads/?escalated=yes` with a red **ESCALATED** badge.

### Changing the request before the final decision

Picking a platform is **not** a commitment. Until the customer sends *1*, every plan field —
platform, format, tier, duration, budget and discount — can still be revised, whether they use the
menu option **4️⃣ Change the platform or package** or simply type the change.

```
You: actually make it tiktok
Bot: 🔄 Request updated before confirmation:
     • Platform: X → TikTok
     • Format: Post → Video
     📋 Quote summary — please review:  …  💰 Total: 15,000 SAR
     ✅ Within your budget (20,000 SAR)
     1️⃣ Yes  2️⃣ Discount  3️⃣ Cancel  4️⃣ Change the platform or package

You: budget 12000
Bot: 🔄 Request updated before confirmation:
     • Budget: 20,000 SAR → 12,000 SAR
     📋 Quote summary …  ⚠️ 3,000 SAR above your budget (max discount 20%)

You: 1
Bot: 📋 Your Quote (TT-VID-B) … 📝 Reference: #<id>
```

Rules that make this safe:

| Rule | Why |
|---|---|
| Nothing is booked before *1* | the lead is created **only** on explicit confirmation |
| A revision beats "yes" | "yes but make it TikTok" re-prices instead of booking the old package |
| Re-quote on every change | the price, the budget check and the escalation decision are recomputed from the new plan |
| Budget can move the deal | lowering the budget below the cheapest discounted price flips the lead to **Escalated** at confirmation time |
| Every change is echoed | a `🔄 … → …` block is prepended so the customer always sees what changed |
| Changes work at any step | also while the bot is asking for budget, discount or contact details |

---

## 4. Architecture

```
WhatsApp user
     │  message
     ▼
Twilio WhatsApp  ──►  POST /webhook/            (assistant/views.py)
                          │  • signature validation (skipped only in DEBUG)
                          │  • idempotency on MessageSid
                          ▼
                    InboundMessage row  ──►  handle_message(phone, text)   (services/agent.py)
                          │
                          │  1. extract(text, context)      → services/extractor.py  (OpenRouter LLM)
                          │  2. merge with ConversationState (per-phone memory in DB)
                          │  3. build_quote(...)            → services/price_sheet.py (pure Python)
                          │  4. decide: ask / quote / escalate
                          ▼
                    create_lead(...)  ──►  Odoo CRM via XML-RPC   (services/odoo_client.py)
                          │
                          ▼
                    LeadRecord row (local mirror for the dashboard)
                          │
                          ▼
                    TwiML reply / JSON reply  ──►  WhatsApp
```

### Components

| File | Responsibility |
|---|---|
| `assistant/views.py` | Twilio webhook (signature, idempotency, TwiML), chat API, dashboard/history/leads views |
| `assistant/services/extractor.py` | LLM extraction to a strict Pydantic schema; context-aware; 3-model fallback |
| `assistant/services/price_sheet.py` | **Single source of truth for money**: base price, duration surcharge, discount caps |
| `assistant/services/agent.py` | Conversation state machine + reply formatting (bilingual) + escalation rules |
| `assistant/services/odoo_client.py` | Odoo XML-RPC `crm.lead` creation, `DEV_MODE` mock |
| `assistant/models.py` | `InboundMessage`, `ConversationState`, `LeadRecord` |
| `assistant/tasks/process_message.py` | Optional Celery path for async replies (content templates) |

### Conversation state machine

```
collect ──► budget ──► discount? ──► contact ──► confirm ──► create lead
   │            │           │           │           │
   │            │           │           │           └─ 1 yes · 2 discount · 3 cancel
   │            │           │           └─ full name + email (phone comes from WhatsApp)
   │            │           └─ ONLY when the budget is short or the customer asks:
   │            │              "X%" · "no discount" · "0"
   │            └─ budget in SAR + optional duration change ("60 days")
   └─ brand · platform · format  (asks only the missing ones)

Margin rule: when the price already fits the stated budget, the discount
question is skipped entirely (no budget given → also skipped). A discount is
discussed only if (a) the customer asks for one, or (b) budget < price AND the
package's maximum discount can actually close the gap.

Short-circuit: if the discount is already above the cap, budget/discount
questions are skipped and the flow jumps to contact → summary → handover.
```

---

## 5. Design decisions & trade-offs

**1. The LLM extracts, Python calculates.**
The model is only allowed to *read* the message into a fixed JSON schema
(`brand`, `platform`, `format`, `tier`, `days`, `budget`, `discount`, `contact`, `email`).
Every riyal is computed by `price_sheet.py`. This is the reason the quote can never drift from
the sheet, and it makes the money logic unit-testable without any network call.

**2. Never invent a value.**
The prompt forbids guessing and every field is nullable. The agent then asks for the *missing*
fields only, and echoes what it already understood (`✓ Brand: STC`) so the customer can correct it.
Skipping is always allowed (`skip` / `تخطي`), and a skipped field stays `null` — it does **not**
become `0`, which is what usually produces wrong quotes in these bots.

**3. Ask, don't assume — but don't interrogate.**
Required fields are only `brand`, `platform`, `format`. Budget, discount, duration and contact
are *qualifying* questions asked in one short message each; the flow never asks for something
the customer already said in the same message (Case A skips the budget question).

**3b. The discount is never volunteered — it protects the margin.**
The bot does **not** ask "would you like a discount?" on every conversation. It only raises the
topic when one of two things is true:

- the customer asked for a discount (in any turn — "can I get a discount?" is understood and the
  percentage is collected, the same as picking menu option *2*), or
- the stated budget is **below** the package price **and** the package's maximum discount can
  actually close that gap (e.g. 45,000 SAR vs a 40,000 SAR budget with a 15% cap → the bot shows
  the 5,000 SAR gap, the cap and the best possible price, then asks for the percentage).

Everything else stays at full price, silently (`requested_discount_pct = 0`):
a customer who can pay the list price is never nudged into asking for less. When even the cap
cannot reach the budget the question is skipped too — a discount would not help, so the flow goes
straight to contact/summary and escalates to a human instead of wasting a turn.

**4. Confirm before writing to the CRM.**
A quote is a conversation, not a commitment. The lead is created only after the customer replies
`1` / `yes` / `نعم`. This keeps the Odoo pipeline clean and gives a natural place to insert the
human-handover branch. Trade-off: one extra turn — accepted deliberately.

**5. Two escalation rules, both explicit.**
- **Discount above cap** → the bot must not promise a price (Case C).
- **Budget below the minimum** → if even the *maximum* allowed discount cannot reach the budget,
  quoting is pointless, so it hands over with the numbers that justify the handover
  (e.g. *"budget 5,000 SAR vs best possible 8,000 SAR, max discount 20%"*).

The escalation reason is written into the Odoo lead description and flagged
`escalated=True`, `stage=Escalated`, probability 5 % so sales can triage.

**6. Language is a property of the conversation, not of the message.**
Script detection decides per message, but for ambiguous input (`1`, `60 days`, mixed
Arabic+email) the conversation keeps its established language. This prevents the classic bug of
an Arabic customer getting an English confirmation because they answered "1".

**7. Deterministic plumbing around a non-deterministic brain.**
Platform/format normalisation and a brand fallback ("tanmay.com" typed as an answer) live in
code, so a model hiccup degrades gracefully instead of looping the same question.

**8. Security by default.**
All secrets come from environment variables (`.env`, never committed; `.env.example` documents
names only). Twilio requests are signature-validated, the webhook is idempotent on `MessageSid`,
and CSRF protection stays on for every page — only the signed webhook and the JSON chat endpoint
are exempt.

**9. Validation is a hard gate (Pydantic).**
`assistant/services/schemas.py` is the only place where a value becomes "real".
It normalises and validates three things: the raw LLM output (`ExtractedRequest`),
the contact details (`ContactInfo`) and — as the last gate before money and CRM calls —
the exact values the price sheet receives (`QuoteRequest`).
So `"Yotube"` → `youtube`, `"Tier A (10M+ subscribers)"` → `A`, `"100,000 SAR"` → `100000`,
`"30%"` → `30`, `900 days` → `None` (asks again), `"null"`/`"غير محدد"` → `None`,
and `"ali@x"` → `None` (re-asks for the email instead of storing a broken address).
An impossible request can never reach `build_quote()` or Odoo — it is rejected with a
friendly question instead of producing a wrong quote.

**Known trade-offs**
- The state machine is deliberately small (one dictionary per phone) rather than a workflow engine.
- The dashboard has no authentication — it is an operator tool for local/internal use; in
  production it would sit behind SSO or Django auth.
- Arabic dialect tuning is prompt-level; a production build would add a golden-set evaluation.



---

## 6. The price sheet (`assistant/services/price_sheet.py`)

| Code | Platform | Format | Tier | Base (SAR) | Days included | Extra 30d | Max discount |
|---|---|---|---|---|---|---|---|
| `YT-INT-A` | YouTube | Integration | A | 90,000 | 30 | +20 % | 10 % |
| `YT-INT-B` | YouTube | Integration | B | 45,000 | 30 | +20 % | 15 % |
| `YT-INT-C` | YouTube | Integration | C | 20,000 | 30 | +20 % | 15 % |
| `YT-DED-A` | YouTube | Dedicated | A | 200,000 | 30 | +20 % | 10 % |
| `YT-DED-B` | YouTube | Dedicated | B | 100,000 | 30 | +20 % | 10 % |
| `SC-STR-A` | Snapchat | Story | A | 30,000 | 30 | +20 % | 15 % |
| `SC-STR-B` | Snapchat | Story | B | 15,000 | 30 | +20 % | 20 % |
| `IG-REEL-A` | Instagram | Reel | A | 35,000 | 30 | +20 % | 15 % |
| `IG-REEL-B` | Instagram | Reel | B | 18,000 | 30 | +20 % | 20 % |
| `TT-VID-A` | TikTok | Video | A | 30,000 | 30 | +20 % | 15 % |
| `TT-VID-B` | TikTok | Video | B | 15,000 | 30 | +20 % | 20 % |
| `X-POST-A` | X | Post | A | 10,000 | 30 | +20 % | 20 % |

**Quote formula**

```
multiplier = 1 + ceil(max(0, requested_days - 30) / 30) × 0.20
gross      = base × multiplier
final      = gross × (1 - requested_discount_pct / 100)

if requested_discount_pct > row.max_discount_pct  →  status = ESCALATE   (no price issued)
if no row for (platform, format, tier)            →  status = UNKNOWN    (asks for an alternative)
otherwise                                          →  status = OK
```

Tier defaults to **B** when the customer doesn't state one (the quote labels it `(default)`).
The same row also decides the *human* escalation: if `gross × (1 - max_discount/100)` is still
above the customer's budget, the deal goes to sales instead of being quoted.

Worked examples:
- YouTube Integration, 60 days, Tier B → `45,000 × 1.20 = 54,000` (Case A)
- YouTube Integration, 30 days, Tier A, 30 % requested → cap 10 % → **ESCALATE** (Case C)
- Instagram Reel, 30 days, Tier B → `18,000` (Case B)

---

## 7. Setup

```bash
git clone <this repo> && cd powr-whatsapp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

npm install                   # Tailwind CSS v4 build tooling (one-off)
npm run css:build             # compile assets/css/input.css -> static/dist/output.css

cp .env.example .env          # then fill in the values (see §8)
python manage.py migrate
python manage.py create_waleed     # the single allow-listed desk account (prints the password)
python manage.py runserver 8000
```

While you are editing templates, run `npm run css:dev` in a second terminal so
`static/dist/output.css` rebuilds on every change (see *Tailwind CSS* below).

Then open:

| URL | What |
|---|---|
| `http://localhost:8000/` | Analytics dashboard (homepage) |
| `http://localhost:8000/chat/` | **POWR Concierge chatbot** — run the three test cases without Twilio |
| `http://localhost:8000/history/` | Chat history + per-conversation view (stores bot replies too) |
| `http://localhost:8000/leads/` | Leads, filter by stage / escalation |
| `http://localhost:8000/admin/` | Django admin |
| `http://localhost:8000/settings/` | **Integration settings** — the tester's console (DB values override `.env`) |
| `http://localhost:8000/login/` | **Sign-in** — every page above requires it |
| `http://localhost:8000/health/` | `ok` (for your load balancer) |
| `POST /webhook/` | Twilio WhatsApp webhook |
| `POST /api/chat/` | JSON endpoint used by the chatbot (signed-in seat only) |

### Desk access — superusers and the allow-list

The web desk (dashboard, chatbot, history, leads, `/api/chat/` and `/admin/`) sits behind a single
sign-in page at **`/login/`**. Any Django **superuser** — created with
`python manage.py createsuperuser` or `python manage.py create_waleed` — signs in with the same
credentials to both the desk and `/admin/`. Non-superuser accounts must be allow-listed through
`POWR_ALLOWED_USERNAMES` (`waleed` by default). The rule lives in `assistant/auth.py` and is
enforced twice:

* `AllowListedUserBackend` refuses `authenticate()` and session checks for every account that is
  neither a superuser nor allow-listed;
* `desk_login_required` guards each view — JSON callers get `401` (the chat UI redirects to sign-in),
  and a session belonging to an account that is no longer allowed in is dropped on sight.

That keeps the OpenRouter budget tied to a handful of trusted accounts. Waleed is greeted in Arabic
on the sign-in page, in a dashboard banner and in a toast right after signing in.

```bash
python manage.py create_waleed                                # create + print a password
WALEED_PASSWORD='…' python manage.py create_waleed --reset    # rotate the password
python manage.py create_waleed --password '…'                 # explicit password
```

### Integration settings — `/settings/` (Waleed's test console)

The integrations (OpenRouter, Odoo, Twilio) are resolved **per call**, from the single
`IntegrationSettings` row first and the environment second:

```
value = IntegrationSettings row (if that field is non-blank)
        else os.environ[OPENROUTER_API_KEY | ODOO_* | TWILIO_* | DEV_MODE]
        else ""            →  the client raises a clear "not configured" error
```

That means production can keep everything in `.env` untouched, while a tester points the
assistant at his own accounts from `/settings/` — no restart, no code change:

* the model is a **singleton** (`pk=1`; saving always edits that row),
* non-secret fields fall back to `.env` when left blank,
* **secrets are write-only** — they are never re-rendered into the HTML. Leaving the box empty
  keeps the stored value; ticking “Use the .env value instead” clears it,
* every field shows where its effective value comes from (`Saved here` / `.env` / `Not set`),
  plus a masked preview (`••••ab77`) for secrets,
* `DEV mode` is a tri-state: *Unknown* = `DEV_MODE` from the environment, *Yes* = mock every
  Odoo call, *No* = always hit the real CRM,
* the row is also editable from `/admin/` (Integration settings) if you prefer.

`assistant/services/runtime_settings.py` is the single resolver used by
`extractor.py` (OpenRouter), `odoo_client.py` and `twilio_client.py`/webhook signature checks.

**Two ways to test the webhook without WhatsApp:**

```bash
# a) the built-in CLI harness (runs cases A, B, C end to end)
python dev_test.py --test-all

# b) a real HTTP call to the Twilio endpoint
curl -X POST http://localhost:8000/webhook/ \
  -d "MessageSid=SM-demo-1" \
  -d "From=whatsapp:+966500000001" \
  --data-urlencode "Body=مرحبا، أنا من شركة نون. نريد حملة على يوتيوب تكامل لمدة شهرين. الميزانية 100,000 ريال."
```

`DEV_MODE=true` makes the Odoo client return a mock lead id (no CRM calls), so you can demo the
whole flow without an Odoo account. **The video was recorded with `DEV_MODE=false`** so the
opportunity is visible in the real Odoo trial.

### Tailwind CSS — compiled locally, no CDN

The UI has **no CDN dependency**: Tailwind v4 is compiled to a plain CSS file that Django serves
itself, so development and production use exactly the same stylesheet.

| File | Role |
|---|---|
| `assets/css/input.css` | Entry point — `@import "tailwindcss"`, `@source` (scans `assistant/templates`), `@custom-variant dark` (class-based dark mode), `@theme` (brand colours, fonts, shadow) |
| `assets/css/app.css` | Design tokens (`--bg`, `--surface`, `--border`, `--muted`, …) and component classes (`card`, `btn`, `input`, `badge`, `nav-shell`, `data` table, …), wrapped in Tailwind's `components` layer so utilities can still override them |
| `static/dist/output.css` | **Build artifact** linked in `base.html` through `{% static 'dist/output.css' %}` |
| `package.json` | `npm run css:dev` (watch) and `npm run css:build` (minified) scripts |

```bash
npm run css:dev     # watch mode — rebuilds output.css on template/CSS changes
npm run css:build   # one-off minified build — run before committing / deploying
```

The theme tokens that used to live in the Play-CDN `tailwind.config` object are now declared with
`@theme` in `input.css`, so `bg-brand`, `text-brand-hover`, `font-display`, `shadow-glow`, etc. keep
working. Tailwind v4 only emits the utilities actually used in the templates (JIT), and
`source(none)` + an explicit `@source` keeps the scan limited to `assistant/templates` — add a new
`@source "…"` line if you introduce another template directory.

**Production**

```bash
npm ci && npm run css:build        # compile the stylesheet
python manage.py collectstatic --noinput   # gather static/ + app statics into staticfiles/
gunicorn config.wsgi               # WhiteNoise serves the hashed, compressed files
```

`static/dist/output.css` is kept in the repo so a Python-only deploy works out of the box; always
re-run `npm run css:build` after changing templates or `assets/css/*.css`.

### Bilingual UI — English & Arabic (EN / AR)

The header carries a two-button language switch (`EN` / `AR`) that posts to `/language/`. The view
wraps Django's `set_language`, stores the choice in the `powr_language` cookie (one year) and flashes
a confirmation through **`django.contrib.messages`** — rendered in the new language, dismissible and
auto-hidden after 5 seconds.

| Piece | Where |
|---|---|
| Locale middleware · `LANGUAGES` · `LOCALE_PATHS` · i18n context processor | `config/settings.py` |
| Switch view (messages framework) | `assistant/views.py::set_language_switch` → `path("language/", …, name="set_language")` |
| Switch UI · flash stack · theme bootstrap | `assistant/templates/assistant/base.html` |
| Arabic catalogue | `locale/ar/LC_MESSAGES/django.po` + compiled `django.mo` |

* **RTL comes for free** — the shell sets `<html lang dir>` from `LANGUAGE_BIDI`; every rule in
  `assets/css/app.css` uses logical properties (`padding-inline`, `inset-inline`, `border-start-*`)
  and the templates use `ps-*` / `start-*` / `text-end`. Directional icons flip with `rtl:rotate-180`.
* **Arabic typography** — `IBM Plex Sans Arabic` is loaded and appended to the font stacks;
  `letter-spacing` and `uppercase` are neutralised in RTL (tracking breaks Arabic glyph joining).
* **Arabic plurals** — the catalogue ships all six Arabic plural forms (`nplurals=6`), so counters
  read «رسالة واحدة» / «رسالتان» / «5 رسائل» / «15 رسالة» instead of a generic "s".

```bash
python manage.py makemessages -l ar      # refresh msgids after changing templates
python manage.py compilemessages -l ar   # rebuild locale/ar/LC_MESSAGES/django.mo
```

> `compilemessages` needs gettext (`msgfmt`); the compiled `.mo` is committed, so a Python-only
> deploy does not need gettext installed.

### Theme toggle

`<html class="dark">` is the single source of truth (via the Tailwind `@custom-variant dark`).
An inline script applies the stored `powr-theme` from `localStorage` **before first paint** (no
flash of the wrong theme), while the toggle updates `aria-pressed`, its label and
`<meta name="theme-color">`; a `storage` listener keeps several tabs in sync.

### Chat composer

The chatbot input is a WhatsApp-style composer: a pill container that highlights as a whole on
focus, a **textarea that grows from one line up to 8 rem** (then scrolls), and a circular send
button that stays disabled until there is text (and while a reply is in flight).
`Enter` sends, `Shift+Enter` adds a line (IME composition is respected), and the keyboard hint
is hidden on touch devices. Newlines render through `whitespace-pre-wrap`, so multi-line messages
look identical in the thread and in History.

### Notifications (toasts)

Django's own `messages` context variable is a shared name, and the History page also needs to list
inbound messages — exposing them as `messages` used to **shadow** the framework variable, so every
inbound row was rendered as a toast and the real flash messages were never consumed (they piled up
and re-appeared later).

The toast stack therefore reads a dedicated, collision-proof variable:

| Piece | Where |
|---|---|
| `flash_messages` context processor — dedupes identical toasts, caps at 3 | `assistant/context_processors.py` (registered in `config/settings.py`) |
| `{% if flash_messages %}` / `{% for message in flash_messages %}` | `assistant/templates/assistant/base.html` |
| History/detail querysets exposed as `inbound_messages` | `assistant/views.py`, `chat_history.html`, `chat_detail.html` |

Rule of thumb: never name a context key `messages` — use `inbound_messages`, `recent_messages`, etc.

---

## 8. Environment variables

Nothing secret is committed — `.env.example` lists the names only.

| Variable | Purpose | Where to get it |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django signing key | generate: `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `true` locally, `false` in production | — |
| `DJANGO_ALLOWED_HOSTS` | comma-separated hosts | your domain |
| `DATABASE_URL` | DB connection (`sqlite:///db.sqlite3` locally) | — |
| `REDIS_URL` | Celery broker (only for the async path) | `redis://localhost:6379/0` |
| `OPENROUTER_API_KEY` | LLM extraction | https://openrouter.ai/keys |
| `ODOO_URL` | Odoo base URL | your Odoo trial, e.g. `https://acme.odoo.com` |
| `ODOO_DB` | Odoo database name | trial subdomain name |
| `ODOO_USERNAME` | Odoo login (email) | — |
| `ODOO_API_KEY` | Odoo API key (**not** your password) | Odoo → Preferences → Account Security → New API Key |
| `DEV_MODE` | `true` = mock Odoo, `false` = real CRM | — |
| `TWILIO_ACCOUNT_SID` | Twilio account | Twilio Console |
| `TWILIO_AUTH_TOKEN` | used to validate webhook signatures | Twilio Console |
| `TWILIO_WHATSAPP_FROM` | sandbox/production sender | Twilio WhatsApp Sandbox |
| `WHATSAPP_SYNC_MODE` | `true` = reply inline with TwiML, `false` = Celery + content templates | — |

### Odoo trial setup (5 minutes)

1. Create a trial at https://www.odoo.com/trial and install **CRM**.
2. Preferences → Account Security → **New API Key** → copy it into `ODOO_API_KEY`.
3. `ODOO_URL=https://<your-trial>.odoo.com`, `ODOO_DB=<your-trial>`, `ODOO_USERNAME=<login email>`.
4. Set `DEV_MODE=false` and run case A — the opportunity appears in **CRM → Pipeline**.

---

## 9. Security

- **No secrets in code or in chat.** Everything is read from the environment; `.env` is git-ignored
  and `.env.example` contains placeholders only.
- **Twilio signature validation** on every webhook call (`RequestValidator`); it is skipped *only*
  when `DEBUG=True` so local curl testing works.
- **Idempotency**: the webhook stores `MessageSid` as a unique key, so Twilio retries never create
  duplicate messages or duplicate leads.
- **CSRF** enabled site-wide; only the signature-validated webhook and the JSON chat endpoint are
  `csrf_exempt`. The chat endpoint is session-authenticated and rejects cross-origin `Origin`s, so
  the exemption cannot be abused by another site.
- **Restricted access**: the desk, the chat API and `/admin/` are behind `/login/`; only Django
  superusers (`createsuperuser` / `create_waleed`) and usernames listed in `POWR_ALLOWED_USERNAMES`
  (`waleed` by default) can authenticate. Every operator view is wrapped in `desk_login_required`
  (`assistant/auth.py`), so no anonymous visitor can trigger an OpenRouter call.
- **Credential handling**: integration secrets can live in the database (`/settings/`) or in `.env`.
  The DB row is editable only by the allow-listed seat (plus Django admin staff), secrets are never
  rendered back into a page (masked preview only), and clients build their credentials per call so a
  rotated key takes effect immediately.
- **Least privilege**: the Odoo API key is a user key that can be revoked at any time; the bot only
  calls `crm.lead:create`.
- **No PII in logs**: only phone numbers and reply previews are logged.

---

## 10. Testing

```bash
python manage.py test assistant   # 59 unit, flow and access-control tests
python dev_test.py --test-all     # cases A, B, C through the real agent (exit code 0/1)
python dev_test.py --test A       # single case
python dev_test.py                # interactive REPL — 'reset' clears state, 'quit' exits
python manage.py check            # Django system checks
```

`python manage.py test assistant` runs the regression suite: platform/format/tier
normalisation, numeric coercion, email validation, `QuoteRequest` gating, the price-sheet
formulas for cases A/B/C, and the conversation flow (including "name-only answer re-asks for
the email", "no discount is offered when the budget already fits", "the discount step runs only
when the budget is short", "bare *yes* at the discount step asks for the percentage", and
"no lead is created before confirmation"). It needs no network access and no Odoo — the LLM is stubbed.

`dev_test.py` drives the **real** `handle_message()` (same code path as WhatsApp) and prints the
full transcript, so it doubles as a regression harness for the conversation flow.

---

## 11. Limitations & next steps

| Area | Now | Next |
|---|---|---|
| WhatsApp 24-hour window | Inline TwiML reply | Approved content templates for outbound-after-24h (async mode is already wired for it) |
| Arabic dialects | Prompt-level instructions + Arabic examples | Golden-set evaluation (Gulf/Hijazi) with extraction accuracy metrics |
| Operator UI | No auth (internal tool) | Put `/`, `/history/`, `/leads/` behind SSO/Django auth |
| Persistence | SQLite in dev | Postgres (`DATABASE_URL`) + Celery/Redis for scale |
| Pricing | Hard-coded sheet module | Load the official sheet from a DB table or Google Sheet with versioning |
| Media | Text only | Handle images/voice notes (transcription) via Twilio media URLs |

---

## 12. Project structure

```
powr-whatsapp/
├── assistant/
│   ├── services/
│   │   ├── agent.py           # conversation state machine + escalation rules + replies
│   │   ├── schemas.py         # Pydantic validation gate (LLM output, contact, quote input)
│   │   ├── extractor.py       # LLM → strict JSON schema (OpenRouter, 3-model fallback)
│   │   ├── price_sheet.py     # price sheet + quote maths (single source of truth)
│   │   ├── odoo_client.py     # crm.lead creation over XML-RPC (+ DEV_MODE mock)
│   │   └── twilio_client.py   # outbound WhatsApp messages (async mode)
│   ├── tasks/process_message.py   # Celery task (async mode)
│   ├── templates/assistant/   # base, dashboard, chat, history, detail, leads
│   ├── models.py              # InboundMessage · ConversationState · LeadRecord
│   ├── views.py               # webhook, chat API, dashboard/history/leads
│   └── urls.py
├── config/                    # settings, urls, celery
├── assets/css/
│   ├── input.css              # Tailwind entry: @source, @custom-variant dark, @theme tokens
│   └── app.css                # design tokens + component classes (card, btn, badge, …)
├── static/dist/output.css     # compiled stylesheet (npm run css:build) → {% static %}
├── locale/ar/LC_MESSAGES/     # Arabic catalogue (django.po + compiled django.mo)
├── package.json               # Tailwind CSS v4 CLI build scripts
├── dev_test.py                # CLI harness for cases A/B/C
├── DESIGN.md                  # architecture write-up
├── VIDEO_SCRIPT.md            # shot-by-shot script used to record the demo
└── .env.example               # environment template (no secrets)
```

---

## 13. Author

**Faheed** — submitted for the POWR AI Developer practical task.
Built with Django 5 · Twilio WhatsApp · OpenRouter LLMs · Odoo CRM (XML-RPC) · Tailwind CSS.

