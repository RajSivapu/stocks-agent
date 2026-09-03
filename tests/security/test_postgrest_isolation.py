from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


@contextmanager
def as_user(connection, owner_id: str | None, role: str = "authenticated"):
    with connection.transaction(force_rollback=True):
        connection.execute(f"SET LOCAL ROLE {role}")
        if owner_id is not None:
            connection.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, true)", (owner_id,)
            )
        yield connection


@pytest.mark.parametrize(
    "view_name",
    [
        "profile",
        "today",
        "holdings",
        "transactions",
        "commands",
        "plans",
        "recommendations",
        "runs",
        "connections",
        "telegram_status",
        "settings",
        "market_quotes",
        "research",
        "run_timeline",
    ],
)
def test_anonymous_cannot_read_any_api_view(tenant_database, view_name):
    with as_user(tenant_database, None, role="anon") as connection:
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(f'SELECT * FROM api."{view_name}"').fetchall()


def test_owner_a_cannot_observe_owner_b_through_filters_or_direct_ids(tenant_database):
    owner_command_count = tenant_database.execute(
        "SELECT count(*) FROM app.portfolio_commands WHERE owner_id = %s", (OWNER_A,)
    ).fetchone()[0]
    with as_user(tenant_database, OWNER_A) as connection:
        holdings = connection.execute(
            "SELECT ticker, shares FROM api.holdings WHERE ticker = 'TSTAAA'"
        ).fetchall()
        transactions = connection.execute(
            "SELECT ticker, qty FROM api.transactions WHERE ticker = 'TSTAAA' ORDER BY id LIMIT 100 OFFSET 0"
        ).fetchall()
        plans = connection.execute(
            "SELECT ticker, amount FROM api.plans WHERE ticker = 'TSTVTI'"
        ).fetchall()

        assert holdings == [("TSTAAA", "1.00000000")]
        assert len(transactions) == 2
        assert all(ticker == "TSTAAA" and qty == "1.00000000" for ticker, qty in transactions)
        assert plans == [("TSTVTI", "300")]
        assert connection.execute(
            "SELECT count(*) FROM api.commands"
        ).fetchone() == (owner_command_count,)


def test_owner_b_sees_only_owner_b_even_with_same_natural_keys(tenant_database):
    with as_user(tenant_database, OWNER_B) as connection:
        assert connection.execute(
            "SELECT ticker, shares FROM api.holdings WHERE ticker = 'TSTAAA'"
        ).fetchall() == [("TSTAAA", "9.00000000")]
        assert connection.execute(
            "SELECT ticker, amount FROM api.plans WHERE ticker = 'TSTVTI'"
        ).fetchall() == [("TSTVTI", "500")]


def test_today_uses_canonical_new_york_market_date_not_run_start_date(tenant_database):
    run_id = uuid4()
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            """
            INSERT INTO app.analysis_runs(
              owner_id, id, kind, started_at, market_date, provider, model
            ) VALUES (
              %s, %s, 'intraday', TIMESTAMPTZ '2000-01-01 00:00:00+00',
              (now() AT TIME ZONE 'America/New_York')::date, 'claude', 'configured'
            )
            """,
            (OWNER_A, run_id),
        )
        with as_user(tenant_database, OWNER_A) as connection:
            assert connection.execute(
                "SELECT market_date, provider, model FROM api.today WHERE run_id = %s",
                (run_id,),
            ).fetchone() == (
                connection.execute(
                    "SELECT (now() AT TIME ZONE 'America/New_York')::date"
                ).fetchone()[0],
                "claude",
                "configured",
            )


def test_command_receipts_are_owner_only_and_omit_replayable_input(tenant_database):
    columns = tenant_database.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'api' AND table_name = 'commands'
        ORDER BY ordinal_position
        """
    ).fetchall()
    names = {name for (name,) in columns}
    assert "normalized_input" not in names
    assert "actor_key" not in names
    assert "idempotency_key" not in names

    owner_a_ids = {
        command_id for (command_id,) in tenant_database.execute(
            "SELECT id FROM app.portfolio_commands WHERE owner_id = %s", (OWNER_A,)
        ).fetchall()
    }
    owner_b_ids = {
        command_id for (command_id,) in tenant_database.execute(
            "SELECT id FROM app.portfolio_commands WHERE owner_id = %s", (OWNER_B,)
        ).fetchall()
    }
    with as_user(tenant_database, OWNER_A) as connection:
        observed = {
            command_id for (command_id,) in connection.execute(
                "SELECT id FROM api.commands"
            ).fetchall()
        }
    assert observed == owner_a_ids
    assert observed.isdisjoint(owner_b_ids)


def test_credential_and_telegram_identifiers_are_not_selectable(tenant_database):
    with as_user(tenant_database, OWNER_A) as connection:
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(
                "SELECT inbound_token_digest FROM app.agent_connections"
            ).fetchall()
    with as_user(tenant_database, OWNER_A) as connection:
        with pytest.raises(Exception, match="permission denied"):
            connection.execute("SELECT telegram_chat_id FROM app.telegram_links").fetchall()


def test_authenticated_role_has_no_direct_portfolio_mutation(tenant_database):
    with as_user(tenant_database, OWNER_A) as connection:
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(
                """
                INSERT INTO app.holdings (owner_id, ticker, shares, avg_cost)
                VALUES (%s, 'TSTNEW', 1, 1)
                """,
                (OWNER_B,),
            )


def test_unset_or_malformed_identity_fails_closed(tenant_database):
    with as_user(tenant_database, None) as connection:
        assert connection.execute("SELECT * FROM api.holdings").fetchall() == []
    with as_user(tenant_database, "not-a-uuid") as connection:
        with pytest.raises(Exception, match="invalid input syntax for type uuid"):
            connection.execute("SELECT * FROM api.holdings").fetchall()
