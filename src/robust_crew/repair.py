"""Day-of-operations recovery.

Two mechanisms, evaluated separately and together against "no repair":

1. REACTIVE (min-conflicts) repair inside the replay. When a crew would
   delay its next leg at the hub by more than ``TRIGGER_MIN`` minutes, or its
   projected flight duty period would exceed the limit, try, in order of the
   delay they leave on that leg:
   - swap the rest of the day with another crew that is idle at the hub and
     can depart on time (both crews must stay legal: legs, block, FDP), or
   - hand the rest of the day to a reserve crew on airport standby
     (``N_RESERVES`` per day, standby window ``RESERVE_WINDOW`` local).
   A swap is preferred to a reserve when both leave no delay. The late crew's
   arrival is treated as known when the decision is made (it is airborne).
   Repairs happen only at the hub, where reserves and other crews are.

2. MORNING RE-SOLVE (small CP-SAT). At ``OPS_CUTOFF_LOCAL`` the operations
   model's probabilities are known. Pairings whose crews have not reported
   yet (report at least ``NOTICE_MIN`` after the cutoff) are released and their
   legs re-partitioned with the soft-robust objective using OPERATIONS
   probabilities, with no more crews than were released.

Usage::

    uv run python -m robust_crew.repair
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from ortools.sat.python import cp_model

from robust_crew import config
from robust_crew.delay_model import OPS_CUTOFF_LOCAL
from robust_crew.ingest import connect
from robust_crew.optimize import COST_SCALE, NUM_WORKERS, SEED, TIME_LIMIT_S, build_day_problem
from robust_crew.rules import DEFAULT_RULES, CrewRules
from robust_crew.simulate import SimFlight, crew_caused_delay, load_day_flights, replay

log = logging.getLogger(__name__)

TRIGGER_MIN = 15.0  # repair if the crew would delay its next leg by more than this
N_RESERVES = 4  # airport-standby reserve crews at the hub per day
RESERVE_WINDOW = (5, 20)  # local hours a reserve can report (inclusive start, exclusive end)
NOTICE_MIN = 60  # morning re-solve only touches crews reporting this long after the cutoff
LAMBDA_OPS = 8.0  # soft-robust weight for the morning re-solve (pay hours per disrupted leg)


@dataclass
class RepairResult:
    """Replay outcome with repairs, plus counts of what the repair did."""

    sim_arr: dict[str, datetime]
    crew_delay_at: dict[str, float]
    broken_connections: int
    duty_violations: int
    legs_orphaned: int
    swaps: int = 0
    reserves_used: int = 0
    triggers: int = 0
    crews_flown: list[list[str]] = field(default_factory=list)


def replay_with_repair(
    flights: list[SimFlight],
    pairings: list[list[str]],
    rules: CrewRules = DEFAULT_RULES,
    n_reserves: int = N_RESERVES,
    trigger_min: float = TRIGGER_MIN,
    hub: str = config.HUB,
    enabled: bool = True,
) -> RepairResult:
    """Replay one day, repairing crew problems at the hub as they appear.

    With ``enabled=False`` it reproduces ``simulate.replay`` exactly (tested).
    """
    by_id = {f.flight_id: f for f in flights}
    tz = ZoneInfo(rules.base_tz)
    m = timedelta(minutes=1)

    # Each crew's remaining plan (completed flights only) and what it has flown.
    plan: list[list[str]] = [[fid for fid in p if by_id[fid].completed] for p in pairings]
    report: list[datetime] = [rules.report_time(by_id[p[0]].sched_dep) for p in pairings]
    flown: list[list[str]] = [[] for _ in pairings]
    owner: dict[str, int] = {fid: c for c, p in enumerate(plan) for fid in p}
    reserves_left = n_reserves
    swaps = triggers = orphaned = 0

    sim_dep: dict[str, datetime] = {}
    sim_arr: dict[str, datetime] = {}
    crew_delay: dict[str, float] = {}

    def crew_ready(c: int, f: SimFlight) -> datetime | None:
        """When crew c can take flight f (None if it is elsewhere or has flown nothing yet)."""
        if not flown[c]:
            return None
        last = by_id[flown[c][-1]]
        if last.dest != f.origin:
            return None
        return sim_arr[last.flight_id] + rules.min_connect_min(last.tail is not None and last.tail == f.tail) * m

    def legal(c_report: datetime, done: list[str], suffix: list[str], delay_min: float) -> bool:
        """Would a crew that reported at c_report and flew ``done`` stay legal flying ``suffix``?"""
        legs = done + suffix
        if len(legs) > rules.max_legs:
            return False
        if not suffix:
            return True
        # Flown legs use their actual block; future legs only their scheduled block
        # (the dispatcher cannot know future airborne times).
        block = sum(by_id[x].actual_block for x in done) + sum(
            (by_id[x].sched_arr - by_id[x].sched_dep).total_seconds() / 60 for x in suffix)
        if block > rules.max_flight_time_min(c_report):
            return False
        end = by_id[suffix[-1]].sched_arr + delay_min * m
        return (end - c_report).total_seconds() / 60 <= rules.max_fdp_min(c_report, len(legs))

    for f in flights:
        if not f.completed:
            continue
        ready = f.sched_dep + f.own_dep_delay * m
        if f.prev_id is not None and f.prev_id in sim_arr:
            ready = max(ready, sim_arr[f.prev_id] + f.turn_min * m)
        elif f.prev_actual_arr is not None:
            ready = max(ready, f.prev_actual_arr + f.turn_min * m)

        c = owner.get(f.flight_id)
        if c is not None:
            cr = crew_ready(c, f)
            if flown[c] and cr is None:
                orphaned += 1
            suffix = plan[c][plan[c].index(f.flight_id):]
            late = 0.0 if cr is None else max(0.0, (cr - ready).total_seconds() / 60)
            duty_bad = flown[c] and not legal(report[c], flown[c], suffix, late)
            if enabled and f.origin == hub and flown[c] and (late > trigger_min or duty_bad):
                triggers += 1
                best = None  # (delay left on f, kind, other crew)
                for d in range(len(plan)):
                    if d == c or not flown[d]:
                        continue
                    rd = crew_ready(d, f)
                    if rd is None or rd > ready + trigger_min * m:
                        continue
                    nxt = [x for x in plan[d] if x not in sim_dep]
                    if not nxt or by_id[nxt[0]].sched_dep <= f.sched_dep:
                        continue  # d is not idle: its next leg leaves first
                    if not legal(report[d], flown[d], suffix, 0.0):
                        continue
                    if cr is None or cr > by_id[nxt[0]].sched_dep + trigger_min * m:
                        continue  # c could not take d's next leg either
                    if not legal(report[c], flown[c], nxt, late):
                        continue
                    left = max(0.0, (rd - ready).total_seconds() / 60)
                    if best is None or left < best[0]:
                        best = (left, "swap", d, nxt)
                local = ready.astimezone(tz)
                res_report = ready - timedelta(minutes=rules.report_buffer_min)
                if (reserves_left > 0 and RESERVE_WINDOW[0] <= local.hour < RESERVE_WINDOW[1]
                        and legal(res_report, [], suffix, 0.0)
                        and (best is None or best[0] > 0)):
                    best = (0.0, "reserve", None, None)
                if best is not None:
                    _, kind, d, nxt = best
                    if kind == "swap":
                        plan[c] = [x for x in plan[c] if x not in suffix] + nxt
                        plan[d] = [x for x in plan[d] if x not in nxt] + suffix
                        for x in suffix:
                            owner[x] = d
                        for x in nxt:
                            owner[x] = c
                        swaps += 1
                    else:
                        reserves_left -= 1
                        plan[c] = [x for x in plan[c] if x not in suffix]
                        plan.append(list(suffix))
                        report.append(res_report)
                        flown.append([])
                        new = len(plan) - 1
                        for x in suffix:
                            owner[x] = new
                    c = owner[f.flight_id]
                    cr = crew_ready(c, f)

        dep = ready
        if c is not None and cr is not None and cr > dep:
            crew_delay[f.flight_id] = (cr - dep).total_seconds() / 60
            dep = cr
        sim_dep[f.flight_id] = dep
        sim_arr[f.flight_id] = dep + f.actual_block * m
        if c is not None:
            flown[c].append(f.flight_id)

    broken = duty = 0
    for c, legs in enumerate(flown):
        for a, b in zip(legs, legs[1:]):
            fa, fb = by_id[a], by_id[b]
            if fa.dest != fb.origin:
                continue
            need = rules.min_connect_min(fa.tail is not None and fa.tail == fb.tail)
            if sim_arr[a] + need * m > fb.sched_dep:
                broken += 1
        if legs:
            fdp = (sim_arr[legs[-1]] - report[c]).total_seconds() / 60
            if fdp > rules.max_fdp_min(report[c], len(legs)):
                duty += 1
    return RepairResult(sim_arr, crew_delay, broken, duty, orphaned, swaps,
                        n_reserves - reserves_left, triggers, [x for x in flown if x])


# --- Morning re-solve ----------------------------------------------------------------

def morning_resolve(
    prob, p_ops: dict, pairings: list[list[str]], lam: float = LAMBDA_OPS
) -> tuple[list[list[str]], dict]:
    """Re-partition not-yet-reported crews' legs with OPERATIONS probabilities.

    ``prob`` is the day's ``optimize.DayProblem``; ``p_ops`` maps
    (i_id, j_id) to the operations-model break probability. Keeps every pairing whose report time is before cutoff + notice; the rest
    are released and their legs re-covered exactly once by enumerated
    pairings, minimizing cost + lam * risk_ops, using no more crews than were
    released. Falls back to the original pairings if no cover is found.
    """
    rules = DEFAULT_RULES
    day = prob.day
    tz = ZoneInfo(rules.base_tz)
    hh, mm = map(int, OPS_CUTOFF_LOCAL.split(":"))
    cutoff = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz) + timedelta(minutes=NOTICE_MIN)
    leg_of = {leg.flight_id: leg for leg in prob.legs}
    keep, released = [], []
    for p in pairings:
        (released if rules.report_time(leg_of[p[0]].dep) >= cutoff else keep).append(p)
    open_legs = {fid for p in released for fid in p}
    info = {"released": len(released), "kept": len(keep)}
    if not released:
        return pairings, {**info, "status": "NOTHING_TO_RESOLVE"}

    cands = []
    for pr in prob.pairings:
        ids = [prob.legs[i].flight_id for i in pr.legs]
        if not all(x in open_legs for x in ids):
            continue
        if rules.report_time(prob.legs[pr.legs[0]].dep) < cutoff:
            continue
        n = len(ids)
        risk = sum(p_ops[(ids[k], ids[k + 1])] * (n - 1 - k) for k in range(n - 1))
        cands.append((ids, pr.cost, risk))

    model = cp_model.CpModel()
    x = [model.NewBoolVar("") for _ in cands]
    rows: dict[str, list] = {fid: [] for fid in open_legs}
    for k, (ids, _, _) in enumerate(cands):
        for fid in ids:
            rows[fid].append(x[k])
    if any(not v for v in rows.values()):
        return pairings, {**info, "status": "NO_COVER"}
    for v in rows.values():
        model.AddExactlyOne(v)
    model.Add(sum(x) <= len(released))
    model.Minimize(sum(round(COST_SCALE * (c + lam * r)) * x[k] for k, (_, c, r) in enumerate(cands)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = TIME_LIMIT_S
    solver.parameters.random_seed = SEED
    solver.parameters.num_workers = NUM_WORKERS
    st = solver.Solve(model)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return pairings, {**info, "status": solver.StatusName(st)}
    new = [cands[k][0] for k in range(len(cands)) if solver.Value(x[k])]
    return keep + new, {**info, "status": solver.StatusName(st), "new_crews": len(new)}


# --- Experiment ----------------------------------------------------------------------

def variants_to_run(plan: pd.DataFrame) -> pd.DataFrame:
    """Planner variants compared with and without repair: baseline, every soft lambda, hard 0.5/0.4."""
    keep = (plan.planner == "baseline") | (plan.planner == "soft") | (
        (plan.planner == "hard") & plan.param.isin([0.5, 0.4]))
    return plan[keep & plan.status.isin(["OPTIMAL", "FEASIBLE"])]


def run() -> pd.DataFrame:
    """Every evaluated schedule under: no repair, reactive, morning, morning + reactive."""
    con = connect()
    plan = con.execute("SELECT date, planner, param, status, crews, cost FROM plan_log").df()
    sched = con.execute("SELECT * FROM schedules ORDER BY date, planner, param, crew, pos").df()
    sched["day"] = pd.to_datetime(sched.date).dt.date
    rows = []
    for day, plans_day in variants_to_run(plan).groupby("date"):
        day = pd.Timestamp(day).date()
        flights = load_day_flights(con, day)
        base = replay(flights, None)
        prob = build_day_problem(con, day)
        p_ops = {(a, b): p for a, b, p in con.execute(
            "SELECT i_id, j_id, p_ops FROM conn_risk WHERE FlightDate = ?", [day]).fetchall()}
        s_day = sched[sched.day == day]
        for _, pr in plans_day.iterrows():
            key = (s_day.planner == pr.planner) & (
                s_day.param.isna() if pd.isna(pr.param) else (s_day.param == pr.param))
            pairings = [list(g.flight_id) for _, g in s_day[key].groupby("crew", sort=True)]
            morning, minfo = morning_resolve(prob, p_ops, pairings)
            for mode, ps, rep in [("none", pairings, False), ("reactive", pairings, True),
                                  ("morning", morning, False), ("morning+reactive", morning, True)]:
                if rep:
                    r = replay_with_repair(flights, ps)
                    extra = {"swaps": r.swaps, "reserves_used": r.reserves_used, "triggers": r.triggers}
                else:
                    r = replay(flights, ps)
                    extra = {"swaps": 0, "reserves_used": 0, "triggers": 0}
                rows.append({
                    "date": day, "split": config.split_of(day), "planner": pr.planner, "param": pr.param,
                    "repair": mode, "crews_planned": pr.crews, "cost": pr.cost,
                    "crews_used": pr.crews + extra["reserves_used"],
                    "crew_delay_min": round(crew_caused_delay(r, base), 1),
                    "broken_connections": r.broken_connections, "duty_violations": r.duty_violations,
                    "legs_orphaned": r.legs_orphaned, **extra,
                    "morning_status": minfo.get("status") if mode.startswith("morning") else None,
                    "morning_released": minfo.get("released") if mode.startswith("morning") else None,
                })
        log.info("repair %s", day)
    con.close()
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    """Mean metrics per split, planner variant and repair mode."""
    metrics = ["crews_used", "crew_delay_min", "broken_connections", "duty_violations",
               "swaps", "reserves_used", "triggers"]
    out: dict = {}
    for split, g in df.groupby("split", sort=False):
        rows = []
        for (planner, param, mode), h in g.groupby(["planner", "param", "repair"], dropna=False, sort=False):
            rows.append({"planner": planner, "param": None if pd.isna(param) else float(param),
                         "repair": mode, "days": int(len(h)),
                         **{k: round(float(h[k].mean()), 2) for k in metrics}})
        out[split] = rows
    return out


def main() -> None:
    """Run the repair comparison; write ``repair_results`` and reports/phase7_repair.json."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = run()
    con = connect()
    con.execute("DROP TABLE IF EXISTS repair_results")
    con.register("df", df)
    con.execute("CREATE TABLE repair_results AS SELECT * FROM df")
    con.unregister("df")
    con.close()
    report = {
        "trigger_min": TRIGGER_MIN, "n_reserves": N_RESERVES, "reserve_window_local": RESERVE_WINDOW,
        "ops_cutoff_local": OPS_CUTOFF_LOCAL, "notice_min": NOTICE_MIN, "lambda_ops": LAMBDA_OPS,
        "summary": summarize(df),
    }
    (config.REPORTS_DIR / "phase7_repair.json").write_text(json.dumps(report, indent=2, default=str))
    print("wrote reports/phase7_repair.json")


if __name__ == "__main__":
    main()
