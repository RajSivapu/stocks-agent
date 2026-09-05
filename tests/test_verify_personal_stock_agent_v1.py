import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from scripts.verify_personal_stock_agent_v1 import verify_release
from test_recovery_bundle import recovery_records, digest

NOW = datetime(2026, 9, 5, 21, tzinfo=timezone.utc)
RUN, PACKET, REPORT, START, COLLECTION, EVALUATION, PUBLICATION, REQUEST = [f"{n:08d}-1111-4111-8111-111111111111" for n in range(1, 9)]
_ledger = recovery_records()
REPORT_KEY = hashlib.sha256(f"v2:weekly:2026-09-05:{_ledger['packets'][0]['packet_hash']}:{_ledger['reports'][0]['report_hash']}".encode()).hexdigest()
REPORT = f"{REPORT_KEY[:8]}-{REPORT_KEY[8:12]}-5{REPORT_KEY[13:16]}-8{REPORT_KEY[17:20]}-{REPORT_KEY[20:32]}"


def tree_hash(files):
    hasher = hashlib.sha256()
    for path, raw in sorted(files.items()):
        hasher.update(path.encode() + b"\0" + raw + b"\0")
    return hasher.hexdigest()


class FakeReleaseSource:
    def scheduled_run(self, deployed_at):
        return RUN

    def deployment(self, deployment_id):
        assert deployment_id == 42
        return copy.deepcopy(self.record)

    def ci(self, run_id):
        assert run_id == 43
        return copy.deepcopy(self.ci_record)

    def merge(self, number):
        assert number == 44
        return copy.deepcopy(self.merge_record)

    def reviews(self, number):
        assert number == 44
        return copy.deepcopy(self.review_records)

    def release_rows(self, run_id):
        assert run_id == RUN
        return copy.deepcopy(self.rows)

    def artifact(self, artifact_id):
        return copy.deepcopy(self.artifacts[artifact_id])


@pytest.fixture
def release(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    raw = {"sql/migrations/20260926_suppression_reasons.sql": b"SELECT 1;\n",
           "supabase/functions/market-briefing-gateway/index.ts": b"gateway\n",
           "supabase/functions/owner-dashboard-api/index.ts": b"dashboard\n",
           "apps/web/src/main.tsx": b"web source\n"}
    for path, content in raw.items():
        destination = repo / path; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(content)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-09-05T17:00:00Z", "GIT_COMMITTER_DATE": "2026-09-05T17:00:00Z"}
    (repo / "supabase/functions/market-briefing-gateway/index.ts").write_bytes(b"prior gateway\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "prior"], cwd=repo, env=env, check=True)
    prior = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "supabase/functions/market-briefing-gateway/index.ts").write_bytes(b"gateway\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate"], cwd=repo, env=env, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    static = tmp_path / "static"; static.mkdir(); (static / "index.html").write_bytes(b"<main>Private</main>")
    source = FakeReleaseSource()
    source.ci_record = {"id": 43, "head_sha": sha, "conclusion": "success", "status": "completed", "path": ".github/workflows/owner-dashboard-ci.yml", "updated_at": "2026-09-05T17:30:00Z"}
    source.merge_record = {"number": 44, "merged": True, "merge_commit_sha": sha, "merged_at": "2026-09-05T18:00:00Z", "head": {"sha": sha}}
    source.review_records = [{"id": 45, "state": "APPROVED", "commit_id": sha, "submitted_at": "2026-09-05T17:45:00Z"}]
    source.artifacts = {46: {"index.ts": b"prior gateway\n"}}
    source.record = {
        "id": 42, "sha": sha, "environment": "production", "project_ref": "p" * 20,
        "deployed_at": "2026-09-05T19:00:00Z", "workflow_run_id": 43, "pull_request_number": 44,
        "run_id": RUN, "candidate_sha": sha,
        "migrations": [{"path": "sql/migrations/20260926_suppression_reasons.sql", "version": "20260926", "sha256": hashlib.sha256(raw["sql/migrations/20260926_suppression_reasons.sql"]).hexdigest()}],
        "functions": [{"function": name, "git_sha": sha, "function_version": 5, "source_sha256": tree_hash({"index.ts": raw[f"supabase/functions/{name}/index.ts"]})} for name in ("market-briefing-gateway", "owner-dashboard-api")],
        "static_assets": {"candidate_sha": sha, "source_sha256": tree_hash({"src/main.tsx": b"web source\n"}), "files": {"index.html": hashlib.sha256(b"<main>Private</main>").hexdigest()}},
        "dry_run": False,
        "dry_run_evidence": {"before": {"tables": {"scheduled_runs": {"count": 1, "ids": [RUN], "sha256": "a" * 64}, "transactions": {"count": 0, "ids": [], "sha256": "b" * 64}}}, "after": {"tables": {"scheduled_runs": {"count": 1, "ids": [RUN], "sha256": "a" * 64}, "transactions": {"count": 0, "ids": [], "sha256": "b" * 64}}}, "table_deltas": {"scheduled_runs": 0, "transactions": 0}, "safe_command_sha256": "c" * 64, "safe_command_exit_code": 0},
        "canaries": {"owner": 200, "anonymous": 401, "non_owner": 403},
        "rollback_capture": {"artifact_id": 46, "git_sha": prior, "captured_at": "2026-09-05T18:15:00Z", "source_sha256": tree_hash(source.artifacts[46])},
        "deployment_outcome": "succeeded",
        "rollback_readiness": {"status": "ready", "function_version": 4, "source_sha256": tree_hash(source.artifacts[46]), "isolated_drill": {"status": "verified", "isolated": True, "source_sha256": tree_hash(source.artifacts[46])}},
    }
    recovery = recovery_records(); packet = recovery["packets"][0]; report = recovery["reports"][0]
    source.rows = {
        "run": [{"id": RUN, "kind": "post-market", "scheduled_phase": "post-market", "scheduled_market_date": "2026-09-05", "status": "completed", "started_at": "2026-09-05T19:10:00Z", "finished_at": "2026-09-05T20:00:00Z", "gateway_request_id": START, "telegram_message_ids": [7]}],
        "intelligence_runs": [{"id": RUN, "phase": "post-market", "market_date": "2026-09-05"}],
        "completions": [{"completion_id": COLLECTION, "run_id": RUN, "receipt": {"packet_id": PACKET, "packet_hash": packet["packet_hash"]}}],
        "run_events": [{"id": COLLECTION, "run_id": RUN, "status": "completed"}],
        "checkpoints": [{"run_id": RUN, "cache_key": "b" * 64}],
        "packets": [{**packet, "id": PACKET, "run_id": RUN}], "events": [], "rankings": [],
        "reports": [{**report, "id": REPORT, "run_id": RUN, "packet_id": PACKET, "idempotency_key": REPORT_KEY, "market_date": "2026-09-05", "kind": "weekly"}],
        "publications": [{**recovery["publications"][0], "report_id": REPORT, "idempotency_key": REPORT_KEY}],
        "evaluation_publications": [{"id": PUBLICATION, "run_id": RUN, "status": "suppressed", "phase": "post-market", "market_date": "2026-09-05"}],
        "requests": [
            {"request_id": START, "run_id": RUN, "operation": "start_run", "status": "completed", "response": {"run_id": RUN, "duplicate": False}},
            {"request_id": EVALUATION, "run_id": RUN, "operation": "evaluate_and_publish", "status": "completed", "response": {"run_id": RUN, "publication_id": PUBLICATION}},
            {"request_id": REQUEST, "run_id": None, "operation": "record_report", "status": "completed", "response": {"report_id": REPORT, "report_hash": report["report_hash"], "rendered_hash": report["rendered_hash"]}},
        ],
        "origins": [{"request_id": REQUEST, "run_id": RUN, "requested_packet_id": PACKET, "scheduled_phase": "post-market", "market_date": "2026-09-05", "requested_kind": "weekly", "requested_report_id": REPORT, "requested_idempotency_key": REPORT_KEY, "requested_report_hash": report["report_hash"]}],
        "quota": [{"id": "99999999-1111-4111-8111-111111111111", "run_id": RUN, "provider": "gdelt", "reserved_requests": 2, "actual_requests": 1}],
    }
    return source, {"deployment_id": 42, "repo_root": repo, "static_root": static, "clock": lambda: NOW}


def test_release_queries_sources_and_binds_exact_receipts(release):
    source, args = release
    result = verify_release(source, **args)
    assert result["candidate_sha"] == source.record["sha"]
    assert result["stage_ids"] == {"collection": COLLECTION, "packet": PACKET, "evaluation": EVALUATION, "report": REQUEST, "publication": REPORT}
    assert result["publication_key"] == REPORT_KEY
    assert result["publication_receipt"]["telegram_message_ids"] == [7]
    source.record.pop("run_id")
    assert verify_release(source, **args)["run_id"] == RUN


def test_release_rejects_caller_json_even_when_labeled_authoritative(release):
    source, args = release
    with pytest.raises(RuntimeError, match="source"):
        verify_release({"authoritative_records": source.record}, **args)


@pytest.mark.parametrize("mutation", [
    lambda s: s.record.update(candidate_sha="a" * 40),
    lambda s: s.ci_record.update(head_sha="a" * 40),
    lambda s: s.merge_record.update(merge_commit_sha="a" * 40),
    lambda s: s.record["functions"][0].update(source_sha256="b" * 64),
    lambda s: s.record["migrations"][0].update(sha256="b" * 64),
    lambda s: s.record["static_assets"]["files"].update({"index.html": "b" * 64}),
    lambda s: s.rows["run"][0].update(started_at="2020-01-01T20:00:00Z", finished_at="2020-01-01T21:00:00Z"),
    lambda s: s.rows["run"][0].update(finished_at="2027-01-01T20:00:00Z"),
    lambda s: s.record.update(deployed_at="2027-01-01T20:00:00Z"),
    lambda s: s.rows["run"][0].update(kind="on-demand"),
    lambda s: s.rows["requests"][0]["response"].update(duplicate=True),
    lambda s: s.rows["requests"][0]["response"].update(dry_run=True),
    lambda s: s.rows["completions"][0].update(completion_id="collection-1"),
    lambda s: s.rows["run_events"][0].update(id="99999999-1111-4111-8111-111111111111"),
    lambda s: s.rows["origins"][0].update(request_id="99999999-1111-4111-8111-111111111111"),
    lambda s: s.rows["origins"][0].update(requested_report_hash="f" * 64),
    lambda s: s.rows["reports"][0].update(packet_id="99999999-1111-4111-8111-111111111111"),
    lambda s: s.rows["packets"][0]["packet"].update(policy_version=999),
    lambda s: s.rows["publications"][0].update(idempotency_key="b" * 64),
    lambda s: s.rows["publications"][0].update(telegram_message_ids=[]),
    lambda s: s.rows["publications"][0].update(telegram_accepted_at=None),
    lambda s: s.rows["publications"][0].update(status="suppressed", telegram_message_ids=[], telegram_accepted_at=None, error="not an explicit suppression reason"),
    lambda s: s.record["rollback_readiness"]["isolated_drill"].update(source_sha256="b" * 64),
    lambda s: s.artifacts[46].update({"index.ts": b"changed rollback"}),
])
def test_release_rejects_relabeling_invented_stages_and_hash_mismatches(release, mutation):
    source, args = release; mutation(source)
    with pytest.raises(RuntimeError):
        verify_release(source, **args)


def test_release_rejects_ancient_records_even_when_every_claimed_time_is_relabelled(release):
    source, args = release
    args["clock"] = lambda: datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="stale|scheduled"):
        verify_release(source, **args)


def test_release_requires_a_real_nonempty_suppression_reason(release):
    source, args = release
    source.rows["publications"][0].update(status="suppressed", telegram_message_ids=[], telegram_accepted_at=None, suppression_reason="NO_TRIGGER")
    source.rows["run"][0].update(status="suppressed", telegram_message_ids=[])
    assert verify_release(source, **args)["publication_receipt"]["suppression_reason"] == "NO_TRIGGER"


def test_release_recomputes_local_git_objects_and_static_bytes(release):
    source, args = release
    (args["static_root"] / "index.html").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="static"):
        verify_release(source, **args)


@pytest.mark.parametrize("value", ["missing", None, True, "false", 0, 1])
def test_release_requires_authoritative_non_dry_run_boolean(release, value):
    source, args = release
    if value == "missing":
        source.record.pop("dry_run")
    else:
        source.record["dry_run"] = value
    with pytest.raises(RuntimeError, match="dry-run"):
        verify_release(source, **args)


def test_production_source_reads_deployment_and_protected_artifact_instead_of_caller_json(monkeypatch):
    from scripts import protected_evidence as evidence
    database = object()
    source = evidence.GitHubProductionDataSource("owner/stocks-agent", "p" * 20, database)
    calls = []
    def get(path):
        calls.append(path)
        if path.endswith("/deployments/42"):
            return {"id": 42, "sha": "a" * 40, "environment": "production", "production_environment": True,
                    "payload": {"release_artifact_id": 46}, "created_at": "2026-09-05T18:00:00Z"}
        if path.endswith("/deployments/42/statuses"):
            return [{"state": "success", "created_at": "2026-09-05T19:00:00Z", "description": "release-artifact:46"}]
        raise AssertionError(path)
    monkeypatch.setattr(source, "_get", get)
    monkeypatch.setattr(source, "artifact", lambda artifact_id: {"release-record.json": b'{"candidate_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","project_ref":"pppppppppppppppppppp","deployment_id":42}'})
    result = source.deployment(42)
    assert result["deployed_at"] == "2026-09-05T19:00:00Z"
    assert result["id"] == 42
    assert len(calls) == 2


def test_production_source_refuses_unprotected_deployment(monkeypatch):
    from scripts.protected_evidence import GitHubProductionDataSource
    source = GitHubProductionDataSource("owner/stocks-agent", "p" * 20, object())
    monkeypatch.setattr(source, "_get", lambda path: {"id": 42, "environment": "staging", "production_environment": False})
    with pytest.raises(RuntimeError, match="production"):
        source.deployment(42)
