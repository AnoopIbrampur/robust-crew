"""How much does the baseline's arbitrary choice among cost-tied schedules matter?

Many schedules share the minimum cost; CP-SAT returns one of them. We re-solve
the baseline on every test day with ``N_SEEDS`` tiny cost perturbations (each
picks a different cost-tied optimum), replay each without repair, and report
the spread of crew-caused delay next to the soft-robust gain.

Usage::

    uv run python -m robust_crew.tiebreak     # after optimize + delay_model predict
"""

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np

from robust_crew import config
from robust_crew.ingest import connect
from robust_crew.optimize import build_day_problem, solve
from robust_crew.simulate import crew_caused_delay, load_day_flights, replay

N_SEEDS = 5


def main() -> dict:
    """Replay baseline tie-break variants on every test day; write reports/tiebreak_sensitivity.json."""
    con = connect()
    out: dict = {"n_seeds": N_SEEDS, "windows": {}}
    for name, (a, b) in config.TEST_WINDOWS.items():
        per_seed = np.zeros(N_SEEDS)
        cost_same = True
        n = 0
        d = a
        while d <= b:
            prob = build_day_problem(con, d)
            flights = load_day_flights(con, d)
            base = replay(flights, None)
            ref = solve(prob, "baseline")
            for s in range(N_SEEDS):
                sol = solve(prob, "baseline", jitter_seed=s)
                cost_same &= abs(sol.cost - ref.cost) < 1e-6
                ps = [[prob.legs[i].flight_id for i in prob.pairings[k].legs] for k in sol.chosen]
                per_seed[s] += crew_caused_delay(replay(flights, ps), base)
            n += 1
            d += timedelta(days=1)
        means = per_seed / n
        out["windows"][name] = {
            "days": n, "mean_crew_delay_by_seed": [round(float(x), 1) for x in means],
            "min": round(float(means.min()), 1), "max": round(float(means.max()), 1),
            "all_cost_tied_optima": bool(cost_same),
        }
    con.close()
    (config.REPORTS_DIR / "tiebreak_sensitivity.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
