"""Build the exact, self-contained source manifest uploaded for an Edge Function."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib


_TEST_SOURCE = re.compile(r"(?:^|/)(?:[^/]+_test|[^/]+\.test)\.(?:js|mjs|ts|tsx)\Z")
_SOURCE = re.compile(r"\.(?:js|mjs|ts|tsx)\Z")
_STATIC_IMPORT = re.compile(
    r"(?:^|\n)\s*(?:import|export)\s+(?:type\s+)?(?:[^;\"']*?\s+from\s+)?[\"']([^\"']+)[\"']",
    re.MULTILINE,
)
_DYNAMIC_IMPORT = re.compile(r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RECOVERY_SUPPORT_PREFIXES = {
    "owner-dashboard-api": ("packages/dashboard-contracts/src/",),
    "market-briefing-gateway": ("supabase/functions/owner-dashboard-api/",),
    "telegram-portfolio": (),
}


def _safe_path(path: object) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and "\\" not in path
        and not PurePosixPath(path).is_absolute()
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def _import_specifiers(path: str, raw: bytes) -> set[str]:
    if _SOURCE.search(path) is None:
        return set()
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("function runtime source is not UTF-8") from error
    return set((*_STATIC_IMPORT.findall(source), *_DYNAMIC_IMPORT.findall(source)))


def _is_filesystem_specifier(specifier: str) -> bool:
    return (
        specifier.startswith(("/", "\\"))
        or "\\" in specifier
        or re.match(r"(?i)^file:", specifier) is not None
        or re.match(r"^[A-Za-z]:/", specifier) is not None
    )


def _git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError("trusted recovery Git history is unavailable")
    return result.stdout


def _git_files(repo: Path, commit: str, prefix: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for entry in _git(repo, "ls-tree", "-rz", commit, "--", prefix).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        path = raw_path.decode()
        if (
            mode not in {"100644", "100755"}
            or kind != "blob"
            or not path.startswith(prefix + "/")
        ):
            raise RuntimeError("trusted recovery Git tree contains an unsafe entry")
        files[path[len(prefix) + 1 :]] = _git(repo, "cat-file", "blob", object_id)
    return files


def _historical_configuration(
    repo: Path, commit: str, name: str
) -> dict[str, object]:
    try:
        configured = tomllib.loads(
            _git(repo, "show", f"{commit}:supabase/config.toml").decode()
        )["functions"][name]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("trusted recovery function configuration is unavailable") from error
    prefix = f"./functions/{name}/"
    if (
        not isinstance(configured, Mapping)
        or configured.get("enabled") is not True
        or type(configured.get("verify_jwt")) is not bool
        or not isinstance(configured.get("entrypoint"), str)
        or not configured["entrypoint"].startswith(prefix)
    ):
        raise RuntimeError("trusted recovery function configuration is unavailable")
    result: dict[str, object] = {
        "verify_jwt": configured["verify_jwt"],
        "entrypoint": configured["entrypoint"][len(prefix) :],
        "import_map": None,
    }
    if configured.get("import_map") is not None:
        import_map = configured["import_map"]
        if not isinstance(import_map, str) or not import_map.startswith(prefix):
            raise RuntimeError("trusted recovery import map is unavailable")
        result["import_map"] = import_map[len(prefix) :]
    return result


def _historical_runtime(
    repo: Path, commit: str, name: str, entrypoint: str
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    prefix = f"supabase/functions/{name}/"
    local_tree = _git_files(repo, commit, prefix.removesuffix("/"))
    support_prefixes = _RECOVERY_SUPPORT_PREFIXES[name]
    pending = [prefix + entrypoint]
    observed: dict[str, bytes] = {}
    while pending:
        path = pending.pop()
        if path in observed:
            continue
        if path.startswith(prefix):
            relative = path[len(prefix) :]
            if relative not in local_tree:
                raise RuntimeError("trusted recovery function closure is incomplete")
            raw = local_tree[relative]
        else:
            if not any(path.startswith(allowed) for allowed in support_prefixes):
                raise RuntimeError("trusted recovery import is outside its allowlist")
            raw = _git(repo, "show", f"{commit}:{path}")
        observed[path] = raw
        for specifier in sorted(_import_specifiers(path, raw)):
            if _is_filesystem_specifier(specifier):
                raise RuntimeError("trusted recovery import uses a filesystem path")
            if not specifier.startswith("."):
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), specifier)
            )
            if not _safe_path(resolved):
                raise RuntimeError("trusted recovery import escapes the repository")
            pending.append(resolved)
    local = {
        path[len(prefix) :]: raw
        for path, raw in observed.items()
        if path.startswith(prefix)
    }
    support = {
        path: raw for path, raw in observed.items() if not path.startswith(prefix)
    }
    return dict(sorted(local.items())), dict(sorted(support.items()))


def git_function_recovery_support(
    repo: Path,
    candidate_sha: str,
    name: str,
    files: Mapping[str, bytes],
    configuration: Mapping[str, object],
) -> dict[str, bytes]:
    """Recover legacy external imports from one unambiguous first-parent Git state.

    The encrypted journal remains authoritative for the function-local bytes and
    management-plane configuration. Git supplies only dependencies that the old
    Supabase download omitted, and only when every matching historical state
    supplies the same exact support bytes.
    """
    if (
        name not in _RECOVERY_SUPPORT_PREFIXES
        or _SHA.fullmatch(candidate_sha) is None
        or not repo.is_dir()
        or repo.is_symlink()
        or set(configuration) != {"verify_jwt", "entrypoint", "import_map"}
        or type(configuration.get("verify_jwt")) is not bool
        or not isinstance(configuration.get("entrypoint"), str)
        or not _safe_path(configuration["entrypoint"])
        or any(not _safe_path(path) or not isinstance(raw, bytes) for path, raw in files.items())
    ):
        raise RuntimeError("legacy function recovery input is incomplete")
    reachable: set[str] = set()
    pending = [str(configuration["entrypoint"])]
    needs_support = False
    while pending:
        path = pending.pop()
        if path in reachable:
            continue
        if path not in files:
            needs_support = True
            break
        reachable.add(path)
        for specifier in sorted(_import_specifiers(path, files[path])):
            if _is_filesystem_specifier(specifier):
                raise RuntimeError("legacy function recovery import uses a filesystem path")
            if not specifier.startswith("."):
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), specifier)
            )
            if resolved == ".." or resolved.startswith("../"):
                needs_support = True
                break
            if not _safe_path(resolved):
                raise RuntimeError("legacy function recovery import is unsafe")
            pending.append(resolved)
        if needs_support:
            break
    if not needs_support:
        return {}
    if _git(repo, "rev-parse", "--is-shallow-repository").decode().strip() != "false":
        raise RuntimeError("trusted recovery Git history is shallow")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", candidate_sha).decode().split()
    if len(parents) < 2 or parents[0] != candidate_sha:
        raise RuntimeError("failed release candidate has no trusted prior history")
    prior_head = parents[1]
    history_paths = [
        f"supabase/functions/{name}",
        "supabase/config.toml",
        *[prefix.removesuffix("/") for prefix in _RECOVERY_SUPPORT_PREFIXES[name]],
    ]
    history = _git(
        repo,
        "log",
        "--first-parent",
        "--format=%H",
        prior_head,
        "--",
        *history_paths,
    ).decode().splitlines()
    commits = list(dict.fromkeys([prior_head, *history]))
    matches: dict[str, dict[str, bytes]] = {}
    for commit in commits:
        try:
            if _historical_configuration(repo, commit, name) != dict(configuration):
                continue
            local, support = _historical_runtime(
                repo, commit, name, str(configuration["entrypoint"])
            )
        except RuntimeError:
            continue
        if local != dict(files):
            continue
        digest = hashlib.sha256()
        for path, raw in sorted(support.items()):
            digest.update(path.encode() + b"\0" + raw + b"\0")
        matches.setdefault(digest.hexdigest(), support)
    if not matches:
        raise RuntimeError("legacy function recovery has no exact historical source")
    if len(matches) != 1:
        raise RuntimeError("legacy function recovery support is ambiguous")
    support = next(iter(matches.values()))
    if not support:
        raise RuntimeError("legacy function recovery closure is inconsistent")
    return support


def function_runtime_manifest(
    name: str,
    files: Mapping[str, bytes],
    entrypoint: str,
) -> dict[str, bytes]:
    """Return the exact non-test import closure, rejecting ambiguous source trees."""
    if not isinstance(name, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise RuntimeError("function runtime name is malformed")
    if not isinstance(files, Mapping) or not files or not _safe_path(entrypoint):
        raise RuntimeError("function runtime files are unavailable")
    if any(not _safe_path(path) or not isinstance(raw, bytes) for path, raw in files.items()):
        raise RuntimeError("function runtime path or bytes are unsafe")
    manifest = {
        path: raw
        for path, raw in sorted(files.items())
        if _TEST_SOURCE.search(path) is None
    }
    if entrypoint not in manifest:
        raise RuntimeError("function runtime entrypoint is unavailable")

    reachable: set[str] = set()
    pending = [entrypoint]
    while pending:
        path = pending.pop()
        if path in reachable:
            continue
        reachable.add(path)
        for specifier in sorted(_import_specifiers(path, manifest[path])):
            if _is_filesystem_specifier(specifier):
                raise RuntimeError(f"{name} runtime import uses a filesystem path")
            if not specifier.startswith("."):
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
            if resolved == ".." or resolved.startswith("../") or not _safe_path(resolved):
                raise RuntimeError(f"{name} runtime import resolves outside its function")
            if resolved not in manifest:
                raise RuntimeError(f"{name} runtime import is missing from its manifest")
            pending.append(resolved)

    unreachable = sorted(set(manifest) - reachable)
    if unreachable:
        raise RuntimeError(f"{name} runtime manifest contains unreachable files")
    return manifest


def configured_function_runtime(
    name: str,
    files: Mapping[str, bytes],
    configured: object,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Normalize one function's configuration and exact runtime import closure."""
    prefix = f"./functions/{name}/"
    if (
        not isinstance(configured, Mapping)
        or configured.get("enabled") is not True
        or type(configured.get("verify_jwt")) is not bool
        or not isinstance(configured.get("entrypoint"), str)
        or not configured["entrypoint"].startswith(prefix)
    ):
        raise RuntimeError("candidate function configuration is incomplete")
    entrypoint = configured["entrypoint"][len(prefix):]
    if not _safe_path(entrypoint):
        raise RuntimeError("candidate function configuration is incomplete")
    manifest = function_runtime_manifest(name, files, entrypoint)
    configuration: dict[str, object] = {
        "verify_jwt": configured["verify_jwt"],
        "entrypoint": entrypoint,
        "import_map": None,
    }
    if configured.get("import_map") is not None:
        import_map = configured["import_map"]
        if (
            not isinstance(import_map, str)
            or not import_map.startswith(prefix)
            or not _safe_path(import_map[len(prefix):])
            or import_map[len(prefix):] not in manifest
        ):
            raise RuntimeError("candidate function import-map configuration is incomplete")
        configuration["import_map"] = import_map[len(prefix):]
    return manifest, configuration


def stage_function_runtime(
    root: Path,
    name: str,
    files: Mapping[str, bytes],
    configuration: Mapping[str, object],
) -> Path:
    """Write the exact isolated source/configuration tree consumed by the release adapter."""
    if set(configuration) != {"verify_jwt", "entrypoint", "import_map"} or type(
        configuration.get("verify_jwt")
    ) is not bool:
        raise RuntimeError("complete native function configuration is required")
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("native function stage root is unavailable")
    source = root / "supabase/functions" / name
    source.mkdir(parents=True)
    for relative, raw in files.items():
        if not _safe_path(relative) or not isinstance(raw, bytes):
            raise RuntimeError("native function stage contains unsafe source")
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    settings = [
        f'[functions.{json.dumps(name)}]',
        "enabled = true",
        f'verify_jwt = {str(configuration["verify_jwt"]).lower()}',
    ]
    for key in ("entrypoint", "import_map"):
        value = configuration[key]
        if value is not None:
            if not isinstance(value, str) or value not in files:
                raise RuntimeError("configured native function file is absent")
            settings.append(
                f'{key} = {json.dumps("./functions/" + name + "/" + value)}'
            )
    config = root / "supabase/config.toml"
    config.write_text("\n".join(settings) + "\n")
    return source / str(configuration["entrypoint"])
