-- Raw zone only. Result tables (daily_metrics, funnel_daily, etc.) belong to
-- Layer 3 and get their own migration once that layer is built.

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    device TEXT NOT NULL,
    country TEXT NOT NULL,
    channel TEXT NOT NULL,
    signup_date DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    hypothesis TEXT,
    primary_metric TEXT,
    mde_pp NUMERIC
);

-- no FK to users: a bounced visitor gets a visit event but never signs up
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    properties JSONB,
    experiment_id TEXT,
    variant TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(event_ts);
CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_event_name ON events(event_name);

-- A variant is only ever recorded on/after signup, so both FKs are safe.
CREATE TABLE IF NOT EXISTS assignments (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    variant TEXT NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, experiment_id)
);
