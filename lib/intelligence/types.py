from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PacketLimits:
    max_candidates: int = 12
    max_evidence_per_candidate: int = 8
    max_item_characters: int = 2_000
    max_serialized_bytes: int = 96 * 1024


@dataclass(frozen=True, slots=True)
class IntelligencePolicy:
    providers: tuple[str, ...]
    alpha_vantage_daily_ceiling: int
    alpha_vantage_phase_budget: Mapping[str, int]
    provider_phase_budgets: Mapping[str, Mapping[str, int]]
    packet: PacketLimits
    suggestion_only: bool
    execution_allowed: bool

    def budget_for(self, provider: str, phase: str) -> int:
        if provider == "alpha_vantage":
            return int(self.alpha_vantage_phase_budget.get(phase, 0))
        return int(self.provider_phase_budgets.get(provider, {}).get(phase, 0))
