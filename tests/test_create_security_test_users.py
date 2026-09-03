from __future__ import annotations

import json
import stat
from io import BytesIO

import pytest

from scripts import create_security_test_users as security_users


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.body))}
        self.stream = BytesIO(self.body)

    def read(self, amount):
        return self.stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_create_users_uses_admin_endpoint_and_returns_login_tokens():
    requests = []
    responses = iter(
        [
            Response({"id": "11111111-1111-4111-8111-111111111111"}),
            Response({"status": "invited"}),
            Response({"access_token": "token-a", "expires_in": 3600}),
            Response({"id": "22222222-2222-4222-8222-222222222222"}),
            Response({"status": "invited"}),
            Response({"access_token": "token-b", "expires_in": 3600}),
        ]
    )

    def opener(request, timeout):
        requests.append((request, timeout))
        return next(responses)

    bundle = security_users.create_security_users(
        "https://example.supabase.co",
        "service-role-test-secret",
        "security-a@example.com",
        "security-b@example.com",
        password_factory=iter(["password-a-long-enough", "password-b-long-enough"]).__next__,
        opener=opener,
    )

    assert [request.full_url for request, _ in requests] == [
        "https://example.supabase.co/auth/v1/admin/users",
        "https://example.supabase.co/rest/v1/rpc/operator_initialize_invited_user",
        "https://example.supabase.co/auth/v1/token?grant_type=password",
        "https://example.supabase.co/auth/v1/admin/users",
        "https://example.supabase.co/rest/v1/rpc/operator_initialize_invited_user",
        "https://example.supabase.co/auth/v1/token?grant_type=password",
    ]
    assert all(timeout == 20 for _, timeout in requests)
    assert json.loads(requests[0][0].data)["email_confirm"] is True
    assert json.loads(requests[1][0].data) == {
        "p_owner_id": "11111111-1111-4111-8111-111111111111",
    }
    assert requests[0][0].get_header("Authorization") == "Bearer service-role-test-secret"
    assert bundle["owner_a"]["access_token"] == "token-a"
    assert bundle["owner_b"]["access_token"] == "token-b"
    assert "service-role-test-secret" not in json.dumps(bundle)
    assert "password" not in json.dumps(bundle)


def test_private_bundle_is_mode_0600_and_never_overwritten(tmp_path):
    destination = tmp_path / "security-users.json"
    bundle = {
        "version": 1,
        "supabase_url": "https://example.supabase.co",
        "owner_a": {"access_token": "token-a"},
        "owner_b": {"access_token": "token-b"},
    }

    security_users.write_private_bundle(destination, bundle)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert json.loads(destination.read_text()) == bundle
    with pytest.raises(FileExistsError):
        security_users.write_private_bundle(destination, bundle)


def test_cleanup_deletes_only_the_two_exact_bundle_identities():
    requests = []
    responses = iter([Response({}), Response({})])

    def opener(request, timeout):
        requests.append((request, timeout))
        return next(responses)

    result = security_users.delete_security_users(
        "https://example.supabase.co",
        "service-role-test-secret",
        {
            "version": 1,
            "supabase_url": "https://example.supabase.co",
            "owner_a": {"id": "11111111-1111-4111-8111-111111111111"},
            "owner_b": {"id": "22222222-2222-4222-8222-222222222222"},
        },
        opener=opener,
    )

    assert result == {"status": "deleted", "count": 2}
    assert [request.method for request, _ in requests] == ["DELETE", "DELETE"]
    assert [request.full_url.rsplit("/", 1)[-1] for request, _ in requests] == [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ]


def test_profile_initialization_failure_rolls_back_the_new_auth_user():
    requests = []
    responses = iter(
        [
            Response({"id": "11111111-1111-4111-8111-111111111111"}),
            Response({"status": "unexpected"}),
            Response({}),
        ]
    )

    def opener(request, timeout):
        requests.append(request)
        return next(responses)

    with pytest.raises(RuntimeError, match="profile initialization"):
        security_users.create_security_users(
            "https://example.supabase.co",
            "service-role-test-secret",
            "security-a@example.com",
            "security-b@example.com",
            password_factory=lambda: "password-a-long-enough",
            opener=opener,
        )
    assert requests[-1].method == "DELETE"
    assert requests[-1].full_url.endswith(
        "/auth/v1/admin/users/11111111-1111-4111-8111-111111111111"
    )


def test_cleanup_rejects_a_bundle_from_another_project():
    with pytest.raises(ValueError, match="project"):
        security_users.delete_security_users(
            "https://example.supabase.co",
            "service-role-test-secret",
            {
                "version": 1,
                "supabase_url": "https://other.supabase.co",
                "owner_a": {"id": "11111111-1111-4111-8111-111111111111"},
                "owner_b": {"id": "22222222-2222-4222-8222-222222222222"},
            },
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.supabase.co",
        "https://example.com",
        "https://user:password@example.supabase.co",
        "https://example.supabase.co/path",
    ],
)
def test_supabase_url_must_be_canonical_https(url):
    with pytest.raises(ValueError, match="Supabase URL"):
        security_users.validate_supabase_url(url)
