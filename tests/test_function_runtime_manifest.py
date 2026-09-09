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
    git_function_recovery_support,
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


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Runtime Recovery Test",
            "-c",
            "user.email=runtime-recovery@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def _legacy_recovery_repo(tmp_path: Path) -> tuple[Path, dict[str, bytes], dict[str, object]]:
    repo = tmp_path / "repo"
    source = repo / "supabase/functions/owner-dashboard-api"
    support = repo / "packages/dashboard-contracts/src"
    source.mkdir(parents=True)
    support.mkdir(parents=True)
    (repo / "supabase/config.toml").write_text(
        '[functions."owner-dashboard-api"]\n'
        "enabled = true\n"
        "verify_jwt = false\n"
        'entrypoint = "./functions/owner-dashboard-api/index.ts"\n'
    )
    index = (
        b'import { value } from "./local.ts";\n'
        b'import { contract } from "../../../packages/dashboard-contracts/src/index.ts";\n'
        b"console.log(value, contract);\n"
    )
    local = b"export const value = 1;\n"
    (source / "index.ts").write_bytes(index)
    (source / "local.ts").write_bytes(local)
    (support / "index.ts").write_bytes(b"export const contract = 'v1';\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit(repo, "legacy runtime")
    return repo, {"index.ts": index, "local.ts": local}, {
        "verify_jwt": False,
        "entrypoint": "index.ts",
        "import_map": None,
    }


def test_recovery_support_comes_from_one_exact_first_parent_history(tmp_path):
    repo, prior_files, configuration = _legacy_recovery_repo(tmp_path)
    (repo / "unrelated.txt").write_text("does not change runtime support\n")
    _commit(repo, "unrelated main change")
    source = repo / "supabase/functions/owner-dashboard-api"
    (source / "dashboard-contracts.ts").write_bytes(
        (repo / "packages/dashboard-contracts/src/index.ts").read_bytes()
    )
    (source / "index.ts").write_text(
        'import { contract } from "./dashboard-contracts.ts";\nconsole.log(contract);\n'
    )
    candidate = _commit(repo, "self contained candidate")

    assert git_function_recovery_support(
        repo, candidate, "owner-dashboard-api", prior_files, configuration
    ) == {
        "packages/dashboard-contracts/src/index.ts": b"export const contract = 'v1';\n"
    }


def test_recovery_support_rejects_ambiguous_historical_dependency_bytes(tmp_path):
    repo, prior_files, configuration = _legacy_recovery_repo(tmp_path)
    (repo / "packages/dashboard-contracts/src/index.ts").write_bytes(
        b"export const contract = 'different';\n"
    )
    _commit(repo, "change dependency without changing function")
    source = repo / "supabase/functions/owner-dashboard-api"
    (source / "dashboard-contracts.ts").write_bytes(b"export const contract = 'candidate';\n")
    (source / "index.ts").write_text(
        'import { contract } from "./dashboard-contracts.ts";\nconsole.log(contract);\n'
    )
    candidate = _commit(repo, "self contained candidate")

    with pytest.raises(RuntimeError, match="ambiguous"):
        git_function_recovery_support(
            repo, candidate, "owner-dashboard-api", prior_files, configuration
        )


def test_recovery_support_rejects_a_shallow_history_before_selecting_support(tmp_path):
    repo, prior_files, configuration = _legacy_recovery_repo(tmp_path)
    source = repo / "supabase/functions/owner-dashboard-api"
    (source / "dashboard-contracts.ts").write_bytes(
        (repo / "packages/dashboard-contracts/src/index.ts").read_bytes()
    )
    (source / "index.ts").write_text(
        'import { contract } from "./dashboard-contracts.ts";\nconsole.log(contract);\n'
    )
    candidate = _commit(repo, "self contained candidate")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth=1", f"file://{repo}", str(shallow)],
        check=True,
        capture_output=True,
    )

    with pytest.raises(RuntimeError, match="shallow"):
        git_function_recovery_support(
            shallow, candidate, "owner-dashboard-api", prior_files, configuration
        )


def test_recovery_history_query_has_no_silent_commit_limit(tmp_path, monkeypatch):
    repo, prior_files, configuration = _legacy_recovery_repo(tmp_path)
    source = repo / "supabase/functions/owner-dashboard-api"
    (source / "dashboard-contracts.ts").write_bytes(
        (repo / "packages/dashboard-contracts/src/index.ts").read_bytes()
    )
    (source / "index.ts").write_text(
        'import { contract } from "./dashboard-contracts.ts";\nconsole.log(contract);\n'
    )
    candidate = _commit(repo, "self contained candidate")
    commands = []
    original = subprocess.run

    def recording_run(command, **options):
        commands.append(command)
        return original(command, **options)

    monkeypatch.setattr("scripts.function_runtime_manifest.subprocess.run", recording_run)

    git_function_recovery_support(
        repo, candidate, "owner-dashboard-api", prior_files, configuration
    )

    history = [command for command in commands if command[:2] == ["git", "log"]]
    assert len(history) == 1
    assert not any(argument.startswith("--max-count") for argument in history[0])
