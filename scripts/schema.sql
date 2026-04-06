-- Estate Intelligence — PostgreSQL Schema
-- Run once: createdb estate_intelligence && psql -d estate_intelligence -f schema.sql

CREATE TABLE IF NOT EXISTS customers (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    property_type   TEXT,
    cameras         JSONB NOT NULL DEFAULT '[]',
    staff_roster    JSONB NOT NULL DEFAULT '[]',
    briefing_time   TEXT NOT NULL DEFAULT '8:00 PM',
    language        TEXT NOT NULL DEFAULT 'en',
    status          TEXT NOT NULL DEFAULT 'active',
    -- Stripe billing
    stripe_customer_id       TEXT,
    stripe_subscription_id  TEXT,
    subscription_status     TEXT DEFAULT 'trialing',
    tier                      TEXT DEFAULT 'starter',
    -- Metadata
    onboarded_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_log (
    id              SERIAL PRIMARY KEY,
    customer_id     INT REFERENCES customers(id) ON DELETE CASCADE,
    event_date      DATE NOT NULL,
    camera_name     TEXT,
    event_type      TEXT,
    event_time      TIMESTAMPTZ NOT NULL,
    description     TEXT,
    raw_vision_json JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS briefings (
    id              SERIAL PRIMARY KEY,
    customer_id     INT REFERENCES customers(id) ON DELETE CASCADE,
    briefing_date    DATE NOT NULL,
    content         TEXT NOT NULL,
    delivered_at    TIMESTAMPTZ,
    telegram_msg_id BIGINT,
    cost_aed        NUMERIC(8,2),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(customer_id, briefing_date)
);

CREATE INDEX IF NOT EXISTS idx_event_log_customer_date ON event_log(customer_id, event_date);
CREATE INDEX IF NOT EXISTS idx_briefings_customer_date ON briefings(customer_id, briefing_date);
CREATE INDEX IF NOT EXISTS idx_customers_telegram_id ON customers(telegram_id);
CREATE INDEX IF NOT EXISTS idx_customers_stripe ON customers(stripe_customer_id);

-- View: active customers who haven't received briefing today
CREATE OR REPLACE VIEW todays_customers AS
SELECT
    c.id,
    c.telegram_id,
    c.name,
    c.property_type,
    c.cameras,
    c.staff_roster,
    c.briefing_time,
    c.language,
    c.tier,
    c.stripe_subscription_id,
    c.subscription_status,
    b.id IS NULL AS needs_briefing_today
FROM customers c
LEFT JOIN briefings b
    ON b.customer_id = c.id AND b.briefing_date = CURRENT_DATE
WHERE c.status = 'active'
  AND c.subscription_status IN ('active', 'trialing');