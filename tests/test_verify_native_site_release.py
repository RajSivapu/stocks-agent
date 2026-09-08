import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_existing_v1_runtime_attestation import PROJECT_REF, _site_receipt, _site_repo


ROOT = Path(__file__).resolve().parents[1]


def test_native_site_release_cli_loads_without_pythonpath_injection():
    result = subprocess.run(
        [sys.executable, "scripts/verify_native_site_release.py", "--help"],
        cwd=ROOT, env={"PATH": os.environ["PATH"], "PYTHONPATH": ""},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_exact_candidate_native_site_receipt_closes_only_owner_site_evidence(tmp_path):
    from scripts.verify_native_site_release import verify_native_site_release

    repo, _source_sha, candidate_sha = _site_repo(tmp_path)
    result = verify_native_site_release(
        _site_receipt(candidate_sha), candidate_sha, PROJECT_REF, repo,
    )

    assert result["candidate_sha"] == candidate_sha
    assert result["project_ref"] == PROJECT_REF
    assert result["evidence_class"] == {
        "owner_site": {"status": "verified", "candidate_sha": candidate_sha},
    }
    assert result["native_site"]["status"] == "verified"


def test_native_site_release_rejects_ui_identical_ancestor_for_exact_release(tmp_path):
    from scripts.verify_native_site_release import verify_native_site_release

    repo, source_sha, candidate_sha = _site_repo(tmp_path)
    with pytest.raises(RuntimeError, match="exact candidate"):
        verify_native_site_release(
            _site_receipt(source_sha), candidate_sha, PROJECT_REF, repo,
        )


def test_native_site_release_preserves_underlying_owner_only_validation(tmp_path):
    from scripts.verify_native_site_release import verify_native_site_release

    repo, _source_sha, candidate_sha = _site_repo(tmp_path)
    receipt = copy.deepcopy(_site_receipt(candidate_sha))
    receipt["site"]["allowed_owner_count"] = 2
    with pytest.raises(RuntimeError, match="owner-only"):
        verify_native_site_release(receipt, candidate_sha, PROJECT_REF, repo)
