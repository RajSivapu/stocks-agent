#!/usr/bin/env python3
"""Post the final deployment success while retaining the durable lease."""
from __future__ import annotations

import argparse
import os
import subprocess

from scripts.deploy_owner_dashboard_api import DurableMutationLease


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--lease-owner", required=True)
    parser.add_argument("--deployment-id", required=True, type=int)
    parser.add_argument("--release-artifact-id", required=True)
    parser.add_argument("--log-url", required=True)
    arguments = parser.parse_args()
    if arguments.deployment_id <= 0 or not arguments.release_artifact_id.isdecimal():
        raise SystemExit("canonical deployment and artifact identities are required")
    # The status call is intentionally inside the lease.  A runner loss after
    # it but before resolve leaves recovery_required, and workflow_run recovery
    # restores even if GitHub had accepted this earlier status.
    with DurableMutationLease(arguments.admin_url, arguments.lease_owner, "release") as lease:
        result = subprocess.run(
            [
                "gh", "api", "--method", "POST",
                f"repos/{os.environ['GITHUB_REPOSITORY']}/deployments/{arguments.deployment_id}/statuses",
                "-f", "state=success",
                "-f", f"description=release-artifact:{arguments.release_artifact_id}",
                "-f", f"log_url={arguments.log_url}",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit("GitHub rejected the terminal deployment status")
        lease.resolve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
