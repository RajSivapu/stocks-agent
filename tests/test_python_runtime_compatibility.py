from dataclasses import MISSING, fields

from lib.intelligence.learning import LearningObservation
from lib.intelligence.research_queue import EnrichmentRequest
from scripts.collect_market_intelligence import _provider_hosts


def test_immutable_mapping_defaults_are_created_by_factories():
    """Keep collector imports compatible with Python 3.11 dataclass validation."""
    for record_type, field_name in (
        (EnrichmentRequest, "requested_window"),
        (LearningObservation, "metrics"),
    ):
        record_field = next(
            value for value in fields(record_type) if value.name == field_name
        )
        assert record_field.default is MISSING
        assert record_field.default_factory is not MISSING
        assert record_field.default_factory() == {}


def test_collector_can_load_every_configured_provider_adapter():
    providers = (
        "gdelt", "alpha_vantage", "finnhub", "yahoo", "sec_edgar",
        "federal_register", "white_house", "doe", "dod", "eia",
        "fred", "bls", "bea",
    )
    for provider in providers:
        assert _provider_hosts(provider)
