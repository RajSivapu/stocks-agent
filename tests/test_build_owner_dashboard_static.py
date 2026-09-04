import json

import pytest

from scripts import build_owner_dashboard_static as build


PROJECT_REF = "hlxpxbxhqctwsqizwjjy"
SITE_ORIGIN = "https://personal-stock-agent.rupesh-sivapu.chatgpt.site"


def test_selects_only_the_modern_public_key():
    value = build.select_publishable_key([
        {"name": "anon", "type": "legacy", "api_key": "eyJlegacy"},
        {"name": "default", "type": "secret", "api_key": "sb_secret_masked"},
        {"name": "default", "type": "publishable", "api_key": "sb_publishable_123456789012345678901234567890"},
    ])
    assert value.startswith("sb_publishable_")


@pytest.mark.parametrize("keys", ([], [{"type": "legacy", "api_key": "eyJlegacy"}], [{"type": "publishable", "api_key": "sb_secret_bad"}]))
def test_rejects_missing_or_wrong_key_classes(keys):
    with pytest.raises(RuntimeError, match="publishable"):
        build.select_publishable_key(keys)


def test_static_release_build_never_returns_the_public_key(tmp_path):
    commands = []
    public_key = "sb_publishable_123456789012345678901234567890"
    built = tmp_path / "apps/web/dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>Owner</title>")
    (built / "_headers").write_text("/*\n  X-Content-Type-Options: nosniff\n")

    def runner(command, **options):
        commands.append((command, options))
        if "api-keys" in command:
            output = json.dumps([{"name": "default", "type": "publishable", "api_key": public_key}])
        elif command[:2] == ["node", "scripts/check_dashboard_bundle.mjs"]:
            output = json.dumps({"status": "verified", "file_count": 5, "initial_js_gzip_bytes": 100, "hashes": [{"file": "index.html", "sha256": "a" * 64}]})
        else:
            output = "build complete"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = build.build_static_release(PROJECT_REF, SITE_ORIGIN, tmp_path, runner)
    assert receipt["status"] == "verified"
    assert receipt["site_origin"] == SITE_ORIGIN
    assert receipt["static_directory"] == "dist"
    assert (tmp_path / "dist/index.html").read_text() == "<!doctype html><title>Owner</title>"
    assert public_key not in json.dumps(receipt)
    build_call = next(options for command, options in commands if command[:2] == ["npm", "run"])
    assert build_call["env"]["VITE_SUPABASE_PUBLISHABLE_KEY"] == public_key


def test_release_refuses_symlinks_in_static_output(tmp_path):
    built = tmp_path / "apps/web/dist"
    built.mkdir(parents=True)
    (built / "index.html").symlink_to(tmp_path / "outside.html")

    def runner(command, **_options):
        if "api-keys" in command:
            output = json.dumps([{
                "type": "publishable", "api_key": "sb_publishable_123456789012345678901234567890",
            }])
        else:
            output = "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    with pytest.raises(RuntimeError, match="symlink"):
        build.build_static_release(PROJECT_REF, SITE_ORIGIN, tmp_path, runner)
