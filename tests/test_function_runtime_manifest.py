from pathlib import Path
import subprocess
import tomllib

import pytest

from scripts.check_function_runtime_manifest import (
    _deno_binary,
    _safe_environment,
    _validate_local_graph,
)
from scripts.function_runtime_manifest import (
    function_runtime_manifest,
    stage_function_runtime,
)
from scripts.release_components import FUNCTIONS


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_manifest_excludes_tests_and_requires_one_closed_reachable_graph():
    files = {
        "index.ts": b'import { value } from "./lib.ts";\nconsole.log(value);\n',
        "lib.ts": b"export const value = 1;\n",
        "lib_test.ts": b'import "./lib.ts";\n',
    }
    assert function_runtime_manifest("example", files, "index.ts") == {
        "index.ts": files["index.ts"],
        "lib.ts": files["lib.ts"],
    }

    with pytest.raises(RuntimeError, match="unreachable"):
        function_runtime_manifest(
            "example", {**files, "unused.ts": b"export const unused = true;\n"}, "index.ts"
        )

    with pytest.raises(RuntimeError, match="outside"):
        function_runtime_manifest(
            "example", {**files, "index.ts": b'import "../sibling/auth.ts";\n'}, "index.ts"
        )

    for specifier in (
        "/tmp/outside.ts",
        "file:///tmp/outside.ts",
        "FILE:///tmp/outside.ts",
        r"C:\\outside.ts",
        r"\\server\\outside.ts",
    ):
        with pytest.raises(RuntimeError, match="filesystem path"):
            function_runtime_manifest(
                "example", {"index.ts": f'import "{specifier}";\n'.encode()}, "index.ts"
            )


def test_every_production_function_is_self_contained_and_manifest_exact():
    for name in FUNCTIONS:
        source = ROOT / "supabase/functions" / name
        files = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in sorted(source.rglob("*"))
            if path.is_file()
        }
        manifest = function_runtime_manifest(name, files, "index.ts")
        assert "index.ts" in manifest
        assert manifest
        assert all("_test." not in path and ".test." not in path for path in manifest)


def test_stage_matches_one_isolated_release_adapter_tree(tmp_path):
    files = {"index.ts": b'import "./lib.ts";\n', "lib.ts": b"export {};\n"}
    configuration = {
        "verify_jwt": False,
        "entrypoint": "index.ts",
        "import_map": None,
    }
    entrypoint = stage_function_runtime(
        tmp_path, "example", files, configuration
    )
    assert entrypoint == tmp_path / "supabase/functions/example/index.ts"
    assert {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == {
        "supabase/config.toml",
        "supabase/functions/example/index.ts",
        "supabase/functions/example/lib.ts",
    }
    assert tomllib.loads((tmp_path / "supabase/config.toml").read_text()) == {
        "functions": {
            "example": {
                "enabled": True,
                "verify_jwt": False,
                "entrypoint": "./functions/example/index.ts",
            }
        }
    }


def test_deno_graph_must_equal_the_isolated_manifest(tmp_path):
    source = tmp_path / "supabase/functions/example"
    source.mkdir(parents=True)
    index = source / "index.ts"
    index.write_text("export {};\n")
    _validate_local_graph(
        {"modules": [{"specifier": index.as_uri(), "local": str(index)}]},
        source,
        {"index.ts"},
    )

    outside = tmp_path / "outside.ts"
    outside.write_text("export {};\n")
    with pytest.raises(RuntimeError, match="escapes"):
        _validate_local_graph(
            {"modules": [{"specifier": outside.as_uri(), "local": str(outside)}]},
            source,
            {"index.ts"},
        )


def test_deno_checker_environment_omits_unapproved_values(monkeypatch):
    monkeypatch.setenv("STOCK_AGENT_PRODUCTION_SECRET", "must-not-be-inherited")
    assert "STOCK_AGENT_PRODUCTION_SECRET" not in _safe_environment()


def test_unpinned_deno_specifier_fails_without_changing_lock(tmp_path):
    lock = tmp_path / "deno.lock"
    lock.write_bytes((ROOT / "supabase/functions/deno.lock").read_bytes())
    before = lock.read_bytes()
    source = tmp_path / "index.ts"
    source.write_text('import "npm:jose@6.2.10";\n')
    result = subprocess.run(
        [
            str(_deno_binary()),
            "check",
            "--cached-only",
            "--no-config",
            "--lock",
            str(lock),
            "--frozen-lockfile",
            str(source),
        ],
        capture_output=True,
        env=_safe_environment(),
        text=True,
    )
    assert result.returncode != 0
    assert lock.read_bytes() == before


def test_function_local_shared_copies_cannot_drift():
    assert (ROOT / "supabase/functions/market-briefing-gateway/auth.ts").read_bytes() == (
        ROOT / "supabase/functions/owner-dashboard-api/auth.ts"
    ).read_bytes()
    assert (ROOT / "supabase/functions/market-briefing-gateway/errors.ts").read_bytes() == (
        ROOT / "supabase/functions/owner-dashboard-api/errors.ts"
    ).read_bytes()
    assert (ROOT / "supabase/functions/owner-dashboard-api/dashboard-contracts.ts").read_bytes() == (
        ROOT / "packages/dashboard-contracts/src/index.ts"
    ).read_bytes()
