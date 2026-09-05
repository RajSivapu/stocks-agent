import os

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_DB_INTEGRATION_TESTS") == "1":
        return

    deselected = [item for item in items if item.get_closest_marker("db_integration")]
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    items[:] = [item for item in items if item not in deselected]
