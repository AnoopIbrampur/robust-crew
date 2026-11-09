"""Enumerate legal one-day pairings: the constraint-satisfaction core.

A pairing is a sequence of legs that starts and ends at the base on one duty
day and satisfies every rule in ``rules.py``. We enumerate them by depth-first
search from each base departure over the connection graph (which already
enforces the connection rules), with forward checking: a partial pairing is
abandoned as soon as a limit that can only get worse with more legs is
already broken (leg count, block time, flight duty period; Part 117 FDP limits
never increase with more segments). Every enumerated pairing is checked
against ``rules.pairing_violations`` in tests.

Cost of a pairing (in pay-hour units, a documented convention, not a real
contract): ``max(block hours, PAY_GUARANTEE_HOURS) + CREW_FIXED_COST_HOURS``.
The guarantee makes short pairings relatively expensive; the fixed cost makes
the optimizer prefer fewer crews.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from robust_crew.models import Leg
from robust_crew.network import Connection
from robust_crew.rules import DEFAULT_RULES, CrewRules

PAY_GUARANTEE_HOURS = 5.0  # minimum pay credit per duty day
CREW_FIXED_COST_HOURS = 4.0  # per-crew overhead (per diem, reserve cover), in pay hours
DEFAULT_MAX_PAIRINGS = 2_000_000  # hard cap; enumeration stops and reports if hit


@dataclass(frozen=True)
class Pairing:
    """A legal one-day pairing (leg indices into the day's leg list)."""

    legs: tuple[int, ...]
    block_min: float
    fdp_min: float
    cost: float


@dataclass
class EnumerationResult:
    """Pairings plus everything needed to report on the enumeration."""

    pairings: list[Pairing]
    seconds: float
    capped: bool
    nodes_expanded: int
    uncoverable: dict[int, str] = field(default_factory=dict)  # leg index -> reason


def pairing_cost(block_min: float) -> float:
    """Pay-hour cost of a pairing with the given scheduled block minutes."""
    return max(block_min / 60, PAY_GUARANTEE_HOURS) + CREW_FIXED_COST_HOURS


def enumerate_pairings(
    legs: list[Leg],
    edges: list[Connection],
    rules: CrewRules = DEFAULT_RULES,
    max_pairings: int = DEFAULT_MAX_PAIRINGS,
) -> EnumerationResult:
    """Enumerate every legal one-day pairing by DFS with forward checking."""
    t0 = time.perf_counter()
    succ: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        succ[e.i].append(e.j)
    for v in succ.values():
        v.sort(key=lambda j: legs[j].dep)

    block = [leg.block_minutes for leg in legs]
    pairings: list[Pairing] = []
    nodes = 0
    capped = False

    for start, first in enumerate(legs):
        if first.origin != rules.base:
            continue
        report = rules.report_time(first.dep)
        flight_limit = rules.max_flight_time_min(report)
        # FDP limit by segment count for this report time (non-increasing in n).
        fdp_limit = [rules.max_fdp_min(report, n) for n in range(1, rules.max_legs + 1)]

        stack: list[tuple[tuple[int, ...], float]] = [((start,), block[start])]
        while stack:
            path, blk = stack.pop()
            nodes += 1
            last = legs[path[-1]]
            n = len(path)
            fdp = (last.arr - report).total_seconds() / 60
            # Forward check: limits that can only get worse with more legs.
            if blk > flight_limit or fdp > fdp_limit[n - 1]:
                continue
            if last.dest == rules.base:
                pairings.append(Pairing(path, blk, fdp, pairing_cost(blk)))
                if len(pairings) >= max_pairings:
                    capped = True
                    break
            if n == rules.max_legs:
                continue
            for j in succ.get(path[-1], ()):
                nxt = legs[j]
                # Cheap look-ahead before pushing: the next leg alone must fit.
                if blk + block[j] > flight_limit:
                    continue
                if (nxt.arr - report).total_seconds() / 60 > fdp_limit[n]:
                    break  # successors are sorted by departure; later ones only end later
                stack.append((path + (j,), blk + block[j]))
        if capped:
            break

    covered = {i for p in pairings for i in p.legs}
    uncoverable = {
        i: _uncoverable_reason(i, legs, succ, rules) for i in range(len(legs)) if i not in covered
    }
    return EnumerationResult(pairings, time.perf_counter() - t0, capped, nodes, uncoverable)


def _uncoverable_reason(i: int, legs: list[Leg], succ: dict[int, list[int]], rules: CrewRules) -> str:
    """Best short explanation of why no legal pairing covers leg ``i``."""
    leg = legs[i]
    if leg.origin == rules.base and not succ.get(i):
        return "departs base but no legal connecting return (e.g. evening departure, overnight at spoke)"
    if leg.dest == rules.base and not any(i in succ.get(k, ()) for k in range(len(legs))):
        return "arrives at base but no legal feeder from base (crew would have overnighted at spoke)"
    return "every route through this leg breaks the duty, block or leg limits"
