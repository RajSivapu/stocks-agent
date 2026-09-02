#!/usr/bin/env python3
"""Rollback-only verification of the decision gateway migration and RPC invariants."""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import config


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20260902_decision_safety_gateway.sql"
TICKER = "TSTGW"
POLICY_VERSION = 2_147_483_000
RPC_SIGNATURES = (
    "activate_market_policy_config(integer)",
    "claim_market_gateway_request(uuid,text,uuid)",
    "complete_market_gateway_request(uuid,uuid,text,jsonb)",
    "start_market_analysis_run(uuid,uuid,text)",
    "apply_market_artifacts(uuid,uuid,uuid,jsonb)",
    "apply_market_decision_bundle(uuid,uuid,uuid,integer,jsonb,jsonb,jsonb)",
    "import_legacy_suggestion(jsonb)",
    "claim_market_publication(uuid)",
    "finish_market_publication(uuid,uuid,text,jsonb,text)",
)


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _call(cur, name, *args):
    placeholders = ",".join(["%s"] * len(args))
    cur.execute(f"SELECT public.{name}({placeholders})", args)
    return cur.fetchone()[0]


def _expect_db_error(cur, callback):
    cur.execute("SAVEPOINT expected_gateway_error")
    try:
        callback()
    except psycopg.Error:
        cur.execute("ROLLBACK TO SAVEPOINT expected_gateway_error")
        cur.execute("RELEASE SAVEPOINT expected_gateway_error")
        return
    cur.execute("RELEASE SAVEPOINT expected_gateway_error")
    raise RuntimeError("database operation unexpectedly succeeded")


def _claim(cur, operation, run_id=None):
    request_id = uuid4()
    receipt = _call(cur, "claim_market_gateway_request", request_id, operation, run_id)
    _require(receipt.get("claimed") is True, "request was not claimed")
    return request_id, receipt["lease_token"]


def _evaluation(request_id, candidate_id, evaluation_id):
    return {
        "id": str(evaluation_id),
        "candidate_id": str(candidate_id),
        "input_digest": "a" * 64,
        "raw_action": "watch",
        "final_action": "watch",
        "policy_status": "approved",
        "reason_codes": [],
        "explanations": ["verification"],
        "normalized": {"ticker": TICKER},
        "evidence": [],
        "analyst": {"completed": True},
        "checker": {"completed": True},
    }


def _suggestion(candidate_id, evaluation_id):
    return {
        "evaluation_id": str(evaluation_id),
        "candidate_id": str(candidate_id),
        "date": str(date.today()),
        "ticker": TICKER,
        "action": "watch",
        "bucket": "growth",
        "depth": "compact",
        "entry_zone_low": None,
        "entry_zone_high": None,
        "valid_until": None,
        "stop": None,
        "target": None,
        "confidence": "medium",
        "bull": "verification",
        "bear": "verification",
        "decisive_factor": "verification",
        "risk_verdict": "approved",
        "reason": "verification",
        "score": 60,
        "price_at_suggestion": "10.00",
        "evidence_as_of": "2026-09-02T17:00:00+00:00",
        "invalidation_price": None,
    }


def _publication():
    return {
        "market_date": str(date.today()),
        "phase": "intraday",
        "kind": "brief",
        "template_version": 1,
        "rendered_body": "rollback-only gateway verification",
        "rendered_hash": "b" * 64,
        "status": "ready",
        "holding_state": [],
    }


def _migration_present(cur):
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='suggestions'
            AND column_name='evaluation_id'
        )
        """
    )
    return cur.fetchone()[0]


def _verify_preflight_and_apply_twice(cur, migration):
    already_present = _migration_present(cur)
    if not already_present:
        cur.execute(
            """
            SELECT count(*) FROM public.suggestions
            WHERE lower(trim(action)) NOT IN ('buy','add','hold','reduce','trim','sell','exit','watch','avoid')
               OR (confidence IS NOT NULL AND lower(trim(confidence)) NOT IN ('low','medium','high'))
               OR (bucket IS NOT NULL AND lower(trim(bucket)) NOT IN ('core','growth','speculative'))
            """
        )
        if cur.fetchone()[0]:
            _expect_db_error(cur, lambda: cur.execute(migration))
            # Existing production labels are never guessed by the migration. Normalize a
            # rollback-only copy so the remaining RPC checks can run without changing data.
            cur.execute(
                """
                UPDATE public.suggestions SET
                  action = CASE WHEN lower(trim(action)) IN
                    ('buy','add','hold','reduce','trim','sell','exit','watch','avoid')
                    THEN action ELSE 'watch' END,
                  confidence = CASE WHEN confidence IS NULL OR lower(trim(confidence)) IN
                    ('low','medium','high') THEN confidence ELSE NULL END,
                  bucket = CASE WHEN bucket IS NULL OR lower(trim(bucket)) IN
                    ('core','growth','speculative') THEN bucket ELSE NULL END
                """
            )
        cur.execute(
            "INSERT INTO public.suggestions(date,ticker,action) VALUES (%s,%s,'Buy') RETURNING id",
            (date.today(), TICKER),
        )
        legacy_id = cur.fetchone()[0]
        cur.execute("SAVEPOINT legacy_preflight")
        cur.execute(
            "INSERT INTO public.suggestions(date,ticker,action) VALUES (%s,%s,'invented')",
            (date.today(), TICKER),
        )
        try:
            cur.execute(migration)
        except psycopg.Error:
            cur.execute("ROLLBACK TO SAVEPOINT legacy_preflight")
            cur.execute("RELEASE SAVEPOINT legacy_preflight")
        else:
            raise RuntimeError("unknown legacy action did not abort migration preflight")
        cur.execute(migration)
        cur.execute(
            """
            SELECT e.policy_status FROM public.suggestions s
            JOIN public.decision_evaluations e ON e.id=s.evaluation_id WHERE s.id=%s
            """,
            (legacy_id,),
        )
        _require(cur.fetchone()[0] == "legacy_unverified", "legacy row was not backfilled")
    else:
        _expect_db_error(
            cur,
            lambda: cur.execute(
                "INSERT INTO public.suggestions(date,ticker,action,evaluation_id) "
                "VALUES (%s,%s,'invented',gen_random_uuid())",
                (date.today(), TICKER),
            ),
        )
        cur.execute(migration)
    cur.execute(migration)


def _verify_legacy_import(cur):
    before = cur.execute(
        "SELECT count(*) FROM public.suggestions WHERE ticker=%s", (TICKER,)
    ).fetchone()[0]
    receipt = _call(
        cur,
        "import_legacy_suggestion",
        Jsonb({"date": str(date.today()), "ticker": TICKER, "action": "Trim"}),
    )
    cur.execute(
        """
        SELECT e.policy_status,s.action,s.decision_source
        FROM public.suggestions s JOIN public.decision_evaluations e ON e.id=s.evaluation_id
        WHERE s.id=%s
        """,
        (receipt["suggestion_id"],),
    )
    _require(cur.fetchone() == ("legacy_unverified", "reduce", "legacy"), "legacy import linkage failed")
    _expect_db_error(
        cur,
        lambda: _call(
            cur,
            "import_legacy_suggestion",
            Jsonb({"date": str(date.today()), "ticker": TICKER, "action": "invented"}),
        ),
    )
    after = cur.execute(
        "SELECT count(*) FROM public.suggestions WHERE ticker=%s", (TICKER,)
    ).fetchone()[0]
    _require(after == before + 1, "failed legacy import left a partial row")


def _verify_request_leases(cur):
    request_id, first_lease = _claim(cur, "start_run")
    duplicate = _call(cur, "claim_market_gateway_request", request_id, "start_run", None)
    _require(duplicate.get("status") == "REQUEST_IN_PROGRESS", "fresh request lease was stolen")
    cur.execute(
        "UPDATE public.market_gateway_requests SET claimed_at=now()-interval '6 minutes' WHERE request_id=%s",
        (request_id,),
    )
    reacquired = _call(cur, "claim_market_gateway_request", request_id, "start_run", None)
    _require(reacquired.get("claimed") is True, "stale request lease was not reacquired")
    _expect_db_error(
        cur,
        lambda: _call(
            cur,
            "complete_market_gateway_request",
            request_id,
            first_lease,
            "completed",
            Jsonb({"bad": "old lease"}),
        ),
    )
    _call(
        cur,
        "complete_market_gateway_request",
        request_id,
        reacquired["lease_token"],
        "completed",
        Jsonb({"ok": True}),
    )


def _verify_start_run(cur):
    request_id, lease = _claim(cur, "start_run")
    first = _call(cur, "start_market_analysis_run", request_id, lease, "intraday")
    _expect_db_error(
        cur,
        lambda: _call(cur, "start_market_analysis_run", request_id, lease, "post-market"),
    )
    second = _call(cur, "start_market_analysis_run", request_id, lease, "intraday")
    _require(first["run_id"] == second["run_id"], "start_run created duplicate runs")
    return first["run_id"]


def _verify_decision_transaction(cur, run_id):
    cur.execute(
        "INSERT INTO public.market_policy_config(version,config,active) VALUES (%s,'{}',false) "
        "ON CONFLICT (version) DO UPDATE SET config=EXCLUDED.config",
        (POLICY_VERSION,),
    )
    _call(cur, "activate_market_policy_config", POLICY_VERSION)
    request_id, lease = _claim(cur, "evaluate_and_publish", run_id)
    candidate_id, evaluation_id = uuid4(), uuid4()
    evaluation = _evaluation(request_id, candidate_id, evaluation_id)
    malformed = Jsonb([{"ticker": TICKER}])
    _expect_db_error(
        cur,
        lambda: _call(
            cur,
            "apply_market_decision_bundle",
            request_id,
            run_id,
            lease,
            POLICY_VERSION,
            Jsonb([evaluation]),
            malformed,
            Jsonb(_publication()),
        ),
    )
    cur.execute("SELECT count(*) FROM public.decision_evaluations WHERE request_id=%s", (request_id,))
    _require(cur.fetchone()[0] == 0, "malformed decision left an evaluation")
    first = _call(
        cur,
        "apply_market_decision_bundle",
        request_id,
        run_id,
        lease,
        POLICY_VERSION,
        Jsonb([evaluation]),
        Jsonb([_suggestion(candidate_id, evaluation_id)]),
        Jsonb(_publication()),
    )
    second = _call(
        cur,
        "apply_market_decision_bundle",
        request_id,
        run_id,
        lease,
        POLICY_VERSION,
        Jsonb([evaluation]),
        Jsonb([_suggestion(candidate_id, evaluation_id)]),
        Jsonb(_publication()),
    )
    _require(second.get("duplicate") is True, "decision retry was not idempotent")
    cur.execute("SELECT count(*) FROM public.suggestions WHERE evaluation_id=%s", (evaluation_id,))
    _require(cur.fetchone()[0] == 1, "decision retry duplicated suggestion")
    return request_id, first["publication_id"], evaluation_id


def _verify_artifacts(cur, run_id):
    request_id, lease = _claim(cur, "record_artifacts", run_id)
    marker = f"gateway-verifier-{request_id}"
    mixed = [
        {"kind": "lesson", "entry_date": str(date.today()), "category": "verification", "content": marker},
        {"kind": "unsupported"},
    ]
    _expect_db_error(
        cur,
        lambda: _call(cur, "apply_market_artifacts", request_id, run_id, lease, Jsonb(mixed)),
    )
    cur.execute("SELECT count(*) FROM public.lessons WHERE content=%s", (marker,))
    _require(cur.fetchone()[0] == 0, "mixed artifact batch was not atomic")
    valid = [{"kind": "lesson", "entry_date": str(date.today()), "category": "verification", "content": marker}]
    receipt = _call(cur, "apply_market_artifacts", request_id, run_id, lease, Jsonb(valid))
    replay = _call(cur, "claim_market_gateway_request", request_id, "record_artifacts", run_id)
    _require(replay.get("response") == receipt, "artifact retry did not return stored receipt")

    cur.execute(
        "INSERT INTO public.paper_watches(ticker,created,status,opened_run_id) VALUES (%s,%s,'active',%s) RETURNING id",
        (TICKER, date.today(), run_id),
    )
    watch_id = cur.fetchone()[0]
    close_request, close_lease = _claim(cur, "record_artifacts", run_id)
    close_marker = f"gateway-close-{close_request}"
    bad_close = [
        {"kind": "lesson", "entry_date": str(date.today()), "category": "verification", "content": close_marker},
        {"kind": "paper_watch_close", "watch_id": watch_id, "ticker": "TSTBAD", "closed_date": str(date.today()), "close_price": "10"},
    ]
    _expect_db_error(
        cur,
        lambda: _call(cur, "apply_market_artifacts", close_request, run_id, close_lease, Jsonb(bad_close)),
    )
    cur.execute("SELECT count(*) FROM public.lessons WHERE content=%s", (close_marker,))
    _require(cur.fetchone()[0] == 0, "failed watch close left another artifact")


def _verify_publication_and_immutability(cur, request_id, evaluation_id):
    claimed = _call(cur, "claim_market_publication", request_id)
    _require(claimed.get("claimed") is True, "ready publication was not claimed")
    cur.execute(
        "UPDATE public.market_publications SET sending_started_at=now()-interval '6 minutes' "
        "WHERE idempotency_key=%s",
        (request_id,),
    )
    stale = _call(cur, "claim_market_publication", request_id)
    again = _call(cur, "claim_market_publication", request_id)
    _require(stale.get("status") == "delivery_unknown", "stale send did not become unknown")
    _require(again.get("claimed") is False, "unknown delivery was reclaimed")
    _expect_db_error(
        cur,
        lambda: cur.execute(
            "UPDATE public.decision_evaluations SET final_action='hold' WHERE id=%s",
            (evaluation_id,),
        ),
    )
    _expect_db_error(
        cur,
        lambda: cur.execute("DELETE FROM public.decision_evaluations WHERE id=%s", (evaluation_id,)),
    )


def _verify_rls_owners_and_grants(cur):
    cur.execute(
        """
        SELECT relname, relrowsecurity FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND relname=ANY(%s)
        """,
        (["market_gateway_requests", "market_policy_config", "decision_evaluations", "market_publications"],),
    )
    rows = cur.fetchall()
    _require(len(rows) == 4 and all(row[1] for row in rows), "gateway RLS is incomplete")
    for signature in RPC_SIGNATURES:
        cur.execute("SELECT to_regprocedure(%s)::oid", (f"public.{signature}",))
        oid = cur.fetchone()[0]
        _require(oid is not None, "gateway function is missing")
        cur.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM pg_catalog.pg_proc p,
                LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) acl
              WHERE p.oid=%s AND acl.grantee=0 AND acl.privilege_type='EXECUTE'
            )
            """,
            (oid,),
        )
        _require(cur.fetchone()[0] is False, "PUBLIC can execute gateway RPC")
        for role in ("anon", "authenticated"):
            cur.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, oid))
            _require(cur.fetchone()[0] is False, "unprivileged role can execute gateway RPC")
        cur.execute("SELECT has_function_privilege('service_role',%s,'EXECUTE')", (oid,))
        _require(cur.fetchone()[0] is True, "service_role cannot execute gateway RPC")
        cur.execute("SELECT pg_get_userbyid(proowner) FROM pg_catalog.pg_proc WHERE oid=%s", (oid,))
        _require(cur.fetchone()[0] not in ("anon", "authenticated", "service_role"), "unsafe RPC owner")


def main():
    connection = None
    try:
        connection = psycopg.connect(config.secret("postgres_url"))
        with connection.cursor() as cur:
            migration = MIGRATION.read_text()
            _verify_preflight_and_apply_twice(cur, migration)
            _verify_legacy_import(cur)
            _verify_request_leases(cur)
            run_id = _verify_start_run(cur)
            request_id, _publication_id, evaluation_id = _verify_decision_transaction(cur, run_id)
            _verify_artifacts(cur, run_id)
            _verify_publication_and_immutability(cur, request_id, evaluation_id)
            _verify_rls_owners_and_grants(cur)
        print("PASS: decision gateway migration is atomic and idempotent")
        return 0
    except Exception as exc:
        print(f"FAIL: decision gateway migration verification ({type(exc).__name__})")
        return 1
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
