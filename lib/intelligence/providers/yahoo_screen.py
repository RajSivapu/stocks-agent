"""Explicitly inactive Yahoo screen boundary.

Yahoo public screen pages do not establish an automatable public API contract.
The adapter therefore emits only zero-transport feasibility coverage.
"""

from __future__ import annotations

from datetime import datetime

from lib.intelligence.screening import ScreenDefinition, ScreenResult, inactive_screen_result


class YahooScreenAdapter:
    provider = "yahoo"

    def __init__(self, http: object) -> None:
        self.http = http

    def collect(self, definition: ScreenDefinition, *, observed_at: datetime) -> ScreenResult:
        if definition.state == "enabled":
            raise ValueError("Yahoo screen transport has no reviewed public API contract")
        return inactive_screen_result(definition, observed_at=observed_at)


__all__ = ["YahooScreenAdapter"]
