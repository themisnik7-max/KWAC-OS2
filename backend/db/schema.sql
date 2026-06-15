-- ============================================================
-- KWAC OS v2 — Complete Database Schema
-- Run once: psql $DATABASE_URL -f schema.sql
-- To reset: DROP SCHEMA public CASCADE; CREATE SCHEMA public; then re-run
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm"; -- for fuzzy text search on names/addresses

-- ============================================================
-- 1. USERS & AUTH
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('agent', 'ceo', 'admin')),
    is_active       BOOLEAN DEFAULT TRUE,
    avatar_url      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- ============================================================
-- 2. AGENTS (extends users with agent-specific data)
-- ============================================================

CREATE TABLE IF NOT EXISTS agents (
    id              UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    team            TEXT,                          -- e.g. "Team Alpha"
    hire_date       DATE,
    phone           TEXT,
    xp_total        INTEGER DEFAULT 0,
    xp_this_week    INTEGER DEFAULT 0,
    xp_this_month   INTEGER DEFAULT 0,
    level           INTEGER DEFAULT 1,
    streak_weeks    INTEGER DEFAULT 0,             -- consecutive weeks submitted
    streak_best     INTEGER DEFAULT 0,
    last_submitted  DATE,
    badges          JSONB DEFAULT '[]',            -- array of badge slugs
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 3. WEEKLY SUBMISSIONS (core performance data)
-- ============================================================

CREATE TABLE IF NOT EXISTS weekly_submissions (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id                UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start              DATE NOT NULL,          -- always Monday
    week_end                DATE NOT NULL,          -- always Sunday
    submitted_at            TIMESTAMPTZ,
    is_locked               BOOLEAN DEFAULT FALSE,  -- locked after Sunday 23:59

    -- LEAD GENERATION
    cold_calls              INTEGER DEFAULT 0,
    social_media_leads      INTEGER DEFAULT 0,
    mail_leads              INTEGER DEFAULT 0,
    portal_leads            INTEGER DEFAULT 0,      -- leads from portals (XE, Spitogatos)
    referrals               INTEGER DEFAULT 0,

    -- FOLLOW UP
    followup_calls          INTEGER DEFAULT 0,

    -- APPOINTMENTS
    first_meetings          INTEGER DEFAULT 0,
    second_meetings         INTEGER DEFAULT 0,
    meetings_with_seller    INTEGER DEFAULT 0,
    meetings_with_buyer     INTEGER DEFAULT 0,
    meetings_with_tenant    INTEGER DEFAULT 0,

    -- LISTINGS
    exclusive_listings      INTEGER DEFAULT 0,
    simple_listings         INTEGER DEFAULT 0,

    -- CONTRACTS
    sale_contracts          INTEGER DEFAULT 0,
    purchase_contracts      INTEGER DEFAULT 0,
    rental_contracts        INTEGER DEFAULT 0,

    -- MARKETING
    photo_shoots            INTEGER DEFAULT 0,
    open_houses             INTEGER DEFAULT 0,
    matterport_scans        INTEGER DEFAULT 0,
    floor_plans             INTEGER DEFAULT 0,

    -- NETWORKING
    new_partners            INTEGER DEFAULT 0,
    referrals_given         INTEGER DEFAULT 0,

    -- TRAINING & ADMIN
    trainings_attended      INTEGER DEFAULT 0,
    team_meetings           INTEGER DEFAULT 0,
    conferences             INTEGER DEFAULT 0,

    -- COMPUTED (filled by XP engine after submit)
    xp_earned               INTEGER DEFAULT 0,
    goals_hit               JSONB DEFAULT '{}',     -- {"cold_calls": true, "first_meetings": false}
    notes                   TEXT,

    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(agent_id, week_start)                    -- one submission per agent per week
);

CREATE INDEX IF NOT EXISTS idx_submissions_agent ON weekly_submissions(agent_id);
CREATE INDEX IF NOT EXISTS idx_submissions_week ON weekly_submissions(week_start);

-- ============================================================
-- 4. WEEKLY GOALS (configurable targets per metric)
-- ============================================================

CREATE TABLE IF NOT EXISTS weekly_goals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    metric          TEXT NOT NULL UNIQUE,           -- matches column name in weekly_submissions
    target          INTEGER NOT NULL,
    xp_value        INTEGER NOT NULL,               -- XP per unit of this metric
    xp_bonus        INTEGER DEFAULT 0,              -- bonus XP for hitting 100% of target
    label_el        TEXT NOT NULL,                  -- Greek label for UI
    category        TEXT NOT NULL,                  -- for grouping in the form
    sort_order      INTEGER DEFAULT 0,
    is_active       BOOLEAN DEFAULT TRUE
);

-- Default goals (edit via admin panel or direct SQL)
INSERT INTO weekly_goals (metric, target, xp_value, xp_bonus, label_el, category, sort_order) VALUES
    ('cold_calls',          30, 1,  20,  'Cold calls',           'lead_generation', 1),
    ('social_media_leads',  5,  3,  15,  'Social media leads',   'lead_generation', 2),
    ('referrals',           2,  5,  10,  'Συστάσεις',            'lead_generation', 3),
    ('followup_calls',      20, 2,  15,  'Follow up calls',      'follow_up',       4),
    ('first_meetings',      4,  10, 30,  '1ο ραντεβού',          'appointments',    5),
    ('second_meetings',     2,  15, 25,  '2ο ραντεβού',          'appointments',    6),
    ('exclusive_listings',  1,  50, 50,  'Αποκλειστική ανάθεση', 'listings',        7),
    ('simple_listings',     2,  20, 20,  'Απλή ανάθεση',         'listings',        8),
    ('sale_contracts',      1,  100,100, 'Συμβόλαιο πώλησης',    'contracts',       9),
    ('rental_contracts',    1,  60, 60,  'Συμβόλαιο ενοικίου',   'contracts',       10),
    ('open_houses',         1,  15, 10,  'Open House',           'marketing',       11),
    ('trainings_attended',  1,  10, 0,   'Εκπαίδευση',           'training',        12)
ON CONFLICT (metric) DO NOTHING;

-- ============================================================
-- 5. SPRINT CALLS (3x per week, coach-run sessions)
-- ============================================================

CREATE TABLE IF NOT EXISTS sprint_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_date    DATE NOT NULL,
    session_number  INTEGER NOT NULL CHECK (session_number IN (1,2,3)),
    coach_id        UUID REFERENCES users(id),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_date, session_number)
);

CREATE TABLE IF NOT EXISTS sprint_entries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID NOT NULL REFERENCES sprint_sessions(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES users(id),
    calls_made      INTEGER DEFAULT 0,
    leads_generated INTEGER DEFAULT 0,
    meetings_booked INTEGER DEFAULT 0,
    entered_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, agent_id)
);

-- ============================================================
-- 6. PROPERTIES (full CRM file)
-- ============================================================

CREATE TABLE IF NOT EXISTS properties (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id            UUID NOT NULL REFERENCES users(id),
    ilist_code          TEXT,                       -- i-list reference code
    status              TEXT DEFAULT 'active' CHECK (status IN ('active','sold','rented','withdrawn')),
    transaction_type    TEXT NOT NULL CHECK (transaction_type IN ('sale','rental')),

    -- Location
    address             TEXT NOT NULL,
    area                TEXT,                       -- neighbourhood/area name
    municipality        TEXT,
    lat                 NUMERIC(10,7),
    lng                 NUMERIC(10,7),

    -- Physical characteristics
    property_type       TEXT,                       -- apartment, house, office, land, storage
    sqm                 NUMERIC(8,2),
    floor               INTEGER,
    total_floors        INTEGER,
    bedrooms            INTEGER,
    bathrooms           INTEGER,
    year_built          INTEGER,
    year_renovated      INTEGER,
    condition           TEXT CHECK (condition IN ('new','excellent','good','fair','needs_work')),
    heating             TEXT,
    parking             BOOLEAN DEFAULT FALSE,
    storage             BOOLEAN DEFAULT FALSE,
    elevator            BOOLEAN DEFAULT FALSE,
    garden              BOOLEAN DEFAULT FALSE,
    view                TEXT,

    -- Pricing
    price_asking        NUMERIC(12,2),
    price_final         NUMERIC(12,2),
    price_per_sqm       NUMERIC(8,2),              -- computed on insert/update
    commission_pct      NUMERIC(5,2),

    -- Dates
    listing_date        DATE,
    sold_rented_date    DATE,
    days_on_market      INTEGER,                   -- computed on close

    -- Phase 1: Listing
    listing_type        TEXT CHECK (listing_type IN ('exclusive','simple')),
    listing_expiry      DATE,
    legal_status        TEXT,                      -- clean title, mortgage, etc.
    energy_class        TEXT,

    -- Notes
    description         TEXT,
    internal_notes      TEXT,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_properties_agent ON properties(agent_id);
CREATE INDEX IF NOT EXISTS idx_properties_status ON properties(status);
CREATE INDEX IF NOT EXISTS idx_properties_area ON properties(area);
CREATE INDEX IF NOT EXISTS idx_properties_latLng ON properties(lat, lng);

-- Property contacts (owner, buyer, lawyers, notary)
CREATE TABLE IF NOT EXISTS property_contacts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id     UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('owner','buyer','tenant','owner_lawyer','buyer_lawyer','notary')),
    person_id       UUID REFERENCES people(id),    -- link to people DB
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Property viewings (υποδείξεις)
CREATE TABLE IF NOT EXISTS property_viewings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id     UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES users(id),
    viewed_at       TIMESTAMPTZ NOT NULL,
    prospect_name   TEXT,
    prospect_phone  TEXT,
    outcome         TEXT CHECK (outcome IN ('interested','not_interested','offer_pending','unknown')),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Written offers (γραπτές προσφορές)
CREATE TABLE IF NOT EXISTS property_offers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id     UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    offered_by      TEXT,                          -- buyer name
    offered_at      DATE NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected','countered')),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Phase 3: Closing documents
CREATE TABLE IF NOT EXISTS property_closing (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id         UUID UNIQUE NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    presale_date        DATE,
    presale_amount      NUMERIC(12,2),             -- προκαταβολή
    contract_date       DATE,
    final_price         NUMERIC(12,2),
    commission_amount   NUMERIC(12,2),
    notary_name         TEXT,
    notary_date         DATE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 7. PEOPLE DB (contacts, leads, clients)
-- ============================================================

CREATE TABLE IF NOT EXISTS people (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name       TEXT NOT NULL,
    phone           TEXT,
    phone_alt       TEXT,
    email           TEXT,
    source          TEXT CHECK (source IN ('google_contacts','email_lead','manual','ilist','referral')),
    category        TEXT CHECK (category IN ('buyer','seller','tenant','landlord','contact','unknown')),
    assigned_to     UUID REFERENCES users(id),
    google_contact_id TEXT UNIQUE,                 -- for sync deduplication
    ilist_code      TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_people_phone ON people(phone);
CREATE INDEX IF NOT EXISTS idx_people_email ON people(email);
CREATE INDEX IF NOT EXISTS idx_people_name ON people USING gin(full_name gin_trgm_ops);

-- Buyer requirements (ζήτηση αγοραστή)
CREATE TABLE IF NOT EXISTS buyer_requirements (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    person_id       UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    transaction_type TEXT CHECK (transaction_type IN ('sale','rental')),
    property_type   TEXT,
    areas           TEXT[],                        -- array of preferred areas
    sqm_min         NUMERIC(8,2),
    sqm_max         NUMERIC(8,2),
    budget_min      NUMERIC(12,2),
    budget_max      NUMERIC(12,2),
    bedrooms_min    INTEGER,
    floor_min       INTEGER,
    floor_max       INTEGER,
    must_have       TEXT,                          -- free text: parking, garden, etc.
    active          BOOLEAN DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 8. BOARD (announcements, open houses, monthly calendar)
-- ============================================================

CREATE TABLE IF NOT EXISTS board_open_houses (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id     UUID NOT NULL REFERENCES properties(id),
    agent_id        UUID NOT NULL REFERENCES users(id),
    scheduled_date  DATE NOT NULL,
    time_start      TIME NOT NULL,
    time_end        TIME NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS board_announcements (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    author_id       UUID NOT NULL REFERENCES users(id),
    type            TEXT CHECK (type IN ('listing','wanted','cooperation','other')),
    title           TEXT NOT NULL,
    body            TEXT,
    expires_at      DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monthly_calendar (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_date      DATE NOT NULL,
    time_start      TIME,
    time_end        TIME,
    title           TEXT NOT NULL,
    type            TEXT CHECK (type IN ('sprint_call','meeting','training','conference','other')),
    location        TEXT,
    description     TEXT,
    uploaded_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 9. PROPERTY VALUATIONS (AI-assisted, Python stats first)
-- ============================================================

CREATE TABLE IF NOT EXISTS valuations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    requested_by        UUID NOT NULL REFERENCES users(id),
    property_id         UUID REFERENCES properties(id),

    -- Input (what the agent entered)
    input_address       TEXT NOT NULL,
    input_area          TEXT,
    input_lat           NUMERIC(10,7),
    input_lng           NUMERIC(10,7),
    input_sqm           NUMERIC(8,2),
    input_floor         INTEGER,
    input_year_built    INTEGER,
    input_year_renovated INTEGER,
    input_condition     TEXT,
    input_type          TEXT,
    input_transaction   TEXT,

    -- Python stats output (no AI)
    comparables_count   INTEGER,
    comparables_ids     UUID[],                    -- property IDs used
    stat_price_min      NUMERIC(12,2),
    stat_price_max      NUMERIC(12,2),
    stat_price_median   NUMERIC(12,2),
    stat_price_per_sqm  NUMERIC(8,2),
    confidence          TEXT CHECK (confidence IN ('high','medium','low')),

    -- AI reasoning (Claude, called only after stats)
    ai_reasoning        TEXT,
    ai_called_at        TIMESTAMPTZ,
    ai_tokens_used      INTEGER,

    -- Feedback loop
    actual_price        NUMERIC(12,2),             -- filled after sale/rental
    feedback_at         TIMESTAMPTZ,
    feedback_by         UUID REFERENCES users(id),

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 10. AI USAGE LOG (track every Claude call for cost control)
-- ============================================================

CREATE TABLE IF NOT EXISTS ai_usage_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    called_by       UUID REFERENCES users(id),
    call_type       TEXT NOT NULL,                 -- 'ceo_chat','weekly_insights','valuation','lead_parse'
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    cost_usd        NUMERIC(8,6),
    duration_ms     INTEGER,
    success         BOOLEAN DEFAULT TRUE,
    error_msg       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_log_type ON ai_usage_log(call_type);
CREATE INDEX IF NOT EXISTS idx_ai_log_date ON ai_usage_log(created_at);

-- ============================================================
-- 11. SYSTEM LOGS (for debugging)
-- ============================================================

CREATE TABLE IF NOT EXISTS system_logs (
    id              BIGSERIAL PRIMARY KEY,
    level           TEXT CHECK (level IN ('info','warning','error')),
    service         TEXT NOT NULL,                 -- 'scheduler','import','sync','auth'
    message         TEXT NOT NULL,
    details         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logs_level ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_created ON system_logs(created_at);

-- ============================================================
-- TRIGGERS: auto-update updated_at
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_agents_updated BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_submissions_updated BEFORE UPDATE ON weekly_submissions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_properties_updated BEFORE UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_people_updated BEFORE UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- TRIGGER: auto-compute price_per_sqm on properties
-- ============================================================

CREATE OR REPLACE FUNCTION compute_price_per_sqm()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.price_asking IS NOT NULL AND NEW.sqm IS NOT NULL AND NEW.sqm > 0 THEN
        NEW.price_per_sqm = ROUND(NEW.price_asking / NEW.sqm, 2);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_price_per_sqm BEFORE INSERT OR UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION compute_price_per_sqm();
