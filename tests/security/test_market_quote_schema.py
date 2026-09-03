from __future__ import annotations

from decimal import Decimal

from tests.security.test_postgrest_isolation import OWNER_A, OWNER_B, as_user


def test_market_quote_view_exposes_only_owner_holding_or_radar_symbols(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            "INSERT INTO app.radar(owner_id, ticker) VALUES (%s, 'WATCHA')",
            (OWNER_A,),
        )
        for owner, ticker, price, digest in (
            (OWNER_A, "TSTAAA", "11.25", "a" * 64),
            (OWNER_A, "WATCHA", "21.50", "b" * 64),
            (OWNER_A, "HIDDEN", "31.75", "c" * 64),
            (OWNER_B, "TSTAAA", "99.99", "d" * 64),
        ):
            tenant_database.execute(
                """
                INSERT INTO app.market_quote_cache(
                  owner_id, ticker, price, provider, source_timestamp,
                  retrieved_at, session, adjustment_status, status, content_digest
                ) VALUES (%s, %s, %s, 'yahoo-chart', now(), now(), 'REGULAR',
                          'raw', 'fresh', %s)
                """,
                (owner, ticker, price, digest),
            )
        with as_user(tenant_database, OWNER_A) as connection:
            rows = connection.execute(
                "SELECT ticker, price FROM api.market_quotes ORDER BY ticker"
            ).fetchall()
            assert rows == [("TSTAAA", Decimal("11.25")), ("WATCHA", Decimal("21.50"))]
        with as_user(tenant_database, OWNER_B) as connection:
            rows = connection.execute(
                "SELECT ticker, price FROM api.market_quotes ORDER BY ticker"
            ).fetchall()
            assert rows == [("TSTAAA", Decimal("99.99"))]


def test_corporate_action_state_is_owner_bound_and_alert_suppressing(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            """
            INSERT INTO app.corporate_action_states(
              owner_id, ticker, state, event_type, detected_at, reason_code
            ) VALUES (%s, 'TSTAAA', 'needs_review', 'split', now(),
                      'unverified_corporate_action')
            """,
            (OWNER_A,),
        )
        tenant_database.execute(
            """
            INSERT INTO app.market_quote_cache(
              owner_id, ticker, price, provider, source_timestamp,
              retrieved_at, session, adjustment_status, status, content_digest
            ) VALUES (%s, 'TSTAAA', 12, 'yahoo-chart', now(), now(), 'REGULAR',
                      'corporate_action_pending', 'fresh', %s)
            """,
            (OWNER_A, "f" * 64),
        )
        with as_user(tenant_database, OWNER_A) as connection:
            row = connection.execute(
                "SELECT corporate_action_state, alerts_suppressed FROM api.market_quotes"
            ).fetchone()
            assert row == ("needs_review", True)
