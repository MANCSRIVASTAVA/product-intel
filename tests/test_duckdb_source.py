"""No database needed - this just checks DuckDB can see the full local
Parquet history, independent of whatever window is loaded into Postgres."""
from __future__ import annotations

import glob

import pandas as pd

from pipeline.duckdb_source import connect


def test_total_count_matches_independent_parquet_sum():
    con = connect()
    duckdb_count = con.execute("SELECT count(*) FROM events").fetchone()[0]

    files = glob.glob("data/raw/events/date=*/events.parquet")
    pandas_count = sum(len(pd.read_parquet(f)) for f in files)

    assert duckdb_count == pandas_count
    assert duckdb_count > 0


def test_date_range_spans_full_history():
    con = connect()
    min_date, max_date = con.execute("SELECT min(date), max(date) FROM events").fetchone()
    assert (max_date - min_date).days == 89
