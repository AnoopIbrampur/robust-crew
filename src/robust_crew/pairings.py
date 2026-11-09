"""Enumerate legal one-day pairings: the constraint-satisfaction core.

A pairing is a sequence of legs that starts and ends at the base on one duty
day and satisfies every rule in ``rules.py``. We enumerate them by depth-first
search from each base departure over the connection graph (which already
enforces the connection rules), with forward checking: a partial pairing is
abandoned as soon as a limit that can only get worse with more legs is
already broken (leg count, block time, flight duty period; Part 117 FDP limits
never increase with more segments). Every enumerated pairing is checked
against ``rules.pairing_violations`` in tests.

Cost of a pairing (in pay-hour units, a documented convention, not a real
contract): ``max(block hours, PAY_GUARANTEE_HOURS) + CREW_FIXED_COST_HOURS``.
The guarantee makes short pairings relatively expensive; the fixed cost makes
the optimizer prefer fewer crews.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from robust_crew.models import Leg
from robust_crew.network import Connection
from robust_crew.rules import DEFAULT_RULES, CrewRules

PAY_GUARANTEE_HOURS = 5.0  # minimum pay credit per duty day
CREW_FIXED_COST_HOURS = 4.0  # per-crew overhead (per diem, reserve cover), in pay hours
DEFAULT_MAX_PAIRINGS = 2_000_000  # hard cap; enumeration stops and reports if hit


@dataclass(frozen=True)
class Pairing:
    """A legal one-day pairing (leg indices into the day's leg list)."""

    legs: tuple[int, ...]
    block_min: float
    fdp_min: float
    cost: float


@dataclass
class EnumerationResult:
    """Pairings plus everything needed to report on the enumeration."""

    pairings: list[Pairing]
    seconds: float
    capped: bool
    nodes_expanded: int
    uncoverable: dict[int, str] = field(default_factory=dict)  # leg index -> reason


def pairing_cost(block_min: float) -> float:
    """Pay-hour cost of a pairing with the given scheduled block minutes."""
    return max(block_min / 60, PAY_GUARANTEE_HOURS) + CREW_FIXED_COST_HOURS


def enumerate_pairings(
    legs: list[Leg],
    edges: list[Connection],
    rules: CrewRules = DEFAULT_RULES,
    max_pairings: int = DEFAULT_MAX_PAIRINGS,
) -> EnumerationResult:
    """Enumerate every legal one-day pairing by DFS with forward checking."""
    t0 = time.perf_counter()
    succ: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        succ[e.i].append(e.j)
    for v in succ.values():
        v.sort(key=lambda j: legs[j].dep)

    block = [leg.block_minutes for leg in legs]
    pairings: list[Pairing] = []
    nodes = 0
    capped = False

    for start, first in enumerate(legs):
        if first.origin != rules.base:
            continue
        report = rules.report_time(first.dep)
        flight_limit = rules.max_flight_time_min(report)
        # FDP limit by segment count for this report time (non-increasing in n).
        fdp_limit = [rules.max_fdp_min(report, n) for n in range(1, rules.max_legs + 1)]

        stack: list[tuple[tuple[int, ...], float]] = [((start,), block[start])]
        while stack:
            path, blk = stack.pop()
            nodes += 1
            last = legs[path[-1]]
            n = len(path)
            fdp = (last.arr - report).total_seconds() / 60
            # Forward check: limits that can only get worse with more legs.
            if blk > flight_limit or fdp > fdp_limit[n - 1]:
                continue
            if last.dest == rules.base:
                pairings.append(Pairing(path, blk, fdp, pairing_cost(blk)))
                if len(pairings) >= max_pairings:
                    capped = True
                    break
            if n == rules.max_legs:
                continue
            for j in succ.get(path[-1], ()):
                nxt = legs[j]
                # Cheap look-ahead before pushing: the next leg alone must fit.
                if blk + block[j] > flight_limit:
                    continue
                if (nxt.arr - report).total_seconds() / 60 > fdp_limit[n]:
                    break  # successors are sorted by departure; later ones only end later
                stack.append((path + (j,), blk + block[j]))
        if capped:
            break

    covered = {i for p in pairings for i in p.legs}
    uncoverable = {
        i: _uncoverable_reason(i, legs, succ, rules) for i in range(len(legs)) if i not in covered
    }
    return EnumerationResult(pairings, time.perf_counter() - t0, capped, nodes, uncoverable)


def _uncoverable_reason(i: int, legs: list[Leg], succ: dict[int, list[int]], rules: CrewRules) -> str:
    """Best short explanation of why no legal pairing covers leg ``i``."""
    leg = legs[i]
    if leg.origin == rules.base and not succ.get(i):
        return "departs base but no legal connecting return (e.g. evening departure, overnight at spoke)"
    if leg.dest == rules.base and not any(i in succ.get(k, ()) for k in range(len(legs))):
        return "arrives at base but no legal feeder from base (crew would have overnighted at spoke)"
    return "every route through this leg breaks the duty, block or leg limits"


# --- Plannable flight set ----------------------------------------------------------

NOT_PARTITIONABLE = "in some legal pairing, but no exact cover of the day can include it"


def select_plannable_set(
    legs: list[Leg], result: EnumerationResult, time_limit_s: float = 60.0, seed: int = 0
) -> tuple[set[int], dict[int, str], dict]:
    """Largest set of legs that some set of pairings covers exactly once.

    Being in a legal pairing does not guarantee an exact cover exists: every
    one-day pairing flies as many legs out of the base as into it, so a leg
    whose only partners are needed elsewhere gets stranded. We solve a set
    packing with CP-SAT: maximize legs covered (each at most once), then, with
    coverage fixed at that maximum, minimize cost as a deterministic
    tie-break. Every planner then covers exactly this set, so their schedules
    are compared on the same flights.

    Returns (plannable leg indices, dropped leg -> reason, solve info).
    """
    from ortools.sat.python import cp_model

    dropped = dict(result.uncoverable)
    if not result.pairings:
        return set(), dropped, {"status": "EMPTY"}
    x, model, rows = _packing_model(result)
    covered = sum(len(p.legs) * x[k] for k, p in enumerate(result.pairings))
    model.Maximize(covered)
    solver = _solver(time_limit_s, seed)
    st1 = solver.Solve(model)
    best = int(round(solver.ObjectiveValue()))
    model.Add(covered == best)
    model.Minimize(sum(int(round(p.cost * 100)) * x[k] for k, p in enumerate(result.pairings)))
    st2 = solver.Solve(model)
    chosen = {i for k, p in enumerate(result.pairings) if solver.Value(x[k]) for i in p.legs}
    for i in rows:
        if i not in chosen:
            dropped[i] = NOT_PARTITIONABLE
    info = {
        "status_max_cover": solver.StatusName(st1),
        "status_tiebreak": solver.StatusName(st2),
        "legs_plannable": len(chosen),
    }
    return chosen, dropped, info


def _packing_model(result: EnumerationResult):
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    x = [model.NewBoolVar(f"p{k}") for k in range(len(result.pairings))]
    rows: dict[int, list] = defaultdict(list)
    for k, p in enumerate(result.pairings):
        for i in p.legs:
            rows[i].append(x[k])
    for vs in rows.values():
        model.AddAtMostOne(vs)
    return x, model, rows


def _solver(time_limit_s: float, seed: int):
    from ortools.sat.python import cp_model

    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit_s
    s.parameters.random_seed = seed
    s.parameters.num_workers = 1  # deterministic
    return s


# --- Report over all days ----------------------------------------------------------

def main() -> None:
    """Enumerate every day; write reports/phase3_pairings_by_day.csv and phase3_pairings.json."""
    import csv
    import json
    from collections import Counter
    from datetime import timedelta

    from robust_crew import config
    from robust_crew.ingest import connect
    from robust_crew.network import build_connection_graph, load_day_legs

    con = connect()
    rows, reasons = [], Counter()
    d = config.DATA_START
    while d <= config.DATA_END:
        legs = load_day_legs(con, d)
        t0 = time.perf_counter()
        edges = build_connection_graph(legs)
        graph_s = time.perf_counter() - t0
        res = enumerate_pairings(legs, edges)
        t1 = time.perf_counter()
        plannable, dropped, info = select_plannable_set(legs, res)
        reasons.update(dropped.values())
        rows.append({
            "date": str(d), "split": config.split_of(d), "legs": len(legs),
            "connections": len(edges), "pairings": len(res.pairings),
            "legs_in_some_pairing": len(legs) - len(res.uncoverable),
            "legs_plannable": len(plannable),
            "plannable_share": round(len(plannable) / len(legs), 4) if legs else None,
            "graph_seconds": round(graph_s, 3), "enumerate_seconds": round(res.seconds, 3),
            "select_seconds": round(time.perf_counter() - t1, 3),
            "capped": res.capped, "solve_status": info.get("status_max_cover"),
        })
        d += timedelta(days=1)
    con.close()

    config.REPORTS_DIR.mkdir(exist_ok=True)
    with open(config.REPORTS_DIR / "phase3_pairings_by_day.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    def agg(rs: list[dict]) -> dict:
        n = len(rs)
        mean = lambda k: round(sum(r[k] for r in rs) / n, 3)  # noqa: E731
        return {
            "days": n, "mean_legs": mean("legs"), "mean_pairings": mean("pairings"),
            "min_pairings": min(r["pairings"] for r in rs), "max_pairings": max(r["pairings"] for r in rs),
            "mean_legs_in_some_pairing": mean("legs_in_some_pairing"),
            "mean_legs_plannable": mean("legs_plannable"), "mean_plannable_share": mean("plannable_share"),
            "mean_enumerate_seconds": mean("enumerate_seconds"),
            "max_enumerate_seconds": max(r["enumerate_seconds"] for r in rs),
            "mean_select_seconds": mean("select_seconds"),
            "days_capped": sum(r["capped"] for r in rs),
            "days_not_optimal": sum(r["solve_status"] != "OPTIMAL" for r in rs),
        }

    splits = sorted({r["split"] for r in rows}, key=lambda s: min(x["date"] for x in rows if x["split"] == s))
    summary = {
        "rules": {k: v for k, v in vars(DEFAULT_RULES).items() if not k.endswith("table")},
        "cost": {"pay_guarantee_hours": PAY_GUARANTEE_HOURS, "crew_fixed_cost_hours": CREW_FIXED_COST_HOURS},
        "max_pairings_cap": DEFAULT_MAX_PAIRINGS,
        "all_days": agg(rows),
        "by_split": {s: agg([r for r in rows if r["split"] == s]) for s in splits},
        "dropped_leg_reasons_total": dict(reasons.most_common()),
    }
    out = config.REPORTS_DIR / "phase3_pairings.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(out.read_text())


if __name__ == "__main__":
    main()
