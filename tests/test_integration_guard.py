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
