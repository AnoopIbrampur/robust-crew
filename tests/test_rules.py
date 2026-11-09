"""Crew legality rules and connection graph."""

from datetime import datetime, timedelta, timezone

import duckdb

from robust_crew.models import Leg
from robust_crew.network import build_connection_graph, build_rotations_table
from robust_crew.rules import (
    CrewRules,
    can_still_close,
    is_legal,
    pairing_violations,
)

R = CrewRules()
UTC = timezone.utc


def t(h: int, m: int = 0, day: int = 15) -> datetime:
    """UTC time on 2023-11-15 (EST = UTC-5, so 13:00 UTC is 08:00 local)."""
    return datetime(2023, 11, day, h, m, tzinfo=UTC)


def leg(fid, o, d, dep, arr, tail="N1"):
    return Leg(fid, o, d, dep, arr, tail)


def out_and_back(dep=t(13), block=90, sit=40, tail_back="N1"):
    a = leg("A", "CLT", "BOS", dep, dep + timedelta(minutes=block))
    b_dep = a.arr + timedelta(minutes=sit)
    b = leg("B", "BOS", "CLT", b_dep, b_dep + timedelta(minutes=block), tail_back)
    return [a, b]


# --- Part 117 table lookups ------------------------------------------------------

def test_table_b_lookups():
    # Report 07:30 local (12:30 UTC) with 2 segments -> 14 h; 5 segments -> 12.5 h.
    assert R.max_fdp_min(t(12, 30), 2) == 14 * 60
    assert R.max_fdp_min(t(12, 30), 5) == 12.5 * 60
    # Report 17:00 local (22:00 UTC), 7+ segments -> 9 h; 8 segments uses the 7+ column.
    assert R.max_fdp_min(t(22), 8) == 9 * 60
    # Report 04:15 local (09:15 UTC), 1 segment -> 10 h.
    assert R.max_fdp_min(t(9, 15), 1) == 10 * 60


def test_table_a_lookups():
    assert R.max_flight_time_min(t(13)) == 9 * 60  # 08:00 local
    assert R.max_flight_time_min(t(1, day=16)) == 8 * 60  # 20:00 local


def test_lookups_use_base_local_time_across_dst():
    # 2023-07-15 12:30 UTC is 08:30 EDT (row 0700-1159), not 07:30.
    summer = datetime(2023, 7, 15, 11, 30, tzinfo=UTC)  # 07:30 EDT
    assert R.max_fdp_min(summer, 1) == 14 * 60
    assert R.max_fdp_min(summer - timedelta(hours=1), 1) == 13 * 60  # 06:30 EDT


# --- Pairing legality ------------------------------------------------------------

def test_simple_out_and_back_is_legal():
    assert is_legal(out_and_back())


def test_rejects_too_short_connection_same_tail():
    v = pairing_violations(out_and_back(sit=25))
    assert any("< 30 min minimum" in s for s in v)


def test_aircraft_change_needs_45_minutes():
    assert is_legal(out_and_back(sit=40, tail_back="N1"))
    v = pairing_violations(out_and_back(sit=40, tail_back="N2"))
    assert any("< 45 min minimum" in s for s in v)


def test_unknown_tail_counts_as_aircraft_change():
    p = out_and_back(sit=40)
    p = [p[0], Leg("B", "BOS", "CLT", p[1].dep, p[1].arr, None)]
    assert not is_legal(p)


def test_rejects_pairing_not_ending_at_base():
    v = pairing_violations(out_and_back()[:1])
    assert any("not base CLT" in s for s in v)


def test_rejects_pairing_not_starting_at_base():
    p = out_and_back()
    v = pairing_violations([p[1]])
    assert any("starts at BOS" in s for s in v)


def test_rejects_broken_chain():
    a, b = out_and_back()
    b = Leg("B", "DCA", "CLT", b.dep, b.arr, b.tail)
    assert any("next departs DCA" in s for s in pairing_violations([a, b]))


def test_rejects_overlong_duty():
    # Report 16:00 local (21:00 UTC): 4 segments -> 12 h FDP limit.
    # Four 2.5 h legs with 1.5 h sits: FDP = 1 + 10 + 4.5 = 15.5 h. Block 10 h also > 8/9 h.
    legs, dep = [], t(22)
    for k, (o, d) in enumerate([("CLT", "BOS"), ("BOS", "CLT"), ("CLT", "MIA"), ("MIA", "CLT")]):
        legs.append(leg(f"L{k}", o, d, dep, dep + timedelta(minutes=150)))
        dep = legs[-1].arr + timedelta(minutes=90)
    v = pairing_violations(legs)
    assert any(s.startswith("FDP") for s in v)
    assert any(s.startswith("block") for s in v)


def test_fdp_violation_without_block_violation():
    # Two short legs with a long sit: block fine, but FDP over the limit.
    rules = CrewRules(max_sit_min=24 * 60)
    a = leg("A", "CLT", "BOS", t(13), t(14))  # report 07:00 local, limit 14 h
    b = leg("B", "BOS", "CLT", t(13, day=16) - timedelta(hours=10), t(13, day=16) - timedelta(hours=9))
    v = pairing_violations([a, b], rules)
    assert any(s.startswith("FDP") for s in v) and not any(s.startswith("block") for s in v)


def test_rejects_long_sit_and_too_many_legs():
    assert any("sit" in s for s in pairing_violations(out_and_back(sit=300)))
    rules = CrewRules(max_legs=1)
    assert any("legs > 1" in s for s in pairing_violations(out_and_back(), rules))


def test_all_numbers_are_configurable():
    p = out_and_back(sit=25)
    assert not is_legal(p)
    assert is_legal(p, CrewRules(min_connect_same_tail_min=20))


def test_forward_check():
    a, b = out_and_back()
    assert can_still_close([a])  # open pairing, can still return to base
    assert not can_still_close([b])  # does not start at base
    assert not can_still_close(out_and_back(sit=10))  # dead connection


# --- Connection graph ------------------------------------------------------------

def test_connection_graph_edges():
    legs = [
        leg("A", "CLT", "BOS", t(13), t(14, 30)),
        leg("B", "BOS", "CLT", t(15, 5), t(16, 30)),  # 35 min, same tail -> edge
        leg("C", "BOS", "CLT", t(15, 5), t(16, 40), tail="N2"),  # 35 min, change -> no edge
        leg("D", "BOS", "CLT", t(15, 20), t(16, 50), tail="N3"),  # 50 min, change -> edge
        leg("E", "DCA", "CLT", t(15, 30), t(17)),  # wrong station
        leg("F", "CLT", "MIA", t(17, 30), t(19, 30), tail="N9"),
    ]
    edges = {(legs[e.i].flight_id, legs[e.j].flight_id): e for e in build_connection_graph(legs)}
    # D->F (40 min) and E->F (30 min) change aircraft, so they need 45 min.
    assert set(edges) == {("A", "B"), ("A", "D"), ("B", "F"), ("C", "F")}
    assert edges[("A", "B")].same_tail and not edges[("A", "D")].same_tail
    assert edges[("B", "F")].gap_min == 60


# --- Aircraft rotations ----------------------------------------------------------

def test_rotations_link_across_midnight_and_skip_cancelled():
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE flights AS SELECT * FROM (VALUES
          ('f1', DATE '2023-11-14', 'AA', 'N1', 'DFW', 'CLT', TIMESTAMPTZ '2023-11-15 01:00+00', TIMESTAMPTZ '2023-11-15 04:00+00', TIMESTAMPTZ '2023-11-15 01:10+00', TIMESTAMPTZ '2023-11-15 04:05+00', false),
          ('f2', DATE '2023-11-15', 'AA', 'N1', 'CLT', 'BOS', TIMESTAMPTZ '2023-11-15 12:00+00', TIMESTAMPTZ '2023-11-15 14:00+00', NULL, NULL, true),
          ('f3', DATE '2023-11-15', 'AA', 'N1', 'CLT', 'MIA', TIMESTAMPTZ '2023-11-15 13:00+00', TIMESTAMPTZ '2023-11-15 15:00+00', TIMESTAMPTZ '2023-11-15 13:00+00', TIMESTAMPTZ '2023-11-15 15:00+00', false),
          ('g1', DATE '2023-11-15', 'AA', 'N2', 'CLT', 'DCA', TIMESTAMPTZ '2023-11-15 12:00+00', TIMESTAMPTZ '2023-11-15 13:00+00', TIMESTAMPTZ '2023-11-15 12:00+00', TIMESTAMPTZ '2023-11-15 13:00+00', false)
        ) v(flight_id, FlightDate, Reporting_Airline, Tail_Number, Origin, Dest, sched_dep_utc, sched_arr_utc, actual_dep_utc, actual_arr_utc, Cancelled)"""
    )
    assert build_rotations_table(con) == 3
    rot = {r[0]: r[1:] for r in con.execute(
        "SELECT flight_id, prev_flight_id, sched_turn_min, prev_dest_matches FROM aircraft_rotations").fetchall()}
    assert rot["f3"] == ("f1", 540, True)  # previous night's arrival, cancelled f2 skipped
    assert rot["f1"][0] is None and rot["g1"][0] is None
