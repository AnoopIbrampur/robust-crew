"""Build the small demo database the hosted Crew Explorer runs on.

The full database (hundreds of MB) holds 24 months of every carrier. The
explorer only needs American Airlines flights on the test days (plus the day
before each, for aircraft rotations), the cheapest and soft-robust schedules
for those days, the plan log and the per-day replay totals. This writes them
to ``app/demo.duckdb`` (about 14 MB, committed to git) so the app can be
deployed without running the pipeline.

Usage::

    uv run python -m robust_crew.demo_extract
"""

from __future__ import annotations

import duckdb

from robust_crew import config

FLIGHT_COLUMNS = [
    "flight_id", "Reporting_Airline", "FlightDate", "Origin", "Dest", "Tail_Number",
    "sched_dep_utc", "sched_arr_utc", "actual_arr_utc", "DepDelay", "LateAircraftDelay",
    "ActualElapsedTime", "CRSElapsedTime", "Cancelled", "Diverted",
]


def build(src: str = str(config.DB_PATH), dst: str = str(config.DEMO_DB_PATH)) -> dict[str, int]:
    """Copy the explorer's tables for the test windows into ``dst``; return row counts."""
    config.DEMO_DB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(dst)
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{src}' AS src_db (READ_ONLY)")
    windows = " OR ".join(
        f"(d BETWEEN DATE '{lo}' AND DATE '{hi}')" for lo, hi in config.TEST_WINDOWS.values())
    test_days = f"(SELECT DISTINCT date AS d FROM src_db.plan_log) WHERE {windows}"
    con.execute(f"CREATE TEMP TABLE days AS SELECT d FROM {test_days}")
    cols = ", ".join(FLIGHT_COLUMNS)
    con.execute(f"""
        CREATE TABLE flights AS SELECT {cols} FROM src_db.flights
        WHERE Reporting_Airline = '{config.CARRIER}'
          AND (FlightDate IN (SELECT d FROM days) OR FlightDate + 1 IN (SELECT d FROM days))
        ORDER BY FlightDate, sched_dep_utc""")
    con.execute("""CREATE TABLE schedules AS SELECT * FROM src_db.schedules
                   WHERE date IN (SELECT d FROM days) AND planner IN ('baseline', 'soft')""")
    con.execute("CREATE TABLE plan_log AS SELECT * FROM src_db.plan_log WHERE date IN (SELECT d FROM days)")
    con.execute("CREATE TABLE replay_results AS SELECT * FROM src_db.replay_results WHERE date IN (SELECT d FROM days)")
    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ["flights", "schedules", "plan_log", "replay_results"]}
    con.execute("DETACH src_db")
    con.close()
    return counts


def main() -> None:
    """Write app/demo.duckdb and print its row counts and size."""
    counts = build()
    print(counts, f"{config.DEMO_DB_PATH.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
