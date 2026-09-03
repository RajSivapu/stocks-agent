from __future__ import annotations

import pytest


MACHINE_ROLES = {
    "stock_agent_gateway",
    "stock_agent_scheduler",
    "stock_agent_telegram",
    "stock_agent_backup",
}


def test_machine_privilege_roles_are_nonlogin_and_cannot_bypass_rls(tenant_database):
    rows = tenant_database.execute(
        """
        SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb
        FROM pg_roles
        WHERE rolname = ANY(%s)
        """,
        (list(MACHINE_ROLES),),
    ).fetchall()

    assert {row[0] for row in rows} == MACHINE_ROLES
    assert all(not any(flags) for _, *flags in rows)


@pytest.mark.parametrize("role", sorted(MACHINE_ROLES))
def test_machine_roles_cannot_select_private_auth_or_vault_tables(tenant_database, role):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(f"SET LOCAL ROLE {role}")
        for relation in (
            "app.holdings",
            "app.agent_connections",
            "auth.users",
            "vault.secrets",
            "machine.telegram_pairing_attempts",
        ):
            with tenant_database.transaction(force_rollback=True):
                with pytest.raises(Exception, match="permission denied"):
                    tenant_database.execute(f"SELECT * FROM {relation}").fetchall()


def test_machine_roles_have_no_cross_role_membership_or_unreviewed_execute(tenant_database):
    memberships = tenant_database.execute(
        """
        SELECT member_role.rolname, granted_role.rolname
        FROM pg_auth_members membership
        JOIN pg_roles member_role ON member_role.oid = membership.member
        JOIN pg_roles granted_role ON granted_role.oid = membership.roleid
        WHERE member_role.rolname = ANY(%s)
           OR granted_role.rolname = ANY(%s)
        """,
        (list(MACHINE_ROLES), list(MACHINE_ROLES)),
    ).fetchall()
    assert memberships == []

    expected = {
        "stock_agent_gateway": set(),
        "stock_agent_scheduler": set(),
        "stock_agent_telegram": {
            "machine.telegram_prepare_command(jsonb)",
            "machine.telegram_consume_pairing(jsonb)",
            "machine.telegram_claim_update(jsonb)",
            "machine.telegram_resolve_link(jsonb)",
            "machine.telegram_unlink(jsonb)",
            "machine.telegram_apply_callback(jsonb)",
            "machine.telegram_portfolio(jsonb)",
            "machine.telegram_plans(jsonb)",
            "machine.telegram_record_delivery(jsonb)",
            "machine.telegram_record_pairing_delivery(jsonb)",
        },
        "stock_agent_backup": set(),
    }
    for role in MACHINE_ROLES:
        executable = tenant_database.execute(
            """
            SELECT p.oid::regprocedure::text
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'machine'
              AND has_function_privilege(%s, p.oid, 'EXECUTE')
            """,
            (role,),
        ).fetchall()
        assert {row[0] for row in executable} == expected[role]
