#!/usr/bin/env python3
"""Authenticated bundle cipher; key input is environment-only."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("encrypt", "decrypt"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    key = os.environ.get("RELEASE_RECOVERY_KEY", "").encode()
    try:
        cipher = Fernet(key)
        raw = args.source.read_bytes()
        result = cipher.encrypt(raw) if args.operation == "encrypt" else cipher.decrypt(raw)
        args.destination.write_bytes(result)
        args.destination.chmod(0o600)
        return 0
    except (OSError, ValueError, InvalidToken):
        # Do not echo inputs, keys, or ciphertext details.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
