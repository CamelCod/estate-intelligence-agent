# Estate Intelligence Agent

> AI-powered daily WhatsApp briefings + conversational Q&A for UAE villa owners. Turn existing cameras into a smart estate intelligence system.

## What It Does

- **Daily briefing** (8pm WhatsApp): staff arrivals, deliveries, anomalies — all from existing RTSP cameras
- **Conversational Q&A**: owner asks "did the cleaner arrive?" and gets an answer from the day's camera events
- **Staff accountability**: cross-references arrivals against roster, flags no-shows and late arrivals
- **No new hardware**: works with any IP camera that has RTSP access

## Architecture

```
IP Camera (RTSP) → Frame Extractor (OpenCV, motion-triggered) → Vision AI (Claude)
                                                                         ↓
Owner ← WhatsApp ← LLM Decision ← Event Log ← PostgreSQL ← n8n webhook
                  ↓
           ESP32 Mesh (optional physical control: gate, lights, alarm)
```

## Quick Start

### 1. Telegram Bot Setup

```bash
# Create bot via @BotFather in Telegram
# Copy the bot token

# Clone and configure
cp .env.example .env
# Add TELEGRAM_BOT_TOKEN, DATABASE_URL, N8N_WEBHOOK
```

### 2. Database

```bash
psql -U postgres -c "CREATE DATABASE estate_intelligence;"
psql -U postgres -d estate_intelligence -f scripts/schema.sql
```

### 3. Run

```bash
pip install -r requirements.txt
python src/bot.py

# Or via Docker
docker compose up -d
```

## Project Structure

```
estate-intelligence-agent/
├── src/
│   └── bot.py              # Telegram onboarding wizard + Q&A handler
├── templates/
│   ├── daily-briefing.md   # Briefing format template
│   ├── onboarding-checklist.md
│   └── pitch-script.md
├── reference/
│   ├── positioning-rules.md   # Brand voice, forbidden words
│   ├── lead-scoring.md        # Qualification matrix
│   └── pricing-tiers.md
├── scripts/
│   ├── schema.sql          # PostgreSQL schema
│   ├── generate_briefing.py # Daily briefing generator (n8n call)
│   └── run_pipeline.sh     # CCTV pipeline launcher
├── tests/
│   ├── test_briefing.py
│   ├── test_lead_scoring.py
│   └── test_bot.py
├── docs/
│   ├── SETUP.md            # Full deployment guide
│   ├── ARCHITECTURE.md     # System design doc
│   └── SALES-KIT.md        # Positioning + pitch scripts
├── .env.example
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Pricing (AED/month)

| Tier | Cameras | Price | Includes |
|------|---------|-------|----------|
| Starter | 1–3 | 199 | Daily briefing |
| Standard | 4–8 | 349 | Briefing + unlimited Q&A |
| Estate | 9–16 | 599 | Full service + priority support |
| Custom | 16+ | Quote | Compounds, managed properties |

## Key Constraints

- **Never position as "security"** — SIRA licensing risk in UAE
- **Never speculate** — if camera data is missing, say so explicitly
- **No raw video storage** — works on event summaries and metadata only
- **One daily briefing max** — no continuous alert flooding

## Tech Stack

- Telegram Bot API (python-telegram-bot v20+)
- PostgreSQL (asyncpg)
- n8n (workflow automation + LLM integration)
- Claude Vision + Claude LLM (OpenRouter or direct Anthropic)
- WhatsApp Business API

## TODO

- [ ] Lead scoring API endpoint
- [ ] Briefing generation pipeline (n8n workflow)
- [ ] Staff roster CRUD UI
- [ ] Arabic language briefing support
- [ ] Multi-property dashboard for property managers