#!/usr/bin/env python3
"""Write one immutable public marker that binds static assets to a tested commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseMarkerRejected(RuntimeError):
    """The static release identity is invalid or could overwrite another file."""


def write_marker(*, output: Path, commit: str, environment: str) -> dict[str, object]:
    if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
        raise ReleaseMarkerRejected("release commit is invalid")
    if environment not in {"staging", "production"}:
        raise ReleaseMarkerRejected("release environment is invalid")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ReleaseMarkerRejected("release marker directory is invalid")
    payload = json.dumps(
        {"commit": commit, "environment": environment},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o644)
    except FileExistsError as error:
        raise ReleaseMarkerRejected("release marker already exists") from error
    except OSError as error:
        raise ReleaseMarkerRejected("release marker could not be created") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
            marker.write(payload)
    except OSError as error:
        raise ReleaseMarkerRejected("release marker could not be written") from error
    return {"status": "written", "private_data": False}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = write_marker(
            output=args.output,
            commit=args.commit,
            environment=args.environment,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except ReleaseMarkerRejected:
        print("release marker rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
