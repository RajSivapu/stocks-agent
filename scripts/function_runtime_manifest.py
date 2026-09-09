"""Build the exact, self-contained source manifest uploaded for an Edge Function."""
from __future__ import annotations

from collections.abc import Mapping
import json
import posixpath
from pathlib import Path, PurePosixPath
import re


_TEST_SOURCE = re.compile(r"(?:^|/)(?:[^/]+_test|[^/]+\.test)\.(?:js|mjs|ts|tsx)\Z")
_SOURCE = re.compile(r"\.(?:js|mjs|ts|tsx)\Z")
_STATIC_IMPORT = re.compile(
    r"(?:^|\n)\s*(?:import|export)\s+(?:type\s+)?(?:[^;\"']*?\s+from\s+)?[\"']([^\"']+)[\"']",
    re.MULTILINE,
)
_DYNAMIC_IMPORT = re.compile(r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)")


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
