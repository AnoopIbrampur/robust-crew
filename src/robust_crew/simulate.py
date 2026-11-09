"""Replay the real day through a crew schedule.

For each test day we re-fly the carrier's whole network (all its flights that
day, so aircraft delays propagate realistically) in scheduled-departure
order. A flight departs at

    max(scheduled departure + own departure delay,
        aircraft ready = previous leg of the tail's simulated arrival + turn,
        crew ready     = previous leg of the pairing's simulated arrival + min connection)

and arrives one actual block time later.

Own delay. BTS departure delays already include delay that the real airline
propagated from late inbound aircraft (``LateAircraftDelay``). Replaying them
as-is and adding our own propagation would double count, so a flight's own
departure delay is ``max(0, DepDelay - LateAircraftDelay)``; aircraft
propagation is then re-created by the simulator. (BTS fills cause columns only
for flights 15+ min late; otherwise LateAircraftDelay is taken as 0.) Real crew
delays sit inside ``CarrierDelay`` and cannot be separated; they stay in the
own delay, which is identical for every planner, so comparisons are fair.
En-route variation (taxi, airborne) is kept via ``ActualElapsedTime``.

Turn time. Aircraft ready uses ``min(MIN_TURN_MIN, scheduled turn)`` so that
a schedule with a short planned turn is not penalised by the simulator. If a
tail's previous leg ended elsewhere (data discontinuity) or is the first leg
of the dataset, there is no aircraft constraint. A previous leg on an earlier
date uses its actual arrival.

Cancelled and diverted flights are dropped from the plan for that day (the
real airline did not complete them). A pairing then continues with its next
completed leg; if the dropped leg breaks the station chain, the next leg gets
no crew constraint (a reserve or repositioned crew is assumed) and is counted
in ``legs_orphaned``.

Crew-caused propagated delay is measured against a counterfactual replay with
no crew constraints (aircraft and own delays only): the sum over all flights
of (arrival with crews - arrival without crews). It includes knock-on delay to
later flights of the same aircraft.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import duckdb
import pandas as pd

from robust_crew import config
from robust_crew.ingest import connect
from robust_crew.rules import DEFAULT_RULES, CrewRules

log = logging.getLogger(__name__)

MIN_TURN_MIN = 30.0  # minimum aircraft turn used by the simulator


@dataclass
class SimFlight:
    """One flight's inputs to the replay."""

    flight_id: str
    origin: str
    dest: str
    tail: str | None
    sched_dep: datetime
    sched_arr: datetime
    own_dep_delay: float  # minutes, >= 0
    actual_block: float  # minutes
    completed: bool  # False if cancelled or diverted
    prev_id: str | None = None  # previous leg of the same tail (same day), if any
    prev_actual_arr: datetime | None = None  # used when the previous leg is on an earlier date
    turn_min: float = MIN_TURN_MIN


def load_day_flights(con: duckdb.DuckDBPyConnection, day: date) -> list[SimFlight]:
    """All of the carrier's flights that day, sorted by scheduled departure."""
    rows = con.execute(
        """
        WITH day AS (
          SELECT * FROM flights WHERE Reporting_Airline = ? AND FlightDate = ?
        ),
        tailprev AS (  -- previous non-cancelled leg of the tail by schedule, any date
          SELECT f.flight_id, f.Tail_Number,
                 lag(f.flight_id) OVER w AS prev_id, lag(f.Dest) OVER w AS prev_dest,
                 lag(f.FlightDate) OVER w AS prev_date, lag(f.actual_arr_utc) OVER w AS prev_actual_arr,
                 lag(f.sched_arr_utc) OVER w AS prev_sched_arr
          FROM flights f
          WHERE f.Reporting_Airline = ? AND f.FlightDate BETWEEN ? - 1 AND ?
            AND f.Tail_Number IS NOT NULL AND NOT f.Cancelled
          WINDOW w AS (PARTITION BY f.Tail_Number ORDER BY f.sched_dep_utc, f.flight_id)
        )
        SELECT d.flight_id, d.Origin, d.Dest, d.Tail_Number, d.sched_dep_utc, d.sched_arr_utc,
               greatest(0, coalesce(d.DepDelay, 0) - coalesce(d.LateAircraftDelay, 0)) AS own_dep,
               coalesce(d.ActualElapsedTime, d.CRSElapsedTime) AS actual_block,
               NOT (d.Cancelled OR d.Diverted) AS completed,
               CASE WHEN tp.prev_dest = d.Origin THEN tp.prev_id END AS prev_id,
               CASE WHEN tp.prev_dest = d.Origin THEN tp.prev_date END AS prev_date,
               CASE WHEN tp.prev_dest = d.Origin THEN tp.prev_actual_arr END AS prev_actual_arr,
               date_diff('minute', tp.prev_sched_arr, d.sched_dep_utc) AS sched_turn
        FROM day d LEFT JOIN tailprev tp ON tp.flight_id = d.flight_id
        ORDER BY d.sched_dep_utc, d.flight_id
        """,
        [config.CARRIER, day, config.CARRIER, day, day],
    ).fetchall()
    out = []
    for (fid, o, de, tail, sdep, sarr, own, block, done, prev_id, prev_date, prev_arr, turn) in rows:
        same_day = prev_id is not None and prev_date == day
        out.append(SimFlight(
            fid, o, de, tail, sdep, sarr, float(own), float(block), bool(done),
            prev_id=prev_id if same_day else None,
            prev_actual_arr=prev_arr if (prev_id is not None and not same_day) else None,
            turn_min=min(MIN_TURN_MIN, max(float(turn), 0.0)) if turn is not None else MIN_TURN_MIN,
        ))
    return out


@dataclass
class ReplayResult:
    """Per-flight simulated times plus schedule-level metrics."""

    sim_dep: dict[str, datetime]
    sim_arr: dict[str, datetime]
    crew_delay_at: dict[str, float]  # minutes the crew constraint added at each flight
    broken_connections: int
    duty_violations: int
    legs_orphaned: int


def replay(
    flights: list[SimFlight],
    pairings: list[list[str]] | None,
    rules: CrewRules = DEFAULT_RULES,
) -> ReplayResult:
    """Replay one day. ``pairings`` lists each crew's flight ids in order (None: no crews)."""
    by_id = {f.flight_id: f for f in flights}
    crew_prev: dict[str, str] = {}  # flight -> previous completed leg of the same crew
    orphaned = 0
    if pairings:
        for p in pairings:
            done = [fid for fid in p if by_id[fid].completed]
            for a, b in zip(done, done[1:]):
                if by_id[a].dest == by_id[b].origin:
                    crew_prev[b] = a
                else:
                    orphaned += 1

    m = timedelta(minutes=1)
    sim_dep: dict[str, datetime] = {}
    sim_arr: dict[str, datetime] = {}
    crew_delay: dict[str, float] = {}
    for f in flights:
        if not f.completed:
            continue
        ready = f.sched_dep + f.own_dep_delay * m
        if f.prev_id is not None and f.prev_id in sim_arr:
            ready = max(ready, sim_arr[f.prev_id] + f.turn_min * m)
        elif f.prev_actual_arr is not None:
            ready = max(ready, f.prev_actual_arr + f.turn_min * m)
        dep = ready
        if f.flight_id in crew_prev:
            a = by_id[crew_prev[f.flight_id]]
            need = rules.min_connect_min(a.tail is not None and a.tail == f.tail)
            crew_ready = sim_arr[a.flight_id] + need * m
            if crew_ready > dep:
                crew_delay[f.flight_id] = (crew_ready - dep).total_seconds() / 60
                dep = crew_ready
        sim_dep[f.flight_id] = dep
        sim_arr[f.flight_id] = dep + f.actual_block * m

    broken = duty = 0
    if pairings:
        for p in pairings:
            done = [fid for fid in p if by_id[fid].completed]
            for a, b in zip(done, done[1:]):
                if crew_prev.get(b) != a:
                    continue
                fa, fb = by_id[a], by_id[b]
                need = rules.min_connect_min(fa.tail is not None and fa.tail == fb.tail)
                if sim_arr[a] + need * m > fb.sched_dep:
                    broken += 1
            if done:
                report = rules.report_time(by_id[p[0]].sched_dep)
                fdp = (sim_arr[done[-1]] - report).total_seconds() / 60
                if fdp > rules.max_fdp_min(report, len(p)):
                    duty += 1
    return ReplayResult(sim_dep, sim_arr, crew_delay, broken, duty, orphaned)


def crew_caused_delay(with_crew: ReplayResult, without: ReplayResult) -> float:
    """Total arrival delay (minutes) caused by crew constraints, knock-on included."""
    return sum((t - without.sim_arr[f]).total_seconds() / 60 for f, t in with_crew.sim_arr.items())


# --- Running all schedules -----------------------------------------------------------

def run_replays() -> tuple[pd.DataFrame, dict]:
    """Replay every stored schedule; return per-schedule metrics and a validation summary."""
    con = connect()
    plan = con.execute(
        "SELECT date, planner, param, status, crews, cost, exp_breaks, exempt_legs FROM plan_log"
    ).df()
    sched = con.execute("SELECT * FROM schedules ORDER BY date, planner, param, crew, pos").df()
    rows, valid = [], []
    for day, plans_day in plan.groupby("date"):
        day = pd.Timestamp(day).date()
        flights = load_day_flights(con, day)
        base = replay(flights, None)
        valid.append(_validation_rows(con, day, base))
        s_day = sched[pd.to_datetime(sched.date).dt.date == day]
        for _, pr in plans_day.iterrows():
            key = (s_day.planner == pr.planner) & (
                s_day.param.isna() if pd.isna(pr.param) else (s_day.param == pr.param))
            g = s_day[key]
            pairings = [list(x.flight_id) for _, x in g.groupby("crew", sort=True)]
            row = {"date": day, "split": config.split_of(day), "planner": pr.planner,
                   "param": pr.param, "status": pr.status, "crews": pr.crews, "cost": pr.cost,
                   "exp_breaks": pr.exp_breaks, "exempt_legs": pr.exempt_legs}
            if pairings:
                res = replay(flights, pairings)
                planned = {fid for p in pairings for fid in p}
                row.update({
                    "crew_delay_min": round(crew_caused_delay(res, base), 1),
                    "crew_delay_direct_min": round(sum(res.crew_delay_at.values()), 1),
                    "flights_crew_delayed": sum(v > 0 for v in res.crew_delay_at.values()),
                    "broken_connections": res.broken_connections,
                    "duty_violations": res.duty_violations,
                    "legs_orphaned": res.legs_orphaned,
                    "planned_flights": len(planned),
                    "planned_flights_completed": sum(1 for f in flights if f.flight_id in planned and f.completed),
                })
            rows.append(row)
        log.info("replayed %s", day)
    con.close()
    v = pd.concat(valid)
    validation = {
        "flights": int(len(v)),
        "mean_actual_arr_delay": round(float(v.actual.mean()), 2),
        "mean_sim_arr_delay_no_crew": round(float(v.sim.mean()), 2),
        "mae_sim_vs_actual": round(float((v.sim - v.actual).abs().mean()), 2),
        "corr_sim_vs_actual": round(float(v[["sim", "actual"]].corr().iloc[0, 1]), 4),
        "note": "No-crew replay vs real arrival delays; real delays also contain crew effects, "
                "so the simulator should come close but slightly under.",
    }
    return pd.DataFrame(rows), validation


def _validation_rows(con, day: date, base: ReplayResult) -> pd.DataFrame:
    df = con.execute(
        """SELECT flight_id, sched_arr_utc, ArrDelay FROM flights
           WHERE Reporting_Airline = ? AND FlightDate = ? AND NOT Cancelled AND NOT Diverted""",
        [config.CARRIER, day]).df()
    df["sim"] = [(base.sim_arr[f] - pd.Timestamp(t).to_pydatetime()).total_seconds() / 60
                 for f, t in zip(df.flight_id, df.sched_arr_utc)]
    return df.rename(columns={"ArrDelay": "actual"})[["sim", "actual"]]


def summarize(results: pd.DataFrame) -> dict:
    """Mean metrics per split and planner variant, plus hard-vs-baseline on paired days."""
    metrics = ["crews", "cost", "crew_delay_min", "broken_connections", "duty_violations",
               "flights_crew_delayed", "legs_orphaned"]
    ok = results[results.crew_delay_min.notna()]
    out: dict = {}
    for split, g in ok.groupby("split", sort=False):
        rows = []
        for (planner, param), h in g.groupby(["planner", "param"], dropna=False, sort=False):
            r = {"planner": planner, "param": None if pd.isna(param) else float(param), "days": int(len(h))}
            r.update({m: round(float(h[m].mean()), 2) for m in metrics})
            if planner == "hard":  # paired comparison: baseline on the same days
                b = g[(g.planner == "baseline") & g.date.isin(h.date)]
                r["paired_baseline"] = {m: round(float(b[m].mean()), 2) for m in metrics}
            rows.append(r)
        out[split] = rows
    return out


def main() -> None:
    """Replay all schedules; write the ``replay_results`` table and reports/phase6_replay.json."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results, validation = run_replays()
    con = connect()
    con.execute("DROP TABLE IF EXISTS replay_results")
    con.register("df", results)
    con.execute("CREATE TABLE replay_results AS SELECT * FROM df")
    con.unregister("df")
    con.close()
    report = {"min_turn_min": MIN_TURN_MIN, "simulator_validation": validation, "summary": summarize(results)}
    out = config.REPORTS_DIR / "phase6_replay.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["simulator_validation"], indent=2))


if __name__ == "__main__":
    main()
