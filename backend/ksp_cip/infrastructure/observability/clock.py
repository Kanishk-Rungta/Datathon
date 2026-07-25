"""Clock port implementations. Injecting time makes analytics testable."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Deterministic clock for tests and reproducible synthetic generation."""

    def __init__(self, moment: datetime) -> None:
        self._moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._moment

    def advance(self, **delta) -> None:
        from datetime import timedelta

        self._moment = self._moment + timedelta(**delta)
