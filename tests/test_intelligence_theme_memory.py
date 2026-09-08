from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

import pytest

from lib.intelligence.packet import EvidencePacket
from lib.intelligence.research_queue import validate_research_nominations
from lib.intelligence.themes import (
    ThemeEpisodeRevision,
    episode_is_active,
    revise_theme_episode,
)


UTC = timezone.utc
RUN_1 = "11111111-1111-4111-8111-111111111111"
RUN_2 = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 9, 12, 14, tzinfo=UTC)
THEME_EPISODE_VECTOR = json.loads(
    (Path(__file__).parent / "fixtures/theme_episode_v2_hash_vector.json").read_text()
)


def _event(*, source_id: str, wording: str = "New magnet plant", polarity: str = "supporting"):
    return {
        "theme_id": "critical_minerals_magnets",
        "theme_mechanism": "domestic_magnet_capacity",
        "subject_identity": "entity:niron-magnetics",
        "jurisdiction": "US",
        "effective_period": {"start": "2026-09-01", "end": "2026-12-31"},
        "authoritative_id": "award:doe:MAGNET-2026-17",
        "wording": wording,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "source_evidence": [{
            "evidence_id": source_id,
            "story_identity": source_id,
            "polarity": polarity,
        }],
        "investigated_entity_ids": ["entity:niron-magnetics"],
        "missing_questions": ["Which public suppliers have current primary exposure?"],
        "invalidation_conditions": ["Program award is rescinded"],
        "next_review_at": (NOW + timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
    }


def test_episode_identity_ignores_wording_and_repeat_does_not_create_revision():
    first = revise_theme_episode(None, _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), origin_run_id=RUN_1)
    repeated = revise_theme_episode(
        first,
        _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", wording="Paraphrased headline"),
        origin_run_id=RUN_2,
    )
    assert repeated is first


def test_episode_persistence_matches_the_shared_v2_golden_document():
    vector = THEME_EPISODE_VECTOR
    revision = revise_theme_episode(
        None, vector["event"], origin_run_id=vector["origin_run_id"],
    )

    assert revision.anchor_hash == vector["anchor_hash"]
    assert revision.episode_id == vector["episode_id"]
    assert revision.content_hash == vector["content_hash"]
    assert revision.revision_id == vector["revision_id"]
    assert revision.to_persistence_row() == vector["persistence_row"]


def test_episode_persistence_normalizes_story_identity_and_rejects_short_state_reason():
    event = _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    event["source_evidence"][0]["story_identity"] = "  official\taward   story  "
    revision = revise_theme_episode(None, event, origin_run_id=RUN_1)
    assert revision.source_membership[0][1] == "official award story"

    event.update(state="closed", closure_reason="x")
    with pytest.raises(ValueError, match="closure reason"):
        revise_theme_episode(None, event, origin_run_id=RUN_1)

    event.update(state="open", closure_reason="Unexpected close", reopen_reason=None)
    with pytest.raises(ValueError, match="closure"):
        revise_theme_episode(None, event, origin_run_id=RUN_1)

    event.update(closure_reason=None, reopen_reason="Unexpected reopen")
    with pytest.raises(ValueError, match="reopen"):
        revise_theme_episode(None, event, origin_run_id=RUN_1)


def test_paraphrase_with_new_evidence_increments_same_episode_and_retains_predecessor():
    first = revise_theme_episode(None, _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), origin_run_id=RUN_1)
    revised = revise_theme_episode(
        first,
        _event(source_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", wording="A second source rephrases it"),
        origin_run_id=RUN_2,
    )
    assert isinstance(revised, ThemeEpisodeRevision)
    assert revised.episode_id == first.episode_id
    assert revised.revision == 2
    assert revised.predecessor_revision_id == first.revision_id
    assert revised.predecessor_content_hash == first.content_hash
    assert revised.first_seen == first.first_seen
    assert revised.added_source_ids == ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",)


def test_syndicated_duplicates_count_once_and_contradiction_retains_history():
    event = _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    event["source_evidence"] = [
        {"evidence_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "story_identity": "wire-1", "polarity": "supporting"},
        {"evidence_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "story_identity": "wire-1", "polarity": "supporting"},
    ]
    first = revise_theme_episode(None, event, origin_run_id=RUN_1)
    assert first.supporting_source_ids == ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",)
    corrected = revise_theme_episode(
        first,
        _event(source_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", polarity="opposing"),
        origin_run_id=RUN_2,
    )
    assert corrected.supporting_source_ids == first.supporting_source_ids
    assert corrected.opposing_source_ids == ("cccccccc-cccc-4ccc-8ccc-cccccccccccc",)
    assert corrected.revision == 2


def test_source_correction_replaces_latest_story_polarity_but_preserves_predecessor_history():
    first = revise_theme_episode(
        None, _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        origin_run_id=RUN_1,
    )
    correction = _event(
        source_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", polarity="opposing",
    )
    correction["source_evidence"][0]["story_identity"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    revised = revise_theme_episode(first, correction, origin_run_id=RUN_2)

    assert revised.predecessor_revision_id == first.revision_id
    assert first.supporting_source_ids == ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",)
    assert revised.supporting_source_ids == ()
    assert revised.opposing_source_ids == ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",)


def test_episode_expiry_is_capped_and_active_requires_validated_successful_source_run():
    event = _event(source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    event["expires_at"] = (NOW + timedelta(days=90)).isoformat().replace("+00:00", "Z")
    revision = revise_theme_episode(None, event, origin_run_id=RUN_1)
    assert revision.expires_at == revision.first_seen + timedelta(days=30)
    assert episode_is_active(revision, as_of=NOW + timedelta(days=29), successful_source_run_ids={RUN_1})
    assert not episode_is_active(revision, as_of=NOW + timedelta(days=29), successful_source_run_ids=set())
    assert not episode_is_active(revision, as_of=revision.expires_at, successful_source_run_ids={RUN_1})


def _packet() -> dict[str, object]:
    return {
        "contract_version": 2,
        "packet_id": "33333333-3333-4333-8333-333333333333",
        "packet_hash": "a" * 64,
        "reference_manifest_id": "44444444-4444-4444-8444-444444444444",
        "research_candidates": [{
            "candidate_key": "entity:niron-magnetics",
            "theme_ids": ["critical_minerals_magnets"],
            "entity_id": "entity:niron-magnetics",
            "security_id": None,
            "roles": ["magnet_manufacturing"],
            "evidence": [
                {"item_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "relationship_eligible": True, "role": "supporting"},
                {"item_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "relationship_eligible": True, "role": "opposing"},
            ],
        }],
        "evidence": [{"item_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, {"item_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}],
        "action_candidates": [],
        "execution_allowed": False,
    }


def _nomination(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "theme_id": "critical_minerals_magnets",
        "entity_id": "entity:niron-magnetics",
        "security_id": None,
        "role": "magnet_manufacturing",
        "reason": "Verify current primary evidence for the nominated relationship.",
        "evidence_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "required_evidence_kind": "primary_exposure",
        "priority": 3,
    }
    value.update(changes)
    return value


def test_nomination_is_exact_key_candidate_bound_and_research_only():
    result = validate_research_nominations([_nomination()], _packet())
    assert len(result) == 1
    nomination = result[0]
    assert nomination.state == "pending"
    assert nomination.authorizes_action is False
    assert nomination.may_mutate_watchlist is False
    assert nomination.expires_at <= nomination.created_at + timedelta(days=7)


@pytest.mark.parametrize("mutation", [
    {"url": "https://www.sec.gov/Archives/x"},
    {"action": "buy"},
    {"watchlist": ["MP"]},
    {"nested": {"query": "browse the web"}},
])
def test_nomination_rejects_extra_or_authoritative_fields(mutation):
    with pytest.raises(ValueError, match="nomination"):
        validate_research_nominations([_nomination(**mutation)], _packet())


def test_nomination_evidence_must_support_exact_candidate_relationship():
    packet = _packet()
    packet["evidence"].append({"item_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"})
    with pytest.raises(ValueError, match="candidate-bound"):
        validate_research_nominations([
            _nomination(evidence_ids=["cccccccc-cccc-4ccc-8ccc-cccccccccccc"])
        ], packet)


def test_nomination_batch_is_bounded_to_three():
    with pytest.raises(ValueError, match="three"):
        validate_research_nominations([_nomination() for _ in range(4)], _packet())
