"""Generate simulated e-commerce events with known, planted effects.

Writes one Parquet file of events per day to data/raw/events/date=YYYY-MM-DD/.
Every probabilistic draw comes from a single seeded numpy.random.Generator,
so a given config produces byte-identical output every run - later layers
validate themselves against this data, so the ground truth has to be stable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"
DEFAULT_OUTPUT_DIR = Path("data/raw/events")

EVENT_COLUMNS = [
    "event_id", "user_id", "session_id", "event_name", "timestamp",
    "properties", "experiment_id", "variant",
]


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _hash_bucket(user_id: str, experiment_id: str) -> int:
    """Deterministic 0-99 bucket from user_id + experiment_id."""
    digest = hashlib.md5(f"{user_id}_{experiment_id}".encode()).hexdigest()
    return int(digest, 16) % 100


def assign_variant(user_id: str, exp_cfg: dict) -> str:
    """True 50/50 split, except exp_srm_bug which deliberately skews ~65/35
    to plant an assignment bug for Layer 5's SRM check to catch."""
    bucket = _hash_bucket(user_id, exp_cfg["experiment_id"])
    threshold = 65 if exp_cfg.get("assignment_bug") else 50
    return "A" if bucket < threshold else "B"


def _active_experiments(experiments: list[dict], day: date) -> list[dict]:
    out = []
    for exp in experiments:
        start = datetime.strptime(exp["start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(exp["end_date"], "%Y-%m-%d").date()
        if start <= day <= end:
            out.append(exp)
    return out


def _sample_segment(rng: np.random.Generator, proportions: dict) -> str:
    keys = list(proportions.keys())
    probs = np.array(list(proportions.values()), dtype=float)
    probs = probs / probs.sum()
    return keys[rng.choice(len(keys), p=probs)]


def _draw_order_value(rng: np.random.Generator, revenue_cfg: dict) -> float:
    """Lognormal, not normal - a purchase can't have a negative value, and a
    plain normal draw with these parameters could occasionally go negative."""
    mean = revenue_cfg["order_value_mean"]
    std = revenue_cfg["order_value_std"]
    sigma2 = np.log(1 + (std / mean) ** 2)
    sigma = np.sqrt(sigma2)
    mu = np.log(mean) - sigma2 / 2
    return float(round(rng.lognormal(mean=mu, sigma=sigma), 2))


@dataclass
class UserState:
    user_id: str
    device: str
    country: str
    channel: str
    signup_date: date
    last_active_date: date
    key_actions_count: int = 0
    experiment_variants: dict = field(default_factory=dict)


class _EventWriter:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.counter = 0

    def make_event(self, user_id, event_name, day, properties, experiment_id=None, variant=None):
        self.counter += 1
        minute_offset = int(self.rng.integers(0, 24 * 60))
        ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=minute_offset)
        return {
            "event_id": f"e_{self.counter:010d}",
            "user_id": user_id,
            "session_id": f"s_{user_id}_{day.isoformat()}",
            "event_name": event_name,
            "timestamp": ts.isoformat(),
            "properties": json.dumps(properties),
            "experiment_id": experiment_id,
            "variant": variant,
        }


def _resolve_checkout_promo(state: UserState, exps_today: list[dict]):
    for exp in exps_today:
        if exp["experiment_id"] == "exp_checkout_promo":
            variant = state.experiment_variants.get(exp["experiment_id"])
            if variant is None:
                variant = assign_variant(state.user_id, exp)
                state.experiment_variants[exp["experiment_id"]] = variant
            return exp["experiment_id"], variant, exp
    return None, None, None


def _walk_funnel_from_product_view(state, day, props, exps_today, base_conv, revenue_cfg, writer, day_events, rng):
    if rng.random() >= base_conv["signup_to_product_view"]:
        return
    day_events.append(writer.make_event(state.user_id, "product_view", day, props))
    state.key_actions_count += 1

    if rng.random() >= base_conv["product_view_to_add_to_cart"]:
        return
    day_events.append(writer.make_event(state.user_id, "add_to_cart", day, props))
    state.key_actions_count += 1

    if rng.random() >= base_conv["add_to_cart_to_checkout_started"]:
        return

    checkout_exp_id, checkout_variant, checkout_exp_cfg = _resolve_checkout_promo(state, exps_today)
    day_events.append(writer.make_event(
        state.user_id, "checkout_started", day, props,
        experiment_id=checkout_exp_id, variant=checkout_variant,
    ))

    purchase_prob = base_conv["checkout_started_to_purchase_completed"]
    if checkout_variant == "B" and checkout_exp_cfg and checkout_exp_cfg.get("planted_effect"):
        purchase_prob += checkout_exp_cfg["planted_effect"]["variant_B_lift_pp"] / 100.0

    if rng.random() >= purchase_prob:
        return

    order_value = _draw_order_value(rng, revenue_cfg)
    day_events.append(writer.make_event(
        state.user_id, "purchase_completed", day, {**props, "order_value": order_value},
        experiment_id=checkout_exp_id, variant=checkout_variant,
    ))
    state.key_actions_count += 2


def _write_day(day: date, day_events: list[dict], output_dir: Path) -> None:
    day_dir = output_dir / f"date={day.isoformat()}"
    day_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(day_events, columns=EVENT_COLUMNS)
    df.to_parquet(day_dir / "events.parquet", index=False)


def generate(config_path: Path = CONFIG_PATH, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    cfg = load_config(config_path)
    sim = cfg["simulation"]
    rng = np.random.default_rng(sim["random_seed"])

    start_date = datetime.strptime(sim["start_date"], "%Y-%m-%d").date()
    days = sim["days"]
    base_daily = sim["target_total_users"] / days
    weekend_mult = sim.get("weekend_multiplier", 0.6)
    noise_std_pct = sim.get("daily_noise_std_pct", 0.1)

    outage_cfg = cfg.get("planted_outage")
    outage_date = (
        datetime.strptime(outage_cfg["date"], "%Y-%m-%d").date() if outage_cfg else None
    )

    funnel_cfg = cfg["funnel"]
    base_conv = funnel_cfg["base_conversion"]
    retention_cfg = cfg["retention"]
    revenue_cfg = cfg["revenue"]
    segments_cfg = cfg["segments"]
    experiments_cfg = cfg.get("experiments", [])

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    writer = _EventWriter(rng)
    users: dict[str, UserState] = {}
    user_counter = 0

    for day_offset in range(days):
        day = start_date + timedelta(days=day_offset)
        day_events: list[dict] = []
        exps_today = _active_experiments(experiments_cfg, day)

        # --- new users ---
        is_weekend = day.weekday() >= 5
        mult = weekend_mult if is_weekend else 1.0
        noise = rng.normal(1.0, noise_std_pct)
        n_new = max(0, round(base_daily * mult * noise))

        if outage_date is not None and day == outage_date and outage_cfg["affected"] == "visit_volume":
            n_new = round(n_new * outage_cfg["multiplier"])

        for _ in range(n_new):
            user_counter += 1
            user_id = f"u_{user_counter:08d}"
            device = _sample_segment(rng, segments_cfg["device"])
            country = _sample_segment(rng, segments_cfg["country"])
            channel = _sample_segment(rng, segments_cfg["channel"])
            props = {"device": device, "country": country, "channel": channel}

            # visit always happens - this is what it means to be a "new user" today
            day_events.append(writer.make_event(user_id, "visit", day, props))

            if rng.random() >= base_conv["visit_to_signup"]:
                continue  # bounced at visit, never becomes a tracked user

            signup_exp_id, signup_variant = None, None
            for exp in exps_today:
                if exp["experiment_id"] == "exp_srm_bug":
                    signup_exp_id = exp["experiment_id"]
                    signup_variant = assign_variant(user_id, exp)

            day_events.append(writer.make_event(
                user_id, "signup", day, props,
                experiment_id=signup_exp_id, variant=signup_variant,
            ))

            state = UserState(user_id, device, country, channel, day, day)
            if signup_exp_id:
                state.experiment_variants[signup_exp_id] = signup_variant
            users[user_id] = state

            _walk_funnel_from_product_view(
                state, day, props, exps_today, base_conv, revenue_cfg, writer, day_events, rng,
            )

        # --- returning users ---
        for user_id, state in users.items():
            if state.signup_date == day:
                continue  # already handled above as a new user today

            days_since_active = (day - state.last_active_date).days
            engagement = min(state.key_actions_count / 5.0, 1.0)
            p_active = retention_cfg["base_return_prob_day1"] * (
                (1 - retention_cfg["daily_decay_rate"]) ** (days_since_active - 1)
            )
            p_active *= (1 + retention_cfg["engagement_boost"] * engagement)
            p_active = min(max(p_active, 0.0), 1.0)

            if rng.random() < p_active:
                state.last_active_date = day
                props = {"device": state.device, "country": state.country, "channel": state.channel}
                day_events.append(writer.make_event(user_id, "visit", day, props))
                _walk_funnel_from_product_view(
                    state, day, props, exps_today, base_conv, revenue_cfg, writer, day_events, rng,
                )

        _write_day(day, day_events, output_dir)

    return output_dir


if __name__ == "__main__":
    out = generate()
    print(f"Wrote simulated events to {out}")
