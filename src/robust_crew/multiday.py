"""Two-day pairings with an overnight at the outstation (Phase 9).

One-day pairings must start and end at CLT on the same duty day, so evening
departures to a spoke and early-morning arrivals from one can never be
planned (about 45% of hub flights; see the Phase 3 report). Here a crew may
also fly OUT to a spoke on day 1, rest there, and fly back IN on day 2.

Planning unit: non-overlapping blocks of ``BLOCK_DAYS`` = 2 consecutive days,
so every planner covers exactly the same flights (the block's plannable set,
found by max set packing as in Phase 3). Pairings never cross a block
boundary; the first day's arrivals from spokes and the last day's departures
to spokes remain unplannable.

Duty model instead of pairing enumeration. Joining every outbound duty with
every compatible inbound duty gives 1-4 million two-day pairings per block,
too many for CP-SAT. But a pairing's cost and risk are sums over its duties
(the rest absorbs any delay into the next duty, so a break's ``legs_after``
stays within its duty). So the solver chooses DUTIES:

- ONE-DAY duties CLT -> ... -> CLT (exactly the Phase 3 pairings),
- OUT duties CLT -> ... -> spoke X starting on day d,
- IN duties X -> ... -> CLT starting on day d + 1,

and, per (spoke, day), cumulative counting constraints guarantee that the
chosen OUT and IN duties can be matched one-to-one with at least
``rules.min_layover_min`` (release + 10 h rest + report) from block-in to
block-out: at every IN departure time t, the number of IN duties leaving by t
is at most the number of OUT duties that arrived by t - min layover, with
equal totals. After solving, a FIFO matching recovers explicit pairings; the
counting condition is exactly what makes FIFO succeed (tested).

Cost (pay hours, a convention): every duty is paid ``max(block hours, 5)``;
every crew (pairing) adds the fixed ``CREW_FIXED_COST_HOURS``; every overnight
adds ``OVERNIGHT_COST_HOURS`` (hotel and per diem). A one-day pairing costs
exactly what it costs in Phase 3.

Risk: the calibrated planning-model break probabilities for connections
inside a duty. The planner assigns no risk to the rest connection; the replay
enforces the rest and counts rest breaks separately.

Day-of repair is not modelled for two-day pairings (no-repair replay only).

Usage::

    uv run python -m robust_crew.multiday
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
from ortools.sat.python import cp_model

from robust_crew import config
from robust_crew.ingest import connect
from robust_crew.models import Leg
from robust_crew.network import Connection, build_connection_graph, load_day_legs
from robust_crew.optimize import COST_SCALE, NUM_WORKERS, SEED, TIEBREAK_SEED, tiebreak_weights
from robust_crew.pairings import CREW_FIXED_COST_HOURS, PAY_GUARANTEE_HOURS
from robust_crew.rules import DEFAULT_RULES, CrewRules
from robust_crew.simulate import crew_caused_delay, load_day_flights, replay

log = logging.getLogger(__name__)

BLOCK_DAYS = 2
OVERNIGHT_COST_HOURS = 3.0  # hotel + per diem per layover, in pay hours (convention)
LAMBDAS = [1, 2, 4, 8, 16]  # soft-robust weights (pay hours per expected disrupted leg)
DET_TIME_LIMIT = 300.0  # CP-SAT deterministic time units per solve (reproducible, unlike wall time)
TIEBREAK_DET_LIMIT = 60.0  # baseline min-cost + tie-break solve (cost-tie plateaus are rarely proven)
WALL_LIMIT_S = 900.0  # safety net only; if ever hit, the run is no longer reproducible (logged)
MAX_COST_INCREASE = 0.02  # lambda selection rule, as in Phase 8
N_BOOT = 10_000


@dataclass(frozen=True)
class Duty:
    """One duty: leg indices into the block's leg list, its kind and its day within the block."""

    legs: tuple[int, ...]
    kind: str  # 'one' (CLT->CLT), 'out' (CLT->spoke), 'in' (spoke->CLT)
    day: int  # 0-based day of the block it starts on
    block_min: float
    cost: float


@dataclass
class BlockProblem:
    """Everything a planner needs for one block."""

    start: date
    legs: list[Leg]
    duties: list[Duty]
    plannable: list[int]
    baseline: list[int] = field(default_factory=list)  # chosen duty indices of the cheapest schedule
    info: dict = field(default_factory=dict)
    risk: list[float] = field(default_factory=list)
    exp_breaks: list[float] = field(default_factory=list)


def duty_cost(kind: str, block_min: float) -> float:
    """Pay-hour cost of a duty; the crew's fixed cost sits on the duty that starts at base."""
    pay = max(block_min / 60, PAY_GUARANTEE_HOURS)
    if kind == "one":
        return pay + CREW_FIXED_COST_HOURS
    if kind == "out":
        return pay + CREW_FIXED_COST_HOURS + OVERNIGHT_COST_HOURS
    return pay


def enumerate_duties(
    legs: list[Leg], edges: list[Connection], from_base: bool, rules: CrewRules = DEFAULT_RULES
) -> list[tuple[tuple[int, ...], float]]:
    """Every legal duty (leg sequence) starting at the base (``from_base``) or at a spoke.

    Same DFS and forward checking as ``pairings.enumerate_pairings``, but every
    legal prefix is a duty, wherever it ends. Returns (leg indices, block minutes).
    """
    succ: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        succ[e.i].append(e.j)
    for v in succ.values():
        v.sort(key=lambda j: legs[j].dep)
    block = [leg.block_minutes for leg in legs]
    out: list[tuple[tuple[int, ...], float]] = []
    for start, first in enumerate(legs):
        if (first.origin == rules.base) != from_base:
            continue
        report = rules.report_time(first.dep)
        flight_limit = rules.max_flight_time_min(report)
        fdp_limit = [rules.max_fdp_min(report, n) for n in range(1, rules.max_legs + 1)]
        stack = [((start,), block[start])]
        while stack:
            path, blk = stack.pop()
            n = len(path)
            if blk > flight_limit or (legs[path[-1]].arr - report).total_seconds() / 60 > fdp_limit[n - 1]:
                continue
            out.append((path, blk))
            if n == rules.max_legs:
                continue
            for j in succ.get(path[-1], ()):
                if blk + block[j] > flight_limit:
                    continue
                if (legs[j].arr - report).total_seconds() / 60 > fdp_limit[n]:
                    break
                stack.append((path + (j,), blk + block[j]))
    return out


def block_duties(day_legs: list[list[Leg]], rules: CrewRules = DEFAULT_RULES) -> tuple[list[Leg], list[Duty]]:
    """All duties of a block. Duties use one local date's legs (as Phase 3 pairings do)."""
    legs: list[Leg] = []
    duties: list[Duty] = []
    n_days = len(day_legs)
    for d, sub in enumerate(day_legs):
        off = len(legs)
        legs += sub
        edges = build_connection_graph(sub, rules)
        for path, blk in enumerate_duties(sub, edges, True, rules):
            ids = tuple(i + off for i in path)
            if sub[path[-1]].dest == rules.base:
                duties.append(Duty(ids, "one", d, blk, duty_cost("one", blk)))
            elif d < n_days - 1:
                duties.append(Duty(ids, "out", d, blk, duty_cost("out", blk)))
        if d > 0:
            for path, blk in enumerate_duties(sub, edges, False, rules):
                if sub[path[-1]].dest == rules.base:
                    ids = tuple(i + off for i in path)
                    duties.append(Duty(ids, "in", d, blk, duty_cost("in", blk)))
    return legs, duties


def _rest_groups(legs: list[Leg], duties: list[Duty], idx: list[int]):
    """(spoke, day) -> (OUT duties [(arrival, k)], IN duties [(departure, k)]) over duty indices ``idx``."""
    groups: dict[tuple[str, int], tuple[list, list]] = defaultdict(lambda: ([], []))
    for k in idx:
        du = duties[k]
        if du.kind == "out":
            groups[(legs[du.legs[-1]].dest, du.day)][0].append((legs[du.legs[-1]].arr, k))
        elif du.kind == "in":
            groups[(legs[du.legs[0]].origin, du.day - 1)][1].append((legs[du.legs[0]].dep, k))
    return groups


def add_rest_counting(model: cp_model.CpModel, x: dict[int, cp_model.IntVar], legs: list[Leg],
                      duties: list[Duty], rules: CrewRules = DEFAULT_RULES) -> None:
    """Constraints under which chosen OUT and IN duties admit a legal one-to-one rest matching."""
    lay = timedelta(minutes=rules.min_layover_min)
    for outs, ins in _rest_groups(legs, duties, list(x)).values():
        model.Add(sum(x[k] for _, k in outs) == sum(x[k] for _, k in ins))
        outs, ins = sorted(outs), sorted(ins)
        seen = set()
        for t, _ in ins:
            if t in seen:
                continue
            seen.add(t)
            model.Add(sum(x[k] for d, k in ins if d <= t) <= sum(x[k] for a, k in outs if a + lay <= t))


def match_rest(legs: list[Leg], duties: list[Duty], chosen: list[int],
               rules: CrewRules = DEFAULT_RULES) -> list[list[int]]:
    """Turn chosen duties into pairings (lists of duty indices); FIFO-match OUT to IN duties."""
    pairings = [[k] for k in chosen if duties[k].kind == "one"]
    lay = timedelta(minutes=rules.min_layover_min)
    for (spoke, day), (outs, ins) in sorted(_rest_groups(legs, duties, chosen).items()):
        outs, ins = sorted(outs), sorted(ins)
        if len(outs) != len(ins):
            raise ValueError(f"{spoke} day {day}: {len(outs)} out vs {len(ins)} in duties")
        for (a, ko), (d, ki) in zip(outs, ins):
            if a + lay > d:
                raise ValueError(f"{spoke}: rest {d - a} shorter than {lay}")
            pairings.append([ko, ki])
    return sorted(pairings, key=lambda p: legs[duties[p[0]].legs[0]].dep)


def _cost_units(du: Duty) -> int:
    """Duty cost in whole minutes of pay credit (exact: block times are whole minutes)."""
    return int(round(du.cost * 60))


TIE_SCALE = 20_000  # > 50 * (most duties a block chooses), so the tie-break never trades away a minute of cost


def _packing(problem_legs: list[Leg], duties: list[Duty]) -> tuple[set[int], list[int], dict]:
    """Plannable set and the cheapest (baseline) schedule.

    1. Maximize legs covered (each at most once, legal rest matching): proven optimal.
    2. With coverage fixed, minimize cost in whole minutes, ties broken by a seeded
       per-duty weight < 50 (scaled so it never outweighs one minute of cost).
       Two-day blocks have huge plateaus of cost-tied schedules, so this is usually
       not proven optimal in the time allowed; it runs CP-SAT's deterministic
       parallel search under a deterministic time limit, so it returns the same
       schedule on every run. ``cost_gap_min`` is the schedule's cost minus the
       solver's proven lower bound, in minutes of pay credit.

    Returns (plannable leg indices, chosen duty indices, solve info).
    """
    import random

    model = cp_model.CpModel()
    x = {k: model.NewBoolVar(f"d{k}") for k in range(len(duties))}
    rows: dict[int, list] = defaultdict(list)
    for k, du in enumerate(duties):
        for i in du.legs:
            rows[i].append(x[k])
    for v in rows.values():
        model.AddAtMostOne(v)
    add_rest_counting(model, x, problem_legs, duties)
    covered = sum(len(du.legs) * x[k] for k, du in enumerate(duties))
    model.Maximize(covered)
    s = _solver()
    st1 = s.Solve(model)
    model.Add(covered == int(round(s.ObjectiveValue())))
    rng = random.Random(TIEBREAK_SEED)
    model.Minimize(sum((_cost_units(duties[k]) * TIE_SCALE + rng.randrange(50)) * x[k] for k in sorted(x)))
    for limit in (TIEBREAK_DET_LIMIT, 4 * TIEBREAK_DET_LIMIT):  # retry with more time if nothing found
        s.parameters.max_deterministic_time = limit
        st2 = s.Solve(model)
        if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
    if st2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"baseline solve returned {s.StatusName(st2)}")
    chosen = [k for k in sorted(x) if s.Value(x[k])]
    cost_min = sum(_cost_units(duties[k]) for k in chosen)
    info = {"cover_status": s.StatusName(st1), "baseline_status": s.StatusName(st2),
            "cost_gap_min": int(cost_min - s.BestObjectiveBound() // TIE_SCALE)}
    return {i for k in chosen for i in duties[k].legs}, chosen, info


def _solver() -> cp_model.CpSolver:
    """CP-SAT with deterministic parallel search: the same answer on every run, proven or not."""
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = WALL_LIMIT_S
    s.parameters.max_deterministic_time = DET_TIME_LIMIT
    s.parameters.interleave_search = True
    s.parameters.random_seed = SEED
    s.parameters.num_workers = NUM_WORKERS
    return s


def score_new_connections() -> int:
    """Score duty connections that no one-day pairing uses (so Phase 4 never scored them).

    Spoke-start duties use connections such as spoke -> CLT -> spoke. They get
    the same features and the same trained, calibrated planning model as every
    other connection (no refit), and go to table ``md_conn_risk``.
    """
    import pickle

    import __main__

    from robust_crew.delay_model import MODEL_DIR, FittedModel, load_features

    # Phase 4 pickled the models from ``python -m robust_crew.delay_model``, i.e. as __main__.FittedModel.
    __main__.FittedModel = FittedModel

    con = connect()
    known = {(a, b) for a, b in con.execute("SELECT i_id, j_id FROM conn_risk").fetchall()}
    rows = []
    for start in all_blocks():
        for d in range(BLOCK_DAYS):
            day = start + timedelta(days=d)
            legs = load_day_legs(con, day)
            edges = {(e.i, e.j): e for e in build_connection_graph(legs)}
            used = set()
            for from_base in (True, False):
                for path, _ in enumerate_duties(legs, list(edges.values()), from_base):
                    used.update(zip(path, path[1:]))
            for i, j in used:
                if (legs[i].flight_id, legs[j].flight_id) in known:
                    continue
                e = edges[(i, j)]
                need = DEFAULT_RULES.min_connect_min(e.same_tail)
                rows.append((day, legs[i].flight_id, legs[j].flight_id, legs[i].dest,
                             e.gap_min, need, e.gap_min - need, e.same_tail))
    df = pd.DataFrame(sorted(set(rows)), columns=["FlightDate", "i_id", "j_id", "station", "gap_min",
                                                  "min_conn", "slack_min", "same_tail"])
    con.execute("DROP TABLE IF EXISTS md_connections")
    con.register("df", df)
    con.execute("CREATE TABLE md_connections AS SELECT * FROM df")
    con.unregister("df")
    with open(MODEL_DIR / "break_models.pkl", "rb") as fh:
        models = pickle.load(fh)
    feats = load_features(con, "md_connections")
    out = pd.DataFrame({"FlightDate": feats.FlightDate, "i_id": feats.i_id, "j_id": feats.j_id,
                        "p_plan": models["planning"].predict_proba(feats)})
    con.execute("DROP TABLE IF EXISTS md_conn_risk")
    con.register("df", out)
    con.execute("CREATE TABLE md_conn_risk AS SELECT * FROM df")
    con.unregister("df")
    con.close()
    return len(out)


def build_block(con: duckdb.DuckDBPyConnection, start: date, n_days: int = BLOCK_DAYS) -> BlockProblem:
    """Duties, plannable set and planning-model risk for one block."""
    days = [start + timedelta(days=i) for i in range(n_days)]
    legs, duties = block_duties([load_day_legs(con, d) for d in days])
    plannable, chosen, info = _packing(legs, duties)
    keep = [k for k, du in enumerate(duties) if all(i in plannable for i in du.legs)]
    new_index = {k: n for n, k in enumerate(keep)}
    baseline = [new_index[k] for k in chosen]
    duties = [duties[k] for k in keep]
    q = ", ".join("?" * n_days)
    p_map = {(a, b): p for a, b, p in con.execute(
        f"SELECT i_id, j_id, p_plan FROM conn_risk WHERE FlightDate IN ({q}) "
        f"UNION ALL SELECT i_id, j_id, p_plan FROM md_conn_risk WHERE FlightDate IN ({q})", days + days
    ).fetchall()}
    prob = BlockProblem(start, legs, duties, sorted(plannable), baseline, info)
    for du in duties:
        n = len(du.legs)
        ps = [p_map[(legs[du.legs[k]].flight_id, legs[du.legs[k + 1]].flight_id)] for k in range(n - 1)]
        prob.risk.append(sum(p * (n - 1 - k) for k, p in enumerate(ps)))
        prob.exp_breaks.append(sum(ps))
    return prob


def solve_block(prob: BlockProblem, planner: str, lam: float | None = None) -> dict:
    """Baseline (min cost) or soft robust (cost + lam * risk) on one block; exact cover + legal rest."""
    t0 = time.perf_counter()
    if planner == "baseline":  # the packing's cheapest schedule (see _packing)
        return _result(prob, prob.baseline, prob.info["baseline_status"], t0)
    model = cp_model.CpModel()
    x = {k: model.NewBoolVar(f"d{k}") for k in range(len(prob.duties))}
    rows: dict[int, list] = {i: [] for i in prob.plannable}
    for k, du in enumerate(prob.duties):
        for i in du.legs:
            rows[i].append(x[k])
    for v in rows.values():
        model.AddExactlyOne(v)
    add_rest_counting(model, x, prob.legs, prob.duties)
    w = tiebreak_weights({k: round(COST_SCALE * (du.cost + lam * prob.risk[k]))
                          for k, du in enumerate(prob.duties)}, TIEBREAK_SEED)
    model.Minimize(sum(w[k] * x[k] for k in w))
    s = _solver()
    st = s.Solve(model)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": s.StatusName(st), "seconds": round(time.perf_counter() - t0, 2), "pairings": []}
    return _result(prob, [k for k in x if s.Value(x[k])], s.StatusName(st), t0)


def _result(prob: BlockProblem, chosen: list[int], status: str, t0: float) -> dict:
    """Pairings and totals for a chosen set of duties."""
    pairings = match_rest(prob.legs, prob.duties, chosen)
    return {
        "status": status, "seconds": round(time.perf_counter() - t0, 2), "pairings": pairings,
        "crews": len(pairings), "duties": len(chosen),
        "overnights": sum(prob.duties[k].kind == "out" for k in chosen),
        "cost": round(sum(prob.duties[k].cost for k in chosen), 4),
        "risk": round(sum(prob.risk[k] for k in chosen), 4),
        "exp_breaks": round(sum(prob.exp_breaks[k] for k in chosen), 4),
    }


# --- Running blocks ------------------------------------------------------------------

def blocks(lo: date, hi: date, n_days: int = BLOCK_DAYS) -> list[date]:
    """Start dates of the non-overlapping blocks that fit inside [lo, hi]."""
    out, d = [], lo
    while d + timedelta(days=n_days - 1) <= hi:
        out.append(d)
        d += timedelta(days=n_days)
    return out


def all_blocks() -> list[date]:
    """CALIB blocks (to choose lambda) and TEST blocks."""
    return [b for lo, hi in [(config.CALIB_START, config.CALIB_END), *config.TEST_WINDOWS.values()]
            for b in blocks(lo, hi)]


def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Plan and replay every block for every planner variant; return (block results, schedules)."""
    con = connect()
    rows, sched = [], []
    variants = [("baseline", None)] + [("soft", float(lam)) for lam in LAMBDAS]
    for start in all_blocks():
        prob = build_block(con, start)
        flights = load_day_flights(con, start, BLOCK_DAYS)
        base = replay(flights, None)
        one_day_plannable = con.execute(
            "SELECT sum(plannable_legs) FROM (SELECT DISTINCT date, plannable_legs FROM plan_log "
            "WHERE date BETWEEN ? AND ?)", [start, start + timedelta(days=BLOCK_DAYS - 1)]).fetchone()[0]
        for planner, lam in variants:
            sol = solve_block(prob, planner, lam)
            row = {"block": start, "split": config.split_of(start), "planner": planner, "param": lam,
                   "status": sol["status"], "seconds": sol["seconds"], "legs": len(prob.legs),
                   "plannable_legs": len(prob.plannable), "one_day_plannable_legs": one_day_plannable,
                   "duty_columns": len(prob.duties), "cover_status": prob.info["cover_status"],
                   "baseline_cost_gap_min": prob.info["cost_gap_min"]}
            if sol["pairings"]:
                pairings, duty_of = [], {}
                for crew, p in enumerate(sol["pairings"]):
                    ids = []
                    for dn, k in enumerate(p):
                        for i in prob.duties[k].legs:
                            fid = prob.legs[i].flight_id
                            ids.append(fid)
                            duty_of[fid] = dn
                            sched.append({"block": start, "planner": planner, "param": lam, "crew": crew,
                                          "pos": len(ids) - 1, "duty": dn, "flight_id": fid})
                    pairings.append(ids)
                res = replay(flights, pairings, duty_of=duty_of)
                row.update({k: sol[k] for k in ["crews", "duties", "overnights", "cost", "risk", "exp_breaks"]})
                row.update({
                    "crew_delay_min": round(crew_caused_delay(res, base), 1),
                    "broken_connections": res.broken_connections, "rest_breaks": res.rest_breaks,
                    "rest_delay_min": res.rest_delay_min, "duty_violations": res.duty_violations,
                    "crew_replacements": res.crew_replacements, "legs_orphaned": res.legs_orphaned,
                })
            rows.append(row)
        log.info("block %s: %d legs, %d plannable (one-day %d), %d duty columns",
                 start, len(prob.legs), len(prob.plannable), one_day_plannable, len(prob.duties))
    con.close()
    return pd.DataFrame(rows), pd.DataFrame(sched)


# --- Results ---------------------------------------------------------------------------

METRICS = ["crews", "duties", "overnights", "cost", "crew_delay_min", "broken_connections",
           "rest_breaks", "crew_replacements", "duty_violations"]


def _ci(x: np.ndarray, rng: np.random.Generator) -> list[float]:
    idx = rng.integers(0, len(x), size=(N_BOOT, len(x)))
    m = x[idx].mean(axis=1)
    return [round(float(np.percentile(m, 2.5)), 2), round(float(np.percentile(m, 97.5)), 2)]


def summarize(df: pd.DataFrame) -> dict:
    """Choose lambda on CALIB blocks; compare soft with baseline on TEST blocks (bootstrap over blocks)."""
    rng = np.random.default_rng(0)
    ok = df[df.status.isin(["OPTIMAL", "FEASIBLE"])]
    v = lambda g, planner, lam: g[(g.planner == planner) & (  # noqa: E731
        g.param.isna() if lam is None else g.param == lam)].set_index("block")
    cal = ok[ok.split == "calib"]
    b = v(cal, "baseline", None)
    cands = []
    for lam in LAMBDAS:
        s = v(cal, "soft", float(lam))
        inc = float(s.cost.mean() / b.cost.mean() - 1)
        cands.append({"lambda": lam, "cost_increase": round(inc, 4), "within_cap": inc <= MAX_COST_INCREASE,
                      "delay_reduction": round(float(b.crew_delay_min.mean() - s.crew_delay_min.mean()), 1)})
    ok_c = [c for c in cands if c["within_cap"]]
    best = max(ok_c, key=lambda c: c["delay_reduction"])["lambda"] if ok_c else None
    out = {"block_days": BLOCK_DAYS, "overnight_cost_hours": OVERNIGHT_COST_HOURS,
           "min_layover_min": DEFAULT_RULES.min_layover_min, "lambdas": LAMBDAS,
           "selection": {"rule": f"largest CALIB delay reduction with cost <= +{MAX_COST_INCREASE:.0%}",
                         "candidates": cands, "best_soft_lambda": best},
           "solver": {"blocks": int(df.block.nunique()), "solves": int(len(df)),
                      "optimal": int((df.status == "OPTIMAL").sum()),
                      "soft_optimal": int((df[df.planner == "soft"].status == "OPTIMAL").sum()),
                      "soft_solves": int((df.planner == "soft").sum()),
                      "max_seconds": float(df.seconds.max()), "det_time_limit": DET_TIME_LIMIT,
                      "baseline_cost_gap_min_max": int(df.baseline_cost_gap_min.max()),
                      "baseline_cost_gap_min_mean": round(float(df.baseline_cost_gap_min.mean()), 1)},
           "coverage": {}, "test": {}}
    for split, g in ok.groupby("split", sort=False):
        bb = v(g, "baseline", None)
        out["coverage"][split] = {
            "blocks": int(len(bb)), "mean_legs": round(float(bb.legs.mean()), 1),
            "plannable_share_one_day": round(float((bb.one_day_plannable_legs / bb.legs).mean()), 4),
            "plannable_share_two_day": round(float((bb.plannable_legs / bb.legs).mean()), 4),
            "mean_extra_legs_planned": round(float((bb.plannable_legs - bb.one_day_plannable_legs).mean()), 1),
            "baseline_overnights": round(float(bb.overnights.mean()), 1),
            "mean_duty_columns": round(float(bb.duty_columns.mean()), 0),
        }
        if split == "calib" or best is None:
            continue
        s = v(g, "soft", float(best))
        blocks_ = bb.index.intersection(s.index)
        res = {"blocks": int(len(blocks_))}
        for m in METRICS:
            x0, x1 = bb.loc[blocks_, m].to_numpy(float), s.loc[blocks_, m].to_numpy(float)
            res[m] = {"baseline": round(float(x0.mean()), 2), "baseline_ci": _ci(x0, rng),
                      "soft": round(float(x1.mean()), 2), "diff": round(float((x1 - x0).mean()), 2),
                      "diff_ci": _ci(x1 - x0, rng)}
        out["test"][split] = res
    return out


LABELS = {"crews": "Crews (pairings)", "duties": "Crew duties", "overnights": "Overnights",
          "cost": "Cost (pay hours)", "crew_delay_min": "Crew-caused delay (min)",
          "broken_connections": "Broken crew connections", "rest_breaks": "Rest cut short",
          "crew_replacements": "Crews replaced at duty limit", "duty_violations": "Duty overruns in the air"}


def readme_block(rep: dict) -> str:
    """Markdown for the README's generated multi-day section."""
    lines = [f"Blocks of {rep['block_days']} days; λ chosen on calibration blocks: "
             f"**{rep['selection']['best_soft_lambda']}** ({rep['selection']['rule']}). "
             f"{rep['solver']['soft_optimal']} of {rep['solver']['soft_solves']} soft-robust solves are proven optimal; the cheapest plan's cost-tie plateau usually is not, "
             f"so it comes from CP-SAT's deterministic search: its cost is on average "
             f"{rep['solver']['baseline_cost_gap_min_mean']:.0f} and at most {rep['solver']['baseline_cost_gap_min_max']} "
             f"minutes of pay credit above the solver's proven lower bound per block "
             f"({rep['solver']['optimal']} of {rep['solver']['solves']} solves proven optimal overall). "
             "Crews and duties count pairings and crew-days; overnights are identical across planners "
             "because the plannable set fixes which flights need one.", "",
             "| Window | Blocks | Plannable flights, one-day | Plannable flights, two-day | Extra flights planned per block | Overnights per block (baseline) |",
             "|---|---|---|---|---|---|"]
    for split, c in rep["coverage"].items():
        lines.append(f"| {split.replace('_', ' ')} | {c['blocks']} | {c['plannable_share_one_day']:.1%} | "
                     f"{c['plannable_share_two_day']:.1%} | {c['mean_extra_legs_planned']:.0f} | {c['baseline_overnights']:.0f} |")
    for split, r in rep["test"].items():
        lines += ["", f"**{split.replace('_', ' ')}**, per 2-day block, cheapest vs soft-robust (paired Δ, 95% bootstrap over blocks):", "",
                  "| Metric | Cheapest | Soft robust | Δ |", "|---|---|---|---|"]
        for m in METRICS:
            x = r[m]
            lines.append(f"| {LABELS[m]} | {x['baseline']:.1f} | {x['soft']:.1f} | "
                         f"{x['diff']:+.1f} [{x['diff_ci'][0]:+.1f}, {x['diff_ci'][1]:+.1f}] |")
    return "\n".join(lines)


def write_readme(block: str) -> None:
    """Splice ``block`` between the MULTIDAY markers in README.md."""
    path = config.ROOT / "README.md"
    text = path.read_text()
    if "<!-- MULTIDAY:START -->" not in text or "<!-- MULTIDAY:END -->" not in text:
        raise RuntimeError("README.md has no MULTIDAY markers")
    new = re.sub(r"(<!-- MULTIDAY:START -->\n).*?(<!-- MULTIDAY:END -->)",
                 lambda m: m.group(1) + block + "\n" + m.group(2), text, flags=re.S)
    path.write_text(new)


def main() -> None:
    """Plan + replay all blocks; write md_* tables, reports/phase9_multiday.json and the README block."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("scored %d connections only multi-day duties use", score_new_connections())
    df, sched = run()
    con = connect()
    for name, d in [("md_results", df), ("md_schedules", sched)]:
        con.execute(f"DROP TABLE IF EXISTS {name}")
        con.register("d", d)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM d")
        con.unregister("d")
    con.close()
    rep = summarize(df)
    (config.REPORTS_DIR / "phase9_multiday.json").write_text(json.dumps(rep, indent=2, default=str))
    write_readme(readme_block(rep))
    print(json.dumps(rep["coverage"], indent=2))


if __name__ == "__main__":
    main()
