import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from test_existing_v1_runtime_attestation import (
    PROJECT_REF, SITE_FILES, _site_build_receipt, _site_receipt, _site_repo,
)


ROOT = Path(__file__).resolve().parents[1]


def _site_package() -> bytes:
    files = {
        "dist/.openai/hosting.json": json.dumps({
            "project_id": "appgprj_test",
            "static": {
                "directory": "dist",
                "not_found_handling": "single-page-application",
            },
        }).encode() + b"\n",
        **{f"dist/{path}": raw for path, raw in SITE_FILES.items()},
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def test_native_site_release_cli_loads_without_pythonpath_injection():
    result = subprocess.run(
        [sys.executable, "scripts/verify_native_site_release.py", "--help"],
        cwd=ROOT, env={"PATH": os.environ["PATH"], "PYTHONPATH": ""},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_exact_candidate_package_is_only_an_offline_content_comparison(tmp_path):
    from scripts.verify_native_site_release import compare_native_site_release

    repo, _source_sha, candidate_sha = _site_repo(tmp_path)
    result = compare_native_site_release(
        _site_package(), candidate_sha, PROJECT_REF, repo,
        protected_build_receipt=_site_build_receipt(candidate_sha),
    )

    assert result["candidate_sha"] == candidate_sha
    assert result["project_ref"] == PROJECT_REF
    assert result["evidence_class"] == {
        "owner_site": {
            "status": "pending",
            "required_evidence": "current_authenticated_native_connector_observation",
        },
    }
    assert result["status"] == "content_consistent"
    assert "verified" not in str(result)


def test_native_site_comparison_rejects_build_receipt_from_another_candidate(tmp_path):
    from scripts.verify_native_site_release import compare_native_site_release

    repo, source_sha, candidate_sha = _site_repo(tmp_path)
    with pytest.raises(RuntimeError, match="protected build"):
        compare_native_site_release(
            _site_package(), candidate_sha, PROJECT_REF, repo,
            protected_build_receipt=_site_build_receipt(source_sha),
        )


def test_native_site_comparison_rejects_package_bytes_that_differ_from_build(tmp_path):
    from scripts.verify_native_site_release import compare_native_site_release

    repo, _source_sha, candidate_sha = _site_repo(tmp_path)
    malformed = io.BytesIO()
    with tarfile.open(fileobj=malformed, mode="w:gz") as archive:
        raw = b"tampered"
        info = tarfile.TarInfo("dist/index.html")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    with pytest.raises(RuntimeError, match="archive|package|build"):
        compare_native_site_release(
            malformed.getvalue(), candidate_sha, PROJECT_REF, repo,
            protected_build_receipt=_site_build_receipt(candidate_sha),
        )


def test_fully_consistent_caller_json_can_never_close_owner_site(tmp_path):
    from scripts.verify_native_site_release import compare_native_site_release

    repo, _source_sha, candidate_sha = _site_repo(tmp_path)
    with pytest.raises(RuntimeError, match="archive bytes"):
        compare_native_site_release(
            _site_receipt(candidate_sha), candidate_sha, PROJECT_REF, repo,
            protected_build_receipt=_site_build_receipt(candidate_sha),
        )
