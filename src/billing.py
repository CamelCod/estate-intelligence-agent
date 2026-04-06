"""
billing.py — Stripe Billing & Subscription Infrastructure
Estate Intelligence Agent

Handles:
  - Customer creation / retrieval in Stripe
  - Subscription creation with 14-day free trial
  - Tier → Stripe Price ID mapping
  - Payment method management
  - Subscription status queries
  - Invoicing helpers
"""

from __future__ import annotations

import os
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

import stripe

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# Stripe Price IDs — create these in your Stripe dashboard and set in .env
TIER_PRICE_IDS: Dict[str, str] = {
    "starter" : os.environ.get("STRIPE_PRICE_STARTER",  "price_starter_placeholder"),
    "standard": os.environ.get("STRIPE_PRICE_STANDARD", "price_standard_placeholder"),
    "estate"  : os.environ.get("STRIPE_PRICE_ESTATE",   "price_estate_placeholder"),
}

TIER_PRICES_AED: Dict[str, int] = {
    "starter" : 199,
    "standard": 349,
    "estate"  : 599,
}

TIER_CAMERA_LIMITS: Dict[str, int] = {
    "starter" : 3,
    "standard": 8,
    "estate"  : 16,
    "custom"  : 9999,
}

TRIAL_PERIOD_DAYS = 14


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BillingCustomer:
    stripe_customer_id: str
    telegram_id       : int
    name              : str
    email             : str
    tier              : str
    subscription_id   : Optional[str] = None
    subscription_status: str          = "trialing"
    trial_end         : Optional[int] = None   # Unix timestamp
    payment_method_id : Optional[str] = None


@dataclass
class CheckoutSession:
    url        : str
    session_id : str
    customer_id: str
    tier       : str


# ─────────────────────────────────────────────────────────────────────────────
# TIER RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def resolve_tier(camera_count: int) -> str:
    """
    Goal  : Map a camera count to the correct pricing tier.
    Input : camera_count — integer, number of RTSP cameras
    Output: tier name string
    Steps :
        1. Check camera_count against each tier limit ascending.
        2. Return the first tier where count fits.
        3. Default to custom for 17+.
    """
    if camera_count <= TIER_CAMERA_LIMITS["starter"]:
        return "starter"
    if camera_count <= TIER_CAMERA_LIMITS["standard"]:
        return "standard"
    if camera_count <= TIER_CAMERA_LIMITS["estate"]:
        return "estate"
    return "custom"


# ─────────────────────────────────────────────────────────────────────────────
# STRIPE CUSTOMER OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def create_stripe_customer(
    telegram_id: int,
    name       : str,
    email      : str,
    phone      : Optional[str] = None,
) -> stripe.Customer:
    """
    Goal  : Create or retrieve a Stripe customer for a Telegram user.
    Input : telegram_id, name, email, optional phone
    Output: stripe.Customer object
    Steps :
        1. Search existing customers by telegram_id metadata.
        2. Return existing if found.
        3. Create new customer with metadata if not found.
    """
    existing = stripe.Customer.search(
        query=f"metadata['telegram_id']:'{telegram_id}'"
    )
    if existing.data:
        log.info("Reusing existing Stripe customer for telegram_id=%s", telegram_id)
        return existing.data[0]

    customer_data: Dict = {
        "name"    : name,
        "email"   : email,
        "metadata": {"telegram_id": str(telegram_id), "platform": "estate_intelligence"},
    }
    if phone:
        customer_data["phone"] = phone

    customer = stripe.Customer.create(**customer_data)
    log.info("Created Stripe customer %s for telegram_id=%s", customer.id, telegram_id)
    return customer


def get_stripe_customer(stripe_customer_id: str) -> Optional[stripe.Customer]:
    """Retrieve a Stripe customer by ID, return None if not found."""
    try:
        return stripe.Customer.retrieve(stripe_customer_id)
    except stripe.error.InvalidRequestError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SUBSCRIPTION OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def create_subscription(
    customer_id: str,
    tier       : str,
    trial_days : int = TRIAL_PERIOD_DAYS,
) -> stripe.Subscription:
    """
    Goal  : Create a Stripe subscription for the given tier with trial period.
    Input : customer_id (Stripe), tier string, optional trial_days override
    Output: stripe.Subscription object
    Steps :
        1. Resolve Price ID from tier.
        2. Create subscription with trial_period_days.
        3. Set payment_behavior=default_incomplete to allow card collection.
        4. Return subscription object.
    """
    price_id = TIER_PRICE_IDS.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier '{tier}'. Valid tiers: {list(TIER_PRICE_IDS)}")

    subscription = stripe.Subscription.create(
        customer          = customer_id,
        items             = [{"price": price_id}],
        trial_period_days = trial_days,
        payment_behavior  = "default_incomplete",
        payment_settings  = {"save_default_payment_method": "on_subscription"},
        expand            = ["latest_invoice.payment_intent"],
        metadata          = {"tier": tier, "platform": "estate_intelligence"},
    )
    log.info("Created subscription %s for customer %s (tier=%s)", subscription.id, customer_id, tier)
    return subscription


def get_subscription(subscription_id: str) -> Optional[stripe.Subscription]:
    """Retrieve a Stripe subscription, expand latest invoice."""
    try:
        return stripe.Subscription.retrieve(
            subscription_id,
            expand=["latest_invoice", "default_payment_method"]
        )
    except stripe.error.InvalidRequestError:
        return None


def cancel_subscription(
    subscription_id: str,
    at_period_end  : bool = True,
) -> stripe.Subscription:
    """
    Cancel a subscription.
    at_period_end=True means service continues until billing period ends.
    """
    return stripe.Subscription.modify(
        subscription_id,
        cancel_at_period_end=at_period_end
    )


def upgrade_subscription(
    subscription_id: str,
    new_tier       : str,
) -> stripe.Subscription:
    """
    Goal  : Upgrade or downgrade a subscription to a new tier.
    Input : subscription_id, new_tier string
    Output: Updated stripe.Subscription
    Steps :
        1. Retrieve current subscription.
        2. Get new price ID.
        3. Modify subscription item to new price.
        4. Set proration to immediate.
    """
    sub      = stripe.Subscription.retrieve(subscription_id)
    item_id  = sub["items"]["data"][0]["id"]
    price_id = TIER_PRICE_IDS.get(new_tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {new_tier}")

    updated = stripe.Subscription.modify(
        subscription_id,
        items             = [{"id": item_id, "price": price_id}],
        proration_behavior= "create_prorations",
        metadata          = {"tier": new_tier},
    )
    log.info("Upgraded subscription %s to tier=%s", subscription_id, new_tier)
    return updated


def is_subscription_active(subscription_id: str) -> bool:
    """
    Return True if the subscription is in a state that allows briefing delivery.
    Active states: active, trialing
    """
    sub = get_subscription(subscription_id)
    if sub is None:
        return False
    return sub.status in ("active", "trialing")


# ─────────────────────────────────────────────────────────────────────────────
# STRIPE CHECKOUT SESSION (for web signup)
# ─────────────────────────────────────────────────────────────────────────────

def create_checkout_session(
    customer_id     : str,
    tier            : str,
    success_url     : str,
    cancel_url      : str,
    telegram_id     : Optional[int] = None,
) -> CheckoutSession:
    """
    Goal  : Create a Stripe Checkout Session for the signup page flow.
    Input : customer_id, tier, redirect URLs
    Output: CheckoutSession with hosted URL and session ID
    Steps :
        1. Resolve Price ID.
        2. Create Checkout session in subscription mode.
        3. Include trial period.
        4. Return hosted checkout URL.
    """
    price_id = TIER_PRICE_IDS.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier '{tier}'")

    meta: Dict = {"tier": tier}
    if telegram_id:
        meta["telegram_id"] = str(telegram_id)

    session = stripe.checkout.Session.create(
        customer          = customer_id,
        mode              = "subscription",
        payment_method_types = ["card"],
        line_items        = [{"price": price_id, "quantity": 1}],
        subscription_data = {
            "trial_period_days": TRIAL_PERIOD_DAYS,
            "metadata"         : meta,
        },
        success_url       = success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url        = cancel_url,
        metadata          = meta,
    )
    return CheckoutSession(
        url        = session.url,
        session_id = session.id,
        customer_id= customer_id,
        tier       = tier,
    )


# ─────────────────────────────────────────────────────────────────────────────
# BILLING PORTAL (self-service)
# ─────────────────────────────────────────────────────────────────────────────

def create_billing_portal_session(
    customer_id : str,
    return_url  : str,
) -> str:
    """Create a Stripe Customer Portal session URL for self-service billing."""
    session = stripe.billing_portal.Session.create(
        customer   = customer_id,
        return_url = return_url,
    )
    return session.url


# ─────────────────────────────────────────────────────────────────────────────
# INVOICE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_upcoming_invoice(customer_id: str) -> Optional[stripe.Invoice]:
    """Preview the upcoming invoice for a customer."""
    try:
        return stripe.Invoice.upcoming(customer=customer_id)
    except stripe.error.InvalidRequestError:
        return None


def list_invoices(customer_id: str, limit: int = 10) -> list:
    """Retrieve the last N invoices for a customer."""
    result = stripe.Invoice.list(customer=customer_id, limit=limit)
    return result.data


# ─────────────────────────────────────────────────────────────────────────────
# USAGE METERING (for Custom tier / overage tracking)
# ─────────────────────────────────────────────────────────────────────────────

def record_briefing_usage(
    subscription_item_id: str,
    quantity            : int = 1,
) -> None:
    """
    Record a usage record for metered billing (Custom tier).
    Subscription item must be configured as metered in Stripe.
    """
    try:
        stripe.SubscriptionItem.create_usage_record(
            subscription_item_id,
            quantity  = quantity,
            action    = "increment",
        )
    except stripe.error.InvalidRequestError as exc:
        log.warning("Usage record failed (non-metered plan?): %s", exc)
