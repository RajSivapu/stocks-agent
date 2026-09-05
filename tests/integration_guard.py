"""Fail-closed configuration boundary for credentialed database tests."""

from __future__ import annotations

from collections.abc import Mapping
import re
from pathlib import Path
from urllib.parse import urlparse


PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")


def require_isolated_supabase_test_project(
    environment: Mapping[str, str], secrets_path: Path,
) -> str:
    """Return the configured test project ref only after fail-closed validation.

    ``secrets_path`` is deliberately not opened here.  Callers must invoke this
    boundary before reading the isolated test credentials, which prevents the
    ordinary suite from falling back to ``config/secrets.local.json``.
    """
    del secrets_path
    if environment.get("RUN_DB_INTEGRATION_TESTS") != "1":
        raise RuntimeError("RUN_DB_INTEGRATION_TESTS=1 is required")

    project_ref = environment.get("SUPABASE_TEST_PROJECT_REF", "")
    parsed = urlparse(environment.get("SUPABASE_URL", ""))
    if (
        not PROJECT_REF_PATTERN.fullmatch(project_ref)
        or parsed.scheme != "https"
        or parsed.hostname != f"{project_ref}.supabase.co"
    ):
        raise RuntimeError("an isolated test project is required")

    if environment.get("SUPABASE_PRODUCTION_PROJECT_REF") == project_ref:
        raise RuntimeError("an isolated test project is required")
    return project_ref
