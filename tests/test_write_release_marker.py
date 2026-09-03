from __future__ import annotations

import json

import pytest

from scripts import write_release_marker


def test_release_marker_is_exact_public_data_and_cannot_overwrite(tmp_path):
    output = tmp_path / "release.json"
    result = write_release_marker.write_marker(
        output=output,
        commit="a" * 40,
        environment="staging",
    )
    assert result == {"status": "written", "private_data": False}
    assert json.loads(output.read_text()) == {
        "commit": "a" * 40,
        "environment": "staging",
    }
    with pytest.raises(write_release_marker.ReleaseMarkerRejected, match="already exists"):
        write_release_marker.write_marker(
            output=output,
            commit="a" * 40,
            environment="staging",
        )


@pytest.mark.parametrize(
    ("commit", "environment"),
    [("short", "staging"), ("a" * 40, "preview"), ("G" * 40, "production")],
)
def test_release_marker_rejects_invalid_public_identity(tmp_path, commit, environment):
    with pytest.raises(write_release_marker.ReleaseMarkerRejected):
        write_release_marker.write_marker(
            output=tmp_path / "release.json",
            commit=commit,
            environment=environment,
        )
