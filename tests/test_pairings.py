"""Pairing enumeration (the CSP) and plannable-set selection."""

import itertools
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from robust_crew import config
from robust_crew.models import Leg
from robust_crew.network import build_connection_graph
from robust_crew.pairings import (
    CREW_FIXED_COST_HOURS,
    NOT_PARTITIONABLE,
    PAY_GUARANTEE_HOURS,
    enumerate_pairings,
    pairing_cost,
    select_plannable_set,
)
from robust_crew.rules import CrewRules, is_legal

UTC = timezone.utc
SPOKES = ["BOS", "DCA", "MIA", "ORD"]


def toy_day(seed: int, n_out: int = 9) -> list[Leg]:
    """Random hub-and-spoke day: outbound CLT->X and returns X->CLT."""
    rng = random.Random(seed)
    legs = []
    for k in range(n_out):
        x = rng.choice(SPOKES)
        dep = datetime(2023, 3, 8, 11, tzinfo=UTC) + timedelta(minutes=rng.randrange(0, 12 * 60, 5))
        block = rng.choice([60, 90, 120, 150])
        tail = f"N{k}"
        legs.append(Leg(f"O{k}", "CLT", x, dep, dep + timedelta(minutes=block), tail))
        ret = legs[-1].arr + timedelta(minutes=rng.choice([30, 40, 50, 70, 200]))
        legs.append(Leg(f"R{k}", x, "CLT", ret, ret + timedelta(minutes=block), rng.choice([tail, None])))
    return sorted(legs, key=lambda leg: (leg.dep, leg.flight_id))


def brute_force(legs: list[Leg], rules: CrewRules) -> set[tuple[int, ...]]:
    """Every legal pairing, by checking every ordered sequence (small instances only)."""
    out = set()
    for n in range(1, rules.max_legs + 1):
        for seq in itertools.permutations(range(len(legs)), n):
            if is_legal([legs[i] for i in seq], rules):
                out.add(seq)
    return out


def test_dfs_finds_exactly_the_legal_pairings():
    rules = CrewRules(max_legs=4)  # keeps brute force tractable
    four_leg = 0
    for seed in range(8):
        legs = toy_day(seed, n_out=6)
        expected = brute_force(legs, rules)
        res = enumerate_pairings(legs, build_connection_graph(legs, rules), rules)
        assert {p.legs for p in res.pairings} == expected, f"seed {seed}"
        four_leg += sum(len(p) == 4 for p in expected)
    assert four_leg >= 5, "instances too easy: few multi-leg pairings exercised"


def test_every_enumerated_pairing_is_legal():
    legs = toy_day(42, n_out=12)
    res = enumerate_pairings(legs, build_connection_graph(legs))
    assert res.pairings and all(is_legal([legs[i] for i in p.legs]) for p in res.pairings)
    assert not res.capped


def test_cap_is_reported():
    legs = toy_day(42, n_out=12)
    res = enumerate_pairings(legs, build_connection_graph(legs), max_pairings=3)
    assert res.capped and len(res.pairings) == 3


def test_cost_has_pay_guarantee_and_fixed_cost():
    assert pairing_cost(60) == PAY_GUARANTEE_HOURS + CREW_FIXED_COST_HOURS
    assert pairing_cost(7 * 60) == 7 + CREW_FIXED_COST_HOURS


def test_uncoverable_legs_get_reasons():
    t = datetime(2023, 3, 8, 13, tzinfo=UTC)
    legs = [
        Leg("A", "CLT", "BOS", t, t + timedelta(hours=2), "N1"),
        Leg("B", "BOS", "CLT", t + timedelta(hours=2, minutes=40), t + timedelta(hours=4, minutes=40), "N1"),
        Leg("C", "CLT", "MIA", t + timedelta(hours=10), t + timedelta(hours=12), "N2"),  # no return
    ]
    legs.sort(key=lambda leg: leg.dep)
    res = enumerate_pairings(legs, build_connection_graph(legs))
    ids = {legs[i].flight_id: why for i, why in res.uncoverable.items()}
    assert set(ids) == {"C"} and "no legal connecting return" in ids["C"]


def test_plannable_set_is_an_exact_cover():
    t = datetime(2023, 3, 8, 13, tzinfo=UTC)
    h = lambda m: timedelta(minutes=m)  # noqa: E731
    # Two outbounds to BOS share a single possible return: only one can be planned.
    legs = sorted([
        Leg("O1", "CLT", "BOS", t, t + h(90), "N1"),
        Leg("O2", "CLT", "BOS", t + h(20), t + h(110), "N2"),
        Leg("R", "BOS", "CLT", t + h(160), t + h(250), "N3"),
    ], key=lambda leg: leg.dep)
    res = enumerate_pairings(legs, build_connection_graph(legs))
    plannable, dropped, info = select_plannable_set(legs, res)
    assert len(plannable) == 2 and info["status_max_cover"] == "OPTIMAL"
    assert list(dropped.values()) == [NOT_PARTITIONABLE]


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="no local database built yet")
def test_real_train_day_pairings_are_legal():
    import duckdb

    from robust_crew.network import load_day_legs

    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    legs = load_day_legs(con, date(2023, 3, 8))
    con.close()
    res = enumerate_pairings(legs, build_connection_graph(legs))
    assert len(res.pairings) > 1000
    assert all(is_legal([legs[i] for i in p.legs]) for p in res.pairings)
