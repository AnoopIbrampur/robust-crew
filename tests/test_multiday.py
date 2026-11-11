"""Two-day pairings: duty enumeration, rest-counting constraints + FIFO matching, rest in the replay."""

from datetime import datetime, timezone
from itertools import permutations

import pytest
from ortools.sat.python import cp_model

from robust_crew.models import Leg
from robust_crew.multiday import Duty, add_rest_counting, enumerate_duties, match_rest
from robust_crew.network import build_connection_graph
from robust_crew.rules import DEFAULT_RULES as R
from robust_crew.rules import connection_violation
from robust_crew.simulate import SimFlight, replay


def t(d, h, m=0):
    return datetime(2023, 11, d, h, m, tzinfo=timezone.utc)


def toy_legs():
    """A small day around CLT with two spokes (UTC; 06:00 local = 11:00 UTC)."""
    legs = [
        Leg("A", "CLT", "BOS", t(15, 11), t(15, 13)),
        Leg("B", "BOS", "CLT", t(15, 14), t(15, 16)),
        Leg("C", "CLT", "DCA", t(15, 17), t(15, 18, 30)),
        Leg("D", "DCA", "CLT", t(15, 19, 30), t(15, 21)),
        Leg("E", "CLT", "BOS", t(15, 22), t(16, 0)),
        Leg("F", "BOS", "CLT", t(15, 15), t(15, 17)),
        Leg("G", "DCA", "CLT", t(15, 12), t(15, 13, 30)),
    ]
    return sorted(legs, key=lambda leg: (leg.dep, leg.flight_id))


def brute_force_duties(legs, from_base):
    """Every ordered leg sequence (up to 4 legs) that is a legal duty, by exhaustive search."""
    out = set()
    for n in range(1, 5):
        for seq in permutations(range(len(legs)), n):
            ls = [legs[i] for i in seq]
            if (ls[0].origin == R.base) != from_base:
                continue
            if any(connection_violation(a, b) for a, b in zip(ls, ls[1:])):
                continue
            report = R.report_time(ls[0].dep)
            if sum(leg.block_minutes for leg in ls) > R.max_flight_time_min(report):
                continue
            if (ls[-1].arr - report).total_seconds() / 60 > R.max_fdp_min(report, n):
                continue
            out.add(seq)
    return out


@pytest.mark.parametrize("from_base", [True, False])
def test_duty_enumeration_matches_brute_force(from_base):
    legs = toy_legs()
    found = {p for p, _ in enumerate_duties(legs, build_connection_graph(legs), from_base)}
    assert found == brute_force_duties(legs, from_base)
    assert found  # the toy day is not trivial


def rest_problem(in_deps):
    """Two OUT duties into BOS (arrive 20:00 and 23:00 UTC day 15) and IN duties out of BOS next day."""
    legs = [Leg("o1", "CLT", "BOS", t(15, 18), t(15, 20)), Leg("o2", "CLT", "BOS", t(15, 21), t(15, 23))]
    legs += [Leg(f"i{k}", "BOS", "CLT", dep, dep.replace(hour=dep.hour + 2)) for k, dep in enumerate(in_deps)]
    duties = [Duty((0,), "out", 0, 120, 0), Duty((1,), "out", 0, 120, 0)]
    duties += [Duty((2 + k,), "in", 1, 120, 0) for k in range(len(in_deps))]
    return legs, duties


def cover_all(legs, duties):
    model = cp_model.CpModel()
    x = {k: model.NewBoolVar("") for k in range(len(duties))}
    for k in x:
        model.Add(x[k] == 1)  # every duty chosen (each covers its own leg)
    add_rest_counting(model, x, legs, duties)
    s = cp_model.CpSolver()
    return s.StatusName(s.Solve(model))


def test_rest_counting_rejects_short_rest():
    # Min layover 11.5 h: arrivals 20:00 and 23:00 need departures >= 07:30 and >= 10:30.
    legs, duties = rest_problem([t(16, 7), t(16, 12)])  # 07:00 is too early for either crew
    assert cover_all(legs, duties) == "INFEASIBLE"


def test_rest_counting_accepts_and_fifo_matches_legal_rest():
    legs, duties = rest_problem([t(16, 8), t(16, 12)])
    assert cover_all(legs, duties) == "OPTIMAL"
    pairings = match_rest(legs, duties, [0, 1, 2, 3])
    assert sorted(pairings) == [[0, 2], [1, 3]]  # 20:00 -> 08:00 (12 h), 23:00 -> 12:00 (13 h)


def test_rest_counting_needs_equal_out_and_in():
    legs, duties = rest_problem([t(16, 12)])  # two crews out, one way back
    assert cover_all(legs, duties) == "INFEASIBLE"


def test_replay_rest_delay_and_rest_break():
    """OUT CLT-BOS lands 23:00 (60 min late); IN BOS-CLT next day at 10:00 needs 11.5 h -> ready 10:30."""
    flights = [
        SimFlight("O", "CLT", "BOS", "N1", t(15, 20), t(15, 22), 60, 120, True),
        SimFlight("I", "BOS", "CLT", "N2", t(16, 10), t(16, 12), 0, 120, True),
    ]
    res = replay(flights, [["O", "I"]], duty_of={"O": 0, "I": 1})
    assert res.sim_dep["I"] == t(16, 10, 30)
    assert res.crew_delay_at == {"I": 30.0} and res.rest_delay_min == 30.0
    assert res.rest_breaks == 1 and res.broken_connections == 0
    assert res.duty_violations == 0 and res.crew_replacements == 0


def test_replay_without_duty_map_is_one_day_behaviour():
    flights = [
        SimFlight("O", "CLT", "BOS", "N1", t(15, 20), t(15, 22), 60, 120, True),
        SimFlight("I", "BOS", "CLT", "N2", t(16, 10), t(16, 12), 0, 120, True),
    ]
    res = replay(flights, [["O", "I"]])  # same crew, no rest: a 45-min connection, then FDP limit
    assert res.rest_breaks == 0
