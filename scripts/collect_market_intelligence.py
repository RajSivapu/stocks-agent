#!/usr/bin/env python3
"""Emit one bounded intelligence packet for the scheduled Analyst/Checker pass."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import gateway  # noqa: E402
from lib.config import load_settings  # noqa: E402
from lib.intelligence.http import BoundedHttpClient  # noqa: E402
from lib.intelligence.pipeline import IntelligencePipeline, PHASES, PipelineRequest  # noqa: E402
from lib.intelligence.policy import load_intelligence_policy  # noqa: E402
from lib.intelligence.providers import build_adapter  # noqa: E402
from lib.intelligence.quota import QuotaSession  # noqa: E402


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid argument")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(add_help=False)
    parser.add_argument("--phase", required=True, choices=PHASES)
    parser.add_argument("--market-date")
    parser.add_argument("--now")
    parser.add_argument("--context-file")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("now must include a timezone")
    return parsed


def _market_date(value: str | None, now: datetime) -> date:
    return date.fromisoformat(value) if value else now.date()


def _adapters(policy, now: datetime):
    adapters = []
    empty_quota = QuotaSession({provider: () for provider in policy.providers})
    for provider in policy.providers:
        adapter = build_adapter(
            provider,
            BoundedHttpClient(allowed_hosts=_provider_hosts(provider)),
            empty_quota,
            clock=lambda: now,
        )
        adapters.append(adapter)
    return adapters


def _provider_hosts(provider: str) -> frozenset[str]:
    from lib.intelligence.providers.alpha_vantage import AlphaVantageAdapter
    from lib.intelligence.providers.finnhub import FinnhubAdapter
    from lib.intelligence.providers.gdelt import GdeltAdapter
    from lib.intelligence.providers.official import OFFICIAL_ADAPTERS
    from lib.intelligence.providers.yahoo import YahooAdapter

    types = {
        "gdelt": GdeltAdapter,
        "alpha_vantage": AlphaVantageAdapter,
        "finnhub": FinnhubAdapter,
        "yahoo": YahooAdapter,
        **OFFICIAL_ADAPTERS,
    }
    return types[provider].allowed_hosts


def _context(path: str | None) -> dict[str, object]:
    if path is None:
        return {}
    raw = Path(path).read_bytes()
    if len(raw) > 65_536:
        raise ValueError("context exceeds bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("context must be an object")
    allowed = {
        "holdings", "plans", "qualified_candidates", "urgent_events",
        "high_materiality_themes", "requested_topics",
    }
    return {key: value[key] for key in sorted(value) if key in allowed}


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    output = stdout or sys.stdout
    try:
        args = _parser().parse_args(argv)
        now = _now(args.now)
        request = PipelineRequest(args.phase, _market_date(args.market_date, now), now, args.dry_run)
        context = _context(args.context_file)
        if args.dry_run:
            pipeline = IntelligencePipeline(object(), (), context=context)
        else:
            policy = load_intelligence_policy(load_settings())
            pipeline = IntelligencePipeline(
                gateway, _adapters(policy, now), context=context, packet_limits=policy.packet
            )
        result = pipeline.run(request)
        output.write(result.to_json_bytes().decode("utf-8") + "\n")
        return 0
    except (SystemExit, TypeError, ValueError):
        output.write(json.dumps({"error": "INVALID_ARGUMENT", "ok": False}, separators=(",", ":"), sort_keys=True) + "\n")
        return 2
    except Exception:
        output.write(json.dumps({"error": "COLLECTION_FAILED", "ok": False}, separators=(",", ":"), sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
