"""Unit tests for validation and row-shaping - no database needed."""
from __future__ import annotations

import json

from pipeline.load import _derive_assignments, _derive_users
from pipeline.validate import validate_and_standardize, validate_event

ALLOWED = {"visit", "signup", "product_view", "add_to_cart", "checkout_started", "purchase_completed"}


def _event(**overrides):
    base = {
        "event_id": "e_1",
        "user_id": "u_1",
        "session_id": "s_1",
        "event_name": "visit",
        "timestamp": "2026-08-01T12:00:00+00:00",
        "properties": json.dumps({"device": "mobile", "country": "US", "channel": "organic"}),
        "experiment_id": None,
        "variant": None,
    }
    base.update(overrides)
    return base


def test_valid_event_passes():
    assert validate_event(_event(), ALLOWED) is None


def test_missing_required_field_is_rejected():
    row = _event()
    del row["user_id"]
    assert validate_event(row, ALLOWED) == "missing_user_id"


def test_unknown_event_name_is_rejected():
    assert validate_event(_event(event_name="sign_up"), ALLOWED) == "unknown_event_name"


def test_invalid_json_properties_is_rejected():
    assert validate_event(_event(properties="{not json"), ALLOWED) == "invalid_properties_json"


def test_bad_variant_is_rejected():
    row = _event(experiment_id="exp_1", variant="C")
    assert validate_event(row, ALLOWED) == "invalid_variant"


def test_future_timestamp_is_rejected():
    assert validate_event(_event(timestamp="2099-01-01T00:00:00+00:00"), ALLOWED) == "timestamp_in_future"


def test_naive_timestamp_is_rejected():
    assert validate_event(_event(timestamp="2026-08-01T12:00:00"), ALLOWED) == "timestamp_not_timezone_aware"


def test_batch_dedupes_by_event_id():
    rows = [_event(), _event()]
    valid, rejected = validate_and_standardize(rows, ALLOWED)
    assert len(valid) == 1
    assert len(rejected) == 0


def test_batch_separates_valid_and_rejected():
    rows = [_event(), _event(event_id="e_2", event_name="bogus")]
    valid, rejected = validate_and_standardize(rows, ALLOWED)
    assert len(valid) == 1
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "unknown_event_name"


def test_derive_users_from_signup_events():
    rows = [_event(event_name="signup")]
    valid, _ = validate_and_standardize(rows, ALLOWED)
    assert _derive_users(valid) == [{
        "user_id": "u_1", "device": "mobile", "country": "US",
        "channel": "organic", "signup_date": "2026-08-01",
    }]


def test_derive_assignments_keeps_earliest_per_user_experiment():
    rows = [
        _event(event_id="e_1", event_name="signup", experiment_id="exp_1", variant="A",
               timestamp="2026-08-02T00:00:00+00:00"),
        _event(event_id="e_2", event_name="checkout_started", experiment_id="exp_1", variant="A",
               timestamp="2026-08-01T00:00:00+00:00"),
    ]
    valid, _ = validate_and_standardize(rows, ALLOWED)
    assignments = _derive_assignments(valid)
    assert len(assignments) == 1
    assert assignments[0]["assigned_at"] == "2026-08-01T00:00:00+00:00"
