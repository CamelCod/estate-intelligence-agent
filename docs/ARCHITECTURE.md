# Estate Intelligence Agent — System Architecture

## Overview

Estate Intelligence transforms existing IP cameras into a smart home monitoring service. It delivers daily WhatsApp briefings and conversational Q&A to UAE villa owners — without replacing any hardware.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         OWNER'S WHATSAPP                            │
└─────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ WhatsApp Business API
                                  │
┌─────────────────────────────────────────────────────────────────────┐
│                         n8n WORKFLOW ENGINE                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ Onboarding   │  │ Daily        │  │ Q&A           │              │
│  │ Webhook       │  │ Briefing     │  │ Webhook       │              │
│  │ /customer-    │  │ Generator    │  │ /cctv-qa      │              │
│  │ onboarded     │  │ (cron)       │  │               │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                 │                 │                       │
│         ▼                 ▼                 ▼                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              Claude LLM (via OpenRouter / Anthropic)          │   │
│  │  - System prompt: estate intelligence rules                   │   │
│  │  - Vision: camera frame descriptions → structured events     │   │
│  │  - Decision: event → briefing content / Q&A response          │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
         │                   │                 │
         ▼                   ▼                 ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│  PostgreSQL     │  │  Event Log     │  │  Briefing      │
│  customers      │  │  (daily)       │  │  delivery log  │
│  table          │  │                │  │                │
└────────────────┘  └────────────────┘  └────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    TELEGRAM BOT (standalone service)                │
│  Onboarding wizard: name → property → cameras → staff → briefing   │
│  Q&A handler: forwards questions to n8n QA webhook                 │
│  State machine: 10 states, ConversationHandler                      │
└─────────────────────────────────────────────────────────────────────┘
         ▲
         │ /start command
         │
┌────────────────┐
│  Villa Owner   │
└────────────────┘
```

---

## Components

### 1. Telegram Onboarding Bot (`src/bot.py`)
Python asyncio service. State machine conversation for customer setup.

**Flow:**
1. `/start` → name
2. Property type (Villa/Apartment/Compound/Office)
3. Camera count → per-camera RTSP URL (socket validated)
4. Staff roster (name, role, days, arrival time per person)
5. Briefing time preference
6. Summary confirmation → save to PostgreSQL → fire `N8N_ONBOARD_WEBHOOK`

**After onboarding:** Any message → `N8N_QA_WEBHOOK` → n8n → Claude → answer

**Environment:**
- `TELEGRAM_BOT_TOKEN` — BotFather token
- `DATABASE_URL` — PostgreSQL connection
- `N8N_ONBOARD_WEBHOOK` — n8n onboarding trigger
- `N8N_QA_WEBHOOK` — n8n Q&A endpoint

### 2. PostgreSQL Schema (`scripts/schema.sql`)
Three tables, one view:

**customers**
- telegram_id, name, property_type
- cameras (JSONB): `[{name, rtsp, index}, ...]`
- staff_roster (JSONB): `[{name, role, days, arrival}, ...]`
- briefing_time, language, status

**event_log**
- customer_id, event_date, camera_name
- event_type: person_detected | vehicle | package | camera_offline | anomaly
- event_time, description, raw_vision_json

**briefings**
- customer_id, briefing_date, content
- delivered_at, telegram_msg_id, cost_aed

**todays_customers view** — active customers who haven't received briefing today (for n8n cron query)

### 3. Daily Briefing Pipeline (n8n workflow)
Triggered by schedule node (per-customer briefing_time).

**Steps:**
1. Query `todays_customers` view
2. For each customer:
   a. Fetch that day's events from `event_log`
   b. Build camera_feed_summary prompt
   c. Call Claude LLM with system prompt from `reference/positioning-rules.md`
   d. Produce WhatsApp-formatted briefing (≤250 words, no markdown)
   e. Send via WhatsApp Business API
   f. Log in `briefings` table

### 4. Q&A Pipeline (n8n workflow)
Triggered by `N8N_QA_WEBHOOK` webhook.

**Steps:**
1. Receive: `{telegram_id, question, timestamp}`
2. Look up customer from telegram_id
3. Fetch that day's events for customer
4. Call Claude LLM with question + event log
5. Return `{answer: "..."}` to Telegram bot

---

## Data Flow — Daily Briefing

```
6:00 PM — n8n cron fires for customer with briefing_time = "6:00 PM"
    │
    ▼
n8n queries todays_customers view → finds customer
    │
    ▼
n8n fetches event_log entries for customer + today
    │
    ▼
n8n calls Claude LLM:
  System: "You are an estate intelligence agent..." (from positioning-rules.md)
  Prompt: "Based on today's events for [customer] with staff roster [roster]..."
    │
    ▼
Claude returns briefing text (≤250 words, WhatsApp format)
    │
    ▼
n8n sends via WhatsApp Business API to customer's WhatsApp number
    │
    ▼
n8n inserts record into briefings table (delivered_at, content)
```

---

## Data Flow — Customer Q&A

```
Owner sends WhatsApp: "Did the cleaner arrive today?"
    │
    ▼
Telegram bot receives message → checks if onboarded
    │
    ▼
Bot forwards to N8N_QA_WEBHOOK: {telegram_id, question, timestamp}
    │
    ▼
n8n looks up customer by telegram_id
    │
    ▼
n8n fetches today's event_log entries for customer
    │
    ▼
n8n calls Claude LLM:
  "Answer the question based on today's events: [question]"
    │
    ▼
Claude returns conversational answer
    │
    ▼
n8n returns {answer: "..."} to bot
    │
    ▼
Bot sends answer to owner via Telegram
```

---

## Data Flow — Onboarding

```
Owner sends /start to Telegram bot
    │
    ▼
Bot runs 10-state conversation (name → property → cameras → staff → briefing)
    │
    ▼
On confirm: Bot saves customer to PostgreSQL
    │
    ▼
Bot fires N8N_ONBOARD_WEBHOOK: {telegram_id, name, cameras, staff, briefing_time}
    │
    ▼
n8n receives webhook → creates customer pipeline (camera streams, event capture)
    │
    ▼
Owner receives first briefing that evening at chosen time
```

---

## Constraints

1. **No "security" framing** — SIRA licensing risk in UAE
2. **No raw video storage** — event summaries and metadata only
3. **No speculation** — if camera data is absent, say so explicitly
4. **One daily briefing max** — no alert flooding
5. **Escalate to human** — genuine intrusion, camera shows emergency, cancellation threat

---

## Environment Variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `DATABASE_URL` | PostgreSQL (user:pass@host:5432/db) |
| `N8N_ONBOARD_WEBHOOK` | n8n webhook for new customer pipeline |
| `N8N_QA_WEBHOOK` | n8n webhook for Q&A |
| `OPENROUTER_API_KEY` | LLM access (or `ANTHROPIC_API_KEY`) |

---

## Deployment

```bash
# 1. Clone
git clone <repo> && cd estate-intelligence-agent

# 2. Configure
cp .env.example .env  # fill in all vars

# 3. Database
createdb estate_intelligence
psql -d estate_intelligence -f scripts/schema.sql

# 4. Run bot
pip install -r requirements.txt
python src/bot.py

# Or Docker
docker compose up -d

# 5. n8n workflows
# Import workflows from docs/n8n-workflows.json
```

---

## Optional: ESP32 Integration

For customers who want physical control (gate, lights, alarm), ESP32-S3 nodes
subscribe to MQTT topics and can be triggered by n8n based on LLM decisions.

```
n8n decision → MQTT command topic → ESP32 node → physical action
```

This is optional — most customers use the briefing + Q&A only.