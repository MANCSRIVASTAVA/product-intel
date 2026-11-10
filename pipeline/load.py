"""Loads validated events into Postgres. Reads DATABASE_URL from the
environment only - never accept a connection string any other way.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from pipeline.validate import validate_and_standardize

load_dotenv()

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
REJECTS_DIR = Path("data/rejects")
RUN_LOG_PATH = Path("data/run_log.jsonl")
ALLOWED_EVENT_NAMES = {
    "visit", "signup", "product_view", "add_to_cart",
    "checkout_started", "purchase_completed",
}

# Neon free tier caps at 1GB - full raw history stays in local Parquet,
# Postgres only needs a recent slice for the live dashboard
RECENT_EVENTS_WINDOW_DAYS = 14


def get_engine() -> Engine:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _has_sql(fragment: str) -> bool:
    return any(line.strip() and not line.strip().startswith("--") for line in fragment.splitlines())


def apply_schema(engine: Engine) -> None:
    sql = SCHEMA_PATH.read_text()
    with engine.begin() as conn:
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement and _has_sql(statement):
                conn.execute(text(statement))


def load_experiments_from_config(engine: Engine, experiments_cfg: list[dict]) -> None:
    with engine.begin() as conn:
        for exp in experiments_cfg:
            conn.execute(
                text(
                    """
                    INSERT INTO experiments (experiment_id, start_date, end_date)
                    VALUES (:experiment_id, :start_date, :end_date)
                    ON CONFLICT (experiment_id) DO NOTHING
                    """
                ),
                {
                    "experiment_id": exp["experiment_id"],
                    "start_date": exp["start_date"],
                    "end_date": exp["end_date"],
                },
            )


def _derive_users(valid_rows: list[dict]) -> list[dict]:
    users: dict[str, dict] = {}
    for row in valid_rows:
        if row["event_name"] != "signup":
            continue
        props = json.loads(row["properties"])
        users[row["user_id"]] = {
            "user_id": row["user_id"],
            "device": props["device"],
            "country": props["country"],
            "channel": props["channel"],
            "signup_date": row["timestamp"][:10],
        }
    return list(users.values())


def _derive_assignments(valid_rows: list[dict]) -> list[dict]:
    assignments: dict[tuple[str, str], dict] = {}
    for row in valid_rows:
        if not row.get("experiment_id"):
            continue
        key = (row["user_id"], row["experiment_id"])
        existing = assignments.get(key)
        if existing is None or row["timestamp"] < existing["assigned_at"]:
            assignments[key] = {
                "user_id": row["user_id"],
                "experiment_id": row["experiment_id"],
                "variant": row["variant"],
                "assigned_at": row["timestamp"],
            }
    return list(assignments.values())


def _bulk_upsert(raw_conn, query: str, rows: list[tuple]) -> None:
    """One round-trip per call instead of one per row."""
    if not rows:
        return
    with raw_conn.cursor() as cur:
        execute_values(cur, query, rows)


def load_day(engine: Engine, day_str: str, events_dir: Path, load_raw_events: bool = True) -> dict:
    """load_raw_events controls only the events table write - users and
    assignments always load in full, since those stay small regardless of
    event volume (one row per user, not per event)."""
    parquet_path = events_dir / f"date={day_str}" / "events.parquet"
    df = pd.read_parquet(parquet_path)
    rows = df.to_dict("records")

    valid, rejected = validate_and_standardize(rows, ALLOWED_EVENT_NAMES)
    users = _derive_users(valid)
    assignments = _derive_assignments(valid)

    raw_conn = engine.raw_connection()
    try:
        _bulk_upsert(
            raw_conn,
            "INSERT INTO users (user_id, device, country, channel, signup_date) "
            "VALUES %s ON CONFLICT (user_id) DO NOTHING",
            [(u["user_id"], u["device"], u["country"], u["channel"], u["signup_date"]) for u in users],
        )
        _bulk_upsert(
            raw_conn,
            "INSERT INTO assignments (user_id, experiment_id, variant, assigned_at) "
            "VALUES %s ON CONFLICT (user_id, experiment_id) DO NOTHING",
            [(a["user_id"], a["experiment_id"], a["variant"], a["assigned_at"]) for a in assignments],
        )
        if load_raw_events:
            _bulk_upsert(
                raw_conn,
                "INSERT INTO events "
                "(event_id, user_id, session_id, event_name, event_ts, properties, experiment_id, variant) "
                "VALUES %s ON CONFLICT (event_id) DO NOTHING",
                [
                    (e["event_id"], e["user_id"], e["session_id"], e["event_name"],
                     e["timestamp"], e["properties"], e["experiment_id"], e["variant"])
                    for e in valid
                ],
            )
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()

    _write_rejects(day_str, rejected)
    stats = {
        "date": day_str,
        "rows_total": len(rows),
        "rows_valid": len(valid),
        "rows_rejected": len(rejected),
        "events_loaded_to_postgres": len(valid) if load_raw_events else 0,
    }
    _append_run_log(stats)
    return stats


def _write_rejects(day_str: str, rejected: list[dict]) -> None:
    if not rejected:
        return
    day_dir = REJECTS_DIR / f"date={day_str}"
    day_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rejected).to_parquet(day_dir / "rejects.parquet", index=False)


def _append_run_log(stats: dict) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RUN_LOG_PATH, "a") as f:
        f.write(json.dumps(stats) + "\n")
