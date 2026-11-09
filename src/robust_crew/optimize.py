"""CP-SAT set-partitioning planners: baseline, soft robust, hard robust.

Each enumerated pairing (restricted to the day's plannable flight set, see
``pairings.select_plannable_set``) is a binary variable; every plannable
flight must be covered exactly once.

- BASELINE: minimize pairing cost (the classical approach).
- SOFT ROBUST: minimize cost + lambda * risk, where a pairing's risk is
  ``sum over its connections of p_break * legs_after``. A break propagates to
  every later leg of the pairing, so a break early in the duty disrupts more
  flying; ``legs_after`` is that count. ``lambda`` is in pay hours per expected
  disrupted leg, swept over ``LAMBDAS``.
- HARD ROBUST: forbid every pairing with a connection whose p_break >= theta,
  swept over ``THETAS``. If no exact cover remains, the day is reported
  INFEASIBLE; constraints are never silently relaxed.

Probabilities are the calibrated PLANNING-feature model only.

Usage::

    uv run python -m robust_crew.optimize     # all CALIB + TEST days, full grid
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import pandas as pd
from ortools.sat.python import cp_model

from robust_crew import config
from robust_crew.ingest import connect
from robust_crew.models import Leg
from robust_crew.network import build_connection_graph, load_day_legs
from robust_crew.pairings import Pairing, enumerate_pairings, select_plannable_set

log = logging.getLogger(__name__)

LAMBDAS = [0.5, 1, 2, 4, 8, 16, 32, 64]  # pay hours per expected disrupted leg
THETAS = [0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1]
TIME_LIMIT_S = 30.0
SEED = 0
NUM_WORKERS = 8
COST_SCALE = 100  # CP-SAT needs integers: objective in 1/100 pay-hour units
DEFAULT_P = None  # connections with no risk score: fail loudly (see DayProblem)


@dataclass
class DayProblem:
    """Everything a planner needs for one day."""

    day: date
    legs: list[Leg]
    plannable: list[int]  # leg indices that must be covered exactly once
    pairings: list[Pairing]  # pairings using only plannable legs
    risk: list[float]  # per pairing: sum p_break * legs_after
    max_p: list[float]  # per pairing: largest connection p_break
    exp_breaks: list[float]  # per pairing: sum p_break (expected broken connections)


@dataclass
class Solution:
    """A planner's schedule for one day plus solver diagnostics."""

    day: date
    planner: str
    param: float | None
    status: str
    chosen: list[int] = field(default_factory=list)  # indices into DayProblem.pairings
    crews: int | None = None
    cost: float | None = None
    risk: float | None = None
    exp_breaks: float | None = None
    objective: float | None = None
    bound: float | None = None
    gap: float | None = None
    seconds: float = 0.0


def build_day_problem(con: duckdb.DuckDBPyConnection, day: date) -> DayProblem:
    """Enumerate pairings, pick the plannable set and attach planning-model risk."""
    legs = load_day_legs(con, day)
    res = enumerate_pairings(legs, build_connection_graph(legs))
    plannable, _, _ = select_plannable_set(legs, res)
    pairings = [p for p in res.pairings if all(i in plannable for i in p.legs)]
    p_map = {
        (a, b): p for a, b, p in con.execute(
            "SELECT i_id, j_id, p_plan FROM conn_risk WHERE FlightDate = ?", [day]).fetchall()
    }
    risk, max_p, exp_b = [], [], []
    for p in pairings:
        r = m = e = 0.0
        n = len(p.legs)
        for k in range(n - 1):
            key = (legs[p.legs[k]].flight_id, legs[p.legs[k + 1]].flight_id)
            if key not in p_map:
                raise KeyError(f"{day}: no risk score for connection {key}")
            pk = p_map[key]
            r += pk * (n - 1 - k)  # legs after this connection
            m = max(m, pk)
            e += pk
        risk.append(r)
        max_p.append(m)
        exp_b.append(e)
    return DayProblem(day, legs, sorted(plannable), pairings, risk, max_p, exp_b)


def solve(problem: DayProblem, planner: str, param: float | None = None) -> Solution:
    """Solve one planner variant. ``planner`` in {'baseline', 'soft', 'hard'}."""
    t0 = time.perf_counter()
    allowed = list(range(len(problem.pairings)))
    if planner == "hard":
        allowed = [k for k in allowed if problem.max_p[k] < param]
    lam = param if planner == "soft" else 0.0

    model = cp_model.CpModel()
    x = {k: model.NewBoolVar(f"p{k}") for k in allowed}
    rows: dict[int, list] = {i: [] for i in problem.plannable}
    for k in allowed:
        for i in problem.pairings[k].legs:
            rows[i].append(x[k])
    if any(not v for v in rows.values()):
        return Solution(problem.day, planner, param, "INFEASIBLE", seconds=time.perf_counter() - t0)
    for v in rows.values():
        model.AddExactlyOne(v)
    weight = {k: round(COST_SCALE * (problem.pairings[k].cost + lam * problem.risk[k])) for k in allowed}
    model.Minimize(sum(weight[k] * x[k] for k in allowed))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = TIME_LIMIT_S
    solver.parameters.random_seed = SEED
    solver.parameters.num_workers = NUM_WORKERS
    st = solver.Solve(model)
    sol = Solution(problem.day, planner, param, solver.StatusName(st), seconds=time.perf_counter() - t0)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        chosen = [k for k in allowed if solver.Value(x[k])]
        sol.chosen = chosen
        sol.crews = len(chosen)
        sol.cost = round(sum(problem.pairings[k].cost for k in chosen), 4)
        sol.risk = round(sum(problem.risk[k] for k in chosen), 4)
        sol.exp_breaks = round(sum(problem.exp_breaks[k] for k in chosen), 4)
        sol.objective = solver.ObjectiveValue() / COST_SCALE
        sol.bound = solver.BestObjectiveBound() / COST_SCALE
        sol.gap = round((sol.objective - sol.bound) / max(abs(sol.objective), 1e-9), 6)
    return sol


def planner_grid() -> list[tuple[str, float | None]]:
    """Every planner variant run on each day."""
    return [("baseline", None)] + [("soft", lam) for lam in LAMBDAS] + [("hard", th) for th in THETAS]


def run_days(days: list[date]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Solve the full grid on ``days``. Returns (solve log, schedule rows)."""
    con = connect()
    logs, sched = [], []
    for day in days:
        prob = build_day_problem(con, day)
        for planner, param in planner_grid():
            sol = solve(prob, planner, param)
            logs.append({
                "date": day, "split": config.split_of(day), "planner": planner, "param": param,
                "status": sol.status, "crews": sol.crews, "cost": sol.cost, "risk": sol.risk,
                "exp_breaks": sol.exp_breaks, "gap": sol.gap, "seconds": round(sol.seconds, 3),
                "plannable_legs": len(prob.plannable), "pairings": len(prob.pairings),
            })
            for crew, k in enumerate(sol.chosen):
                for pos, i in enumerate(prob.pairings[k].legs):
                    sched.append({"date": day, "planner": planner, "param": param, "crew": crew,
                                  "pos": pos, "flight_id": prob.legs[i].flight_id})
        log.info("%s: %d plannable legs, %d pairings", day, len(prob.plannable), len(prob.pairings))
    con.close()
    return pd.DataFrame(logs), pd.DataFrame(sched)


def evaluation_days() -> list[date]:
    """All CALIB and TEST-window days (CALIB is used to choose lambda/theta)."""
    days = []
    for a, b in [(config.CALIB_START, config.CALIB_END), *config.TEST_WINDOWS.values()]:
        d = a
        while d <= b:
            days.append(d)
            d += timedelta(days=1)
    return days


def summarize(logs: pd.DataFrame) -> dict:
    """Per split and planner variant: feasibility, crews, cost, expected breaks, solver stats."""
    out: dict = {}
    for split, g in logs.groupby("split", sort=False):
        rows = []
        for (planner, param), h in g.groupby(["planner", "param"], dropna=False, sort=False):
            ok = h[h.status.isin(["OPTIMAL", "FEASIBLE"])]
            rows.append({
                "planner": planner, "param": None if pd.isna(param) else float(param),
                "days": int(len(h)), "days_feasible": int(len(ok)),
                "days_optimal": int((h.status == "OPTIMAL").sum()),
                "mean_crews": round(float(ok.crews.mean()), 2) if len(ok) else None,
                "mean_cost": round(float(ok.cost.mean()), 2) if len(ok) else None,
                "mean_exp_breaks": round(float(ok.exp_breaks.mean()), 3) if len(ok) else None,
                "mean_risk": round(float(ok.risk.mean()), 3) if len(ok) else None,
                "max_gap": float(ok.gap.max()) if len(ok) else None,
                "mean_seconds": round(float(h.seconds.mean()), 3),
                "max_seconds": round(float(h.seconds.max()), 3),
            })
        out[split] = rows
    return out


def main() -> None:
    """Run the planner grid on CALIB + TEST days; write schedules and the Phase 5 report."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logs, sched = run_days(evaluation_days())
    con = connect()
    for name, df in [("plan_log", logs), ("schedules", sched)]:
        con.execute(f"DROP TABLE IF EXISTS {name}")
        con.register("df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    report = {
        "lambdas": LAMBDAS, "thetas": THETAS, "time_limit_s": TIME_LIMIT_S, "seed": SEED,
        "num_workers": NUM_WORKERS, "probabilities": "planning model (calibrated)",
        "summary": summarize(logs),
    }
    config.REPORTS_DIR.mkdir(exist_ok=True)
    out = config.REPORTS_DIR / "phase5_planners.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(out.read_text())


if __name__ == "__main__":
    main()
