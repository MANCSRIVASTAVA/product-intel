-- Raw zone

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

-- Results zone, owned by Layers 4-7, not by this pipeline

CREATE TABLE IF NOT EXISTS daily_metrics (
    metric_date DATE NOT NULL,
    metric_name TEXT NOT NULL,
    segment_dimension TEXT NOT NULL DEFAULT 'all',
    segment_value TEXT NOT NULL DEFAULT 'all',
    value NUMERIC NOT NULL,
    PRIMARY KEY (metric_date, metric_name, segment_dimension, segment_value)
);

CREATE TABLE IF NOT EXISTS funnel_daily (
    metric_date DATE NOT NULL,
    funnel_step TEXT NOT NULL,
    segment_dimension TEXT NOT NULL DEFAULT 'all',
    segment_value TEXT NOT NULL DEFAULT 'all',
    users_reached INTEGER NOT NULL,
    PRIMARY KEY (metric_date, funnel_step, segment_dimension, segment_value)
);

CREATE TABLE IF NOT EXISTS retention_cohorts (
    cohort_week DATE NOT NULL,
    days_since_signup INTEGER NOT NULL,
    cohort_size INTEGER NOT NULL,
    active_users INTEGER NOT NULL,
    retention_rate NUMERIC NOT NULL,
    PRIMARY KEY (cohort_week, days_since_signup)
);

CREATE TABLE IF NOT EXISTS experiment_results (
    metric_date DATE NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    metric_name TEXT NOT NULL,
    variant_a_rate NUMERIC,
    variant_b_rate NUMERIC,
    uplift_absolute NUMERIC,
    uplift_relative NUMERIC,
    p_value NUMERIC,
    ci_lower NUMERIC,
    ci_upper NUMERIC,
    srm_p_value NUMERIC,
    sample_size_a INTEGER,
    sample_size_b INTEGER,
    verdict TEXT,
    PRIMARY KEY (metric_date, experiment_id, metric_name)
);

CREATE TABLE IF NOT EXISTS churn_scores (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    score_date DATE NOT NULL,
    churn_probability NUMERIC NOT NULL,
    PRIMARY KEY (user_id, score_date)
);

CREATE TABLE IF NOT EXISTS insights (
    insight_id TEXT PRIMARY KEY,
    insight_date DATE NOT NULL,
    insight_type TEXT NOT NULL,
    severity TEXT,
    metric_name TEXT,
    headline TEXT,
    summary TEXT,
    recommendation TEXT,
    evidence JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_insights_date ON insights(insight_date);
