"""Replay simulator on a hand-built 4-flight day with hand-computed answers."""

from dataclasses import replace
from datetime import datetime, timezone

from robust_crew.simulate import SimFlight, crew_caused_delay, replay


def t(h, m=0):
    return datetime(2023, 11, 15, h, m, tzinfo=timezone.utc)


def toy_day():
    """One crew flies F1-F4. Aircraft N1 flies F1, F2; aircraft N2 flies F3, F4.

    Hand computation (UTC; min crew connection 30 same tail / 45 change; turn 30):
      F1 CLT-BOS  sched 12:00  own delay 30 -> dep 12:30, block 120 -> arr 14:30
      F2 BOS-CLT  sched 14:40  aircraft ready 14:30+30 = 15:00, crew ready 14:30+30 = 15:00
                  -> dep 15:00 (aircraft binds; crew adds 0), arr 17:00
      F3 CLT-DCA  sched 17:30  N2's first leg; crew ready 17:00+45 = 17:45 (aircraft change)
                  -> crew adds 15, dep 17:45, arr 19:15
      F4 DCA-CLT  sched 19:40  aircraft ready 19:15+30 = 19:45, crew ready 19:15+30 = 19:45
                  -> dep 19:45, arr 21:15
    Without crew constraints: F3 dep 17:30 arr 19:00; F4 aircraft ready 19:30 < sched 19:40
      -> dep 19:40 arr 21:10.
    Crew-caused delay = F3 +15 + F4 +5 (knock-on through aircraft N2) = 20 min.
    Broken connections (sim arrival + min connection > scheduled departure):
      F1->F2 15:00 > 14:40, F2->F3 17:45 > 17:30, F3->F4 19:45 > 19:40 -> 3.
    Duty: report 11:00 UTC = 06:00 EST, 4 segments -> 12 h limit; FDP 21:15-11:00 = 10.25 h -> 0.
    """
    return [
        SimFlight("F1", "CLT", "BOS", "N1", t(12), t(14), 30, 120, True),
        SimFlight("F2", "BOS", "CLT", "N1", t(14, 40), t(16, 40), 0, 120, True, prev_id="F1"),
        SimFlight("F3", "CLT", "DCA", "N2", t(17, 30), t(19), 0, 90, True),
        SimFlight("F4", "DCA", "CLT", "N2", t(19, 40), t(21, 10), 0, 90, True, prev_id="F3"),
    ]


PAIRING = [["F1", "F2", "F3", "F4"]]


def test_hand_computed_propagation():
    flights = toy_day()
    res = replay(flights, PAIRING)
    assert res.sim_dep == {"F1": t(12, 30), "F2": t(15), "F3": t(17, 45), "F4": t(19, 45)}
    assert res.sim_arr["F4"] == t(21, 15)
    assert res.crew_delay_at == {"F3": 15.0}
    base = replay(flights, None)
    assert base.sim_arr == {"F1": t(14, 30), "F2": t(17), "F3": t(19), "F4": t(21, 10)}
    assert crew_caused_delay(res, base) == 20.0


def test_hand_computed_metrics():
    res = replay(toy_day(), PAIRING)
    assert res.broken_connections == 3
    assert res.duty_violations == 0
    assert res.legs_orphaned == 0


def test_duty_violation_when_delays_push_past_fdp():
    flights = toy_day()
    flights[0] = replace(flights[0], own_dep_delay=150)  # F1 2.5 h late: everything slides
    res = replay(flights, PAIRING)
    # F1 arr 16:30; F2 dep 17:00 arr 19:00; F3 dep 19:45 arr 21:15; F4 dep 21:45 arr 23:15.
    # FDP = 23:15 - 11:00 = 12.25 h > 12 h.
    assert res.sim_arr["F4"] == t(23, 15)
    assert res.duty_violations == 1


def test_short_scheduled_turn_is_not_penalised():
    flights = toy_day()
    flights[3] = replace(flights[3], turn_min=10)  # planned 10-min turn: simulator uses it
    base = replay(flights, None)
    assert base.sim_dep["F4"] == t(19, 40)  # 19:00 + 10 < 19:40


def test_cancelled_leg_dropped_and_chain_orphaned():
    flights = toy_day()
    flights[1] = replace(flights[1], completed=False)  # F2 cancelled: crew stuck at BOS
    res = replay(flights, PAIRING)
    assert "F2" not in res.sim_arr
    assert res.legs_orphaned == 1  # F1 (to BOS) -> F3 (from CLT) breaks the station chain
    assert res.sim_dep["F3"] == t(17, 30)  # no crew constraint on F3
    assert res.crew_delay_at == {}


def test_no_crews_means_no_crew_delay():
    flights = toy_day()
    base = replay(flights, None)
    assert crew_caused_delay(base, base) == 0
