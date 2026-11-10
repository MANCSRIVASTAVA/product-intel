"""Integration tests against the real Neon database. Skipped automatically
if DATABASE_URL isn't set, since these need actual network access.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pipeline.load import get_engine, load_day
from simulator.generate import DEFAULT_OUTPUT_DIR

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="requires a live Neon connection"
)


@pytest.fixture(scope="module")
def engine():
    return get_engine()


def _table_counts(engine, tables):
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}


def test_backfill_has_loaded_data(engine):
    counts = _table_counts(engine, ["users", "experiments", "events", "assignments"])
    assert counts["users"] > 0
    assert counts["experiments"] == 2
    assert counts["events"] > 0
    assert counts["assignments"] > 0


def test_events_table_only_covers_the_recent_window(engine):
    with engine.connect() as conn:
        min_ts, max_ts = conn.execute(text("SELECT min(event_ts), max(event_ts) FROM events")).fetchone()
    assert (max_ts - min_ts).days <= 14


def test_reloading_an_already_loaded_day_is_a_no_op(engine):
    before = _table_counts(engine, ["users", "events", "assignments"])
    load_day(engine, "2026-10-20", DEFAULT_OUTPUT_DIR, load_raw_events=True)
    after = _table_counts(engine, ["users", "events", "assignments"])
    assert before == after


def test_srm_bug_assignment_is_skewed(engine):
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT variant, count(*) FROM assignments "
                    "WHERE experiment_id = 'exp_srm_bug' GROUP BY variant"
                )
            ).fetchall()
        )
    total = sum(rows.values())
    assert abs(rows["A"] / total - 0.5) > 0.1


def test_checkout_promo_assignment_is_balanced(engine):
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT variant, count(*) FROM assignments "
                    "WHERE experiment_id = 'exp_checkout_promo' GROUP BY variant"
                )
            ).fetchall()
        )
    total = sum(rows.values())
    assert abs(rows["A"] / total - 0.5) < 0.05


def test_no_rejected_rows_in_run_log():
    log_path = Path("data/run_log.jsonl")
    total_rejected = sum(
        json.loads(line)["rows_rejected"] for line in log_path.read_text().splitlines()
    )
    assert total_rejected == 0


def test_result_tables_exist(engine):
    expected = {
        "daily_metrics", "funnel_daily", "retention_cohorts",
        "experiment_results", "churn_scores", "insights",
    }
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            ).fetchall()
        }
    assert expected <= tables


@pytest.mark.skipif("DATABASE_URL_READONLY" not in os.environ, reason="requires the readonly role")
def test_readonly_role_can_select_but_not_write():
    url = os.environ["DATABASE_URL_READONLY"].replace("postgresql://", "postgresql+psycopg2://", 1)
    readonly_engine = create_engine(url)

    with readonly_engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM users")).scalar()
    assert count > 0

    with pytest.raises(Exception, match="permission denied"), readonly_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (user_id, device, country, channel, signup_date) "
                "VALUES ('test_ro_check', 'x', 'x', 'x', '2026-01-01')"
            )
        )
