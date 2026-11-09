"""Phase 1 data-quality report.

Writes reports/phase1_summary.json (every number), reports/phase1_data_quality.md
(the same numbers as tables) and reports/figures/daily_hub_flights.png.

Usage::

    uv run python -m robust_crew.quality
"""

from __future__ import annotations

import json
from datetime import date

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from robust_crew import config  # noqa: E402

KEY_COLUMNS = [
    "Tail_Number", "CRSDepTime", "CRSArrTime", "CRSElapsedTime", "DepTime", "DepDelay",
    "ArrTime", "ArrDelay", "TaxiOut", "TaxiIn", "WheelsOff", "WheelsOn", "AirTime",
    "Distance", "CarrierDelay", "LateAircraftDelay",
]
WEATHER_COLUMNS = ["tmpf", "sknt", "gust", "vsby", "p01i", "skyc1", "ceiling_ft", "wxcodes"]


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(con, sql, params=None):
    return con.execute(sql, params or []).fetchall()[0][0]


def collect(con: duckdb.DuckDBPyConnection) -> dict:
    """Compute every Phase 1 quality number from the database."""
    c, h = config.CARRIER, config.HUB
    s: dict = {"carrier": c, "hub": h, "splits": {
        "train": [str(config.TRAIN_START), str(config.TRAIN_END)],
        "calib": [str(config.CALIB_START), str(config.CALIB_END)],
        "test": [str(config.TEST_START), str(config.TEST_END)],
    }}

    log = _rows(con, "SELECT * FROM ingest_log WHERE source='bts' ORDER BY year, month")
    s["ingest"] = {
        "months_loaded": len(log),
        "months_expected": len(config.MONTHS),
        "raw_rows_total": sum(r["raw_rows"] for r in log),
        "kept_rows_total": sum(r["kept_rows"] for r in log),
        "parity_failures": [f'{r["year"]}-{r["month"]:02d}' for r in log if r["raw_rows"] != r["file_lines"]],
    }

    s["flights"] = {
        "rows": _one(con, "SELECT count(*) FROM flights"),
        "date_min": str(_one(con, "SELECT min(FlightDate) FROM flights")),
        "date_max": str(_one(con, "SELECT max(FlightDate) FROM flights")),
        "carrier_rows": _one(con, "SELECT count(*) FROM flights WHERE Reporting_Airline=?", [c]),
        "carrier_hub_rows": _one(con, "SELECT count(*) FROM flights WHERE Reporting_Airline=? AND (Origin=? OR Dest=?)", [c, h, h]),
        "other_carrier_hub_rows": _one(con, "SELECT count(*) FROM flights WHERE Reporting_Airline<>?", [c]),
        "duplicate_flight_ids": _one(con, "SELECT count(*) - count(DISTINCT flight_id) FROM flights"),
        "unknown_origin_tz": _one(con, "SELECT count(*) FROM flights WHERE origin_tz IS NULL"),
        "unknown_dest_tz": _one(con, "SELECT count(*) FROM flights WHERE dest_tz IS NULL"),
        "arr_clock_mismatch": _one(con, "SELECT count(*) FROM flights WHERE NOT arr_clock_ok"),
    }

    s["daily_hub_departures"] = _rows(con, """
        WITH d AS (
          SELECT FlightDate, split, count(*) AS deps, sum(Cancelled::INT) AS canc
          FROM flights WHERE Reporting_Airline=? AND Origin=? GROUP BY 1, 2)
        SELECT split, count(*) AS days, round(avg(deps), 1) AS mean, min(deps) AS min,
               max(deps) AS max, round(100.0 * sum(canc) / sum(deps), 2) AS cancelled_pct
        FROM d GROUP BY split ORDER BY min(FlightDate)""", [c, h])

    s["outcomes_by_split"] = _rows(con, """
        SELECT split, count(*) AS flights,
               round(100 * avg(Cancelled::INT), 2) AS cancelled_pct,
               round(100 * avg(Diverted::INT), 3) AS diverted_pct,
               round(avg(DepDelay), 1) AS mean_dep_delay_min,
               round(100 * avg((ArrDelay >= 15)::INT), 1) AS arr_del15_pct
        FROM flights WHERE Reporting_Airline=? AND (Origin=? OR Dest=?)
        GROUP BY split ORDER BY min(FlightDate)""", [c, h, h])

    # Missing-value rates on the carrier's hub flights. Actual-time columns are
    # structurally null for cancelled (and partly diverted) flights, so they are
    # also reported over operated, non-diverted flights only.
    miss = {}
    for col in KEY_COLUMNS:
        r = _rows(con, f"""
            SELECT round(100 * avg(("{col}" IS NULL)::INT), 3) AS all_flights,
                   round(100 * avg(("{col}" IS NULL)::INT) FILTER (WHERE NOT Cancelled AND NOT Diverted), 3) AS operated
            FROM flights WHERE Reporting_Airline=? AND (Origin=? OR Dest=?)""", [c, h, h])[0]
        miss[col] = r
    s["missing_pct_carrier_hub"] = miss
    s["delay_cause_note"] = (
        "BTS fills delay-cause columns only for flights arriving 15+ minutes late; "
        "nulls elsewhere are expected."
    )

    if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='weather_obs'").fetchall()[0][0]:
        stations = _rows(con, """
            SELECT station, count(*) AS obs, min(valid_utc)::DATE AS first, max(valid_utc)::DATE AS last,
                   count(DISTINCT date_trunc('hour', valid_utc)) AS hours_covered
            FROM weather_obs GROUP BY station ORDER BY obs DESC""")
        total_hours = ((config.DATA_END - config.DATA_START).days + 1) * 24
        for r in stations:
            r["hour_coverage_pct"] = round(100 * r["hours_covered"] / total_hours, 2)
            r["first"], r["last"] = str(r["first"]), str(r["last"])
        wmiss = _rows(con, "SELECT " + ", ".join(
            f'round(100 * avg(("{col}" IS NULL)::INT), 2) AS "{col}"' for col in WEATHER_COLUMNS
        ) + " FROM weather_obs")[0]
        s["weather"] = {
            "rows": sum(r["obs"] for r in stations),
            "stations": stations,
            "missing_pct": wmiss,
            "missing_note": "gust, ceiling_ft and wxcodes are null when no gust / no ceiling / no weather is reported",
        }
    return s


def plot_daily(con: duckdb.DuckDBPyConnection) -> str:
    """Plot daily scheduled hub departures and arrivals for the carrier."""
    rows = con.execute("""
        SELECT FlightDate,
               count(*) FILTER (WHERE Origin=?) AS deps,
               count(*) FILTER (WHERE Dest=?) AS arrs,
               count(*) FILTER (WHERE Origin=? AND Cancelled) AS canc
        FROM flights WHERE Reporting_Airline=? AND (Origin=? OR Dest=?)
        GROUP BY 1 ORDER BY 1""", [config.HUB] * 3 + [config.CARRIER] + [config.HUB] * 2).fetchall()
    days = [r[0] for r in rows]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(days, [r[1] for r in rows], lw=0.9, label="scheduled departures")
    ax.plot(days, [r[2] for r in rows], lw=0.9, alpha=0.7, label="scheduled arrivals")
    ax.bar(days, [r[3] for r in rows], width=1.0, color="tab:red", label="cancelled departures")
    for name, a, b, col in [("calib", config.CALIB_START, config.CALIB_END, "tab:orange"),
                            ("test", config.TEST_START, config.TEST_END, "tab:green")]:
        ax.axvspan(a, b, color=col, alpha=0.12, label=f"{name} window")
    ax.set_title(f"{config.CARRIER} daily flights at {config.HUB}")
    ax.set_ylabel("flights per day")
    ax.legend(loc="lower left", fontsize=8, ncol=3)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out = config.FIGURES_DIR / "daily_hub_flights.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return str(out.relative_to(config.ROOT))


def _md_table(rows: list[dict]) -> str:
    if not rows:
        return "_none_\n"
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def write_markdown(s: dict, fig: str) -> str:
    """Render the summary as Markdown."""
    f, i = s["flights"], s["ingest"]
    md = [f"# Phase 1 data quality: {s['carrier']} at {s['hub']}\n",
          "_Generated by `python -m robust_crew.quality`; do not edit by hand._\n",
          "## Ingest\n",
          f"- BTS months loaded: {i['months_loaded']} of {i['months_expected']}",
          f"- Raw rows read: {i['raw_rows_total']:,}; rows kept ({s['carrier']} network + all carriers at {s['hub']}): {i['kept_rows_total']:,}",
          f"- Row-count parity failures (parsed rows != file lines): {i['parity_failures'] or 'none'}\n",
          "## Flights table\n",
          _md_table([{k: v for k, v in f.items()}]),
          "## Daily scheduled departures from the hub\n",
          _md_table(s["daily_hub_departures"]),
          f"![daily flights]({fig.replace('reports/', '')})\n",
          "## Outcomes by split (carrier flights touching the hub)\n",
          _md_table(s["outcomes_by_split"]),
          "## Missing values, carrier flights touching the hub (%)\n",
          _md_table([{"column": k, **v} for k, v in s["missing_pct_carrier_hub"].items()]),
          f"{s['delay_cause_note']}\n"]
    if "weather" in s:
        w = s["weather"]
        md += ["## Weather (IEM ASOS)\n", f"Observations: {w['rows']:,}\n",
               _md_table(w["stations"]), "Missing values (%):\n",
               _md_table([w["missing_pct"]]), f"{w['missing_note']}\n"]
    return "\n".join(md)


def main() -> None:
    """Build the report files."""
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    s = collect(con)
    fig = plot_daily(con)
    con.close()
    s["generated"] = str(date.today())
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "phase1_summary.json").write_text(json.dumps(s, indent=2, default=str))
    (config.REPORTS_DIR / "phase1_data_quality.md").write_text(write_markdown(s, fig))
    print((config.REPORTS_DIR / "phase1_data_quality.md").read_text())


if __name__ == "__main__":
    main()
