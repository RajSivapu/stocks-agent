#!/usr/bin/env python3
"""Candidate-owned, local read-only verifier used by protected release evidence."""
from __future__ import annotations

import argparse
import re
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.candidate_sha):
        raise SystemExit("candidate SHA is malformed")
    result = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    if result.returncode != 0 or result.stdout.strip() != args.candidate_sha:
        raise SystemExit("candidate SHA/ref mismatch")
    # Deliberately no network/database/gateway import: collection's restricted
    # reader supplies the surrounding before/after evidence.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
