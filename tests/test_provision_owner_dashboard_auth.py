import json

import pytest

from scripts import provision_owner_dashboard_auth as provision


PROJECT_URL = "https://hlxpxbxhqctwsqizwjjy.supabase.co"
OWNER_EMAIL = "owner@example.com"
SERVICE_KEY = "sb_secret_" + "a" * 40
OWNER_ID = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"


def test_configuration_is_exact_and_receipts_never_return_identity_or_secret():
    assert provision.validate_configuration(PROJECT_URL, OWNER_EMAIL, SERVICE_KEY) == (
        PROJECT_URL, OWNER_EMAIL, SERVICE_KEY,
    )
    for url, email, key in [
        (PROJECT_URL + "/auth", OWNER_EMAIL, SERVICE_KEY),
        (PROJECT_URL, "not-an-email", SERVICE_KEY),
        (PROJECT_URL, OWNER_EMAIL, "short"),
    ]:
        with pytest.raises(ValueError):
            provision.validate_configuration(url, email, key)


def test_provisions_exactly_one_confirmed_owner_and_returns_bounded_receipt():
    calls = []
    users_responses = iter([
        {"users": []},
        {"users": [{"id": OWNER_ID, "email": OWNER_EMAIL, "email_confirmed_at": "2026-09-04T03:00:00Z"}]},
    ])

    def requester(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "GET":
            return 200, json.dumps(next(users_responses)).encode()
        return 200, json.dumps({"id": OWNER_ID, "email": OWNER_EMAIL}).encode()

    receipt = provision.provision_owner_account(
        PROJECT_URL, OWNER_EMAIL, SERVICE_KEY, requester=requester,
    )
    assert receipt["status"] == "verified"
    assert receipt["action"] == "created"
    assert receipt["auth_user_count"] == 1
    assert OWNER_EMAIL not in json.dumps(receipt)
    assert OWNER_ID not in json.dumps(receipt)
    assert SERVICE_KEY not in json.dumps(receipt)
    assert [call[0] for call in calls] == ["GET", "POST", "GET"]
    assert all(SERVICE_KEY not in (call[3] or b"").decode() for call in calls)


def test_existing_exact_confirmed_owner_is_idempotent():
    def requester(method, _url, _headers, _body):
        assert method == "GET"
        return 200, json.dumps({
            "users": [{"id": OWNER_ID, "email": OWNER_EMAIL, "confirmed_at": "2026-09-04T03:00:00Z"}],
        }).encode()

    receipt = provision.provision_owner_account(
        PROJECT_URL, OWNER_EMAIL, SERVICE_KEY, requester=requester,
    )
    assert receipt["action"] == "already_present"
    assert receipt["auth_user_count"] == 1


def test_refuses_any_unexpected_or_unconfirmed_user_before_creation():
    for users in [
        [{"id": OWNER_ID, "email": "other@example.com", "confirmed_at": "2026-09-04T03:00:00Z"}],
        [{"id": OWNER_ID, "email": OWNER_EMAIL, "confirmed_at": None}],
        [
            {"id": OWNER_ID, "email": OWNER_EMAIL, "confirmed_at": "2026-09-04T03:00:00Z"},
            {"id": "7903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "email": "other@example.com", "confirmed_at": "2026-09-04T03:00:00Z"},
        ],
    ]:
        methods = []

        def requester(method, _url, _headers, _body):
            methods.append(method)
            return 200, json.dumps({"users": users}).encode()

        with pytest.raises(RuntimeError, match="exactly one confirmed owner"):
            provision.provision_owner_account(PROJECT_URL, OWNER_EMAIL, SERVICE_KEY, requester=requester)
        assert methods == ["GET"]


def test_admin_api_failure_is_bounded():
    def requester(_method, _url, _headers, _body):
        return 500, b'{"message":"contains private backend detail"}'

    with pytest.raises(RuntimeError, match="Auth admin request failed") as error:
        provision.provision_owner_account(PROJECT_URL, OWNER_EMAIL, SERVICE_KEY, requester=requester)
    assert "private backend detail" not in str(error.value)
