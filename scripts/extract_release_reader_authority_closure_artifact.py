#!/usr/bin/env python3
"""Extract and byte-bind the immutable primary authority-closure artifact."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import zipfile


MEMBER = "release-reader-authority-closure.json"
MAX_ARCHIVE_BYTES = 131_072
MAX_MEMBER_BYTES = 16_384


def extract_closure_artifact(
    archive: Path,
    digest: str,
    expected_local: Path,
    output: Path,
) -> None:
    if (
        not archive.is_file() or archive.is_symlink()
        or archive.stat().st_size > MAX_ARCHIVE_BYTES
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        or hashlib.sha256(archive.read_bytes()).hexdigest() != digest.removeprefix("sha256:")
        or not expected_local.is_file() or expected_local.is_symlink()
        or expected_local.stat().st_size > MAX_MEMBER_BYTES
        or output.name != MEMBER
        or output.exists() or output.is_symlink()
        or output.parent.exists()
    ):
        raise RuntimeError("closure artifact archive or destination is unsafe")
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) != 1 or members[0].filename != MEMBER:
                raise RuntimeError("closure artifact members are not exact")
            member = members[0]
            mode = member.external_attr >> 16
            if (
                member.is_dir() or stat.S_ISLNK(mode)
                or member.file_size > MAX_MEMBER_BYTES
            ):
                raise RuntimeError("closure artifact member is unsafe")
            payload = bundle.read(member)
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError("closure artifact archive is malformed") from error
    if payload != expected_local.read_bytes():
        raise RuntimeError("local closure receipt differs from uploaded artifact")
    output.parent.mkdir(mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    output.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--expected-local", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extract_closure_artifact(
        args.archive.resolve(), args.digest, args.expected_local.resolve(),
        args.output.resolve(),
    )
    print("closure artifact bytes verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
