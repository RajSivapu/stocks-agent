#!/usr/bin/env python3
"""Rollback-only verification for the owner-only market alert lifecycle."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import config


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20260905_owner_alert_lifecycle.sql"


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _call(cur, name, *args):
    placeholders = ",".join(["%s"] * len(args))
    cur.execute(f"SELECT public.{name}({placeholders})", args)
    return cur.fetchone()[0]


def _expect_db_error(cur, callback):
    cur.execute("SAVEPOINT expected_alert_error")
    try:
        callback()
    except psycopg.Error:
        cur.execute("ROLLBACK TO SAVEPOINT expected_alert_error")
        cur.execute("RELEASE SAVEPOINT expected_alert_error")
        return True
    cur.execute("RELEASE SAVEPOINT expected_alert_error")
    raise RuntimeError("database operation unexpectedly succeeded")


def _snapshot(rule_id):
    return {
        "rule_id": str(rule_id),
        "version": 1,
        "state": "draft",
        "ticker": "TSTAL",
        "profile": "balanced",
        "severity": "review",
        "session": "regular",
        "confirmation": "bar_close",
        "conditions": [{
            "kind": "price_zone",
            "operator": "inside",
            "left": "10",
            "right": "11",
            "timeframe": "quote",
        }],
        "cooldown_seconds": 1200,
        "fire_limit": 3,
        "valid_until": "2099-09-09T21:00:00.000Z",
        "owner_note": "Rollback-only verifier",
    }


def _insert_source_evaluation(cur):
    policy_version = cur.execute(
        "SELECT version FROM public.market_policy_config WHERE active ORDER BY version DESC LIMIT 1"
    ).fetchone()
    _require(policy_version is not None, "active market policy is required")
    run_id = uuid4()
    request_id = uuid4()
    evaluation_id = uuid4()
    candidate_id = uuid4()
    lease = uuid4()
    cur.execute(
        "INSERT INTO public.analysis_runs(id,kind,status) VALUES (%s,'intraday','completed')",
        (run_id,),
    )
    cur.execute(
        """
        INSERT INTO public.market_gateway_requests(
          request_id,operation,run_id,status,lease_token,response,finished_at
        ) VALUES (%s,'evaluate_and_publish',%s,'completed',%s,'{}'::jsonb,now())
        """,
        (request_id, run_id, lease),
    )
    cur.execute(
        """
        INSERT INTO public.decision_evaluations(
          id,request_id,run_id,candidate_id,policy_version,input_digest,raw_action,
          final_action,policy_status,reason_codes,explanations,normalized,evidence,analyst,checker
        ) VALUES (
          %s,%s,%s,%s,%s,%s,'watch','watch','approved','[]'::jsonb,'[]'::jsonb,
          '{"ticker":"TSTAL"}'::jsonb,'[]'::jsonb,
          '{"completed":true}'::jsonb,'{"completed":true}'::jsonb
        )
        """,
        (evaluation_id, request_id, run_id, candidate_id, policy_version[0], "a" * 64),
    )
    return request_id, evaluation_id


def _draft(cur, request_id, evaluation_id, fingerprint):
    draft_id = uuid4()
    receipt = _call(
        cur,
        "create_market_alert_drafts",
        request_id,
        Jsonb([{
            "id": str(draft_id),
            "source_evaluation_id": str(evaluation_id),
            "rule_snapshot": _snapshot(draft_id),
            "fingerprint": fingerprint,
        }]),
    )
    _require(receipt["created_count"] == 1, "alert draft was not created")
    return draft_id


def verify(cur):
    cur.execute(MIGRATION.read_text())
    cur.execute(MIGRATION.read_text())
    request_id, evaluation_id = _insert_source_evaluation(cur)
    draft_id = _draft(cur, request_id, evaluation_id, "a" * 64)
    duplicate_id = uuid4()
    duplicate_draft = _call(
        cur,
        "create_market_alert_drafts",
        request_id,
        Jsonb([{
            "id": str(duplicate_id),
            "source_evaluation_id": str(evaluation_id),
            "rule_snapshot": _snapshot(duplicate_id),
            "fingerprint": "a" * 64,
        }]),
    )
    _require(duplicate_draft["created_count"] == 0,
             "equivalent draft fingerprint was not deduplicated")
    owner_chat = 9_000_000_001
    owner_user = 9_000_000_002
    arm_update = 9_000_000_003
    armed = _call(
        cur, "apply_market_alert_action", draft_id, "arm", arm_update,
        owner_chat, owner_user, 1, None,
    )
    _require(armed["state"] == "active" and UUID(str(armed["rule_id"])) == draft_id,
             "draft did not arm")
    duplicate = _call(
        cur, "apply_market_alert_action", draft_id, "arm", arm_update,
        owner_chat, owner_user, 1, None,
    )
    _require(duplicate.get("duplicate") is True, "duplicate action was not idempotent")
    duplicate_update_rejected = _expect_db_error(
        cur,
        lambda: _call(cur, "apply_market_alert_action", draft_id, "dismiss", arm_update,
                      owner_chat, owner_user, 1, None),
    )
    wrong_owner_rejected = _expect_db_error(
        cur,
        lambda: _call(cur, "apply_market_alert_action", draft_id, "pause", arm_update + 1,
                      owner_chat + 1, owner_user, 1, None),
    )
    stale_version_rejected = _expect_db_error(
        cur,
        lambda: _call(cur, "apply_market_alert_action", draft_id, "pause", arm_update + 2,
                      owner_chat, owner_user, 2, None),
    )
    paused = _call(
        cur, "apply_market_alert_action", draft_id, "pause", arm_update + 3,
        owner_chat, owner_user, 1, None,
    )
    _require(paused["state"] == "paused" and paused["version"] == 2,
             "valid pause did not create a new version")
    resumed = _call(
        cur, "apply_market_alert_action", draft_id, "resume", arm_update + 4,
        owner_chat, owner_user, 2, None,
    )
    _require(resumed["state"] == "active" and resumed["version"] == 3,
             "valid resume did not create a new version")

    expired_id = _draft(cur, request_id, evaluation_id, "b" * 64)
    cur.execute(
        "UPDATE public.market_alert_drafts SET expires_at=now()-interval '1 minute' WHERE id=%s",
        (expired_id,),
    )
    expired_draft_rejected = _expect_db_error(
        cur,
        lambda: _call(cur, "apply_market_alert_action", expired_id, "arm", arm_update + 5,
                      owner_chat, owner_user, 1, None),
    )

    event_request = uuid4()
    event_lease = uuid4()
    cur.execute(
        """
        INSERT INTO public.market_gateway_requests(request_id,operation,status,lease_token)
        VALUES (%s,'evaluate_alert_rules','claimed',%s)
        """,
        (event_request, event_lease),
    )
    event_id = uuid4()
    evaluated_at = cur.execute("SELECT now()").fetchone()[0]
    event_receipt = _call(
        cur,
        "record_market_alert_evaluations",
        event_request,
        Jsonb([{
            "id": str(event_id),
            "rule_id": str(draft_id),
            "rule_version": 3,
            "fingerprint": "c" * 64,
            "status": "triggered",
            "reason_codes": [],
            "observed_at": evaluated_at.isoformat(),
            "evaluated_at": evaluated_at.isoformat(),
            "market_session": "regular",
            "condition_results": [{"condition_index": 0, "passed": True}],
            "evidence_ids": ["quote-rollback"],
        }]),
    )
    _require(event_receipt["event_count"] == 1, "alert event was not recorded")
    publication_id = uuid4()
    linked = _call(
        cur,
        "create_market_alert_publication",
        event_request,
        publication_id,
        evaluated_at.date(),
        "entry_trigger",
        "rollback event preview",
        "d" * 64,
        [event_id],
        None,
    )
    _require(linked["linked_event_count"] == 1, "alert publication was not linked")
    linked_event = cur.execute(
        "SELECT publication_id FROM public.market_alert_events WHERE id=%s", (event_id,)
    ).fetchone()
    _require(linked_event and linked_event[0] == publication_id,
             "event publication receipt was not stored")
    claim = _call(cur, "claim_market_publication", event_request)
    accepted_at = cur.execute("SELECT now()").fetchone()[0]
    finished = _call(
        cur,
        "finish_market_alert_publication",
        event_request,
        UUID(str(claim["lease_token"])),
        "delivered",
        Jsonb([9_000_000_010]),
        None,
        accepted_at,
    )
    returned_acceptance = datetime.fromisoformat(finished["telegram_accepted_at"])
    _require(returned_acceptance == accepted_at,
             "Telegram acceptance timestamp was not returned exactly")
    stored_acceptance = cur.execute(
        "SELECT telegram_accepted_at=%s FROM public.market_publications WHERE id=%s",
        (accepted_at, publication_id),
    ).fetchone()[0]
    _require(stored_acceptance, "Telegram acceptance timestamp was not stored exactly")
    acknowledgement = _call(
        cur, "apply_market_alert_action", event_id, "acknowledge", arm_update + 6,
        owner_chat, owner_user, 3, None,
    )
    _require(UUID(str(acknowledgement["event_id"])) == event_id
             and UUID(str(acknowledgement["publication_id"])) == publication_id,
             "acknowledgement was not bound to its event and publication")
    acknowledgement_bound = cur.execute(
        """
        SELECT event_id=%s AND publication_id=%s
        FROM public.market_alert_actions WHERE telegram_update_id=%s
        """,
        (event_id, publication_id, arm_update + 6),
    ).fetchone()[0]
    _require(acknowledgement_bound, "acknowledgement linkage was not persisted")

    cur.execute(
        "UPDATE public.market_alert_rules SET valid_until=now()-interval '1 minute' WHERE id=%s",
        (draft_id,),
    )
    expired = _call(cur, "expire_market_alert_rules")
    _require(expired["expired_count"] == 1, "due rule was not expired")
    expiry_versioned = cur.execute(
        """
        SELECT r.state='expired' AND r.current_version=4
          AND v.snapshot->>'state'='expired' AND (v.snapshot->>'version')::int=4
        FROM public.market_alert_rules r
        JOIN public.market_alert_rule_versions v
          ON v.rule_id=r.id AND v.version=r.current_version
        WHERE r.id=%s
        """,
        (draft_id,),
    ).fetchone()[0]
    _require(expiry_versioned, "rule expiry did not create an immutable version")

    publish_draft_id = _draft(cur, request_id, evaluation_id, "e" * 64)
    draft_publication_request = uuid4()
    cur.execute(
        """
        INSERT INTO public.market_gateway_requests(request_id,operation,status,lease_token)
        VALUES (%s,'evaluate_alert_rules','claimed',%s)
        """,
        (draft_publication_request, uuid4()),
    )
    draft_publication_id = uuid4()
    draft_linked = _call(
        cur,
        "create_market_alert_publication",
        draft_publication_request,
        draft_publication_id,
        evaluated_at.date(),
        "new_idea",
        "rollback draft preview",
        "f" * 64,
        [],
        publish_draft_id,
    )
    _require(UUID(str(draft_linked["linked_draft_id"])) == publish_draft_id,
             "draft publication was not linked")
    draft_publication_linked = cur.execute(
        "SELECT publication_id=%s FROM public.market_alert_drafts WHERE id=%s",
        (draft_publication_id, publish_draft_id),
    ).fetchone()[0]
    _require(draft_publication_linked, "draft publication receipt was not stored")
    overflow_drafts = []
    for index in range(3):
        overflow_id = uuid4()
        overflow_drafts.append({
            "id": str(overflow_id),
            "source_evaluation_id": str(evaluation_id),
            "rule_snapshot": _snapshot(overflow_id),
            "fingerprint": str(index + 1) * 64,
        })
    hourly_cap_rejected = _expect_db_error(
        cur,
        lambda: _call(
            cur, "create_market_alert_drafts", request_id, Jsonb(overflow_drafts)
        ),
    )
    _expect_db_error(
        cur,
        lambda: cur.execute("UPDATE public.market_alert_events SET status='unsafe_to_evaluate' WHERE id=%s", (event_id,)),
    )
    return {
        "drafts_created": 3,
        "rules_armed": 1,
        "events_recorded": 1,
        "publications_linked": 1,
        "draft_publication_linked": draft_publication_linked,
        "duplicate_draft_suppressed": True,
        "duplicate_update_rejected": duplicate_update_rejected,
        "wrong_owner_rejected": wrong_owner_rejected,
        "stale_version_rejected": stale_version_rejected,
        "expired_draft_rejected": expired_draft_rejected,
        "acknowledgement_bound": acknowledgement_bound,
        "telegram_acceptance_stored": stored_acceptance,
        "expiry_versioned": expiry_versioned,
        "hourly_cap_rejected": hourly_cap_rejected,
    }


def remaining_test_rows(connection):
    with connection.cursor() as cur:
        exists = cur.execute(
            "SELECT to_regclass('public.market_alert_drafts') IS NOT NULL"
        ).fetchone()[0]
        if not exists:
            return 0
        return cur.execute(
            "SELECT count(*) FROM public.market_alert_drafts WHERE rule_snapshot->>'ticker'='TSTAL'"
        ).fetchone()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if not args.rollback:
        raise SystemExit("verification is rollback-only; pass --rollback")
    connection = psycopg.connect(config.secret("postgres_url"))
    try:
        with connection.cursor() as cur:
            receipt = verify(cur)
        connection.rollback()
        receipt["remaining_test_rows"] = remaining_test_rows(connection)
        print(json.dumps(receipt, sort_keys=True))
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
