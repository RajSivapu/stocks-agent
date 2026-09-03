from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError
from uuid import UUID

import pytest

from scripts import delete_account


OWNER = UUID("11111111-1111-4111-8111-111111111111")
DELETION = UUID("22222222-2222-4222-8222-222222222222")


class Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class Transaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("transaction_started")

    def __exit__(self, error_type, *_args):
        self.events.append("rolled_back" if error_type else "committed")
        return False


class Connection:
    def __init__(self):
        self.events = []
        self.calls = []

    def transaction(self):
        return Transaction(self.events)

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "operator_prepare_account_deletion" in query:
            return Cursor({
                "status": "ready_for_auth_deletion",
                "deletion_request_id": str(DELETION),
                "deleted_at": "2026-09-03T12:00:00+00:00",
                "archives_expire_after": "2026-10-08T12:00:00+00:00",
                "duplicate": False,
            })
        if "count(*) FROM app.profiles" in query:
            return Cursor(0)
        raise AssertionError(query)


class Response:
    headers = {"Content-Length": "0"}

    def read(self, amount):
        return BytesIO(b"").read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_active_rows_commit_before_auth_identity_is_deleted():
    connection = Connection()
    requests = []

    def opener(request, timeout):
        assert connection.events == ["transaction_started", "committed"]
        requests.append((request, timeout))
        return Response()

    result = delete_account.delete_connection(
        connection,
        owner_id=OWNER,
        deletion_request_id=DELETION,
        confirmation=f"DELETE AUTH {OWNER}",
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-test-secret",
        opener=opener,
    )

    assert requests[0][0].method == "DELETE"
    assert requests[0][0].full_url.endswith(f"/auth/v1/admin/users/{OWNER}")
    assert result == {
        "status": "deleted",
        "deleted_at": "2026-09-03T12:00:00+00:00",
        "archives_expire_after": "2026-10-08T12:00:00+00:00",
    }
    assert str(OWNER) not in str(result)
    assert "service-role-test-secret" not in str(result)


def test_exact_owner_confirmation_is_required_before_any_mutation():
    connection = Connection()
    with pytest.raises(ValueError, match="confirmation"):
        delete_account.delete_connection(
            connection,
            owner_id=OWNER,
            deletion_request_id=DELETION,
            confirmation="DELETE MY ACCOUNT",
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-test-secret",
            opener=lambda *_args, **_kwargs: Response(),
        )
    assert connection.calls == []


def test_auth_failure_is_retryable_after_the_database_purge_commits():
    connection = Connection()

    def failed_opener(*_args, **_kwargs):
        raise OSError("network unavailable")

    with pytest.raises(delete_account.AuthDeletionPending, match="retry"):
        delete_account.delete_connection(
            connection,
            owner_id=OWNER,
            deletion_request_id=DELETION,
            confirmation=f"DELETE AUTH {OWNER}",
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-test-secret",
            opener=failed_opener,
        )
    assert connection.events == ["transaction_started", "committed"]


def test_missing_auth_identity_is_idempotent_when_the_profile_is_already_gone():
    connection = Connection()

    def missing_opener(request, timeout):
        raise HTTPError(request.full_url, 404, "not found", {}, BytesIO(b""))

    result = delete_account.delete_connection(
        connection,
        owner_id=OWNER,
        deletion_request_id=DELETION,
        confirmation=f"DELETE AUTH {OWNER}",
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-test-secret",
        opener=missing_opener,
    )
    assert result["status"] == "deleted"
