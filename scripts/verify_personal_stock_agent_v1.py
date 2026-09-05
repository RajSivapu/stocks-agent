#!/usr/bin/env python3
"""Verify one protected deployment against local Git bytes and queried production rows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Callable, Mapping, Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.export_recovery_bundle import canonical_json, sha256

MAX_SCHEDULED_RECEIPT_AGE_SECONDS = 7 * 24 * 60 * 60
SHA = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
FUNCTIONS = ("market-briefing-gateway", "owner-dashboard-api")


@runtime_checkable
class ReleaseDataSource(Protocol):
    def scheduled_run(self, deployed_at: str) -> str: ...
    def deployment(self, deployment_id: int) -> Mapping: ...
    def ci(self, workflow_run_id: int) -> Mapping: ...
    def merge(self, pull_request_number: int) -> Mapping: ...
    def reviews(self, pull_request_number: int) -> list[Mapping]: ...
    def release_rows(self, run_id: str) -> Mapping: ...
    def artifact(self, artifact_id: int) -> Mapping[str, bytes]: ...


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "receipt timestamp has no timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as error:
        raise RuntimeError("receipt timestamp is invalid") from error


def path_is_safe(path: str) -> bool:
    return bool(path) and "\\" not in path and not PurePosixPath(path).is_absolute() and all(part not in {"", ".", ".."} for part in path.split("/"))


def tree_sha256(files: Mapping[str, bytes]) -> str:
    require(files and all(path_is_safe(name) and isinstance(raw, bytes) for name, raw in files.items()), "artifact paths or bytes are invalid")
    digest = hashlib.sha256()
    for name, raw in sorted(files.items()):
        digest.update(name.encode() + b"\0" + raw + b"\0")
    return digest.hexdigest()


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    require(result.returncode == 0, "candidate Git object is unavailable")
    return result.stdout


def git_commit(repo: Path, sha: str) -> datetime:
    require(bool(SHA.fullmatch(sha)), "candidate SHA is malformed")
    raw = git(repo, "cat-file", "commit", sha)
    require(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha, "candidate Git object hash mismatch")
    return timestamp(git(repo, "show", "-s", "--format=%cI", sha).decode().strip())


def git_files(repo: Path, sha: str, prefix: str) -> dict[str, bytes]:
    files = {}
    for entry in git(repo, "ls-tree", "-rz", sha, "--", prefix).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        require(mode in {"100644", "100755"} and kind == "blob", "candidate artifact includes non-file entries")
        name = path.decode()
        require(name.startswith(prefix + "/"), "candidate artifact path mismatch")
        files[name[len(prefix) + 1:]] = git(repo, "cat-file", "blob", object_id)
    require(files, "candidate source tree is empty")
    return files


def verify_artifacts(repo: Path, static_root: Path, candidate: str, record: Mapping, source: ReleaseDataSource, now: datetime, deployed: datetime) -> None:
    migrations = git_files(repo, candidate, "sql/migrations")
    expected_migrations = [{"path": f"sql/migrations/{path}", "version": Path(path).name.split("_", 1)[0], "sha256": sha256(raw)} for path, raw in sorted(migrations.items()) if path.endswith(".sql")]
    require(record["migrations"] == expected_migrations, "migration byte hashes or complete version set differ from candidate")
    functions = record["functions"]
    require(isinstance(functions, list) and [row["function"] for row in functions] == list(FUNCTIONS), "function evidence is incomplete")
    for row in functions:
        require(row["git_sha"] == candidate and type(row["function_version"]) is int and row["function_version"] > 0
                and row["source_sha256"] == tree_sha256(git_files(repo, candidate, f"supabase/functions/{row['function']}")), "function byte hash or candidate SHA mismatch")
    static = record["static_assets"]
    require(static["candidate_sha"] == candidate and static["source_sha256"] == tree_sha256(git_files(repo, candidate, "apps/web")), "static source candidate hash mismatch")
    require(static_root.is_dir() and not static_root.is_symlink(), "static build is unavailable")
    local_files = {}
    for path in sorted(static_root.rglob("*")):
        require(not path.is_symlink(), "static build includes a symlink")
        if path.is_file():
            local_files[path.relative_to(static_root).as_posix()] = sha256(path.read_bytes())
    require(local_files and "index.html" in local_files and local_files == static["files"], "static artifact bytes do not match protected deployment")
    capture = record["rollback_capture"]
    captured = timestamp(capture["captured_at"])
    require(captured < deployed <= now and git_commit(repo, capture["commit_sha"]) <= captured, "rollback capture is not predeployment")
    captured_files = source.artifact(capture["artifact_id"])
    captured_hash = tree_sha256(captured_files)
    require(captured_files == git_files(repo, capture["commit_sha"], "supabase/functions/market-briefing-gateway")
            and captured_hash == capture["source_sha256"], "captured rollback artifact bytes mismatch")
    outcome = record.get("deployment_outcome")
    if outcome == "succeeded":
        ready = record["rollback_readiness"]
        drill = ready["isolated_drill"]
        require(ready["status"] == "ready" and type(ready["function_version"]) is int and ready["function_version"] > 0
                and ready["source_sha256"] == captured_hash and drill["status"] == "verified" and drill["isolated"] is True
                and drill["source_sha256"] == captured_hash and timestamp(drill["started_at"]) <= timestamp(drill["completed_at"]), "successful deployment rollback readiness is incomplete")
    elif outcome == "failed":
        rollback = record["rollback"]
        gateway, runtime = rollback["gateway"], rollback["runtime_login"]
        require(rollback["status"] == "rolled_back" and gateway["status"] == "restored" and gateway["commit_sha"] == capture["commit_sha"]
                and gateway["source_sha256"] == captured_hash and type(gateway["function_version"]) is int and gateway["function_version"] > 0
                and captured <= timestamp(rollback["gateway_restored_at"]) <= timestamp(rollback["dashboard_cleaned_at"]) < deployed
                and runtime["login"] is False and runtime["memberships"] == 0
                and rollback["dashboard_secrets_unset"] == ["DASHBOARD_ALLOWED_ORIGINS", "DASHBOARD_DATABASE_URL", "DASHBOARD_OWNER_USER_ID"], "gateway-first rollback evidence is incomplete")
    else:
        raise RuntimeError("deployment outcome is missing or unsafe")


def one(rows: object, label: str) -> Mapping:
    require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping), f"scheduled {label} receipt is missing or ambiguous")
    return rows[0]


def report_identity(kind: str, market_date: str, packet_hash: str, report_hash: str) -> tuple[str, str]:
    key = sha256(f"v2:{kind}:{market_date}:{packet_hash}:{report_hash}".encode())
    return key, f"{key[:8]}-{key[8:12]}-5{key[13:16]}-8{key[17:20]}-{key[20:32]}"


def verify_scheduled(rows: Mapping, run_id: str, deployed: datetime, now: datetime) -> dict:
    run = one(rows["run"], "run")
    intelligence = one(rows["intelligence_runs"], "intelligence run")
    require(UUID.fullmatch(run_id) and run["id"] == intelligence["id"] == run_id, "scheduled run identity mismatch")
    phase, market_date = run["scheduled_phase"], run["scheduled_market_date"]
    start, end = timestamp(run["started_at"]), timestamp(run["finished_at"])
    require(phase in {"pre-market", "intraday", "post-market"} and run["kind"] == intelligence["phase"] == phase
            and intelligence["market_date"] == market_date and run["status"] in {"completed", "suppressed"}
            and deployed < start <= end <= now and (now - end).total_seconds() <= MAX_SCHEDULED_RECEIPT_AGE_SECONDS, "scheduled receipt is stale, future, or not postdeployment")
    requests = rows["requests"]
    require(all(isinstance(row, Mapping) and UUID.fullmatch(str(row.get("request_id", ""))) for row in requests), "scheduled request UUID is invalid")
    start_request = one([row for row in requests if row["request_id"] == run["gateway_request_id"]], "start")
    require(start_request["operation"] == "start_run" and start_request["run_id"] == run_id and start_request["status"] == "completed"
            and start_request["response"]["run_id"] == run_id and start_request["response"].get("duplicate") is False
            and start_request["response"].get("dry_run") is not True, "scheduled start is duplicate or dry-run")
    completion = one(rows["completions"], "collection")
    packet = one(rows["packets"], "packet")
    require(UUID.fullmatch(completion["completion_id"]) and completion["run_id"] == run_id
            and any(row["id"] == completion["completion_id"] and row["run_id"] == run_id and row["status"] == "completed" for row in rows["run_events"])
            and rows["checkpoints"] and all(row["run_id"] == run_id for row in rows["checkpoints"]), "scheduled collection stage is not persisted")
    require(UUID.fullmatch(packet["id"]) and packet["run_id"] == run_id and sha256(canonical_json(packet["packet"]).encode()) == packet["packet_hash"]
            and completion["receipt"]["packet_id"] == packet["id"] and completion["receipt"]["packet_hash"] == packet["packet_hash"], "scheduled packet content or collection hash mismatch")
    event_ids = set()
    for row in rows["events"]:
        require(UUID.fullmatch(row["id"]) and row["run_id"] == run_id and sha256(canonical_json(row["canonical"]).encode()) == row["content_hash"], "event content hash mismatch")
        event_ids.add(row["id"])
    for row in rows["rankings"]:
        require(UUID.fullmatch(row["id"]) and row["run_id"] == run_id and row["event_id"] in event_ids
                and sha256(canonical_json(row["canonical"]).encode()) == row["content_hash"], "ranking content or event relationship mismatch")
    evaluation = one([row for row in requests if row["operation"] == "evaluate_and_publish" and row["run_id"] == run_id and row["status"] == "completed"], "evaluation")
    evaluation_publication = one([row for row in rows["evaluation_publications"] if row["id"] == evaluation["response"]["publication_id"]], "evaluation publication")
    require(UUID.fullmatch(evaluation_publication["id"]) and evaluation_publication["run_id"] == run_id and evaluation_publication["phase"] == phase
            and evaluation_publication["market_date"] == market_date and evaluation_publication["status"] == "suppressed", "scheduled evaluation publication mismatch")
    origin = one(rows["origins"], "report origin")
    report_request = one([row for row in requests if row["request_id"] == origin["request_id"] and row["operation"] == "record_report" and row["run_id"] is None and row["status"] == "completed"], "report request")
    report = one([row for row in rows["reports"] if row["id"] == report_request["response"]["report_id"]], "report")
    require((origin["requested_idempotency_key"], origin["requested_report_id"]) == report_identity(origin["requested_kind"], market_date, packet["packet_hash"], origin["requested_report_hash"])
            and (report["idempotency_key"], report["id"]) == report_identity(report["kind"], market_date, packet["packet_hash"], report["report_hash"]), "scheduled original or rendered report identity mismatch")
    allowed = {"pre-market": {"morning", "monthly"}, "intraday": {"intraday", "urgent"}, "post-market": {"weekly", "monthly", "theme", "urgent"}}
    require(origin["run_id"] == run_id and origin["scheduled_phase"] == phase and origin["market_date"] == market_date and origin["requested_kind"] in allowed[phase]
            and UUID.fullmatch(origin["requested_report_id"]) and re.fullmatch(r"[0-9a-f]{64}", origin["requested_idempotency_key"])
            and re.fullmatch(r"[0-9a-f]{64}", origin["requested_report_hash"])
            and UUID.fullmatch(report["id"]) and report["run_id"] == run_id and report["market_date"] == market_date
            and report["packet_id"] == origin["requested_packet_id"] == packet["id"]
            and sha256(canonical_json(report["report"]).encode()) == report["report_hash"] == report_request["response"]["report_hash"]
            and sha256(report["rendered_text"].encode()) == report["rendered_hash"] == report_request["response"]["rendered_hash"], "scheduled report origin, relationship, or hash mismatch")
    publication = one([row for row in rows["publications"] if row["report_id"] == report["id"]], "publication")
    require(publication["idempotency_key"] == report["idempotency_key"] and re.fullmatch(r"[0-9a-f]{64}", publication["idempotency_key"]), "scheduled outbox identity mismatch")
    ids = publication["telegram_message_ids"]
    if publication["status"] == "delivered":
        accepted = timestamp(publication["telegram_accepted_at"])
        require(isinstance(ids, list) and ids and all(type(value) is int and value > 0 for value in ids)
                and start <= accepted <= end and publication.get("suppression_reason") is None and run["telegram_message_ids"] == ids, "original Telegram delivery receipt is incomplete")
        delivery = {"status": "accepted_by_telegram", "telegram_message_ids": ids, "telegram_accepted_at": publication["telegram_accepted_at"]}
    else:
        reason = publication.get("suppression_reason")
        require(publication["status"] == "suppressed" and ids == [] and publication["telegram_accepted_at"] is None
                and isinstance(reason, str) and reason.strip() and run["telegram_message_ids"] == [], "explicit scheduled suppression reason is missing")
        delivery = {"status": "suppressed", "telegram_message_ids": [], "suppression_reason": reason}
    require(rows["quota"] and all(row["run_id"] == run_id and UUID.fullmatch(row["id"]) and type(row["actual_requests"]) is int
            and type(row["reserved_requests"]) is int and 0 <= row["actual_requests"] <= row["reserved_requests"] for row in rows["quota"]), "scheduled quota receipts are incomplete")
    return {"run_id": run_id, "packet_id": packet["id"], "packet_hash": packet["packet_hash"], "report_id": report["id"], "report_hash": report["report_hash"],
            "stage_ids": {"collection": completion["completion_id"], "packet": packet["id"], "evaluation": evaluation["request_id"], "report": report_request["request_id"], "publication": publication["report_id"]},
            "publication_key": publication["idempotency_key"], "publication_receipt": delivery}


def verify_release(source: ReleaseDataSource, *, deployment_id: int, repo_root: Path = ROOT, static_root: Path = ROOT / "dist",
                   clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, object]:
    require(not isinstance(source, Mapping) and isinstance(source, ReleaseDataSource), "protected production data source is required")
    try:
        now = clock()
        require(now.tzinfo is not None, "verifier clock must have timezone")
        record = source.deployment(deployment_id)
        candidate = record["sha"]
        candidate_commit_time = git_commit(repo_root, candidate)
        ci, merge = source.ci(record["workflow_run_id"]), source.merge(record["pull_request_number"])
        deployed, merged = timestamp(record["deployed_at"]), timestamp(merge["merged_at"])
        require(record["id"] == deployment_id and record["environment"] == "production" and record["candidate_sha"] == candidate
                and ci["head_sha"] == candidate and ci["status"] == "completed" and ci["conclusion"] == "success"
                and ci["path"] == ".github/workflows/owner-dashboard-ci.yml" and ci["id"] == record["workflow_run_id"]
                and merge["merged"] is True and merge["merge_commit_sha"] == candidate
                and merged <= candidate_commit_time < deployed <= now and candidate_commit_time <= timestamp(ci["updated_at"]) <= deployed, "protected CI/merge/deployment candidate SHA or time mismatch")
        reviewed_head = merge["head"]["sha"]
        require(bool(re.fullmatch(r"[0-9a-f]{40}", reviewed_head)), "reviewed PR head is malformed")
        reviewed_head_time = git_commit(repo_root, reviewed_head)
        reviews = source.reviews(record["pull_request_number"])
        require(any(row["state"] == "APPROVED" and row["commit_id"] == reviewed_head
                    and reviewed_head_time <= timestamp(row["submitted_at"]) <= merged <= candidate_commit_time <= deployed for row in reviews), "independent review of exact PR head is missing")
        if reviewed_head != candidate:
            ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", reviewed_head, candidate], cwd=repo_root, capture_output=True, check=False)
            if ancestor.returncode != 0:
                reviewed_tree = subprocess.run(["git", "rev-parse", f"{reviewed_head}^{{tree}}"], cwd=repo_root, text=True, capture_output=True, check=False)
                candidate_tree = subprocess.run(["git", "rev-parse", f"{candidate}^{{tree}}"], cwd=repo_root, text=True, capture_output=True, check=False)
                require(reviewed_tree.returncode == candidate_tree.returncode == 0 and reviewed_tree.stdout == candidate_tree.stdout, "reviewed PR head does not bind candidate merge/tree")
        verify_artifacts(repo_root, static_root, candidate, record, source, now, deployed)
        require(record.get("dry_run") is False, "protected deployment dry-run authority must be false")
        dry = record["dry_run_evidence"]
        before, after = dry["before"], dry["after"]
        argv = dry.get("safe_command_argv")
        script_sha = dry.get("candidate_script_sha256")
        expected_command_hash = hashlib.sha256(json.dumps({"argv": argv, "candidate_sha": candidate,
            "candidate_script_sha256": script_sha}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        require(isinstance(before, Mapping) and isinstance(after, Mapping) and before["tables"] == after["tables"]
                and isinstance(dry["table_deltas"], Mapping) and set(dry["table_deltas"]) == set(before["tables"])
                and all(type(value) is int and value == 0 for value in dry["table_deltas"].values())
                and type(dry["safe_command_exit_code"]) is int and dry["safe_command_exit_code"] == 0
                and isinstance(argv, list) and all(isinstance(arg, str) for arg in argv) and candidate in argv and "--dry-run" in argv
                and isinstance(script_sha, str) and script_sha == sha256(git(repo_root, "show", f"{candidate}:scripts/deploy_owner_dashboard_api.py"))
                and dry["safe_command_sha256"] == expected_command_hash
                and before.get("source") == after.get("source")
                and all(isinstance(value, Mapping) and set(value) == {"count", "rows_sha256"} and type(value.get("count")) is int
                        and isinstance(value.get("rows_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", value["rows_sha256"])
                        for value in before["tables"].values()), "protected dry-run side-effect evidence is incomplete")
        require(record["canaries"] == {"owner": 200, "anonymous": 401, "non_owner": 403}, "protected owner/denial canaries are incomplete")
        run_id = source.scheduled_run(record["deployed_at"])
        chain = verify_scheduled(source.release_rows(run_id), run_id, deployed, now)
        return {"status": "verified", "candidate_sha": candidate, "deployment_id": deployment_id, **chain}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise RuntimeError("protected release evidence is unavailable or malformed") from error


def main() -> int:
    from scripts.protected_evidence import GitHubProductionDataSource, PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--deployment-id", type=int, required=True)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--static-root", type=Path, required=True)
    args = parser.parse_args()
    with PostgresReadOnlySource(os.environ.get("RELEASE_READONLY_DATABASE_URL", ""), args.production_project_ref) as database:
        source = GitHubProductionDataSource(args.repository, args.production_project_ref, database)
        print(json.dumps(verify_release(source, deployment_id=args.deployment_id, static_root=args.static_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
