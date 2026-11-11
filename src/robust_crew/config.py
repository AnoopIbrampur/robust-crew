"""Project-wide configuration: scope, date splits, paths, data sources.

Every number that shapes the experiment lives here (crew legality numbers
will live in rules.py, which is their single source of truth).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

# --- Scope -------------------------------------------------------------------
CARRIER = "AA"  # operating carrier (BTS Reporting_Airline); crews belong to it
HUB = "CLT"  # crew base; every pairing starts and ends here

# --- Time splits (inclusive, local scheduled departure date) ------------------
# Train: model fitting + historical route stats. Calib: probability calibration
# and validation. Test windows: planning + replay evaluation only.
#
# Two test windows, chosen by season:
#   test_summer  Jul-Aug 2023: convective-thunderstorm season, CLT's main
#                delay driver; the regime where robustness should pay.
#   test_winter  Nov-Dec 2023: the originally planned window. It turned out
#                unusually calm for AA (Phase 1 report), so it measures what
#                robustness costs when little goes wrong.
# The two-window design was adopted AFTER Phase 1 showed Nov-Dec 2023 was calm;
# the original window is kept so that choice cannot hide an unfavourable result.
# Sep-Oct 2023 is an unused gap. One model (trained on TRAIN, calibrated on
# CALIB) is frozen and used for both test windows.
TRAIN_START = date(2022, 1, 1)
TRAIN_END = date(2023, 4, 30)
CALIB_START = date(2023, 5, 1)
CALIB_END = date(2023, 6, 30)
TEST_WINDOWS: dict[str, tuple[date, date]] = {
    "test_summer": (date(2023, 7, 1), date(2023, 8, 31)),
    "test_winter": (date(2023, 11, 1), date(2023, 12, 31)),
}
TEST_START = min(a for a, _ in TEST_WINDOWS.values())  # nothing on/after this may train or calibrate
TEST_END = max(b for _, b in TEST_WINDOWS.values())

DATA_START = TRAIN_START
DATA_END = TEST_END


def months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """Return (year, month) tuples covering [start, end]."""
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


MONTHS = months_in_range(DATA_START, DATA_END)


def split_of(d: date) -> str:
    """Split a date belongs to: 'train', 'calib', a TEST_WINDOWS key, 'gap' or 'none'."""
    if TRAIN_START <= d <= TRAIN_END:
        return "train"
    if CALIB_START <= d <= CALIB_END:
        return "calib"
    for name, (a, b) in TEST_WINDOWS.items():
        if a <= d <= b:
            return name
    if CALIB_END < d < TEST_END:
        return "gap"
    return "none"


def is_test(split: str) -> bool:
    """True for any test-window split name."""
    return split in TEST_WINDOWS


# --- Paths ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
BTS_RAW_DIR = RAW_DIR / "bts"
WEATHER_RAW_DIR = RAW_DIR / "weather"
DB_PATH = DATA_DIR / "robust_crew.duckdb"
DEMO_DB_PATH = ROOT / "app" / "demo.duckdb"  # small committed extract the hosted explorer runs on
REPORTS_DIR = ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

# --- BTS Reporting Carrier On-Time Performance ------------------------------------
BTS_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)

# Delete each monthly zip after it is loaded (re-downloadable; saves ~650 MB).
KEEP_RAW_ZIPS = False

# Columns kept from the ~110 in the raw file. Beyond the identifiers and times,
# the delay-cause, taxi and elapsed-time columns are kept so that the replay
# simulator (Phase 6) can separate a flight's own delay from delay it inherited
# from the real airline's crews and aircraft.
BTS_COLUMNS = [
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Tail_Number",
    "Origin",
    "Dest",
    "CRSDepTime",
    "DepTime",
    "DepDelay",
    "TaxiOut",
    "WheelsOff",
    "WheelsOn",
    "TaxiIn",
    "CRSArrTime",
    "ArrTime",
    "ArrDelay",
    "Cancelled",
    "CancellationCode",
    "Diverted",
    "CRSElapsedTime",
    "ActualElapsedTime",
    "AirTime",
    "Distance",
    "CarrierDelay",
    "WeatherDelay",
    "NASDelay",
    "SecurityDelay",
    "LateAircraftDelay",
]

# --- Iowa Environmental Mesonet ASOS ------------------------------------------------
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
IEM_FIELDS = [
    "tmpf",
    "sknt",
    "gust",
    "vsby",
    "p01i",
    "skyc1",
    "skyc2",
    "skyc3",
    "skyc4",
    "skyl1",
    "skyl2",
    "skyl3",
    "skyl4",
    "wxcodes",
]
N_WEATHER_DESTINATIONS = 15  # top destinations from the hub, ranked on TRAIN only

USER_AGENT = "robust-crew research project (python-requests)"
