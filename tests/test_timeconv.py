"""Tests for BTS local-time -> UTC conversion."""

from datetime import date, datetime, timezone

import polars as pl
import pytest

from robust_crew.timeconv import add_utc_times, hhmm_to_minutes

UTC = timezone.utc


def _flight(**kw) -> dict:
    base = dict(
        FlightDate=date(2023, 11, 15),
        Origin="CLT",
        Dest="BOS",
        CRSDepTime=800,
        CRSArrTime=1000,
        CRSElapsedTime=120.0,
        DepDelay=0.0,
        ArrDelay=0.0,
    )
    base.update(kw)
    return base


def _convert(*flights: dict) -> pl.DataFrame:
    return add_utc_times(pl.DataFrame(list(flights)))


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_hhmm_to_minutes():
    out = pl.select(hhmm_to_minutes(pl.Series([0, 5, 1435, 2400]))).to_series().to_list()
    assert out == [0, 5, 875, 1440]


def test_same_zone_crossing_midnight():
    # CLT 23:30 EST -> BOS 01:30 EST next day.
    r = _convert(_flight(CRSDepTime=2330, CRSArrTime=130)).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 11, 16, 4, 30)
    assert r["sched_arr_utc"] == _utc(2023, 11, 16, 6, 30)
    assert r["arr_clock_ok"] is True


def test_crossing_time_zones_westbound():
    # CLT 20:00 EST -> LAX 22:30 PST, 5.5 h block.
    r = _convert(
        _flight(Dest="LAX", CRSDepTime=2000, CRSArrTime=2230, CRSElapsedTime=330.0)
    ).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 11, 16, 1, 0)
    assert r["sched_arr_utc"] == _utc(2023, 11, 16, 6, 30)
    assert r["arr_clock_ok"] is True


def test_red_eye_crossing_midnight_and_zones():
    # LAX 23:00 PST -> CLT 06:50 EST next day; local clocks look like arr < dep.
    r = _convert(
        _flight(Origin="LAX", Dest="CLT", CRSDepTime=2300, CRSArrTime=650, CRSElapsedTime=290.0)
    ).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 11, 16, 7, 0)
    assert r["sched_arr_utc"] == _utc(2023, 11, 16, 11, 50)
    assert r["arr_clock_ok"] is True


def test_2400_departure_is_end_of_day():
    r = _convert(_flight(CRSDepTime=2400, CRSArrTime=200)).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 11, 16, 5, 0)  # midnight EST
    assert r["arr_clock_ok"] is True


def test_arizona_has_no_dst():
    # PHX stays at UTC-7 all year; July departure 08:00 MST = 15:00 UTC.
    r = _convert(
        _flight(FlightDate=date(2023, 7, 1), Origin="PHX", Dest="CLT",
                CRSDepTime=800, CRSArrTime=1530, CRSElapsedTime=270.0)
    ).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 7, 1, 15, 0)
    assert r["arr_clock_ok"] is True  # 15:00 + 4.5 h = 19:30 UTC = 15:30 EDT


def test_flight_spanning_dst_end():
    # Departs CLT 2023-11-04 23:00 EDT (03:00 UTC), 3 h block, lands after the
    # 02:00 fall-back: 06:00 UTC = 01:00 EST. Elapsed time is real time, so the
    # local clock only advances 2 h.
    r = _convert(
        _flight(FlightDate=date(2023, 11, 4), Dest="DEN", CRSDepTime=2300,
                CRSArrTime=2300, CRSElapsedTime=300.0)
    ).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 11, 5, 3, 0)
    assert r["sched_arr_utc"] == _utc(2023, 11, 5, 8, 0)  # 01:00 MST
    assert r["arr_clock_ok"] is False  # CRSArrTime 2300 deliberately wrong


def test_nonexistent_spring_forward_time_reads_as_wall_clock():
    # 2023-03-12 02:30 does not exist in New York; treat as 03:30 EDT = 07:30 UTC.
    r = _convert(_flight(FlightDate=date(2023, 3, 12), CRSDepTime=230, CRSArrTime=530)).row(0, named=True)
    assert r["sched_dep_utc"] == _utc(2023, 3, 12, 7, 30)


def test_actual_times_from_delays():
    r = _convert(_flight(DepDelay=45.0, ArrDelay=30.0)).row(0, named=True)
    assert r["actual_dep_utc"] == _utc(2023, 11, 15, 13, 45)
    assert r["actual_arr_utc"] == _utc(2023, 11, 15, 15, 30)


def test_early_departure_negative_delay():
    r = _convert(_flight(DepDelay=-5.0, ArrDelay=-12.0)).row(0, named=True)
    assert r["actual_dep_utc"] == _utc(2023, 11, 15, 12, 55)
    assert r["actual_arr_utc"] == _utc(2023, 11, 15, 14, 48)


def test_cancelled_and_diverted_have_null_actuals():
    df = _convert(
        _flight(DepDelay=None, ArrDelay=None),  # cancelled
        _flight(DepDelay=10.0, ArrDelay=None),  # diverted
    )
    assert df["actual_dep_utc"].to_list()[0] is None
    assert df["actual_arr_utc"].to_list() == [None, None]
    assert df["actual_dep_utc"].to_list()[1] == _utc(2023, 11, 15, 13, 10)


def test_unknown_airport_gives_null_not_crash():
    r = _convert(_flight(Origin="ZZZ")).row(0, named=True)
    assert r["sched_dep_utc"] is None


def test_row_order_preserved_across_zones():
    df = _convert(
        _flight(Origin="LAX", Dest="CLT", CRSDepTime=900, CRSArrTime=1650, CRSElapsedTime=290.0),
        _flight(),
        _flight(Origin="DEN", Dest="CLT", CRSDepTime=900, CRSArrTime=1415, CRSElapsedTime=195.0),
    )
    assert df["Origin"].to_list() == ["LAX", "CLT", "DEN"]
    assert df["arr_clock_ok"].to_list() == [True, True, True]
