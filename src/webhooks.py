"""
webhooks.py — Stripe Webhook Handler + FastAPI Application
Estate Intelligence Agent

Handles incoming Stripe webhook events:
  - checkout.session.completed   → activate subscription in DB
  - customer.subscription.updated → sync tier/status changes
  - customer.subscription.deleted → mark customer inactive
  - invoice.payment_failed        → send Telegram alert + pause briefings
  - invoice.payment_succeeded     → resume briefings, update billing record

Also exposes REST API endpoints for:
  - POST /signup           → create Stripe customer + checkout session
  - POST /portal           → create billing portal session
  - GET  /subscription/:id → subscription status
  - POST /eval             → run a propositional eval suite
  - GET  /health           → health check
"""

from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, Optional

import stripe
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from billing import (
    create_stripe_customer,
    create_checkout_session,
    create_billing_portal_session,
    get_subscription,
    is_subscription_active,
    TIER_PRICES_AED,
    resolve_tier,
)
from eval import EvalRunner, CognitiveReport

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Estate Intelligence Agent — API",
    description = "Billing, eval, and subscription management for Estate Intelligence",
    version     = "1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

WEBHOOK_SECRET    = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BASE_URL          = os.environ.get("BASE_URL", "https://yourdomain.com")
TELEGRAM_BOT_TOKEN= os.environ.get("TELEGRAM_BOT_TOKEN", "")


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE POOL (lazy import to avoid circular deps)
# ─────────────────────────────────────────────────────────────────────────────

_db_pool = None


async def get_db():
    """Return the asyncpg pool, initializing on first use."""
    global _db_pool
    if _db_pool is None:
        import asyncpg
        _db_pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return _db_pool


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST / RESPONSE MODELS
# ─────────────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    name          : str
    email         : EmailStr
    phone         : Optional[str] = None
    telegram_id   : Optional[int] = None
    camera_count  : int           = 1
    tier          : Optional[str] = None   # auto-resolved from camera_count if not set


class PortalRequest(BaseModel):
    stripe_customer_id: str


class EvalRequest(BaseModel):
    suite  : str
    payload: Any
    kwargs : Dict[str, Any] = {}


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "estate-intelligence-agent"}


# ─────────────────────────────────────────────────────────────────────────────
# SIGNUP — creates Stripe customer + returns checkout URL
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/signup")
async def signup(body: SignupRequest):
    """
    Goal  : Onboard a new customer and start their 14-day free trial.
    Input : name, email, camera_count (+ optional telegram_id, tier)
    Output: Stripe checkout URL for payment method collection
    Steps :
        1. Resolve tier from camera_count if not provided.
        2. Create Stripe customer.
        3. Create Checkout Session (subscription + trial).
        4. Return session URL for redirect.
    """
    tier = body.tier or resolve_tier(body.camera_count)
    if tier == "custom":
        return JSONResponse(
            status_code=200,
            content={
                "tier"   : "custom",
                "message": "Custom plans require a quote. Please contact us via WhatsApp.",
                "whatsapp": "https://wa.me/971XXXXXXXX?text=I%20need%20a%20custom%20estate%20intelligence%20plan",
            }
        )

    try:
        customer = create_stripe_customer(
            telegram_id = body.telegram_id or 0,
            name        = body.name,
            email       = body.email,
            phone       = body.phone,
        )

        session = create_checkout_session(
            customer_id  = customer.id,
            tier         = tier,
            success_url  = f"{BASE_URL}/success",
            cancel_url   = f"{BASE_URL}/#pricing",
            telegram_id  = body.telegram_id,
        )

        return {
            "checkout_url"      : session.url,
            "session_id"        : session.session_id,
            "stripe_customer_id": customer.id,
            "tier"              : tier,
            "price_aed"         : TIER_PRICES_AED.get(tier),
            "trial_days"        : 14,
        }

    except stripe.error.StripeError as exc:
        log.error("Stripe error during signup: %s", exc)
        raise HTTPException(status_code=502, detail=f"Payment provider error: {exc.user_message}")


# ─────────────────────────────────────────────────────────────────────────────
# BILLING PORTAL
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/portal")
async def billing_portal(body: PortalRequest):
    """Return a Stripe Customer Portal URL for self-service billing management."""
    try:
        url = create_billing_portal_session(
            customer_id = body.stripe_customer_id,
            return_url  = f"{BASE_URL}/dashboard",
        )
        return {"portal_url": url}
    except stripe.error.StripeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# SUBSCRIPTION STATUS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/subscription/{subscription_id}")
async def subscription_status(subscription_id: str):
    """Get live subscription status from Stripe."""
    sub = get_subscription(subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {
        "id"         : sub.id,
        "status"     : sub.status,
        "tier"       : sub.metadata.get("tier"),
        "trial_end"  : sub.trial_end,
        "current_period_end": sub.current_period_end,
        "cancel_at_period_end": sub.cancel_at_period_end,
    }


# ─────────────────────────────────────────────────────────────────────────────
# EVAL ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/eval")
async def run_eval(body: EvalRequest):
    """
    Run a propositional eval suite and return the structured result.

    Available suites: briefing, lead, rtsp, subscription
    """
    try:
        result = EvalRunner.run(body.suite, body.payload, **body.kwargs)
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/eval/report")
async def run_eval_report(body: EvalRequest):
    """Run eval and return a human-readable propositional text report."""
    try:
        result = EvalRunner.run(body.suite, body.payload, **body.kwargs)
        return {"report": CognitiveReport.render_text(result), "verdict": result.verdict}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# STRIPE WEBHOOK HANDLER
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/webhooks/stripe")
async def stripe_webhook(
    request           : Request,
    stripe_signature  : Optional[str] = Header(None, alias="stripe-signature"),
):
    """
    Goal  : Process Stripe lifecycle events and sync to our database.
    Input : Raw Stripe webhook payload + signature header
    Output: {"received": true} on success, 400 on signature failure
    Steps :
        1. Verify webhook signature.
        2. Dispatch to event-specific handler.
        3. Return 200 to acknowledge receipt.
    """
    payload = await request.body()

    # ── Verify signature ────────────────────────────────────────────────────
    if WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, WEBHOOK_SECRET
            )
        except stripe.error.SignatureVerificationError:
            log.warning("Invalid Stripe webhook signature")
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        # Dev mode: skip signature check
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)

    log.info("Stripe event: %s", event["type"])

    # ── Dispatch to handler ───────────────────────────────────────────────────
    handlers: Dict[str, Any] = {
        "checkout.session.completed"         : _handle_checkout_completed,
        "customer.subscription.updated"     : _handle_subscription_updated,
        "customer.subscription.deleted"      : _handle_subscription_deleted,
        "invoice.payment_succeeded"         : _handle_payment_succeeded,
        "invoice.payment_failed"            : _handle_payment_failed,
        "customer.subscription.trial_will_end": _handle_trial_ending,
    }
    handler = handlers.get(event["type"])
    if handler:
        try:
            await handler(event["data"]["object"])
        except Exception as exc:
            log.error("Webhook handler error for %s: %s", event["type"], exc, exc_info=True)
            # Return 200 anyway — Stripe will retry if we return 5xx
    else:
        log.debug("Unhandled event type: %s", event["type"])

    return {"received": True}


# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK EVENT HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_checkout_completed(session: Dict[str, Any]) -> None:
    """
    Checkout completed → subscription is now created.
    Update the customer record in PostgreSQL with Stripe IDs.
    """
    customer_id     = session.get("customer")
    subscription_id = session.get("subscription")
    metadata        = session.get("metadata", {})
    telegram_id     = metadata.get("telegram_id")
    tier            = metadata.get("tier", "starter")

    if not telegram_id:
        log.warning("checkout.session.completed: no telegram_id in metadata, skipping DB update")
        return

    db = await get_db()
    await db.execute(
        """
        UPDATE customers
        SET    stripe_customer_id  = $1,
               stripe_subscription_id = $2,
               subscription_status = 'trialing',
               tier                = $3,
               status              = 'active'
        WHERE  telegram_id = $4
        """,
        customer_id, subscription_id, tier, int(telegram_id)
    )
    log.info("Activated customer telegram_id=%s (tier=%s)", telegram_id, tier)


async def _handle_subscription_updated(sub: Dict[str, Any]) -> None:
    """Sync subscription status and tier changes to the database."""
    customer_id = sub.get("customer")
    status      = sub.get("status")
    tier        = sub.get("metadata", {}).get("tier")

    db = await get_db()
    await db.execute(
        """
        UPDATE customers
        SET    subscription_status = $1,
               tier                = COALESCE($2, tier)
        WHERE  stripe_customer_id  = $3
        """,
        status, tier, customer_id
    )
    log.info("Subscription updated for customer %s: status=%s tier=%s", customer_id, status, tier)


async def _handle_subscription_deleted(sub: Dict[str, Any]) -> None:
    """Subscription cancelled → mark customer inactive."""
    customer_id = sub.get("customer")
    db = await get_db()
    await db.execute(
        "UPDATE customers SET status = 'inactive', subscription_status = 'cancelled' WHERE stripe_customer_id = $1",
        customer_id
    )
    log.info("Subscription deleted for customer %s", customer_id)


async def _handle_payment_succeeded(invoice: Dict[str, Any]) -> None:
    """Payment succeeded → resume briefings (set status active if was past_due)."""
    customer_id = invoice.get("customer")
    db = await get_db()
    await db.execute(
        """
        UPDATE customers
        SET    subscription_status = 'active',
               status              = 'active'
        WHERE  stripe_customer_id  = $1
          AND  subscription_status IN ('past_due', 'unpaid')
        """,
        customer_id
    )
    log.info("Payment succeeded for customer %s", customer_id)


async def _handle_payment_failed(invoice: Dict[str, Any]) -> None:
    """
    Payment failed → mark past_due and send Telegram notification.
    Briefings are paused until payment resolves.
    """
    customer_id   = invoice.get("customer")
    attempt_count = invoice.get("attempt_count", 1)

    db = await get_db()
    row = await db.fetchrow(
        "SELECT telegram_id, name FROM customers WHERE stripe_customer_id = $1",
        customer_id
    )

    await db.execute(
        "UPDATE customers SET subscription_status = 'past_due' WHERE stripe_customer_id = $1",
        customer_id
    )

    if row and TELEGRAM_BOT_TOKEN:
        await _send_telegram_alert(
            row["telegram_id"],
            f"Hi {row['name'].split()[0]}, your Estate Intelligence payment failed "
            f"(attempt {attempt_count}). Your daily briefings will pause until payment is resolved. "
            f"Please update your payment method: {BASE_URL}/billing"
        )

    log.warning("Payment FAILED for customer %s (attempt %s)", customer_id, attempt_count)


async def _handle_trial_ending(sub: Dict[str, Any]) -> None:
    """Trial ending in 3 days → send reminder to add payment method."""
    customer_id = sub.get("customer")
    db = await get_db()
    row = await db.fetchrow(
        "SELECT telegram_id, name FROM customers WHERE stripe_customer_id = $1",
        customer_id
    )
    if row and TELEGRAM_BOT_TOKEN:
        await _send_telegram_alert(
            row["telegram_id"],
            f"Hi {row['name'].split()[0]}, your free trial ends in 3 days. "
            f"Add a payment method to keep your daily estate briefings: {BASE_URL}/billing"
        )
    log.info("Trial ending alert sent for customer %s", customer_id)


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM NOTIFICATION HELPER
# ─────────────────────────────────────────────────────────────────────────────

async def _send_telegram_alert(telegram_id: int, message: str) -> None:
    """Send a message to a Telegram user via Bot API."""
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"chat_id": telegram_id, "text": message})
            resp.raise_for_status()
    except Exception as exc:
        log.warning("Telegram alert failed for %s: %s", telegram_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# RUN (dev server)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhooks:app", host="0.0.0.0", port=8000, reload=True)
