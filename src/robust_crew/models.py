"""Small shared data types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Leg:
    """One scheduled flight leg, with times in UTC."""

    flight_id: str
    origin: str
    dest: str
    dep: datetime  # scheduled departure, tz-aware UTC
    arr: datetime  # scheduled arrival, tz-aware UTC
    tail: str | None = None

    @property
    def block_minutes(self) -> float:
        """Scheduled block time in minutes."""
        return (self.arr - self.dep).total_seconds() / 60
