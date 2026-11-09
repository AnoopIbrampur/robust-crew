"""Crew legality rules: the single source of truth for every crew-rule number.

The duty and flight-time limits use the values of FAA 14 CFR Part 117
Table A (maximum flight time, unaugmented) and Table B (maximum flight duty
period, unaugmented, lineholders), keyed by report time in the crew's
acclimated time zone (the base's). Everything else (connection minimums, the
report/release buffers, max legs, max sit) is a simplified planning
convention.

This is a simplified model INSPIRED BY Part 117. It omits rest requirements,
cumulative limits, augmentation, deadheads, reserve rules, extensions and
every contractual rule. It is not a claim of legal compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from robust_crew import config
from robust_crew.models import Leg

# Part 117 Table B: (start hhmm inclusive, end hhmm inclusive) -> max FDP hours
# for 1, 2, 3, 4, 5, 6, 7+ flight segments.
PART117_TABLE_B: list[tuple[int, int, tuple[float, ...]]] = [
    (0, 359, (9, 9, 9, 9, 9, 9, 9)),
    (400, 459, (10, 10, 10, 10, 9, 9, 9)),
    (500, 559, (12, 12, 12, 12, 11.5, 11, 10.5)),
    (600, 659, (13, 13, 12, 12, 11.5, 11, 10.5)),
    (700, 1159, (14, 14, 13, 13, 12.5, 12, 11.5)),
    (1200, 1259, (13, 13, 13, 13, 12.5, 12, 11.5)),
    (1300, 1659, (12, 12, 12, 12, 11.5, 11, 10.5)),
    (1700, 2159, (12, 12, 11, 11, 10, 9, 9)),
    (2200, 2259, (11, 11, 10, 10, 9, 9, 9)),
    (2300, 2359, (10, 10, 10, 9, 9, 9, 9)),
]

# Part 117 Table A: (start hhmm, end hhmm) -> max flight time hours.
PART117_TABLE_A: list[tuple[int, int, float]] = [
    (0, 459, 8),
    (500, 1959, 9),
    (2000, 2359, 8),
]


@dataclass(frozen=True)
class CrewRules:
    """All crew-rule parameters. Change numbers here, nowhere else."""

    base: str = config.HUB
    base_tz: str = "America/New_York"  # acclimated time zone of a CLT-based crew
    min_connect_same_tail_min: int = 30  # crew stays with the aircraft
    min_connect_change_tail_min: int = 45  # crew changes aircraft
    report_buffer_min: int = 60  # report this long before first departure
    release_buffer_min: int = 30  # released this long after last arrival
    max_legs: int = 6
    max_sit_min: int = 240  # planning convention: longest sit between legs
    max_duty_min: int | None = None  # optional extra cap on report->release
    fdp_table: list = field(default_factory=lambda: PART117_TABLE_B)
    flight_time_table: list = field(default_factory=lambda: PART117_TABLE_A)

    def min_connect_min(self, same_tail: bool) -> int:
        """Minimum crew connection time for a same- or different-aircraft connection."""
        return self.min_connect_same_tail_min if same_tail else self.min_connect_change_tail_min

    def _local_hhmm(self, t: datetime) -> int:
        lt = t.astimezone(ZoneInfo(self.base_tz))
        return lt.hour * 100 + lt.minute

    def report_time(self, first_dep: datetime) -> datetime:
        """Report time for a duty whose first leg departs at ``first_dep``."""
        return first_dep - timedelta(minutes=self.report_buffer_min)

    def max_fdp_min(self, report: datetime, n_segments: int) -> float:
        """Maximum flight duty period (minutes) from Table B."""
        hhmm = self._local_hhmm(report)
        col = min(max(n_segments, 1), 7) - 1
        for lo, hi, hours in self.fdp_table:
            if lo <= hhmm <= hi:
                return hours[col] * 60
        raise ValueError(f"no FDP row for {hhmm}")

    def max_flight_time_min(self, report: datetime) -> float:
        """Maximum scheduled block (flight) time (minutes) from Table A."""
        hhmm = self._local_hhmm(report)
        for lo, hi, hours in self.flight_time_table:
            if lo <= hhmm <= hi:
                return hours * 60
        raise ValueError(f"no flight-time row for {hhmm}")


DEFAULT_RULES = CrewRules()


def same_tail(a: Leg, b: Leg) -> bool:
    """True if both legs are flown by the same (known) aircraft."""
    return a.tail is not None and a.tail == b.tail


def connection_violation(a: Leg, b: Leg, rules: CrewRules = DEFAULT_RULES) -> str | None:
    """Why a crew cannot fly leg ``b`` right after leg ``a`` (None if it can)."""
    if a.dest != b.origin:
        return f"{a.flight_id}->{b.flight_id}: arrives {a.dest}, next departs {b.origin}"
    gap = (b.dep - a.arr).total_seconds() / 60
    need = rules.min_connect_min(same_tail(a, b))
    if gap < need:
        return f"{a.flight_id}->{b.flight_id}: {gap:.0f} min connection < {need} min minimum"
    if gap > rules.max_sit_min:
        return f"{a.flight_id}->{b.flight_id}: {gap:.0f} min sit > {rules.max_sit_min} min maximum"
    return None


def fdp_minutes(legs: list[Leg], rules: CrewRules = DEFAULT_RULES) -> float:
    """Flight duty period: report time to last scheduled arrival (block-in)."""
    return (legs[-1].arr - rules.report_time(legs[0].dep)).total_seconds() / 60


def duty_minutes(legs: list[Leg], rules: CrewRules = DEFAULT_RULES) -> float:
    """Duty period: report to release (FDP plus the release buffer)."""
    return fdp_minutes(legs, rules) + rules.release_buffer_min


def pairing_violations(legs: list[Leg], rules: CrewRules = DEFAULT_RULES) -> list[str]:
    """Every rule a one-day pairing breaks (empty list means legal)."""
    if not legs:
        return ["empty pairing"]
    out: list[str] = []
    if legs[0].origin != rules.base:
        out.append(f"starts at {legs[0].origin}, not base {rules.base}")
    if legs[-1].dest != rules.base:
        out.append(f"ends at {legs[-1].dest}, not base {rules.base}")
    if len(legs) > rules.max_legs:
        out.append(f"{len(legs)} legs > {rules.max_legs} maximum")
    for a, b in zip(legs, legs[1:]):
        if (v := connection_violation(a, b, rules)) is not None:
            out.append(v)
    report = rules.report_time(legs[0].dep)
    block = sum(leg.block_minutes for leg in legs)
    if block > (lim := rules.max_flight_time_min(report)):
        out.append(f"block {block:.0f} min > {lim:.0f} min flight-time limit")
    fdp = fdp_minutes(legs, rules)
    if fdp > (lim := rules.max_fdp_min(report, len(legs))):
        out.append(f"FDP {fdp:.0f} min > {lim:.0f} min limit for {len(legs)} segments")
    if rules.max_duty_min is not None and (d := duty_minutes(legs, rules)) > rules.max_duty_min:
        out.append(f"duty {d:.0f} min > {rules.max_duty_min} min maximum")
    return out


def is_legal(legs: list[Leg], rules: CrewRules = DEFAULT_RULES) -> bool:
    """True if the pairing breaks no rule."""
    return not pairing_violations(legs, rules)


def can_still_close(legs: list[Leg], rules: CrewRules = DEFAULT_RULES) -> bool:
    """Forward check for enumeration: could extending ``legs`` ever become legal?

    False once a limit that only grows with more legs is already exceeded
    (legs, block, FDP at the current segment count; FDP limits never rise with
    more segments). Ignores the end-at-base rule, which extension can fix.
    """
    if not legs or legs[0].origin != rules.base or len(legs) > rules.max_legs:
        return False
    for a, b in zip(legs, legs[1:]):
        if connection_violation(a, b, rules) is not None:
            return False
    report = rules.report_time(legs[0].dep)
    if sum(leg.block_minutes for leg in legs) > rules.max_flight_time_min(report):
        return False
    return fdp_minutes(legs, rules) <= rules.max_fdp_min(report, len(legs))
