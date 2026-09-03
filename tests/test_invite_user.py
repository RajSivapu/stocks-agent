from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from scripts import invite_user


class Response:
    def __init__(self, payload, status=200):
        self.body = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.body))}
        self.status = status
        self.stream = BytesIO(self.body)

    def read(self, amount):
        return self.stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_invitation_creates_one_confirmed_auth_identity_and_private_profile():
    requests = []
    responses = iter([
        Response({"id": "11111111-1111-4111-8111-111111111111"}),
        Response({"status": "invited"}),
    ])

    def opener(request, timeout):
        requests.append((request, timeout))
        return next(responses)

    receipt = invite_user.create_invited_user(
        "https://example.supabase.co",
        "service-role-test-secret",
        "Friend@Example.com",
        "receipt-pepper-with-more-than-thirty-two-bytes",
        opener=opener,
    )

    assert [request.full_url for request, _ in requests] == [
        "https://example.supabase.co/auth/v1/admin/users",
        "https://example.supabase.co/rest/v1/rpc/operator_initialize_invited_user",
    ]
    auth_body = json.loads(requests[0][0].data)
    assert auth_body == {"email": "friend@example.com", "email_confirm": True}
    assert json.loads(requests[1][0].data) == {
        "p_owner_id": "11111111-1111-4111-8111-111111111111",
    }
    assert set(receipt) == {"status", "receipt", "created_at"}
    assert receipt["status"] == "invited"
    assert "friend@example.com" not in json.dumps(receipt)
    assert "11111111-1111-4111-8111-111111111111" not in json.dumps(receipt)
    assert len(receipt["receipt"]) == 64


def test_profile_failure_removes_the_new_auth_identity():
    requests = []
    responses = iter([
        Response({"id": "11111111-1111-4111-8111-111111111111"}),
        RuntimeError("profile failed"),
        Response({}, 204),
    ])

    def opener(request, timeout):
        requests.append(request)
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    with pytest.raises(RuntimeError, match="invitation profile initialization failed"):
        invite_user.create_invited_user(
            "https://example.supabase.co",
            "service-role-test-secret",
            "friend@example.com",
            "receipt-pepper-with-more-than-thirty-two-bytes",
            opener=opener,
        )
    assert requests[-1].method == "DELETE"
    assert requests[-1].full_url.endswith("/auth/v1/admin/users/11111111-1111-4111-8111-111111111111")


def test_invitation_requires_custom_smtp_acknowledgement_and_strict_email():
    with pytest.raises(ValueError, match="custom SMTP"):
        invite_user.authorize_invitation(False)
    for email in ("not-an-email", "a@b", "a b@example.com"):
        with pytest.raises(ValueError, match="email"):
            invite_user.canonical_email(email)
