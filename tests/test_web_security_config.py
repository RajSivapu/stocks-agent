from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_auth_policy_is_invite_only_short_lived_and_rotating():
    config = (ROOT / "supabase" / "config.toml").read_text(encoding="utf-8")

    assert re.search(r"(?m)^jwt_expiry\s*=\s*900$", config)
    assert re.search(r"(?m)^enable_signup\s*=\s*false$", config)
    assert re.search(r"(?m)^enable_refresh_token_rotation\s*=\s*true$", config)
    assert re.search(r"(?m)^refresh_token_reuse_interval\s*=\s*10$", config)
    assert re.search(r"(?m)^otp_length\s*=\s*6$", config)
    assert re.search(r"(?m)^otp_expiry\s*=\s*600$", config)


def test_static_host_policy_has_no_wildcard_or_private_cache():
    headers = (ROOT / "apps" / "web" / "public" / "_headers").read_text(
        encoding="utf-8"
    )

    assert "script-src 'self'" in headers
    assert "frame-ancestors 'none'" in headers
    assert "worker-src 'none'" in headers
    assert "Strict-Transport-Security:" in headers
    assert "X-Content-Type-Options: nosniff" in headers
    assert "Referrer-Policy: no-referrer" in headers
    assert "Cache-Control: no-store" in headers
    assert "connect-src *" not in headers
    assert "unsafe-inline" not in headers
    assert "unsafe-eval" not in headers


def test_web_source_never_registers_a_service_worker_or_names_server_secrets():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "apps" / "web").rglob("*"))
        if path.is_file()
        and "node_modules" not in path.parts
        and "dist" not in path.parts
        and path.suffix in {".ts", ".tsx", ".js", ".html", ".json", ".webmanifest"}
    )

    assert "serviceWorker.register" not in source
    assert "SUPABASE_SERVICE_ROLE" not in source
    assert "TELEGRAM_BOT_TOKEN" not in source
    assert "DATABASE_URL" not in source
