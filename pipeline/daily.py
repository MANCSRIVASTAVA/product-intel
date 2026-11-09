"""CLI entry point for the pipeline.

    python -m pipeline.daily --backfill        # every day in the configured window
    python -m pipeline.daily --date 2026-08-01  # a single day, for debugging
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from pipeline.load import (
    RECENT_EVENTS_WINDOW_DAYS,
    apply_schema,
    get_engine,
    load_day,
    load_experiments_from_config,
)
from simulator.generate import CONFIG_PATH, DEFAULT_OUTPUT_DIR, load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="single day to load, YYYY-MM-DD")
    parser.add_argument("--backfill", action="store_true", help="load every day in the simulator's window")
    args = parser.parse_args()

    if not args.date and not args.backfill:
        parser.error("pass --backfill or --date YYYY-MM-DD")

    cfg = load_config(CONFIG_PATH)
    sim = cfg["simulation"]

    engine = get_engine()
    apply_schema(engine)
    load_experiments_from_config(engine, cfg.get("experiments", []))

    if args.backfill:
        start = datetime.strptime(sim["start_date"], "%Y-%m-%d").date()
        days = sim["days"]
        recent_cutoff = start + timedelta(days=days - RECENT_EVENTS_WINDOW_DAYS)
        for offset in range(days):
            day = start + timedelta(days=offset)
            load_raw_events = day >= recent_cutoff
            print(load_day(engine, day.isoformat(), DEFAULT_OUTPUT_DIR, load_raw_events=load_raw_events))
    else:
        print(load_day(engine, args.date, DEFAULT_OUTPUT_DIR))


if __name__ == "__main__":
    main()
