from __future__ import annotations

from uuid import UUID

import pytest

from scripts import provision_operator


OPERATOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_staging_operator_provision_is_idempotent_and_does_not_expose_identity(
    foundation_database,
):
    with foundation_database.transaction(force_rollback=True):
        foundation_database.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, 'operator@invalid.example')",
            (OPERATOR_ID,),
        )
        first = provision_operator.provision_connection(foundation_database, OPERATOR_ID)
        second = provision_operator.provision_connection(foundation_database, OPERATOR_ID)

        assert first == second
        assert first == {
            "status": "provisioned",
            "operator_digest": provision_operator.operator_digest(OPERATOR_ID),
            "private_data": False,
        }
        assert str(OPERATOR_ID) not in str(first)
        assert foundation_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OPERATOR_ID,)
        ).fetchone() == ("active",)
        assert foundation_database.execute(
            "SELECT role FROM app.app_admins WHERE user_id = %s", (OPERATOR_ID,)
        ).fetchone() == ("operator",)


def test_staging_operator_provision_refuses_missing_or_deactivated_identity(
    foundation_database,
):
    with foundation_database.transaction(force_rollback=True):
        with pytest.raises(provision_operator.OperatorProvisionRejected, match="Auth user"):
            provision_operator.provision_connection(foundation_database, OPERATOR_ID)

        foundation_database.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, 'operator@invalid.example')",
            (OPERATOR_ID,),
        )
        foundation_database.execute(
            "INSERT INTO app.profiles (id, status) VALUES (%s, 'deactivated')",
            (OPERATOR_ID,),
        )
        with pytest.raises(provision_operator.OperatorProvisionRejected, match="not active"):
            provision_operator.provision_connection(foundation_database, OPERATOR_ID)


def test_staging_operator_target_requires_exact_staging_confirmation():
    provision_operator.authorize_target(
        project_ref="stock-agent-staging",
        environment="staging",
        confirmation="PROVISION STAGING OPERATOR stock-agent-staging",
    )
    for environment, confirmation in (
        ("production", "PROVISION STAGING OPERATOR stock-agent-staging"),
        ("staging", "wrong"),
    ):
        with pytest.raises(provision_operator.OperatorProvisionRejected):
            provision_operator.authorize_target(
                project_ref="stock-agent-staging",
                environment=environment,
                confirmation=confirmation,
            )
