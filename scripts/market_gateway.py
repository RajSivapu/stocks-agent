#!/usr/bin/env python3
"""Safe stdin/stdout adapter for the market-briefing gateway."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import gateway


def _print(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=gateway.OPERATIONS)
    parser.add_argument("--run-id")
    parser.add_argument("--request-id")
    parser.add_argument("--dry-run", action="store_true")
    try:
        args = parser.parse_args(argv)
        raw = sys.stdin.buffer.read(gateway.MAX_REQUEST_BYTES + 1)
        if len(raw) > gateway.MAX_REQUEST_BYTES:
            raise ValueError("input too large")
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        result = gateway.call(
            args.operation,
            payload,
            run_id=args.run_id,
            request_id=args.request_id,
            dry_run=args.dry_run,
        )
    except gateway.GatewayError as error:
        _print({"code": error.code, "ok": False})
        return 1
    except (ValueError, json.JSONDecodeError):
        _print({"code": "INVALID_LOCAL_INPUT", "ok": False})
        return 2
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
