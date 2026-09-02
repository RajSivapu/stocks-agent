import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_publish_script_resolves_repo_imports_when_run_by_path(tmp_path):
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": "",
        "SUPABASE_URL": "not-a-url",
        "SUPABASE_SERVICE_ROLE_KEY": "invalid-test-key",
    })

    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "publish_market_policy.py")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout.startswith("FAIL:")
    assert "ModuleNotFoundError" not in result.stderr
