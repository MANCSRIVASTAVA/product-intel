"""Validation and standardization for raw simulator events before they load
into Postgres. Nothing here touches a database - every check is a pure
function over a single row or a batch, so it's fully unit-testable.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

REQUIRED_FIELDS = ["event_id", "user_id", "event_name", "timestamp"]
MAX_AGE = timedelta(days=5 * 365)


def is_missing(value) -> bool:
    """True for None or NaN - pandas gives NaN for optional columns once a
    batch has mixed values, and NaN is truthy unlike None."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def validate_event(row: dict, allowed_event_names: set[str]) -> str | None:
    """Return a rejection reason, or None if the row is valid."""
    for required in REQUIRED_FIELDS:
        if row.get(required) in (None, ""):
            return f"missing_{required}"

    try:
        ts = datetime.fromisoformat(str(row["timestamp"]))
    except ValueError:
        return "bad_timestamp_format"

    if ts.tzinfo is None:
        return "timestamp_not_timezone_aware"

    now = datetime.now(UTC)
    if ts > now:
        return "timestamp_in_future"
    if ts < now - MAX_AGE:
        return "timestamp_too_old"

    if row["event_name"] not in allowed_event_names:
        return "unknown_event_name"

    try:
        json.loads(row["properties"]) if row.get("properties") else {}
    except (TypeError, ValueError):
        return "invalid_properties_json"

    if not is_missing(row.get("experiment_id")) and row.get("variant") not in ("A", "B"):
        return "invalid_variant"

    return None


def standardize_event(row: dict) -> dict:
    row = dict(row)
    ts = datetime.fromisoformat(str(row["timestamp"])).astimezone(UTC)
    row["timestamp"] = ts.isoformat()
    row["event_name"] = row["event_name"].strip().lower()
    # normalize NaN to None so downstream code can just check for None
    row["experiment_id"] = None if is_missing(row.get("experiment_id")) else row["experiment_id"]
    row["variant"] = None if is_missing(row.get("variant")) else row["variant"]
    return row


def validate_and_standardize(
    rows: list[dict], allowed_event_names: set[str]
) -> tuple[list[dict], list[dict]]:
    """Split a batch into (valid, rejected). Rejected rows carry a 'reason'
    field. Valid rows are deduped by event_id within the batch - the DB's
    own ON CONFLICT is the real idempotency guarantee across re-runs, this
    is just defense in depth within a single load."""
    valid: list[dict] = []
    rejected: list[dict] = []
    seen_ids: set[str] = set()

    for row in rows:
        reason = validate_event(row, allowed_event_names)
        if reason:
            rejected.append({**row, "reason": reason})
            continue
        if row["event_id"] in seen_ids:
            continue
        seen_ids.add(row["event_id"])
        valid.append(standardize_event(row))

    return valid, rejected
