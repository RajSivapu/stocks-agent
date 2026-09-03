from __future__ import annotations

from contextlib import contextmanager

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
        "plans",
        "recommendations",
        "runs",
        "connections",
        "telegram_status",
        "settings",
        "market_quotes",
    ],
)
def test_anonymous_cannot_read_any_api_view(tenant_database, view_name):
    with as_user(tenant_database, None, role="anon") as connection:
        with pytest.raises(Exception, match="permission denied"):
            connection.execute(f'SELECT * FROM api."{view_name}"').fetchall()


def test_owner_a_cannot_observe_owner_b_through_filters_or_direct_ids(tenant_database):
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

        assert holdings == [("TSTAAA", 1)]
        assert len(transactions) == 2
        assert all(ticker == "TSTAAA" and qty == 1 for ticker, qty in transactions)
        assert plans == [("TSTVTI", 300)]


def test_owner_b_sees_only_owner_b_even_with_same_natural_keys(tenant_database):
    with as_user(tenant_database, OWNER_B) as connection:
        assert connection.execute(
            "SELECT ticker, shares FROM api.holdings WHERE ticker = 'TSTAAA'"
        ).fetchall() == [("TSTAAA", 9)]
        assert connection.execute(
            "SELECT ticker, amount FROM api.plans WHERE ticker = 'TSTVTI'"
        ).fetchall() == [("TSTVTI", 500)]


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
