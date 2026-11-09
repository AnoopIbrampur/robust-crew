"""Flight legs, the crew connection graph, and aircraft rotations.

Planning flight set for a day: the carrier's flights with that local
scheduled departure date that depart from or arrive at the hub. Spoke-to-spoke
flights belong to other crew bases and are excluded. Cancelled flights are
KEPT: a planner does not know in advance which flights will be cancelled; the
replay simulator drops them on the day.

Aircraft rotations use BTS ``Tail_Number``, which records the aircraft that
actually flew, including day-of-operations swaps. No public source has the
planned tail assignment, so the same-tail flag is slightly optimistic about
what a planner could know. This is a documented limitation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from robust_crew import config
from robust_crew.models import Leg
from robust_crew.rules import DEFAULT_RULES, CrewRules, connection_violation, same_tail


@dataclass(frozen=True)
class Connection:
    """A feasible crew connection: leg ``i`` then leg ``j`` (indices into a leg list)."""

    i: int
    j: int
    gap_min: float
    same_tail: bool


def load_day_legs(
    con: duckdb.DuckDBPyConnection,
    day: date,
    carrier: str = config.CARRIER,
    hub: str = config.HUB,
) -> list[Leg]:
    """Planning legs for one local date, sorted by scheduled departure.

    Drops (and does not plan) flights whose BTS scheduled times are internally
    inconsistent (``arr_clock_ok`` false; 7 such flights in 2022-2023).
    """
    rows = con.execute(
        """SELECT flight_id, Origin, Dest, sched_dep_utc, sched_arr_utc, Tail_Number
           FROM flights
           WHERE Reporting_Airline = ? AND FlightDate = ? AND (Origin = ? OR Dest = ?)
             AND arr_clock_ok
           ORDER BY sched_dep_utc, flight_id""",
        [carrier, day, hub, hub],
    ).fetchall()
    return [Leg(fid, o, d, dep, arr, tail) for fid, o, d, dep, arr, tail in rows]


def build_connection_graph(legs: list[Leg], rules: CrewRules = DEFAULT_RULES) -> list[Connection]:
    """All crew connections i -> j allowed by the rules.

    An edge exists when dest(i) == origin(j) and the scheduled gap is at least
    the minimum connection (30 min same aircraft, 45 min aircraft change) and
    at most the maximum sit. ``legs`` must be sorted by departure.
    """
    by_origin: dict[str, list[int]] = {}
    for idx, leg in enumerate(legs):
        by_origin.setdefault(leg.origin, []).append(idx)
    edges: list[Connection] = []
    for i, a in enumerate(legs):
        for j in by_origin.get(a.dest, []):
            b = legs[j]
            if b.dep <= a.arr:
                continue
            if connection_violation(a, b, rules) is None:
                edges.append(Connection(i, j, (b.dep - a.arr).total_seconds() / 60, same_tail(a, b)))
    return edges


def build_rotations_table(con: duckdb.DuckDBPyConnection, carrier: str = config.CARRIER) -> int:
    """Create ``aircraft_rotations``: each flight's previous and next flight on the same tail.

    Uses the carrier's whole network (a tail's previous leg may not touch the
    hub) and runs across midnight, so the first flight of a day links to the
    tail's last flight of the previous night. Cancelled flights are excluded:
    they did not move the aircraft.
    """
    con.execute("DROP TABLE IF EXISTS aircraft_rotations")
    con.execute(
        """CREATE TABLE aircraft_rotations AS
           WITH f AS (
             SELECT flight_id, FlightDate, Tail_Number AS tail, Origin, Dest,
                    sched_dep_utc, sched_arr_utc, actual_dep_utc, actual_arr_utc
             FROM flights
             WHERE Reporting_Airline = ? AND NOT Cancelled AND Tail_Number IS NOT NULL
           )
           SELECT flight_id, FlightDate, tail, Origin, Dest,
                  lag(flight_id) OVER w AS prev_flight_id,
                  lag(Dest) OVER w AS prev_dest,
                  lag(sched_arr_utc) OVER w AS prev_sched_arr_utc,
                  lag(actual_arr_utc) OVER w AS prev_actual_arr_utc,
                  lead(flight_id) OVER w AS next_flight_id,
                  date_diff('minute', lag(sched_arr_utc) OVER w, sched_dep_utc) AS sched_turn_min,
                  (lag(Dest) OVER w = Origin) AS prev_dest_matches
           FROM f
           WINDOW w AS (PARTITION BY tail ORDER BY sched_dep_utc, flight_id)""",
        [carrier],
    )
    return con.execute("SELECT count(*) FROM aircraft_rotations").fetchall()[0][0]


def rotation_summary(con: duckdb.DuckDBPyConnection) -> dict:
    """Sanity numbers for the rotations table (continuity and turn times)."""
    r = con.execute(
        """SELECT count(*) AS flights,
                  count(DISTINCT tail) AS tails,
                  avg((prev_flight_id IS NULL)::INT) AS first_leg_share,
                  avg((NOT prev_dest_matches)::INT) FILTER (WHERE prev_flight_id IS NOT NULL) AS discontinuity_share,
                  median(sched_turn_min) FILTER (WHERE sched_turn_min < 600) AS median_sched_turn_min,
                  avg((sched_turn_min < 0)::INT) FILTER (WHERE prev_flight_id IS NOT NULL) AS overlap_share
           FROM aircraft_rotations"""
    ).fetchall()[0]
    keys = ["flights", "tails", "first_leg_share", "discontinuity_share", "median_sched_turn_min", "overlap_share"]
    return dict(zip(keys, r))


def main() -> None:
    """Build ``aircraft_rotations`` and write reports/phase2_network.json."""
    import json

    from robust_crew.ingest import connect

    con = connect()
    build_rotations_table(con)
    summary: dict = {"rotations": rotation_summary(con), "sample_days": []}
    for d in [date(2022, 11, 16), date(2023, 3, 8)]:  # train-window days only
        legs = load_day_legs(con, d)
        edges = build_connection_graph(legs)
        summary["sample_days"].append({
            "date": str(d),
            "legs": len(legs),
            "hub_departures": sum(leg.origin == config.HUB for leg in legs),
            "connections": len(edges),
            "same_tail_connections": sum(e.same_tail for e in edges),
        })
    con.close()
    config.REPORTS_DIR.mkdir(exist_ok=True)
    out = config.REPORTS_DIR / "phase2_network.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(out.read_text())


if __name__ == "__main__":
    main()
