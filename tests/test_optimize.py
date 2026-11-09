"""CP-SAT planners on a hand-built problem."""

from datetime import date, datetime, timezone

from robust_crew.models import Leg
from robust_crew.optimize import DayProblem, solve
from robust_crew.pairings import Pairing

T = datetime(2023, 7, 12, 12, tzinfo=timezone.utc)
LEGS = [Leg(f"L{i}", "CLT", "X", T, T) for i in range(4)]  # times unused by the solver


def problem() -> DayProblem:
    # Cover legs {0,1,2,3}. Options:
    #   k0 = (0,1,2,3) cheap but risky; k1 = (0,1), k2 = (2,3) safer, one more crew.
    #   k3 = (0,1) duplicate of k1 but riskier.
    pairings = [
        Pairing((0, 1, 2, 3), 400, 500, 12.0),
        Pairing((0, 1), 200, 300, 9.0),
        Pairing((2, 3), 200, 300, 9.0),
        Pairing((0, 1), 200, 300, 9.0),
    ]
    return DayProblem(
        day=date(2023, 7, 12), legs=LEGS, plannable=[0, 1, 2, 3], pairings=pairings,
        risk=[3.0, 0.2, 0.2, 0.9], max_p=[0.6, 0.2, 0.2, 0.45], exp_breaks=[1.2, 0.2, 0.2, 0.45],
    )


def covered(sol, prob):
    return sorted(i for k in sol.chosen for i in prob.pairings[k].legs)


def test_baseline_picks_cheapest_exact_cover():
    prob = problem()
    sol = solve(prob, "baseline")
    assert sol.status == "OPTIMAL" and sol.chosen == [0]
    assert sol.crews == 1 and sol.cost == 12.0 and sol.gap == 0
    assert covered(sol, prob) == [0, 1, 2, 3]


def test_soft_trades_cost_for_risk():
    prob = problem()
    low = solve(prob, "soft", 0.5)  # 12 + 1.5 = 13.5 < 18 + 0.2 = 18.2
    high = solve(prob, "soft", 4.0)  # 12 + 12 = 24 > 18 + 1.6 = 19.6
    assert low.chosen == [0]
    assert sorted(high.chosen) == [1, 2] and high.crews == 2 and high.exp_breaks == 0.4
    assert covered(high, prob) == [0, 1, 2, 3]


def test_hard_forbids_risky_pairings():
    prob = problem()
    sol = solve(prob, "hard", 0.5)  # k0 (0.6) forbidden; k3 (0.45) allowed but costlier tie
    assert 0 not in sol.chosen and sol.status == "OPTIMAL"
    assert covered(sol, prob) == [0, 1, 2, 3]


def test_hard_reports_infeasible_instead_of_relaxing():
    prob = problem()
    sol = solve(prob, "hard", 0.1)  # every pairing has max_p >= 0.1
    assert sol.status == "INFEASIBLE" and sol.chosen == [] and sol.crews is None
