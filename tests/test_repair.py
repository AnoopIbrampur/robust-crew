"""Reactive repair: swaps with idle crews and reserve takeovers."""

from datetime import datetime, timezone

from robust_crew.repair import replay_with_repair
from robust_crew.simulate import SimFlight, replay


def t(h, m=0):
    return datetime(2023, 11, 15, h, m, tzinfo=timezone.utc)


def day():
    """Crew A: A1 BOS->... flies X-CLT late, then A2 CLT-DCA, A3 DCA-CLT.
    Crew B: B1 MIA-CLT arrives early, then B2 CLT-ORD much later.

      A0 CLT-BOS 12:00-14:00 on time; A1 BOS-CLT sched 14:40, own delay 60 -> arr 17:40
      A2 CLT-DCA sched 17:00 (N3)   crew A ready 17:40+45 = 18:25 -> 85 min late without repair
      B0 CLT-MIA 11:30-13:30, B1 MIA-CLT 14:10-16:10 on time -> B idle at CLT from 16:55
      B2 CLT-ORD sched 19:30 (N5)
    Swap: B takes A2 (ready 16:55 <= 17:00), A takes B2 (ready 18:25 <= 19:30). No delay.
    """
    return [
        SimFlight("B0", "CLT", "MIA", "N4", t(11, 30), t(13, 30), 0, 120, True),
        SimFlight("A0", "CLT", "BOS", "N1", t(12), t(14), 0, 120, True),
        SimFlight("B1", "MIA", "CLT", "N4", t(14, 10), t(16, 10), 0, 120, True, prev_id="B0"),
        SimFlight("A1", "BOS", "CLT", "N1", t(14, 40), t(16, 40), 60, 120, True, prev_id="A0"),
        SimFlight("A2", "CLT", "DCA", "N3", t(17), t(18, 30), 0, 90, True),
        SimFlight("B2", "CLT", "ORD", "N5", t(19, 30), t(21, 30), 0, 120, True),
    ]


PAIRINGS = [["A0", "A1", "A2"], ["B0", "B1", "B2"]]


def test_no_repair_baseline_delay():
    res = replay(day(), PAIRINGS)
    assert res.crew_delay_at == {"A2": 85.0}


def test_swap_with_idle_crew_removes_delay():
    res = replay_with_repair(day(), PAIRINGS, n_reserves=0)
    assert res.swaps == 1 and res.reserves_used == 0
    assert res.crew_delay_at == {}
    assert ["B0", "B1", "A2"] in res.crews_flown and ["A0", "A1", "B2"] in res.crews_flown


def test_reserve_used_when_no_swap_possible():
    flights = day()
    pairings = [["A0", "A1", "A2"]]  # crew B does not exist
    flights = [f for f in flights if not f.flight_id.startswith("B")]
    res = replay_with_repair(flights, pairings, n_reserves=1)
    assert res.reserves_used == 1 and res.swaps == 0
    assert res.crew_delay_at == {}
    assert ["A2"] in res.crews_flown


def test_nothing_possible_leaves_delay():
    flights = [f for f in day() if not f.flight_id.startswith("B")]
    res = replay_with_repair(flights, [["A0", "A1", "A2"]], n_reserves=0)
    assert res.crew_delay_at == {"A2": 85.0} and res.triggers == 1


def test_disabled_repair_matches_plain_replay():
    a = replay(day(), PAIRINGS)
    b = replay_with_repair(day(), PAIRINGS, enabled=False)
    assert a.sim_arr == b.sim_arr and a.crew_delay_at == b.crew_delay_at
    assert (a.broken_connections, a.duty_violations) == (b.broken_connections, b.duty_violations)


def test_disabled_repair_matches_plain_replay_with_replacement():
    flights = day()
    flights[3] = SimFlight("A1", "BOS", "CLT", "N1", t(14, 40), t(16, 40), 600, 120, True, prev_id="A0")
    a = replay(flights, PAIRINGS)
    b = replay_with_repair(flights, PAIRINGS, enabled=False)
    assert a.crew_replacements == b.crew_replacements == 1
    assert a.sim_arr == b.sim_arr and a.crew_delay_at == b.crew_delay_at
    assert (a.broken_connections, a.duty_violations) == (b.broken_connections, b.duty_violations)
