"""Download and clean BTS on-time data and IEM ASOS weather into DuckDB.

Tables written to config.DB_PATH:

- ``bts_raw``      typed BTS rows for the carrier's whole network plus every
                   carrier's flights touching the hub (for congestion features)
- ``ingest_log``   per-month raw row count, rows kept, and the parity check
- ``flights``      ``bts_raw`` plus UTC times, time zones and split label
- ``weather_obs``  ASOS observations (UTC) for the hub and top destinations

Usage::

    uv run python -m robust_crew.ingest bts       # download + load all months
    uv run python -m robust_crew.ingest flights   # build UTC flights table
    uv run python -m robust_crew.ingest weather   # download + load weather
"""

from __future__ import annotations

import argparse
import logging
import shutil
import time
import zipfile
from datetime import timedelta
from pathlib import Path

import duckdb
import polars as pl
import requests

from robust_crew import config
from robust_crew.timeconv import add_utc_times

log = logging.getLogger(__name__)

# Numeric BTS columns and their DuckDB types (all others stay VARCHAR).
_BTS_TYPES = {
    "FlightDate": "DATE",
    "Flight_Number_Reporting_Airline": "INTEGER",
    "CRSDepTime": "INTEGER",
    "DepTime": "INTEGER",
    "WheelsOff": "INTEGER",
    "WheelsOn": "INTEGER",
    "CRSArrTime": "INTEGER",
    "ArrTime": "INTEGER",
    "Cancelled": "DOUBLE",
    "Diverted": "DOUBLE",
}
_DOUBLE_COLS = [
    "DepDelay", "TaxiOut", "TaxiIn", "ArrDelay", "CRSElapsedTime", "ActualElapsedTime",
    "AirTime", "Distance", "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay",
    "LateAircraftDelay",
]


def connect(path: Path = config.DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open the project DuckDB database, creating its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def _get(url: str, *, params: dict | None = None, stream: bool = False, tries: int = 4) -> requests.Response:
    """HTTP GET with retries and exponential backoff."""
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, stream=stream, timeout=300,
                             headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == tries:
                raise
            wait = 5 * 2 ** (attempt - 1)
            log.warning("GET %s failed (%s); retry %d in %ds", url, exc, attempt, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


# --- BTS -----------------------------------------------------------------------------

def download_bts_month(year: int, month: int, dest_dir: Path = config.BTS_RAW_DIR) -> Path:
    """Download one monthly BTS zip (skips if already present and complete)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"bts_{year}_{month:02d}.zip"
    if out.exists() and zipfile.is_zipfile(out):
        return out
    url = config.BTS_URL.format(year=year, month=month)
    tmp = out.with_suffix(".part")
    head = requests.head(url, timeout=60, headers={"User-Agent": config.USER_AGENT})
    head.raise_for_status()
    total = int(head.headers.get("Content-Length", 0))
    # The BTS server drops long transfers; resume from where we stopped.
    for attempt in range(1, 11):
        have = tmp.stat().st_size if tmp.exists() else 0
        if total and have >= total:
            break
        headers = {"User-Agent": config.USER_AGENT}
        if have:
            headers["Range"] = f"bytes={have}-"
        log.info("downloading %s (attempt %d, from byte %d of %d)", url, attempt, have, total)
        try:
            with requests.get(url, headers=headers, stream=True, timeout=120) as r:
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                with open(tmp, mode) as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        except requests.RequestException as exc:
            log.warning("transfer interrupted (%s)", exc)
            time.sleep(min(60, 5 * attempt))
    if not tmp.exists() or (total and tmp.stat().st_size != total) or not zipfile.is_zipfile(tmp):
        raise IOError(f"{url}: incomplete download after retries")
    tmp.rename(out)
    return out


def _count_data_lines(csv_path: Path) -> int:
    """Count data rows (lines minus header) in a CSV without parsing it."""
    n, last = 0, b"\n"
    with open(csv_path, "rb") as fh:
        while chunk := fh.read(1 << 24):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    if last != b"\n":
        n += 1
    return n - 1


def _select_list() -> str:
    cols = []
    for c in config.BTS_COLUMNS:
        if c in _BTS_TYPES:
            cols.append(f'CAST("{c}" AS {_BTS_TYPES[c]}) AS "{c}"')
        elif c in _DOUBLE_COLS:
            cols.append(f'CAST("{c}" AS DOUBLE) AS "{c}"')
        else:
            cols.append(f'"{c}"')
    return ", ".join(cols)


def load_bts_month(con: duckdb.DuckDBPyConnection, year: int, month: int, zip_path: Path) -> dict:
    """Load one month into ``bts_raw`` (idempotent) and log the row-count parity.

    Reads every field as text first so that no row can be silently dropped by
    type sniffing; raises if DuckDB's row count differs from the file's line
    count. Casts are explicit and fail loudly on unexpected values.
    """
    work = zip_path.parent / f"tmp_{year}_{month:02d}"
    work.mkdir(exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"{zip_path}: expected one CSV, found {names}")
            csv_path = Path(zf.extract(names[0], work))

        raw_lines = _count_data_lines(csv_path)
        src = f"read_csv('{csv_path}', header=true, all_varchar=true)"
        raw_rows = con.execute(f"SELECT count(*) FROM {src}").fetchall()[0][0]
        if raw_rows != raw_lines:
            raise ValueError(f"{csv_path.name}: parsed {raw_rows} rows but file has {raw_lines} lines")

        con.execute(f"CREATE TABLE IF NOT EXISTS bts_raw AS SELECT {_select_list()} FROM {src} LIMIT 0")
        con.execute(
            "DELETE FROM bts_raw WHERE year(FlightDate) = ? AND month(FlightDate) = ?", [year, month]
        )
        con.execute(
            f"""INSERT INTO bts_raw SELECT {_select_list()} FROM {src}
                WHERE Reporting_Airline = ? OR Origin = ? OR Dest = ?""",
            [config.CARRIER, config.HUB, config.HUB],
        )
        kept = con.execute(
            "SELECT count(*) FROM bts_raw WHERE year(FlightDate) = ? AND month(FlightDate) = ?",
            [year, month],
        ).fetchall()[0][0]
        con.execute(
            """CREATE TABLE IF NOT EXISTS ingest_log (
                   source VARCHAR, year INTEGER, month INTEGER, raw_rows BIGINT,
                   file_lines BIGINT, kept_rows BIGINT, loaded_at TIMESTAMP)"""
        )
        con.execute("DELETE FROM ingest_log WHERE source = 'bts' AND year = ? AND month = ?", [year, month])
        con.execute(
            "INSERT INTO ingest_log VALUES ('bts', ?, ?, ?, ?, ?, now())",
            [year, month, raw_rows, raw_lines, kept],
        )
        return {"year": year, "month": month, "raw_rows": raw_rows, "kept_rows": kept}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ingest_bts(months: list[tuple[int, int]] = config.MONTHS) -> None:
    """Download and load every configured month."""
    con = connect()
    for year, month in months:
        zp = download_bts_month(year, month)
        info = load_bts_month(con, year, month, zp)
        log.info("bts %d-%02d: %d raw rows, %d kept", year, month, info["raw_rows"], info["kept_rows"])
    con.close()


def build_flights(con: duckdb.DuckDBPyConnection | None = None) -> int:
    """Build the ``flights`` table: bts_raw + UTC times + split label + flight_id."""
    own = con is None
    con = con or connect()
    raw = con.sql("SELECT * FROM bts_raw").pl()
    df = add_utc_times(raw)
    df = df.with_columns(
        pl.concat_str(
            [pl.col("FlightDate").dt.strftime("%Y%m%d"), pl.col("Reporting_Airline"),
             pl.col("Flight_Number_Reporting_Airline").cast(pl.Utf8), pl.col("Origin")],
            separator="_",
        ).alias("flight_id"),
        pl.col("FlightDate").map_elements(config.split_of, return_dtype=pl.Utf8).alias("split"),
        (pl.col("Cancelled") > 0).alias("Cancelled"),
        (pl.col("Diverted") > 0).alias("Diverted"),
    )
    con.execute("DROP TABLE IF EXISTS flights")
    con.register("df_flights", df.to_arrow())
    con.execute("CREATE TABLE flights AS SELECT * FROM df_flights")
    con.unregister("df_flights")
    n = con.execute("SELECT count(*) FROM flights").fetchall()[0][0]
    if own:
        con.close()
    return n


# --- Weather -------------------------------------------------------------------------

def top_destinations(con: duckdb.DuckDBPyConnection, n: int = config.N_WEATHER_DESTINATIONS) -> list[str]:
    """Carrier's top-n destinations from the hub, ranked on the TRAIN window only."""
    rows = con.execute(
        """SELECT Dest, count(*) AS n FROM bts_raw
           WHERE Reporting_Airline = ? AND Origin = ? AND FlightDate BETWEEN ? AND ?
           GROUP BY Dest ORDER BY n DESC, Dest LIMIT ?""",
        [config.CARRIER, config.HUB, config.TRAIN_START, config.TRAIN_END, n],
    ).fetchall()
    return [r[0] for r in rows]


def download_weather(station: str, dest_dir: Path = config.WEATHER_RAW_DIR) -> Path:
    """Download all ASOS routine + special observations (UTC) for one station."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"asos_{station}.csv"
    if out.exists() and out.stat().st_size > 0:
        return out
    start, end = config.DATA_START, config.DATA_END + timedelta(days=1)
    params = [("station", station)] + [("data", f) for f in config.IEM_FIELDS] + [
        ("year1", start.year), ("month1", start.month), ("day1", start.day),
        ("year2", end.year), ("month2", end.month), ("day2", end.day),
        ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
        ("missing", "empty"), ("trace", "0.0001"), ("report_type", "3"), ("report_type", "4"),
    ]
    r = _get(config.IEM_URL, params=params)
    text = r.text
    if not text.startswith("station,"):
        raise ValueError(f"unexpected IEM response for {station}: {text[:200]!r}")
    out.write_text(text)
    time.sleep(2)  # be polite to IEM
    return out


def load_weather(con: duckdb.DuckDBPyConnection, csv_paths: list[Path]) -> int:
    """Load station CSVs into ``weather_obs`` with a derived ceiling column.

    Ceiling (ft) is the lowest broken (BKN), overcast (OVC) or vertical
    visibility (VV) layer; null means no ceiling reported (unlimited).
    """
    files = ", ".join(f"'{p}'" for p in csv_paths)
    layers = " ".join(
        f"WHEN skyc{i} IN ('BKN','OVC','VV') THEN skyl{i}" for i in range(1, 5)
    )
    con.execute("DROP TABLE IF EXISTS weather_obs")
    con.execute(
        f"""CREATE TABLE weather_obs AS
            SELECT station,
                   CAST(valid AS TIMESTAMP) AT TIME ZONE 'UTC' AS valid_utc,
                   CAST(tmpf AS DOUBLE) AS tmpf, CAST(sknt AS DOUBLE) AS sknt,
                   CAST(gust AS DOUBLE) AS gust, CAST(vsby AS DOUBLE) AS vsby,
                   CAST(p01i AS DOUBLE) AS p01i,
                   skyc1, skyc2, skyc3, skyc4,
                   CAST(skyl1 AS DOUBLE) AS skyl1, CAST(skyl2 AS DOUBLE) AS skyl2,
                   CAST(skyl3 AS DOUBLE) AS skyl3, CAST(skyl4 AS DOUBLE) AS skyl4,
                   wxcodes,
                   CAST(CASE {layers} ELSE NULL END AS DOUBLE) AS ceiling_ft
            FROM read_csv([{files}], header=true, all_varchar=true, union_by_name=true)"""
    )
    return con.execute("SELECT count(*) FROM weather_obs").fetchall()[0][0]


def ingest_weather() -> None:
    """Download and load weather for the hub and its top destinations."""
    con = connect()
    stations = [config.HUB] + [d for d in top_destinations(con) if d != config.HUB]
    log.info("weather stations: %s", stations)
    paths = [download_weather(s) for s in stations]
    n = load_weather(con, paths)
    log.info("weather_obs: %d rows", n)
    con.close()


def main() -> None:
    """Command-line entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["bts", "flights", "weather", "all"])
    args = ap.parse_args()
    if args.step in ("bts", "all"):
        ingest_bts()
    if args.step in ("flights", "all"):
        log.info("flights: %d rows", build_flights())
    if args.step in ("weather", "all"):
        ingest_weather()


if __name__ == "__main__":
    main()
