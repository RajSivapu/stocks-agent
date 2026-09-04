import re
from pathlib import Path


def test_github_actions_are_pinned_to_full_commit_shas() -> None:
    workflow = Path(".github/workflows/owner-dashboard-ci.yml").read_text()
    references = re.findall(r"uses:\s+([^\s]+)@([^\s]+)", workflow)

    assert references
    for action, revision in references:
        assert re.fullmatch(r"[0-9a-f]{40}", revision), f"{action} is not pinned to a full SHA"


def test_setup_node_v4_pin_is_verified() -> None:
    workflow = Path(".github/workflows/owner-dashboard-ci.yml").read_text()
    assert "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020" in workflow
