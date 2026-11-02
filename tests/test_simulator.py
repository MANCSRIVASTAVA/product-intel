"""Tests for the event simulator.

These don't just check "the code runs" - they check that the configured
funnel rates and the three planted ground-truth effects (the checkout_promo
lift, the srm_bug assignment skew, and the outage) actually come out of the
generated data. Every later layer's own validation depends on this being true.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from simulator.generate import generate, load_config


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("events")
    generate(output_dir=out_dir)
    files = sorted(out_dir.glob("date=*/events.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    cfg = load_config()
    return df, cfg, out_dir


def test_date_range_has_no_gaps_or_dupes(generated):
    _, cfg, out_dir = generated
    dates = sorted(p.name.split("=")[1] for p in out_dir.glob("date=*"))
    assert len(dates) == cfg["simulation"]["days"]
    assert len(set(dates)) == len(dates)


def test_event_schema_is_valid(generated):
    df, cfg, _ = generated
    allowed = set(cfg["funnel"]["steps"])
    assert set(df["event_name"].unique()) <= allowed
    assert df["event_id"].is_unique
    assert df[["event_id", "user_id", "event_name", "timestamp"]].notna().all().all()
    df["properties"].apply(json.loads)  # every row must be valid JSON


def test_baseline_funnel_conversion_matches_config(generated):
    # pair by session_id, not user_id - repeat sessions would inflate this
    # into a cumulative "ever converted" rate instead of the per-session one
    df, cfg, _ = generated
    base = cfg["funnel"]["base_conversion"]
    plain = df[df["experiment_id"].isna()]
    sessions_at = {
        step: set(plain.loc[plain.event_name == step, "session_id"])
        for step in cfg["funnel"]["steps"]
    }
    rate = len(sessions_at["product_view"] & sessions_at["add_to_cart"]) / len(sessions_at["product_view"])
    assert abs(rate - base["product_view_to_add_to_cart"]) < 0.03


def test_planted_checkout_promo_lift(generated):
    df, cfg, _ = generated
    exp = next(e for e in cfg["experiments"] if e["experiment_id"] == "exp_checkout_promo")
    exp_events = df[df["experiment_id"] == "exp_checkout_promo"]
    checkout = exp_events[exp_events.event_name == "checkout_started"]
    purchase = exp_events[exp_events.event_name == "purchase_completed"]

    rates = {}
    for variant in ["A", "B"]:
        checkout_users = set(checkout.loc[checkout.variant == variant, "user_id"])
        purchase_users = set(purchase.loc[purchase.variant == variant, "user_id"])
        rates[variant] = len(checkout_users & purchase_users) / len(checkout_users)

    lift = rates["B"] - rates["A"]
    planted = exp["planted_effect"]["variant_B_lift_pp"] / 100.0
    assert abs(lift - planted) < 0.02


def test_planted_srm_bug_skews_assignment(generated):
    df, _, _ = generated
    signup = df[(df.event_name == "signup") & (df.experiment_id == "exp_srm_bug")]
    counts = signup["variant"].value_counts(normalize=True)
    assert abs(counts.get("A", 0.0) - 0.5) > 0.08


def test_planted_outage_halves_visit_volume(generated):
    # outage only cuts new-user volume, not returning traffic - isolate
    # first-ever visits per user or the dip gets diluted by returning users
    df, cfg, _ = generated
    outage_date = cfg["planted_outage"]["date"]
    visits = df[df.event_name == "visit"].copy()
    visits["date"] = visits["timestamp"].str[:10]
    first_seen = visits.groupby("user_id")["date"].min()
    visits["is_new"] = visits["user_id"].map(first_seen) == visits["date"]
    new_visits = visits[visits["is_new"]]
    daily_new_counts = new_visits.groupby("date").size()
    outage_count = daily_new_counts[outage_date]
    baseline = daily_new_counts.drop(outage_date).mean()
    assert outage_count < baseline * 0.7


def test_same_seed_is_deterministic(tmp_path):
    out1, out2 = tmp_path / "run1", tmp_path / "run2"
    generate(output_dir=out1)
    generate(output_dir=out2)
    df1 = pd.read_parquet(next(out1.glob("date=*/events.parquet")))
    df2 = pd.read_parquet(next(out2.glob("date=*/events.parquet")))
    pd.testing.assert_frame_equal(df1, df2)
