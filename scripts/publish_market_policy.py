#!/usr/bin/env python3
"""Publish and activate the checked-in owner-reviewed market policy."""

from lib import db
from lib.config import load_settings
from lib.policy_config import build_policy_config, validate_policy_config


def publish() -> None:
    policy = build_policy_config(load_settings())
    validate_policy_config(policy)
    client = db._sb()
    client.table("market_policy_config").upsert(
        {"version": policy["version"], "config": policy, "active": False},
        on_conflict="version",
    ).execute()
    client.rpc(
        "activate_market_policy_config",
        {"p_version": policy["version"]},
    ).execute()


if __name__ == "__main__":
    try:
        publish()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}")
        raise SystemExit(1)
    print("PASS: activated market policy version 1")
