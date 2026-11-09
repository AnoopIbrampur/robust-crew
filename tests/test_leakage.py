"""No test-window (or calibration-window) data may reach training tables."""

from datetime import date, timedelta

import duckdb
import pytest

from robust_crew import config
from robust_crew.leakage import TRAINING_TABLES, assert_no_dates_after


def test_splits_are_ordered_and_disjoint():
    assert config.TRAIN_START <= config.TRAIN_END < config.CALIB_START <= config.CALIB_END
    assert config.CALIB_END < config.TEST_START <= config.TEST_END
    windows = sorted(config.TEST_WINDOWS.values())
    for (a1, b1), (a2, _) in zip(windows, windows[1:]):
        assert a1 <= b1 < a2, "test windows overlap"


def test_split_of_boundaries():
    assert config.split_of(config.TRAIN_END) == "train"
    assert config.split_of(config.CALIB_START) == "calib"
    assert config.split_of(config.TEST_START - timedelta(days=1)) == "calib"
    assert config.split_of(date(2023, 7, 1)) == "test_summer"
    assert config.split_of(date(2023, 9, 15)) == "gap"
    assert config.split_of(date(2023, 12, 31)) == "test_winter"
    assert config.is_test("test_summer") and not config.is_test("gap")
    assert config.split_of(date(2021, 12, 31)) == "none"


def test_guard_catches_a_leak():
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT ?::DATE AS d", [config.TRAIN_END])
    assert_no_dates_after(con, "t", "d")  # last train day is fine
    con.execute("INSERT INTO t VALUES (DATE '2023-11-15')")
    with pytest.raises(AssertionError, match="leakage"):
        assert_no_dates_after(con, "t", "d")


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="no local database built yet")
@pytest.mark.parametrize("table,date_col", sorted(TRAINING_TABLES.items()) or [pytest.param(None, None, marks=pytest.mark.skip(reason="no training tables registered yet"))])
def test_registered_training_tables_have_no_future_dates(table, date_col):
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    try:
        assert_no_dates_after(con, table, date_col)
    finally:
        con.close()
