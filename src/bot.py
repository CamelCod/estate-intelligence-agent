"""
Estate Intelligence — Telegram Onboarding Bot
Runs standalone with python-telegram-bot v20+
Deploy on Ubuntu server alongside n8n stack
"""

import asyncio
import json
import logging
import os
import re
import socket
import urllib.request
from datetime import datetime
from typing import Optional

import asyncpg
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ── Config (set via environment variables) ─────────────────────────────────────
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
DB_DSN      = os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost/estate_intelligence")
N8N_ONBOARD_WEBHOOK = os.environ.get("N8N_ONBOARD_WEBHOOK", "http://localhost:5678/webhook/customer-onboarded")
N8N_QA_WEBHOOK = os.environ.get("N8N_QA_WEBHOOK", "http://localhost:5678/webhook/cctv-qa")

# ── Conversation states ────────────────────────────────────────────────────────
(
    STATE_NAME,
    STATE_PROPERTY,
    STATE_CAMERAS,
    STATE_RTSP_COLLECT,
    STATE_RTSP_CONFIRM,
    STATE_STAFF_COUNT,
    STATE_STAFF_COLLECT,
    STATE_BRIEFING_TIME,
    STATE_CONFIRM,
    STATE_DONE,
) = range(10)

# ── Helpers ────────────────────────────────────────────────────────────────────

def validate_rtsp(url: str) -> tuple[bool, str]:
    """
    Quick RTSP reachability check.
    Tries to open a TCP socket to host:port from the RTSP URL.
    Full stream authentication is validated separately by the pipeline.
    """
    url = url.strip()
    if not url.lower().startswith("rtsp://"):
        return False, "URL must start with rtsp://"

    try:
        stripped = url[7:]           # remove rtsp://
        if "@" in stripped:
            stripped = stripped.split("@", 1)[1]
        host_part = stripped.split("/")[0]
        if ":" in host_part:
            host, port_str = host_part.rsplit(":", 1)
            port = int(port_str)
        else:
            host, port = host_part, 554

        sock = socket.create_connection((host, port), timeout=4)
        sock.close()
        return True, "reachable"
    except socket.timeout:
        return False, f"Connection timed out — is the camera on the same network?"
    except Exception as e:
        return False, f"Could not reach camera: {e}"


def briefing_time_keyboard() -> ReplyKeyboardMarkup:
    times = [["6:00 PM", "7:00 PM", "8:00 PM"],
             ["9:00 PM", "10:00 PM", "Custom"]]
    return ReplyKeyboardMarkup(times, one_time_keyboard=True, resize_keyboard=True)


def property_type_keyboard() -> ReplyKeyboardMarkup:
    options = [["Villa", "Compound"], ["Apartment", "Office / Commercial"]]
    return ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True)


async def ping_n8n(customer_data: dict):
    """Fire-and-forget webhook to n8n to create the briefing pipeline."""
    try:
        payload = json.dumps(customer_data).encode()
        req = urllib.request.Request(
            N8N_ONBOARD_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        log.info("n8n webhook fired for customer %s", customer_data.get("telegram_id"))
    except Exception as e:
        log.error("n8n webhook failed: %s", e)


async def save_customer(data: dict):
    """Persist customer record to PostgreSQL."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        await conn.execute(
            """
            INSERT INTO customers (
                telegram_id, name, property_type, cameras,
                staff_roster, briefing_time, onboarded_at, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,'active')
            ON CONFLICT (telegram_id) DO UPDATE SET
                name=$2, property_type=$3, cameras=$4,
                staff_roster=$5, briefing_time=$6,
                onboarded_at=$7, status='active'
            """,
            data["telegram_id"],
            data["name"],
            data["property_type"],
            json.dumps(data["cameras"]),
            json.dumps(data["staff"]),
            data["briefing_time"],
            datetime.utcnow(),
        )
    finally:
        await conn.close()


# ── Conversation handlers ──────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    ctx.user_data["cameras"] = []
    ctx.user_data["staff"] = []

    await update.message.reply_text(
        "Welcome to your *Estate Intelligence* setup.\n\n"
        "I'll walk you through connecting your cameras and setting up your daily home briefing. "
        "Takes about 3 minutes.\n\n"
        "Let's start — *what's your first name?*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return STATE_NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip().split()[0].capitalize()
    ctx.user_data["name"] = name
    ctx.user_data["telegram_id"] = update.effective_user.id

    await update.message.reply_text(
        f"Nice to meet you, {name}. *What type of property is this for?*",
        parse_mode="Markdown",
        reply_markup=property_type_keyboard(),
    )
    return STATE_PROPERTY


async def got_property(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["property_type"] = update.message.text.strip()

    await update.message.reply_text(
        "Got it.\n\n"
        "*How many cameras do you want to connect?*\n\n"
        "Send a number — you can always add more later.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return STATE_CAMERAS


async def got_camera_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 32):
        await update.message.reply_text("Please send a number between 1 and 32.")
        return STATE_CAMERAS

    ctx.user_data["camera_count"] = int(text)
    ctx.user_data["camera_index"] = 1

    await update.message.reply_text(
        f"Perfect — {text} camera(s).\n\n"
        "Now send me the *RTSP URL* for camera 1.\n\n"
        "It looks like:\n`rtsp://admin:password@192.168.1.64:554/stream1`\n\n"
        "You'll find it in your camera app settings under *Network* or *Stream*.",
        parse_mode="Markdown",
    )
    return STATE_RTSP_COLLECT


async def got_rtsp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    idx = ctx.user_data["camera_index"]

    await update.message.reply_text(f"Testing camera {idx}...")

    ok, reason = validate_rtsp(url)

    if ok:
        ctx.user_data["pending_rtsp"] = url
        await update.message.reply_text(
            f"Camera {idx} is reachable.\n\n"
            f"What's a short name for this camera?\n"
            f"Examples: *Front door*, *Garden*, *Garage*, *Side gate*",
            parse_mode="Markdown",
        )
        return STATE_RTSP_CONFIRM
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Try again", callback_data="retry_rtsp"),
            InlineKeyboardButton("Skip this camera", callback_data="skip_camera"),
        ]])
        await update.message.reply_text(
            f"Could not reach camera {idx}.\n\n_{reason}_\n\n"
            "Make sure:\n"
            "- Your server and camera are on the same network\n"
            "- The IP address and port are correct\n"
            "- The camera is powered on\n\n"
            "Want to try again or skip?",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return STATE_RTSP_COLLECT


async def got_camera_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    cam_name = update.message.text.strip()
    url = ctx.user_data.pop("pending_rtsp")
    idx = ctx.user_data["camera_index"]

    ctx.user_data["cameras"].append({"name": cam_name, "rtsp": url, "index": idx})

    total = ctx.user_data["camera_count"]
    next_idx = idx + 1

    if next_idx <= total:
        ctx.user_data["camera_index"] = next_idx
        await update.message.reply_text(
            f"*{cam_name}* saved.\n\n"
            f"Now send me the RTSP URL for camera {next_idx}.",
            parse_mode="Markdown",
        )
        return STATE_RTSP_COLLECT
    else:
        cam_list = "\n".join(f"  - {c['name']}" for c in ctx.user_data["cameras"])
        await update.message.reply_text(
            f"All cameras connected:\n{cam_list}\n\n"
            f"Now let's set up your staff roster.\n\n"
            f"How many people have regular access to your property?\n"
            f"(cleaners, drivers, nannies, gardeners — not family)",
            parse_mode="Markdown",
        )
        ctx.user_data["staff_index"] = 1
        return STATE_STAFF_COUNT


async def rtsp_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = ctx.user_data["camera_index"]

    if query.data == "retry_rtsp":
        await query.message.reply_text(f"Ok — send the RTSP URL for camera {idx} again.")
        return STATE_RTSP_COLLECT
    elif query.data == "skip_camera":
        total = ctx.user_data["camera_count"]
        next_idx = idx + 1
        if next_idx <= total:
            ctx.user_data["camera_index"] = next_idx
            await query.message.reply_text(
                f"Skipped camera {idx}. Send the RTSP URL for camera {next_idx}."
            )
            return STATE_RTSP_COLLECT
        else:
            if not ctx.user_data["cameras"]:
                await query.message.reply_text(
                    "You need at least one working camera to use the service. "
                    "Please check your camera settings and run /start again when ready."
                )
                return ConversationHandler.END

            await query.message.reply_text(
                f"Using {len(ctx.user_data['cameras'])} camera(s).\n\n"
                "Now let's set up your staff roster.\n\n"
                "How many people have regular access to your property?",
            )
            ctx.user_data["staff_index"] = 1
            return STATE_STAFF_COUNT
    return STATE_RTSP_COLLECT


async def got_staff_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text == "0" or text.lower() in ("none", "no one", "nobody"):
        ctx.user_data["staff_count"] = 0
        return await ask_briefing_time(update, ctx)

    if not text.isdigit() or not (1 <= int(text) <= 20):
        await update.message.reply_text("Please send a number (or 0 if no staff).")
        return STATE_STAFF_COUNT

    ctx.user_data["staff_count"] = int(text)
    ctx.user_data["staff_index"] = 1
    await ask_next_staff(update, ctx)
    return STATE_STAFF_COLLECT


async def ask_next_staff(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    idx = ctx.user_data["staff_index"]
    await update.message.reply_text(
        f"*Staff member {idx}*\n\n"
        "Send their details in this format:\n\n"
        "`Name, Role, Days, Arrival time`\n\n"
        "Example:\n`Mariam, Cleaner, Mon/Wed/Fri, 8am`\n\n"
        "Or: `Khalid, Driver, Daily, 7am`",
        parse_mode="Markdown",
    )


async def got_staff_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    parts = [p.strip() for p in raw.split(",")]

    if len(parts) < 3:
        await update.message.reply_text(
            "Please use the format: `Name, Role, Days, Arrival time`\n"
            "Example: `Mariam, Cleaner, Mon/Wed/Fri, 8am`",
            parse_mode="Markdown",
        )
        return STATE_STAFF_COLLECT

    staff_entry = {
        "name":    parts[0],
        "role":    parts[1] if len(parts) > 1 else "Staff",
        "days":    parts[2] if len(parts) > 2 else "Daily",
        "arrival": parts[3] if len(parts) > 3 else "8am",
    }
    ctx.user_data["staff"].append(staff_entry)

    total = ctx.user_data["staff_count"]
    next_idx = ctx.user_data["staff_index"] + 1

    if next_idx <= total:
        ctx.user_data["staff_index"] = next_idx
        await update.message.reply_text(
            f"*{staff_entry['name']}* added.",
            parse_mode="Markdown",
        )
        await ask_next_staff(update, ctx)
        return STATE_STAFF_COLLECT
    else:
        return await ask_briefing_time(update, ctx)


async def ask_briefing_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    staff = ctx.user_data.get("staff", [])
    if staff:
        roster_lines = "\n".join(f"  - {s['name']} ({s['role']})" for s in staff)
        roster_msg = f"Staff saved:\n{roster_lines}\n\n"
    else:
        roster_msg = "No staff roster — I'll focus on deliveries and anomalies.\n\n"

    await update.message.reply_text(
        roster_msg +
        "*What time should I send your daily briefing?*\n\n"
        "Most owners prefer 8pm — after dinner, before sleep.",
        parse_mode="Markdown",
        reply_markup=briefing_time_keyboard(),
    )
    return STATE_BRIEFING_TIME


async def got_briefing_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    time_val = update.message.text.strip()

    if time_val == "Custom":
        await update.message.reply_text(
            "Send your preferred time (e.g. `9:30 PM` or `21:30`)",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return STATE_BRIEFING_TIME

    ctx.user_data["briefing_time"] = time_val
    return await show_summary(update, ctx)


async def show_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    d = ctx.user_data
    name = d["name"]
    prop = d.get("property_type", "Property")
    cams = d.get("cameras", [])
    staff = d.get("staff", [])
    btime = d.get("briefing_time", "8:00 PM")

    cam_lines  = "\n".join(f"  - {c['name']}" for c in cams) or "  (none)"
    staff_lines = "\n".join(
        f"  - {s['name']} — {s['role']} ({s['days']}, from {s['arrival']})"
        for s in staff
    ) or "  (no regular staff)"

    summary = (
        f"*Here's your setup, {name}:*\n\n"
        f"Property: {prop}\n\n"
        f"Cameras ({len(cams)}):\n{cam_lines}\n\n"
        f"Staff:\n{staff_lines}\n\n"
        f"Daily briefing: {btime}\n\n"
        "Everything look right?"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, activate", callback_data="confirm_yes"),
        InlineKeyboardButton("Start over", callback_data="confirm_no"),
    ]])

    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return STATE_CONFIRM


async def confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        await query.message.reply_text(
            "No problem — let's start over. Send /start when ready."
        )
        return ConversationHandler.END

    d = ctx.user_data
    customer_data = {
        "telegram_id":    d["telegram_id"],
        "name":           d["name"],
        "property_type":  d.get("property_type", "Villa"),
        "cameras":        d.get("cameras", []),
        "staff":          d.get("staff", []),
        "briefing_time":  d.get("briefing_time", "8:00 PM"),
    }

    try:
        await save_customer(customer_data)
        await ping_n8n(customer_data)
        success = True
    except Exception as e:
        log.error("Onboarding persistence failed: %s", e)
        success = False

    if success:
        await query.message.reply_text(
            f"*You're all set, {d['name']}!*\n\n"
            f"Your first briefing will arrive tonight at *{d['briefing_time']}*.\n\n"
            "You can ask me anything about your home anytime — just send a message like:\n"
            "_Did the cleaner arrive today?_\n"
            "_Was there a delivery this afternoon?_\n\n"
            "I'll check your cameras and reply.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await query.message.reply_text(
            "Something went wrong saving your setup. "
            "Please contact support or try /start again."
        )

    return ConversationHandler.END


async def qa_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handles free-form questions from already-onboarded customers.
    Forwards to n8n which queries the day's event log via Claude.
    """
    telegram_id = update.effective_user.id
    question = update.message.text.strip()

    payload = json.dumps({
        "telegram_id": telegram_id,
        "question": question,
        "timestamp": datetime.utcnow().isoformat(),
    }).encode()

    try:
        req = urllib.request.Request(
            N8N_QA_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            answer = result.get("answer", "I don't have that information right now.")
    except Exception as e:
        log.error("QA webhook failed: %s", e)
        answer = "I'm having trouble checking your cameras right now. Please try again in a moment."

    await update.message.reply_text(answer)


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Setup cancelled. Send /start when you're ready to connect your cameras.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_NAME:         [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            STATE_PROPERTY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, got_property)],
            STATE_CAMERAS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, got_camera_count)],
            STATE_RTSP_COLLECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_rtsp),
                CallbackQueryHandler(rtsp_callback, pattern="^(retry_rtsp|skip_camera)$"),
            ],
            STATE_RTSP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_camera_name)],
            STATE_STAFF_COUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_staff_count)],
            STATE_STAFF_COLLECT:[MessageHandler(filters.TEXT & ~filters.COMMAND, got_staff_member)],
            STATE_BRIEFING_TIME:[MessageHandler(filters.TEXT & ~filters.COMMAND, got_briefing_time)],
            STATE_CONFIRM:      [CallbackQueryHandler(confirm_callback, pattern="^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # Q&A for already-onboarded customers (messages outside onboarding flow)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, qa_handler))

    log.info("Estate Intelligence bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()