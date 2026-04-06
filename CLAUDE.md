# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Type

**Estate Intelligence Agent** — AI-powered daily WhatsApp briefings + conversational Q&A for UAE villa owners. The system transforms existing IP cameras into a smart estate monitoring service without replacing any hardware.

---

## Core Architecture

```
IP Camera (RTSP) → Frame Extractor → Vision AI → LLM Decision → PostgreSQL Event Log
                                                                      ↓
Owner ← WhatsApp ← Daily Briefing Generator ← n8n Workflow ← Cron trigger
                  ↓
           Telegram Bot (onboarding wizard + Q&A handler)
                  ↓
           n8n webhook → customer record → pipeline creation
```

**Two deployed services:**
1. **Telegram Bot** (`src/bot.py`) — Customer onboarding wizard + live Q&A handler. Runs 24/7.
2. **Daily Briefing Pipeline** — n8n workflow triggered at owner's briefing time. Generates WhatsApp message from that day's event log.

---

## Tech Stack

- Python 3.11+ (asyncio, asyncpg, python-telegram-bot v20+)
- PostgreSQL (customers, event_log, briefings tables)
- n8n (workflow automation, webhook endpoints for onboarding + Q&A + briefing generation)
- Claude Vision + Claude LLM (via OpenRouter or direct Anthropic)
- WhatsApp Business API

---

## Key Services

### Telegram Onboarding Bot (`src/bot.py`)

State machine conversation (10 states):
1. `/start` → collect name
2. Property type (Villa / Apartment / Compound / Office)
3. Camera count → RTSP URL per camera (with socket validation)
4. Staff roster (name, role, days, arrival time per person)
5. Briefing time preference
6. Summary + confirmation → save to PostgreSQL → fire n8n webhook

**Q&A mode**: After onboarding, any text message goes to `N8N_QA_WEBHOOK` → n8n queries event log via Claude → returns answer.

Key environment variables:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `DATABASE_URL` — PostgreSQL connection string
- `N8N_ONBOARD_WEBHOOK` — fires on customer confirmation
- `N8N_QA_WEBHOOK` — fires on any Q&A message

### Database Schema (`scripts/schema.sql`)

- `customers`: telegram_id, name, property_type, cameras (JSONB), staff_roster (JSONB), briefing_time, status
- `event_log`: customer_id, event_date, camera_name, event_type, event_time, description, raw_vision_json
- `briefings`: customer_id, briefing_date, content, delivered_at, telegram_msg_id, cost_aed
- View: `todays_customers` — active customers who haven't received briefing today

### Briefing Generation

n8n workflow (triggered by cron or scheduling node):
1. Fetch `todays_customers` view from PostgreSQL
2. For each customer: fetch that day's events from `event_log`
3. Build `camera_feed_summary` prompt
4. Call Claude LLM with system prompt → daily briefing text
5. Send via WhatsApp Business API
6. Log delivery in `briefings` table

---

## Naming Conventions

- Feature branches: `feat/onboarding-arabic`, `fix/briefing-timezone`
- Database migrations: `001_add_customer_language.sql`
- Environment variables: `SHOUTY_UPPER_SNAKE`

---

## Build Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run bot (dev) — requires TELEGRAM_BOT_TOKEN, DATABASE_URL env vars
python src/bot.py

# Run all tests
python -m pytest tests/ -v

# Run single test file
python -m pytest tests/test_bot.py -v

# Docker (production)
docker compose up -d

# Database setup
psql -U postgres -d estate_intelligence -f scripts/schema.sql
```

---

## Key Files

```
estate-intelligence-agent/
├── src/
│   └── bot.py              # Telegram bot — onboarding + Q&A
├── templates/
│   ├── daily-briefing.md   # WhatsApp briefing format (≤250 words, no markdown)
│   ├── onboarding-checklist.md
│   └── pitch-script.md     # Warm outreach + discovery call
├── reference/
│   ├── positioning-rules.md  # Brand voice, forbidden words (never "security")
│   ├── lead-scoring.md       # Hot/Warm/Not Yet qualification matrix
│   └── pricing-tiers.md
├── scripts/
│   ├── schema.sql           # PostgreSQL schema
│   └── generate_briefing.py # Standalone briefing generator (used by n8n)
├── tests/
│   ├── test_briefing.py     # Briefing format + word count validation
│   ├── test_lead_scoring.py # Score calculation tests
│   └── test_bot.py          # State machine tests
└── docs/
    ├── ARCHITECTURE.md      # System design doc
    └── SALES-KIT.md         # Sales copy + positioning guide
```

---

## Constraints (Never Violate)

1. **Never say "security"** — SIRA licensing risk in UAE. Use "estate intelligence", "property awareness", "staff accountability"
2. **Never speculate** — if camera data is absent, say so explicitly: "I don't have a clear view of that"
3. **No raw video storage** — event summaries and metadata only
4. **One daily briefing per customer** — no alert flooding
5. **Escalate to human** — genuine intrusion, camera shows physical emergency, customer threatening to cancel

---

## Pricing (AED/month)

| Tier | Cameras | Price |
|------|---------|-------|
| Starter | 1–3 | 199 |
| Standard | 4–8 | 349 |
| Estate | 9–16 | 599 |
| Custom | 16+ | Quote |