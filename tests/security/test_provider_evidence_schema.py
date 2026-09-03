from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


def test_run_evidence_is_owner_bound_immutable_and_content_addressed(tenant_database):
    run_id = uuid4()
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "INSERT INTO app.analysis_runs(owner_id, id, kind) VALUES (%s, %s, 'intraday')",
            (OWNER_A, run_id),
        )
        tenant_database.execute(
            """
            INSERT INTO app.run_evidence(
              owner_id, run_id, evidence_id, category, source_identifier,
              retrieved_at, content_hash, claims, status
            ) VALUES (%s, %s, 'quote-current', 'market_snapshot', 'yahoo-chart',
                      now(), %s, %s, 'fresh')
            """,
            (OWNER_A, run_id, "a" * 64, Jsonb(["server quote"])),
        )
        with pytest.raises(Exception, match="duplicate key"):
            with tenant_database.transaction(force_rollback=True):
                tenant_database.execute(
                    """
                    INSERT INTO app.run_evidence(
                      owner_id, run_id, evidence_id, category, source_identifier,
                      retrieved_at, content_hash, claims, status
                    ) VALUES (%s, %s, 'quote-current', 'market_snapshot', 'other',
                              now(), %s, '[]', 'fresh')
                    """,
                    (OWNER_A, run_id, "b" * 64),
                )
        with pytest.raises(Exception, match="foreign key"):
            with tenant_database.transaction(force_rollback=True):
                tenant_database.execute(
                    """
                    INSERT INTO app.run_evidence(
                      owner_id, run_id, evidence_id, category, source_identifier,
                      retrieved_at, content_hash, claims, status
                    ) VALUES (%s, %s, 'cross-owner', 'news', 'issuer',
                              now(), %s, '[]', 'fresh')
                    """,
                    (OWNER_B, run_id, "c" * 64),
                )
        with pytest.raises(Exception, match="owner_id is immutable"):
            with tenant_database.transaction(force_rollback=True):
                tenant_database.execute(
                    "UPDATE app.run_evidence SET owner_id = %s WHERE owner_id = %s AND run_id = %s",
                    (OWNER_B, OWNER_A, run_id),
                )


def test_source_search_receipt_requires_explicit_bounded_result(tenant_database):
    run_id = uuid4()
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "INSERT INTO app.analysis_runs(owner_id, id, kind) VALUES (%s, %s, 'intraday')",
            (OWNER_A, run_id),
        )
        tenant_database.execute(
            """
            INSERT INTO app.source_search_receipts(
              owner_id, run_id, searched_at, categories, sources,
              result_status, content_hash
            ) VALUES (%s, %s, now(), ARRAY['news','filing'], %s,
                      'no_new_material_evidence', %s)
            """,
            (OWNER_A, run_id, Jsonb(["issuer", "sec"]), "d" * 64),
        )
        with pytest.raises(Exception):
            with tenant_database.transaction(force_rollback=True):
                tenant_database.execute(
                    """
                    INSERT INTO app.source_search_receipts(
                      owner_id, run_id, searched_at, categories, sources,
                      result_status, content_hash
                    ) VALUES (%s, %s, now(), ARRAY[]::text[], '[]', 'nothing', %s)
                    """,
                    (OWNER_A, run_id, "e" * 64),
                )
