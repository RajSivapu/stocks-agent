import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.integration_guard import require_isolated_supabase_test_project


def test_guard_refuses_production_project_even_when_opted_in(tmp_path):
    env = {
        "RUN_DB_INTEGRATION_TESTS": "1",
        "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "SUPABASE_TEST_PROJECT_REF": "zzzzzzzzzzzzzzzzzzzz",
    }
    with pytest.raises(RuntimeError, match="isolated test project"):
        require_isolated_supabase_test_project(env, tmp_path / "missing.json")


def test_guard_requires_explicit_opt_in(tmp_path):
    with pytest.raises(RuntimeError, match="RUN_DB_INTEGRATION_TESTS"):
        require_isolated_supabase_test_project({}, tmp_path / "missing.json")


def test_guard_accepts_only_the_exact_isolated_project_ref(tmp_path):
    project_ref = "abcdefghijklmnopqrst"
    env = {
        "RUN_DB_INTEGRATION_TESTS": "1",
        "SUPABASE_URL": f"https://{project_ref}.supabase.co",
        "SUPABASE_TEST_PROJECT_REF": project_ref,
    }

    assert require_isolated_supabase_test_project(env, tmp_path / "missing.json") == project_ref


@pytest.mark.parametrize(
    "url,test_project_ref",
    [
        ("https://abcdefghijklmnopqrst.supabase.co", "abcdefghijklmnopqrsx"),
        ("https://abcdefghijklmnopqrst.example.com", "abcdefghijklmnopqrst"),
        ("not-a-url", "abcdefghijklmnopqrst"),
        ("https://abcdefghijklmnopqrst.supabase.co", "too-short"),
    ],
)
def test_guard_rejects_non_matching_or_invalid_project_ref(tmp_path, url, test_project_ref):
    with pytest.raises(RuntimeError, match="isolated test project"):
        require_isolated_supabase_test_project(
            {
                "RUN_DB_INTEGRATION_TESTS": "1",
                "SUPABASE_URL": url,
                "SUPABASE_TEST_PROJECT_REF": test_project_ref,
            },
            tmp_path / "missing.json",
        )


def test_guard_refuses_a_declared_production_project_ref(tmp_path):
    project_ref = "abcdefghijklmnopqrst"
    env = {
        "RUN_DB_INTEGRATION_TESTS": "1",
        "SUPABASE_URL": f"https://{project_ref}.supabase.co",
        "SUPABASE_TEST_PROJECT_REF": project_ref,
        "SUPABASE_PRODUCTION_PROJECT_REF": project_ref,
    }

    with pytest.raises(RuntimeError, match="isolated test project"):
        require_isolated_supabase_test_project(env, tmp_path / "missing.json")


def _collect_database_tests(run_db_integration_tests: str | None, *pytest_args: str) -> str:
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    if run_db_integration_tests is None:
        environment.pop("RUN_DB_INTEGRATION_TESTS", None)
    else:
        environment["RUN_DB_INTEGRATION_TESTS"] = run_db_integration_tests
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *pytest_args, "tests/test_db.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in {0, 5}, result.stderr
    return result.stdout


def test_database_integration_collection_requires_explicit_opt_in():
    flag_off = _collect_database_tests(None)
    flag_off_with_marker_filter = _collect_database_tests(None, "-m", "db_integration")
    flag_on = _collect_database_tests("1")

    assert "test_holding_stop_roundtrip" not in flag_off
    assert "4 deselected" in flag_off
    assert "test_holding_stop_roundtrip" not in flag_off_with_marker_filter
    assert "test_holding_stop_roundtrip" in flag_on
    assert "16 tests collected" in flag_on
