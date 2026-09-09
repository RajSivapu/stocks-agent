#!/usr/bin/env python3
"""Compile each Edge Function from its exact isolated release-adapter stage."""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.function_runtime_manifest import (
    configured_function_runtime,
    stage_function_runtime,
)
from scripts.release_components import FUNCTIONS, ROOT


DENO_VERSION = "2.9.6"
_ENVIRONMENT_ALLOWLIST = (
    "DENO_DIR",
    "HOME",
    "PATH",
    "SYSTEMROOT",
    "TMPDIR",
    "XDG_CACHE_HOME",
)


def _safe_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in _ENVIRONMENT_ALLOWLIST
        if key in os.environ
    }
    environment["NO_COLOR"] = "1"
    return environment


def _deno_binary() -> Path:
    machine = platform.machine().lower()
    target = {
        ("darwin", "arm64"): "darwin-arm64",
        ("darwin", "aarch64"): "darwin-arm64",
        ("darwin", "x86_64"): "darwin-x64",
        ("linux", "aarch64"): "linux-arm64-glibc",
        ("linux", "arm64"): "linux-arm64-glibc",
        ("linux", "x86_64"): "linux-x64-glibc",
        ("win32", "aarch64"): "win32-arm64",
        ("win32", "arm64"): "win32-arm64",
        ("win32", "amd64"): "win32-x64",
        ("win32", "x86_64"): "win32-x64",
    }.get((sys.platform, machine))
    if target is None:
        raise RuntimeError("pinned Deno binary is unsupported on this platform")
    executable = "deno.exe" if sys.platform == "win32" else "deno"
    binary = ROOT / "node_modules/@deno" / target / executable
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("pinned platform Deno binary is unavailable; run npm ci")
    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        check=True,
        env=_safe_environment(),
        text=True,
    )
    first_line = result.stdout.splitlines()[0].split() if result.stdout else []
    if first_line[:2] != ["deno", DENO_VERSION]:
        raise RuntimeError("local Deno binary version differs from the pinned release")
    return binary


def _validate_local_graph(
    graph: object,
    source: Path,
    expected_files: set[str],
) -> None:
    if not isinstance(graph, dict) or not isinstance(graph.get("modules"), list):
        raise RuntimeError("Deno module graph is malformed")
    root = source.resolve()
    actual_files: set[str] = set()
    for module in graph["modules"]:
        if not isinstance(module, dict):
            raise RuntimeError("Deno module graph is malformed")
        specifier = module.get("specifier")
        if not isinstance(specifier, str):
            raise RuntimeError("Deno module graph is malformed")
        if not specifier.lower().startswith("file:"):
            continue
        local = module.get("local")
        if not isinstance(local, str):
            raise RuntimeError("Deno local module path is unavailable")
        path = Path(local).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("Deno module graph escapes the isolated function stage")
        actual_files.add(path.relative_to(root).as_posix())
    if actual_files != expected_files:
        raise RuntimeError("Deno local module graph differs from the release manifest")


def main() -> int:
    counts: dict[str, int] = {}
    document = tomllib.loads((ROOT / "supabase/config.toml").read_text())
    configured = document.get("functions")
    if not isinstance(configured, dict):
        raise RuntimeError("Supabase function configuration is unavailable")
    deno = _deno_binary()
    lock = ROOT / "supabase/functions/deno.lock"
    if not lock.is_file() or lock.is_symlink():
        raise RuntimeError("reviewed Deno dependency lock is unavailable")
    for name in FUNCTIONS:
        source = ROOT / "supabase/functions" / name
        files = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in sorted(source.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }
        manifest, configuration = configured_function_runtime(
            name, files, configured.get(name)
        )
        with tempfile.TemporaryDirectory(prefix=f"function-runtime-check-{name}-") as directory:
            stage = Path(directory)
            entrypoint = stage_function_runtime(
                stage, name, manifest, configuration
            )
            subprocess.run(
                [
                    str(deno),
                    "check",
                    "--cached-only",
                    "--no-config",
                    "--lock",
                    str(lock),
                    "--frozen-lockfile",
                    str(entrypoint),
                ],
                cwd=stage,
                check=True,
                env=_safe_environment(),
            )
            graph_result = subprocess.run(
                [
                    str(deno),
                    "info",
                    "--json",
                    "--no-remote",
                    "--no-config",
                    "--lock",
                    str(lock),
                    "--frozen-lockfile",
                    str(entrypoint),
                ],
                cwd=stage,
                capture_output=True,
                check=True,
                env=_safe_environment(),
                text=True,
            )
            _validate_local_graph(
                json.loads(graph_result.stdout),
                entrypoint.parent,
                set(manifest),
            )
        counts[name] = len(manifest)
    print(json.dumps({"status": "verified", "runtime_files": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
