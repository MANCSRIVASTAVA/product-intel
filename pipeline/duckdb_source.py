"""DuckDB view over the full local raw-event history. Postgres only keeps a
recent window (see RECENT_EVENTS_WINDOW_DAYS in load.py); heavy historical
analysis reads this instead.
"""
from __future__ import annotations

import duckdb

PARQUET_GLOB = "data/raw/events/date=*/events.parquet"


def connect(parquet_glob: str = PARQUET_GLOB) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW events AS SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=1)"
    )
    return con
