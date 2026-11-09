"""Guards against test-period information reaching training or planning.

Every table derived from historical data for model training or historical
route statistics must be registered in ``TRAINING_TABLES`` with the column
holding its (local) flight date. ``tests/test_leakage.py`` checks each one
against the configured split boundaries.
"""

from __future__ import annotations

from datetime import date

import duckdb

from robust_crew import config

# table name -> date column. Filled in as later phases create training tables.
TRAINING_TABLES: dict[str, str] = {
    "route_month_stats": "max_date",  # historical route delay stats (delay_model.py)
}


def max_date(con: duckdb.DuckDBPyConnection, table: str, date_col: str) -> date | None:
    """Latest date in ``table.date_col`` (None if the table is empty)."""
    return con.execute(f'SELECT max(CAST("{date_col}" AS DATE)) FROM "{table}"').fetchall()[0][0]


def assert_no_dates_after(
    con: duckdb.DuckDBPyConnection, table: str, date_col: str, cutoff: date = config.TRAIN_END
) -> None:
    """Raise AssertionError if any row of ``table`` is dated after ``cutoff``."""
    latest = max_date(con, table, date_col)
    if latest is not None and latest > cutoff:
        raise AssertionError(f"leakage: {table}.{date_col} has dates up to {latest} (> {cutoff})")
