from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


def connection(tenant_database, owner):
    return tenant_database.execute(
        "SELECT public_id FROM app.agent_connections WHERE owner_id = %s", (owner,)
    ).fetchone()[0]


def rpc(tenant_database, name, value):
    try:
        with tenant_database.transaction():
            tenant_database.execute("SET LOCAL ROLE stock_agent_gateway")
            return tenant_database.execute(
                f"SELECT machine.{name}(%s::jsonb)", (Jsonb(value),)
            ).fetchone()[0]
    finally:
        tenant_database.execute("RESET ROLE")


def request(public_id, secret, operation, run_id=None, payload=None, request_id=None, dry_run=False):
    return {
        "connection_id": str(public_id),
        "secret_digest": secret,
        "contract_version": 2,
        "operation": operation,
        "request_id": str(request_id or uuid4()),
        "run_id": str(run_id) if run_id else None,
        "dry_run": dry_run,
        "payload": payload if payload is not None else {},
    }


def start(tenant_database, owner=OWNER_A, phase="intraday", secret="aa" * 32):
    public_id = connection(tenant_database, owner)
    result = rpc(tenant_database, "agent_start_run", request(
        public_id, secret, "start_run", payload={
            "phase": phase, "market_date": "2026-09-03", "trigger_request_id": None,
        },
    ))
    return public_id, result["run_id"]


def test_gateway_digest_comparison_checks_all_32_bytes(tenant_database):
    assert tenant_database.execute(
        "SELECT machine.agent_constant_time_equal(decode(repeat('aa', 32), 'hex'), decode(repeat('aa', 32), 'hex'))"
    ).fetchone()[0]
    assert not tenant_database.execute(
        "SELECT machine.agent_constant_time_equal(decode(repeat('aa', 32), 'hex'), decode(repeat('aa', 31) || 'ab', 'hex'))"
    ).fetchone()[0]
    assert not tenant_database.execute(
        "SELECT machine.agent_constant_time_equal(decode(repeat('aa', 31), 'hex'), decode(repeat('aa', 32), 'hex'))"
    ).fetchone()[0]


def test_gateway_fixture_has_active_v2_digest_bound_credentials(tenant_database):
    public_id = connection(tenant_database, OWNER_A)
    assert tenant_database.execute(
        """
        SELECT status, contract_version,
               machine.agent_constant_time_equal(inbound_token_digest, decode(repeat('aa', 32), 'hex'))
        FROM app.agent_connections
        WHERE public_id = %s
        """,
        (public_id,),
    ).fetchone() == ("active", 2, True)


def test_gateway_credential_is_owner_bound_rotatable_and_revocation_is_indistinguishable(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        public_a, run_a = start(tenant_database)
        public_b = connection(tenant_database, OWNER_B)
        with pytest.raises(Exception, match="agent credential or run unavailable"):
            rpc(tenant_database, "agent_read_bounded_context", request(
                public_b, "bb" * 32, "read_bounded_context", run_a,
            ))

        tenant_database.execute(
            "UPDATE app.agent_connections SET inbound_token_digest = decode(%s, 'hex') WHERE public_id = %s",
            ("cc" * 32, public_a),
        )
        with pytest.raises(Exception, match="agent credential or run unavailable"):
            rpc(tenant_database, "agent_read_bounded_context", request(
                public_a, "aa" * 32, "read_bounded_context", run_a,
            ))
        assert rpc(tenant_database, "agent_read_bounded_context", request(
            public_a, "cc" * 32, "read_bounded_context", run_a,
        ))["run_id"] == run_a
        tenant_database.execute(
            "UPDATE app.agent_connections SET status = 'revoked' WHERE public_id = %s", (public_a,)
        )
        for candidate in (public_a, uuid4()):
            with pytest.raises(Exception, match="agent credential or run unavailable"):
                rpc(tenant_database, "agent_read_bounded_context", request(
                    candidate, "cc" * 32, "read_bounded_context", run_a,
                ))


def test_gateway_request_replay_is_digest_bound_and_run_operations_are_capped(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        public_id, run_id = start(tenant_database)
        replay_id = uuid4()
        first = rpc(tenant_database, "agent_read_bounded_context", request(
            public_id, "aa" * 32, "read_bounded_context", run_id, request_id=replay_id,
        ))
        duplicate = rpc(tenant_database, "agent_read_bounded_context", request(
            public_id, "aa" * 32, "read_bounded_context", run_id, request_id=replay_id,
        ))
        assert duplicate == first
        with pytest.raises(Exception, match="request replay conflict"):
            rpc(tenant_database, "agent_read_bounded_context", request(
                public_id, "aa" * 32, "read_bounded_context", run_id,
                payload={"unexpected": True}, request_id=replay_id,
            ))
        for _ in range(11):
            rpc(tenant_database, "agent_read_bounded_context", request(
                public_id, "aa" * 32, "read_bounded_context", run_id,
            ))
        with pytest.raises(Exception, match="run operation limit reached"):
            rpc(tenant_database, "agent_read_bounded_context", request(
                public_id, "aa" * 32, "read_bounded_context", run_id,
            ))


def test_dry_run_authenticates_but_creates_no_gateway_or_run_rows(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        public_id = connection(tenant_database, OWNER_A)
        before = tenant_database.execute(
            "SELECT count(*) FROM app.market_gateway_requests WHERE owner_id = %s", (OWNER_A,)
        ).fetchone()[0]
        result = rpc(tenant_database, "agent_start_run", request(
            public_id, "aa" * 32, "start_run", dry_run=True,
            payload={"phase": "intraday", "market_date": "2026-09-03", "trigger_request_id": None},
        ))
        after = tenant_database.execute(
            "SELECT count(*) FROM app.market_gateway_requests WHERE owner_id = %s", (OWNER_A,)
        ).fetchone()[0]
        assert result["status"] == "dry_run"
        assert after == before


def test_artifact_rpc_writes_only_allowlisted_owner_rows(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        public_id, run_id = start(tenant_database)
        receipt = rpc(tenant_database, "agent_record_permitted_artifacts", request(
            public_id, "aa" * 32, "record_permitted_artifacts", run_id,
            payload={"mutations": [{
                "kind": "lesson", "entry_date": "2026-09-03",
                "category": "process", "content": "Use independent current evidence.",
            }]},
        ))
        assert receipt["counts"] == {"lesson": 1}
        assert str(tenant_database.execute(
            "SELECT owner_id FROM app.lessons WHERE content = 'Use independent current evidence.'"
        ).fetchone()[0]) == OWNER_A
        with pytest.raises(Exception, match="artifact kind is not permitted"):
            rpc(tenant_database, "agent_record_permitted_artifacts", request(
                public_id, "aa" * 32, "record_permitted_artifacts", run_id,
                payload={"mutations": [{"kind": "write_file", "path": "config/watchlist.json"}]},
            ))


def test_gateway_limits_six_runs_per_day_and_one_on_demand_per_hour(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        public_id = connection(tenant_database, OWNER_A)
        for _ in range(6):
            rpc(tenant_database, "agent_start_run", request(
                public_id, "aa" * 32, "start_run",
                payload={"phase": "intraday", "market_date": "2026-09-03", "trigger_request_id": None},
            ))
        with pytest.raises(Exception, match="daily run limit reached"):
            rpc(tenant_database, "agent_start_run", request(
                public_id, "aa" * 32, "start_run",
                payload={"phase": "post-market", "market_date": "2026-09-03", "trigger_request_id": None},
            ))

    with tenant_database.transaction(force_rollback=True):
        public_id, _ = start(tenant_database, phase="on-demand")
        with pytest.raises(Exception, match="on-demand run limit reached"):
            rpc(tenant_database, "agent_start_run", request(
                public_id, "aa" * 32, "start_run",
                payload={"phase": "on-demand", "market_date": "2026-09-03", "trigger_request_id": None},
            ))


def test_analysis_submission_requires_server_owned_current_run_evidence(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        public_id, run_id = start(tenant_database)
        dimensions = {
            name: {"status": "supported", "summary": "Reviewed.", "evidence_ids": ["quote"]}
            for name in (
                "fundamentals", "valuation", "catalyst", "technical", "portfolio_fit",
                "downside", "bear_case", "invalidation", "decisive_factor",
            )
        }
        payload = {
            "phase": "intraday", "market_date": "2026-09-03", "title": "Review",
            "suggestion_only": True, "provider": "claude", "model": "configured",
            "analyst": {"completed": True, "action": "watch", "confidence": "medium", "thesis": "Mixed."},
            "checker": {"completed": True, "verdict": "approve", "reason": "Bounded."},
            "dimensions": dimensions,
            "evidence_refs": [{"evidence_id": "quote", "run_id": run_id, "content_hash": "c" * 64}],
            "prior_suggestion_ids": [], "candidates": [],
        }
        with pytest.raises(Exception, match="evidence_missing"):
            rpc(tenant_database, "agent_submit_analysis", request(
                public_id, "aa" * 32, "submit_analysis", run_id, payload=payload,
            ))
        tenant_database.execute(
            """
            INSERT INTO app.run_evidence(
              owner_id, run_id, evidence_id, category, source_identifier,
              retrieved_at, content_hash, status
            ) VALUES (%s, %s, 'quote', 'market_snapshot', 'server', now(), %s, 'fresh')
            """,
            (OWNER_A, run_id, "c" * 64),
        )
        tenant_database.execute(
            """
            INSERT INTO app.source_search_receipts(
              owner_id, run_id, searched_at, categories, sources, result_status, content_hash
            ) VALUES (%s, %s, now(), ARRAY['news'], '["issuer"]',
                      'no_new_material_evidence', %s)
            """,
            (OWNER_A, run_id, "d" * 64),
        )
        receipt = rpc(tenant_database, "agent_submit_analysis", request(
            public_id, "aa" * 32, "submit_analysis", run_id, payload=payload,
        ))
        assert receipt["status"] == "accepted"
        assert tenant_database.execute(
            "SELECT count(*) FROM app.agent_analysis_submissions WHERE owner_id = %s AND run_id = %s",
            (OWNER_A, run_id),
        ).fetchone()[0] == 1
