"""Upgrade-safe release state machine for the configured private Sites path.

Transport implementations must query the management planes, preserve opaque
platform identities and runtime configuration, and keep secret-bearing journals
encrypted. This module never treats a local build as deployment evidence.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Mapping, Protocol

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FUNCTIONS = ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")
ARTIFACTS = (*FUNCTIONS, "owner-web-site")
COMPONENTS = ("runtime-role", "dashboard-secrets", *ARTIFACTS)
BACKEND_COMPONENTS = ("runtime-role", "dashboard-secrets", *FUNCTIONS)
MANAGED_SECRETS = ("DASHBOARD_ALLOWED_ORIGINS", "DASHBOARD_DATABASE_URL", "DASHBOARD_OWNER_USER_ID")
CONTENT_KEYS = ("exists", "configuration", "files", "values")


class ComponentTransport(Protocol):
    def capture(self, name: str) -> Mapping: ...
    def apply(self, name: str, candidate: Mapping) -> Mapping: ...
    def restore(self, name: str, prior: Mapping) -> None: ...


def same_content(left: Mapping, right: Mapping) -> bool:
    return all(left[key] == right[key] for key in CONTENT_KEYS)


def restored_function_snapshot(prior: Mapping, current: Mapping) -> bool:
    """A redeploy can advance a version, never replace an existing function ID."""
    return (prior["exists"] and current["exists"] and current["identity"] == prior["identity"]
            and same_content(prior, current)
            and all(re.fullmatch(r"[1-9][0-9]*", str(value)) for value in (prior["version"], current["version"]))
            and int(current["version"]) > int(prior["version"]))


class EncryptedJournal:
    """Authenticated private journal; durable retention must succeed first."""
    def __init__(self, path: Path, key: bytes, *, retain: Callable[[bytes], None]):
        self.path, self.cipher, self.retain = path, Fernet(key), retain

    def __call__(self, journal: Mapping) -> None:
        encrypted = self.cipher.encrypt(canonical(journal))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".pending")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
            self.retain(encrypted)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self) -> dict:
        try:
            return json.loads(self.cipher.decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError) as error:
            raise RuntimeError("recovery journal authentication failed") from error


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def validate_snapshot(name: str, value: Mapping, *, candidate: bool = False) -> dict:
    if (name not in COMPONENTS or not isinstance(value, Mapping)
            or set(value) != {"exists", "identity", "version", "configuration", "files", "values"}
            or type(value.get("exists")) is not bool
            or any(not isinstance(value.get(key), dict) for key in ("configuration", "files", "values"))):
        raise RuntimeError("complete component capture is required")
    if value["exists"]:
        if not candidate and (not value["identity"] or not value["version"]):
            raise RuntimeError("authoritative component identity/version is required")
        if name in ARTIFACTS and not value["files"]:
            raise RuntimeError("prior deployed bytes are required")
    elif value["identity"] is not None or value["version"] is not None or value["files"] or value["values"]:
        raise RuntimeError("absence capture conflicts with prior state")
    for path, raw in value["files"].items():
        if (not isinstance(path, str) or not path or "\\" in path or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/")) or not isinstance(raw, str)):
            raise RuntimeError("component byte capture has an unsafe path")
    canonical(value)  # reject non-JSON and non-finite values before any mutation
    return copy.deepcopy(dict(value))


def verify_component_readback(name: str, expected: Mapping, observed: Mapping) -> dict:
    expected, observed = validate_snapshot(name, expected), validate_snapshot(name, observed)
    if observed != expected:
        raise RuntimeError(f"{name} deployed bytes, identity or configuration mismatch")
    return {"component": name, "identity": observed["identity"], "version": observed["version"],
            "snapshot_sha256": hashlib.sha256(canonical(observed)).hexdigest(), "readback": "verified"}


def _journal_components(journal: Mapping) -> tuple[str, ...]:
    names = tuple(journal.get("components", {}))
    if names not in (COMPONENTS, BACKEND_COMPONENTS):
        raise RuntimeError("complete release recovery journal is required")
    return names


def recover_components(transport: ComponentTransport, journal: dict, *, persist: Callable[[dict], None]) -> dict:
    """An unchanged component is never passed to a mutation operation.

    A remote write can complete before the caller receives its response, so
    `changed` records an attempted write, durably before that write begins.
    Attempts do not prove ownership: only an exact bound candidate or a reviewed
    adapter's explicit attestation authorizes recovery writes. Unknown drift is
    retained for intervention. Other components still receive recovery checks.
    """
    if journal.get("format") != 1:
        raise RuntimeError("complete release recovery journal is required")
    if journal.get("status") == "preparing" and journal.get("components") == {}:
        journal["status"] = "rolled_back"
        persist(copy.deepcopy(journal))
        return {"status": "rolled_back", "components": []}
    components = _journal_components(journal)
    errors = []
    for name in reversed(components):
        entry = journal["components"][name]
        if type(entry.get("changed")) is not bool:
            raise RuntimeError("component mutation boundary is unknown")
        if entry["changed"] is False:
            continue
        try:
            prior = validate_snapshot(name, entry["prior"])
            if hashlib.sha256(canonical(prior)).hexdigest() != entry["prior_sha256"]:
                raise RuntimeError("prior recovery bytes changed")
            hydrate = getattr(transport, "hydrate_recovery", None)
            if callable(hydrate): hydrate(name, prior, entry.get("candidate"))
            current = validate_snapshot(name, transport.capture(name))
            restored_content = name in FUNCTIONS and restored_function_snapshot(prior, current)
            if current != prior and restored_content:
                entry["restoration"] = {"original_identity": prior["identity"],
                    "original_version": prior["version"], "identity": current["identity"], "version": current["version"]}
            elif current != prior:
                candidate = validate_snapshot(name, entry["candidate"], candidate=True)
                bound = entry.get("deployed")
                if bound is not None:
                    bound = validate_snapshot(name, bound)
                    if not same_content(candidate, bound):
                        raise RuntimeError("bound deployment differs from attempted candidate")
                    candidate = bound
                if name in FUNCTIONS and prior["exists"] and current["identity"] != prior["identity"]:
                    raise RuntimeError("existing function identity drift is not owned by this release")
                attest = getattr(transport, "attest_recovery", None)
                owned = (attest(name, prior, candidate, current) is True if callable(attest) else current == candidate)
                if not owned:
                    raise RuntimeError("current component is not proven to belong to this release")
                restored = transport.restore(name, prior)
                expected = prior
                if restored is not None:
                    # Supabase redeployment allocates a new version. Only a
                    # complete, independently read-back byte/config equivalent
                    # snapshot may supply that newly allocated identity.
                    restored = validate_snapshot(name, restored)
                    if name not in FUNCTIONS or not restored_function_snapshot(prior, restored):
                        raise RuntimeError("restoration changed prior component content")
                    expected = restored
                    entry["restoration"] = {"original_identity": prior["identity"],
                        "original_version": prior["version"], "identity": restored["identity"],
                        "version": restored["version"]}
                verify_component_readback(name, expected, transport.capture(name))
            entry["changed"] = False
            persist(copy.deepcopy(journal))
        except Exception:
            errors.append(name)
    journal["status"] = "recovery_required" if errors else "rolled_back"
    persist(copy.deepcopy(journal))
    if errors:
        raise RuntimeError("component recovery remains incomplete: " + ", ".join(errors))
    return {"status": "rolled_back", "components": list(components)}


def execute_release(transport: ComponentTransport, candidates: Mapping, *, persist: Callable[[dict], None],
                    checkpoint: Callable[[str], None] = lambda _name: None,
                    before_mutations: Callable[[], None] = lambda: None,
                    after_mutations: Callable[[], None] = lambda: None,
                    components: tuple[str, ...] = COMPONENTS) -> dict:
    """Capture *all* prior state before the first attempted component write.

    `persist` is a required durable, authenticated encrypted journal sink; it
    must finish successfully before mutation. A caller must hold the protected
    database lease throughout capture, mutation, verification, and recovery.
    """
    if components not in (COMPONENTS, BACKEND_COMPONENTS) or tuple(candidates) != components:
        raise RuntimeError("complete release candidate is required")
    candidates = {name: validate_snapshot(name, candidates[name], candidate=True) for name in components}
    prior = {name: validate_snapshot(name, transport.capture(name)) for name in components}
    journal = {"format": 1, "status": "prepared", "captured_at": datetime.now(timezone.utc).isoformat(), "components": {
        name: {"changed": False, "prior": value, "prior_sha256": hashlib.sha256(canonical(value)).hexdigest()}
        for name, value in prior.items()}}
    persist(copy.deepcopy(journal))
    receipts = []
    try:
        checkpoint("preflight")
        before_mutations()
        for name in components:
            if prior[name] == candidates[name]:
                continue
            pending = copy.deepcopy(journal)
            pending["components"][name]["changed"] = True
            pending["components"][name]["candidate"] = candidates[name]
            pending["status"] = "recovery_required"
            persist(pending)
            journal = pending
            deployed = validate_snapshot(name, transport.apply(name, candidates[name]))
            expected = {**candidates[name], "identity": deployed["identity"], "version": deployed["version"]}
            if name in FUNCTIONS and prior[name]["exists"]:
                expected["identity"] = prior[name]["identity"]
            verify_component_readback(name, expected, deployed)
            # Retain assigned IDs before later steps can fail. A lost response
            # instead requires platform-specific attestation of the candidate.
            journal["components"][name]["deployed"] = copy.deepcopy(deployed)
            persist(copy.deepcopy(journal))
            checkpoint(name)
            receipts.append(verify_component_readback(name, expected, transport.capture(name)))
        after_mutations()
        journal["status"] = "verified"
        persist(copy.deepcopy(journal))
        return {"status": "verified", "components": receipts}
    except Exception:
        recover_components(transport, journal, persist=persist)
        raise


def site_configuration(repo_root: Path = ROOT) -> dict:
    path = repo_root / ".openai/hosting.json"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("configured zero-cost Sites hosting path is unavailable")
    value = json.loads(path.read_text())
    if (set(value) != {"project_id", "static"} or not isinstance(value["project_id"], str)
            or not value["project_id"].startswith("appgprj_")
            or value["static"] != {"directory": "dist", "not_found_handling": "single-page-application"}):
        raise RuntimeError("configured zero-cost Sites static hosting path is unsafe")
    return value


def capture_managed_secrets(inventory: list[Mapping], prior_values: Mapping[str, str]) -> dict:
    """Supabase exposes secret digests, never recoverable secret values.

    The protected secret store must supply each existing managed value; its
    digest is checked against the current platform inventory before rotation.
    Missing values abort capture instead of attempting destructive cleanup.
    """
    if not isinstance(inventory, list):
        raise RuntimeError("managed secret inventory is unavailable")
    managed = sorted(({"name": row["name"], "digest": row.get("digest")}
                      for row in inventory if row.get("name") in MANAGED_SECRETS), key=lambda row: row["name"])
    if len({row["name"] for row in managed}) != len(managed):
        raise RuntimeError("managed secret inventory is ambiguous")
    values = {}
    for row in managed:
        value = prior_values.get(row["name"])
        if not isinstance(value, str) or hashlib.sha256(value.encode()).hexdigest() != row.get("digest"):
            raise RuntimeError("managed secret value/digest is unavailable or mismatched")
        values[row["name"]] = value
    absent = [name for name in MANAGED_SECRETS if name not in values]
    return {"exists": bool(values), "identity": "dashboard-secrets" if values else None,
            "version": hashlib.sha256(canonical(managed)).hexdigest() if values else None,
            "configuration": {"absent": absent}, "files": {}, "values": values}


def require_site_transport(repo_root: Path = ROOT, environment: Mapping | None = None, *, adapter=None, inspect_current: bool = True):
    configuration = site_configuration(repo_root)
    # Native Sites is configured, but this repository has no protected CI
    # transport that can capture/download a prior private Site version and
    # restore its active identity. A user-supplied executable or JSON receipt
    # must not bypass that missing capability. Only an in-process reviewed
    # native implementation can satisfy this contract; no env executable hook.
    if (adapter is None or isinstance(adapter, Mapping)
            or getattr(adapter, "project_id", None) != configuration["project_id"]
            or any(not callable(getattr(adapter, name, None)) for name in ("capture", "apply", "restore", "attest_recovery"))):
        raise RuntimeError("protected native Sites deployment/readback/recovery transport is unavailable; release blocked before mutation")
    if not inspect_current:
        return adapter
    snapshot = validate_snapshot("owner-web-site", adapter.capture("owner-web-site"))
    access = snapshot["configuration"].get("access_policy", {})
    if (access.get("access_mode") != "custom" or len(access.get("allowed_account_user_ids", [])) != 1
            or access.get("external_visitor_count") != 0 or access.get("workspace_group_ids")
            or access.get("tenant_group_ids")):
        raise RuntimeError("protected native Sites transport cannot prove owner-only access")
    return adapter


class SiteBoundTransport:
    def __init__(self, transport, site): self.transport, self.site = transport, site
    def provider(self, name): return self.site if name == "owner-web-site" else self.transport
    def capture(self, name): return self.provider(name).capture(name)
    def apply(self, name, candidate): return self.provider(name).apply(name, candidate)
    def restore(self, name, prior): return self.provider(name).restore(name, prior)
    def hydrate_recovery(self, name, prior, candidate):
        hydrate = getattr(self.provider(name), "hydrate_recovery", None)
        if callable(hydrate): hydrate(name, prior, candidate)
    def attest_recovery(self, name, prior, candidate, current):
        attest = getattr(self.provider(name), "attest_recovery", None)
        return attest(name, prior, candidate, current) is True if callable(attest) else current == candidate


def execute_protected_release(transport: ComponentTransport, candidates: Mapping, *, repo_root: Path,
                              site_adapter, persist: Callable[[dict], None], checkpoint=lambda _name: None,
                              before_mutations=lambda: None, after_mutations=lambda: None) -> dict:
    site = require_site_transport(repo_root, adapter=site_adapter)
    return execute_release(SiteBoundTransport(transport, site), candidates, persist=persist, checkpoint=checkpoint,
                           before_mutations=before_mutations, after_mutations=after_mutations)


def load_native_release_adapter(context: Mapping, *, repo_root: Path = ROOT):
    """Load only the reviewed repository-native transport, never an env command.

    This adapter owns the protected database, runtime role, secrets, and Edge
    functions. Native Sites publication is a separate owner-only operation with
    its own exact-source receipt because the Sites connector is not available to
    GitHub Actions.
    """
    site_configuration(repo_root)
    name = "scripts.configured_native_release_adapter"
    specification = importlib.util.find_spec(name)
    expected = repo_root / "scripts/configured_native_release_adapter.py"
    if specification is None or specification.origin is None or Path(specification.origin).resolve() != expected.resolve():
        raise RuntimeError("protected native backend transport is not configured; release blocked before mutation")
    adapter = importlib.import_module(name).create_adapter(dict(context))
    if any(not callable(getattr(adapter, method, None)) for method in (
            "capture", "apply", "restore", "plan", "retain", "receipt", "artifact",
            "recover_retained", "journal_receipt", "finalize_backend_evidence")):
        raise RuntimeError("native component capture/deployment/readback/recovery capabilities are incomplete")
    return adapter


def run_native_release(adapter, context: Mapping, *, repo_root: Path, journal_path: Path, key: bytes,
                       migrate: Callable[[], None], checkpoint=lambda _name: None,
                       verify_receipt: Callable[[dict], None] | None = None,
                       on_unjournaled_failure: Callable[[], None] = lambda: None) -> dict:
    """Shared production orchestration, including postdeployment failure recovery."""
    # The durable lease is already recovery_required when this function starts.
    # Retain a no-mutation phase before candidate planning, validation, or remote
    # capture. Independent recovery can safely close it because no component
    # mutation is reachable before execute_release replaces this phase.
    try:
        sink = EncryptedJournal(journal_path, key, retain=adapter.retain)
        def persist(journal): sink({**journal, "release_context": dict(context)})
        persist({"format": 1, "status": "preparing",
                 "captured_at": datetime.now(timezone.utc).isoformat(), "components": {}})
    except Exception:
        on_unjournaled_failure()
        raise
    candidates = adapter.plan(dict(context))
    receipt = {}
    def verify():
        checkpoint("verification")
        receipt.update(adapter.receipt(context["candidate_sha"]))
        if verify_receipt is not None:
            verify_receipt(receipt)
        else:
            adapter.verify(receipt)
    result = execute_release(adapter, candidates, components=BACKEND_COMPONENTS,
        persist=persist, checkpoint=checkpoint, before_mutations=migrate, after_mutations=verify)
    recovery = (adapter.journal_receipt() if callable(getattr(adapter, "journal_receipt", None))
                else {"format": 1, "encrypted": True})
    if callable(getattr(adapter, "finalize_backend_evidence", None)):
        adapter.finalize_backend_evidence(receipt, recovery)
    return {**receipt, "deployment_outcome": "succeeded", "component_mutations": result,
            "recovery_journal": recovery}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    checks = parser.add_mutually_exclusive_group(required=True)
    checks.add_argument("--check-backend-transport", action="store_true")
    checks.add_argument("--check-database-connectivity", action="store_true")
    arguments = parser.parse_args()
    if arguments.check_backend_transport:
        load_native_release_adapter({"project_ref": os.environ.get("PROJECT_REF"),
                                     "candidate_sha": os.environ.get("CANDIDATE_SHA")})
    else:
        from scripts.deploy_owner_dashboard_api import verify_release_database_transport
        project_ref = os.environ.get("PROJECT_REF", "").strip()
        admin_url = os.environ.get("POSTGRES_URL", "").strip()
        session_template = os.environ.get("SUPAVISOR_SESSION_URL", "").strip()
        if not project_ref or not admin_url or not session_template:
            raise SystemExit(
                "PROJECT_REF, POSTGRES_URL, and SUPAVISOR_SESSION_URL are required"
            )
        print(json.dumps(verify_release_database_transport(
            project_ref, admin_url, session_template,
        ), sort_keys=True, separators=(",", ":")))
