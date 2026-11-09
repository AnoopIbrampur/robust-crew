"""Convert BTS local scheduled times to UTC.

BTS reports clock times (hhmm) in each airport's local time, and the date is
the local scheduled departure date. Rather than parse every clock field (which
makes overnight arrivals ambiguous), we anchor on one unambiguous instant:

    sched_dep_utc = FlightDate + CRSDepTime, localized to the origin time zone

and derive everything else from durations reported by BTS:

    sched_arr_utc = sched_dep_utc + CRSElapsedTime
    actual_dep_utc = sched_dep_utc + DepDelay
    actual_arr_utc = sched_arr_utc + ArrDelay

The local arrival clock (CRSArrTime) is used only as a consistency check.
"""

from __future__ import annotations

from functools import lru_cache

import airportsdata
import polars as pl


@lru_cache(maxsize=1)
def airport_timezones() -> dict[str, str]:
    """Map IATA code -> IANA time zone name, from the airportsdata package."""
    return {code: rec["tz"] for code, rec in airportsdata.load("IATA").items() if rec.get("tz")}


def hhmm_to_minutes(col: pl.Expr) -> pl.Expr:
    """Convert an hhmm integer clock (e.g. 1435, 2400) to minutes after midnight.

    2400 maps to 1440, i.e. midnight at the end of the day.
    """
    return (col // 100) * 60 + (col % 100)


def _localize(naive: pl.Expr, tz: str) -> pl.Expr:
    """Localize naive local datetimes in `tz` and convert to UTC.

    Ambiguous times (the repeated hour when DST ends) take the earlier instant.
    Non-existent times (the skipped hour when DST starts) are read as standard
    time, i.e. shifted forward by the gap, which is how a wall clock behaves.
    """
    direct = naive.dt.replace_time_zone(tz, ambiguous="earliest", non_existent="null")
    shifted = (naive - pl.duration(hours=1)).dt.replace_time_zone(
        tz, ambiguous="earliest", non_existent="null"
    ) + pl.duration(hours=1)
    return pl.coalesce(direct, shifted).dt.convert_time_zone("UTC")


def add_utc_times(df: pl.DataFrame, tz_map: dict[str, str] | None = None) -> pl.DataFrame:
    """Add UTC scheduled/actual departure and arrival columns to a BTS frame.

    Expects columns: FlightDate (date), Origin, Dest, CRSDepTime, CRSArrTime
    (hhmm ints), CRSElapsedTime, DepDelay, ArrDelay (minutes, nullable).

    Adds: origin_tz, dest_tz, sched_dep_utc, sched_arr_utc, actual_dep_utc,
    actual_arr_utc (all tz-aware UTC), and arr_clock_ok (bool: does the
    derived scheduled arrival, shown in the destination's local time, match
    CRSArrTime?). Rows whose origin time zone is unknown get null times.
    """
    tz_map = tz_map if tz_map is not None else airport_timezones()
    df = df.with_row_index("_row").with_columns(
        pl.col("CRSElapsedTime", "DepDelay", "ArrDelay").cast(pl.Float64),
        pl.col("CRSDepTime", "CRSArrTime").cast(pl.Int32),
    ).with_columns(
        pl.col("Origin").replace_strict(tz_map, default=None).alias("origin_tz"),
        pl.col("Dest").replace_strict(tz_map, default=None).alias("dest_tz"),
        (
            pl.col("FlightDate").cast(pl.Datetime("us"))
            + pl.duration(minutes=hhmm_to_minutes(pl.col("CRSDepTime")))
        ).alias("_dep_local"),
    )

    # Localize per origin time zone (polars needs a literal tz per expression).
    parts = []
    for (tz,), part in df.group_by(["origin_tz"], maintain_order=True):
        if tz is None:
            part = part.with_columns(pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("sched_dep_utc"))
        else:
            part = part.with_columns(_localize(pl.col("_dep_local"), tz).alias("sched_dep_utc"))
        parts.append(part)
    df = pl.concat(parts, how="vertical") if parts else df

    df = df.with_columns(
        (pl.col("sched_dep_utc") + pl.duration(minutes=pl.col("CRSElapsedTime"))).alias("sched_arr_utc"),
        (pl.col("sched_dep_utc") + pl.duration(minutes=pl.col("DepDelay"))).alias("actual_dep_utc"),
    ).with_columns(
        (pl.col("sched_arr_utc") + pl.duration(minutes=pl.col("ArrDelay"))).alias("actual_arr_utc"),
    )

    # Consistency check: derived arrival in destination local time vs CRSArrTime.
    parts = []
    for (tz,), part in df.group_by(["dest_tz"], maintain_order=True):
        if tz is None:
            part = part.with_columns(pl.lit(None, dtype=pl.Boolean).alias("arr_clock_ok"))
        else:
            local = pl.col("sched_arr_utc").dt.convert_time_zone(tz)
            mins = local.dt.hour().cast(pl.Int32) * 60 + local.dt.minute().cast(pl.Int32)
            reported = hhmm_to_minutes(pl.col("CRSArrTime")) % 1440
            part = part.with_columns((mins == reported).alias("arr_clock_ok"))
        parts.append(part)
    df = pl.concat(parts, how="vertical") if parts else df

    return df.sort("_row").drop("_dep_local", "_row")
