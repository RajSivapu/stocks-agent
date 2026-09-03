from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from scripts import migrate_single_owner_to_tenant as migration
from scripts import verify_multitenancy_migration as verifier


OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
REQUEST_ID = UUID("44444444-4444-4444-8444-444444444444")
EVALUATION_ID = UUID("55555555-5555-4555-8555-555555555555")
PUBLICATION_ID = UUID("66666666-6666-4666-8666-666666666666")
COMMAND_ID = UUID("77777777-7777-4777-8777-777777777777")


def seed_owner(connection, *, include_portfolio=True):
    connection.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
        (OWNER_ID, "owner@example.com"),
    )
    if not include_portfolio:
        return
    connection.execute(
        "INSERT INTO public.holdings (ticker, shares, avg_cost, bucket) VALUES ('TSTAAA', 2, 10, 'growth')"
    )
    connection.execute(
        "INSERT INTO public.transactions (ticker, side, qty, price, executed_on) VALUES ('TSTAAA', 'buy', 2, 10, DATE '2026-08-17')"
    )
    connection.execute(
        """
        INSERT INTO public.analysis_runs (id, kind, status)
        VALUES (%s, 'pre-market', 'completed')
        """,
        (RUN_ID,),
    )
    connection.execute(
        """
        INSERT INTO public.market_gateway_requests
          (request_id, operation, run_id, status, lease_token, finished_at)
        VALUES (%s, 'finish_run', %s, 'completed', %s, now())
        """,
        (REQUEST_ID, RUN_ID, UUID("88888888-8888-4888-8888-888888888888")),
    )
    connection.execute(
        "INSERT INTO public.market_policy_config (version, config, active) VALUES (1, '{}', true)"
    )
    connection.execute(
        """
        INSERT INTO public.decision_evaluations
          (id, request_id, run_id, candidate_id, policy_version, input_digest,
           raw_action, final_action, policy_status)
        VALUES (%s, %s, %s, %s, 1, %s, 'watch', 'watch', 'approved')
        """,
        (
            EVALUATION_ID,
            REQUEST_ID,
            RUN_ID,
            UUID("99999999-9999-4999-8999-999999999999"),
            "a" * 64,
        ),
    )
    suggestion_id = connection.execute(
        """
        INSERT INTO public.suggestions
          (date, ticker, action, run_id, evaluation_id, decision_source)
        VALUES (DATE '2026-09-02', 'TSTAAA', 'watch', %s, %s, 'gateway')
        RETURNING id
        """,
        (RUN_ID, EVALUATION_ID),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO public.suggestion_grades (suggestion_id, result) VALUES (%s, 'pending')",
        (suggestion_id,),
    )
    connection.execute(
        """
        INSERT INTO public.portfolio_commands
          (id, telegram_update_id, chat_id, user_id, operation, ticker, qty, price,
           expected_shares, status)
        VALUES (%s, 9001, 100, 101, 'buy', 'TSTAAA', 2, 10, 0, 'applied')
        """,
        (COMMAND_ID,),
    )
    connection.execute(
        """
        INSERT INTO public.owner_investment_plans
          (ticker, bucket, amount, cadence, next_due_on, due_day)
        VALUES ('TSTVTI', 'core', 300, 'monthly', DATE '2026-09-21', 21)
        """
    )
    connection.execute(
        """
        INSERT INTO public.market_publications
          (id, idempotency_key, run_id, market_date, phase, kind, template_version,
           rendered_body, rendered_hash, status)
        VALUES (%s, %s, %s, DATE '2026-09-02', 'pre-market', 'brief', 1,
                'test body', %s, 'delivered')
        """,
        (PUBLICATION_ID, REQUEST_ID, RUN_ID, "b" * 64),
    )


def run_in_rollback(connection, callback):
    with connection.transaction(force_rollback=True):
        return callback()


def test_backfill_moves_all_owner_tables_and_is_idempotent(foundation_database):
    def scenario():
        seed_owner(foundation_database)

        receipt = migration.migrate_connection(
            foundation_database,
            owner_email="owner@example.com",
            rollback_only=False,
        )
        again = migration.migrate_connection(
            foundation_database,
            owner_email="OWNER@example.com",
            rollback_only=False,
        )

        assert receipt == again
        assert receipt["passed"] is True
        assert receipt["owner_id_hash"] == hashlib.sha256(str(OWNER_ID).encode()).hexdigest()
        assert receipt["before_counts"] == receipt["after_counts"]
        assert receipt["row_digest"]
        assert receipt["relationship_digest"]
        assert foundation_database.execute(
            "SELECT to_regclass('public.holdings') IS NULL"
        ).fetchone()[0]
        assert foundation_database.execute(
            "SELECT count(*) FROM app.holdings WHERE owner_id = %s", (OWNER_ID,)
        ).fetchone()[0] == 1
        assert foundation_database.execute(
            "SELECT role FROM app.app_admins WHERE user_id = %s", (OWNER_ID,)
        ).fetchone()[0] == "operator"
        assert foundation_database.execute(
            "SELECT to_regprocedure('machine.backfill_single_owner_to_tenant(uuid)') IS NULL"
        ).fetchone()[0]
        assert foundation_database.execute(
            """
            SELECT count(*) = 0
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'machine' AND p.proname LIKE 'single_owner_%'
            """
        ).fetchone()[0]

        owner_fks = foundation_database.execute(
            """
            SELECT count(*)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'app' AND c.contype = 'f'
              AND array_length(c.conkey, 1) = 2
            """
        ).fetchone()[0]
        assert owner_fks >= 15

        with pytest.raises(Exception, match="owner_id is immutable"):
            foundation_database.execute(
                "UPDATE app.holdings SET owner_id = %s WHERE ticker = 'TSTAAA'",
                (OTHER_ID,),
            )

    run_in_rollback(foundation_database, scenario)


def test_backfill_rejects_unknown_existing_owner(foundation_database):
    def scenario():
        seed_owner(foundation_database)
        foundation_database.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, 'other@example.com')",
            (OTHER_ID,),
        )
        foundation_database.execute(
            "UPDATE public.holdings SET owner_id = %s WHERE ticker = 'TSTAAA'",
            (OTHER_ID,),
        )

        with pytest.raises(migration.MigrationRejected, match="unknown owner rows"):
            migration.migrate_connection(
                foundation_database, owner_email="owner@example.com", rollback_only=True
            )

    run_in_rollback(foundation_database, scenario)


def test_backfill_rejects_orphaned_relationship(foundation_database):
    def scenario():
        seed_owner(foundation_database, include_portfolio=False)
        foundation_database.execute(
            "ALTER TABLE public.suggestion_grades DROP CONSTRAINT suggestion_grades_suggestion_id_fkey"
        )
        foundation_database.execute(
            "INSERT INTO public.suggestion_grades (suggestion_id, result) VALUES (987654, 'orphan')"
        )

        with pytest.raises(migration.MigrationRejected, match="orphaned relationship"):
            migration.migrate_connection(
                foundation_database, owner_email="owner@example.com", rollback_only=True
            )

    run_in_rollback(foundation_database, scenario)


def test_backfill_rejects_duplicate_owner_natural_key(foundation_database):
    def scenario():
        seed_owner(foundation_database, include_portfolio=False)
        foundation_database.execute("ALTER TABLE public.holdings DROP CONSTRAINT holdings_pkey")
        foundation_database.execute(
            "ALTER TABLE public.holdings DROP CONSTRAINT holdings_owner_ticker_key"
        )
        foundation_database.execute(
            "INSERT INTO public.holdings (ticker, shares, avg_cost) VALUES ('TSTDUP', 1, 1), ('TSTDUP', 2, 2)"
        )

        with pytest.raises(migration.MigrationRejected, match="duplicate owner key"):
            migration.migrate_connection(
                foundation_database, owner_email="owner@example.com", rollback_only=True
            )

    run_in_rollback(foundation_database, scenario)


def test_backfill_rejects_unsupported_legacy_label(foundation_database):
    def scenario():
        seed_owner(foundation_database, include_portfolio=False)
        foundation_database.execute(
            "INSERT INTO public.holdings (ticker, shares, avg_cost, bucket) VALUES ('TSTBAD', 1, 1, 'yolo')"
        )

        with pytest.raises(migration.MigrationRejected, match="unsupported legacy label"):
            migration.migrate_connection(
                foundation_database, owner_email="owner@example.com", rollback_only=True
            )

    run_in_rollback(foundation_database, scenario)


@pytest.mark.parametrize(
    ("before_counts", "after_counts", "before_digest", "after_digest", "message"),
    [
        ({"holdings": 1}, {"holdings": 0}, "a", "a", "row count changed"),
        ({"holdings": 1}, {"holdings": 1}, "a", "b", "row digest changed"),
    ],
)
def test_parity_verifier_rejects_count_or_digest_change(
    before_counts, after_counts, before_digest, after_digest, message
):
    with pytest.raises(migration.MigrationRejected, match=message):
        migration.assert_parity(
            before_counts=before_counts,
            after_counts=after_counts,
            before_digest=before_digest,
            after_digest=after_digest,
        )


def test_owner_email_must_resolve_exactly_once(foundation_database):
    def scenario():
        with pytest.raises(migration.MigrationRejected, match="exactly one Auth user"):
            migration.resolve_owner_id(foundation_database, "missing@example.com")

    run_in_rollback(foundation_database, scenario)


def test_production_cutover_requires_exact_confirmation():
    with pytest.raises(migration.MigrationRejected, match="production cutover"):
        migration.authorize_target(
            project_ref="prod-ref",
            production_project_ref="prod-ref",
            production_cutover=False,
            confirmation=None,
            owner_email="owner@example.com",
        )
    with pytest.raises(migration.MigrationRejected, match="confirmation"):
        migration.authorize_target(
            project_ref="prod-ref",
            production_project_ref="prod-ref",
            production_cutover=True,
            confirmation="yes",
            owner_email="owner@example.com",
        )

    migration.authorize_target(
        project_ref="prod-ref",
        production_project_ref="prod-ref",
        production_cutover=True,
        confirmation="MIGRATE prod-ref FOR owner@example.com",
        owner_email="owner@example.com",
    )


def test_disposable_rollback_verifier_uses_synthetic_owner_and_leaves_no_cutover():
    result = verifier.verify_disposable_rollback()

    assert result["receipt"]["passed"] is True
    assert result["receipt"]["before_counts"] == result["receipt"]["after_counts"]
    assert result["rolled_back"] is True
    assert result["synthetic_tickers"] == ["TSTAAA", "TSTVTI"]
