#!/usr/bin/env python3
"""Compare immutable ledger folds with stored holdings without exposing portfolio values."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

import psycopg


ZERO = Decimal("0")
SHARE_QUANTUM = Decimal("0.00000001")
PRICE_QUANTUM = Decimal("0.0001")
MONEY_QUANTUM = Decimal("0.01")


class ProjectionError(RuntimeError):
    pass


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ProjectionError(f"{field} is not decimal") from error
    if not parsed.is_finite():
        raise ProjectionError(f"{field} is not finite")
    return parsed


def fold_events(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(event) for event in events]
    voided = {
        int(row["corrects_transaction_id"])
        for row in rows
        if row.get("event_type") == "void" and row.get("corrects_transaction_id") is not None
    }
    ordered = sorted(
        rows,
        key=lambda row: (str(row["executed_on"]), int(row["ledger_sequence"])),
    )
    shares = ZERO
    cost_basis = ZERO
    realized = ZERO
    opened_at: str | None = None
    bucket: str | None = None
    for row in ordered:
        event_type = row.get("event_type")
        if event_type in ("void", "legacy_record") or row.get("id") in voided:
            continue
        if event_type not in ("opening", "trade"):
            raise ProjectionError("unknown ledger event type")
        qty = _decimal(row.get("qty"), "qty")
        price = _decimal(row.get("price"), "price")
        fees = _decimal(row.get("fees", "0"), "fees")
        if qty <= ZERO or price <= ZERO or fees < ZERO:
            raise ProjectionError("invalid trade values")
        if row.get("side") == "buy":
            if shares == ZERO:
                opened_at = str(row["executed_on"])
                bucket = str(row.get("bucket") or "unclassified")
            elif row.get("bucket") is not None:
                bucket = str(row["bucket"])
            shares += qty
            cost_basis += qty * price + fees
        elif row.get("side") == "sell":
            if qty > shares:
                raise ProjectionError("negative historical balance")
            if qty == shares:
                realized += qty * price - cost_basis - fees
                shares = ZERO
                cost_basis = ZERO
                opened_at = None
                bucket = None
            else:
                average = (cost_basis / shares).quantize(
                    SHARE_QUANTUM, rounding=ROUND_HALF_UP
                )
                realized += qty * (price - average) - fees
                shares -= qty
                cost_basis -= average * qty
        else:
            raise ProjectionError("unknown ledger side")

    average_cost = ZERO if shares == ZERO else cost_basis / shares
    return {
        "shares": shares.quantize(SHARE_QUANTUM),
        "avg_cost": average_cost.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
        "realized_pnl": realized.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
        "opened_at": opened_at,
        "bucket": bucket,
        "projection_sequence": max(
            (int(row["ledger_sequence"]) for row in rows), default=0
        ),
    }


def verify_database(connection: psycopg.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT identities.owner_id, identities.ticker,
               holding.shares, holding.avg_cost, holding.projection_sequence
        FROM (
          SELECT owner_id, ticker FROM app.holdings
          UNION
          SELECT owner_id, ticker FROM app.transactions
          WHERE event_type IN ('opening', 'trade', 'void')
        ) AS identities
        LEFT JOIN app.holdings AS holding
          ON holding.owner_id = identities.owner_id AND holding.ticker = identities.ticker
        ORDER BY identities.owner_id, identities.ticker
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    checked_at = datetime.now(timezone.utc).isoformat()
    for owner_id, ticker, shares, avg_cost, sequence in rows:
        folded = connection.execute(
            "SELECT app.fold_holding(%s, %s)", (owner_id, ticker)
        ).fetchone()[0]
        folded_shares = Decimal(str(folded["shares"]))
        passed = (
            (
                folded_shares == ZERO
                and shares is None
                and avg_cost is None
                and sequence is None
            )
            or (
                folded_shares > ZERO
                and shares is not None
                and avg_cost is not None
                and sequence is not None
                and folded_shares == Decimal(str(shares))
                and Decimal(str(folded["avg_cost"])) == Decimal(str(avg_cost))
                and int(folded["projection_sequence"]) == int(sequence)
            )
        )
        results.append(
            {
                "owner_hash": hashlib.sha256(str(owner_id).encode()).hexdigest(),
                "ticker_hash": hashlib.sha256(str(ticker).encode()).hexdigest(),
                "sequence": int(folded["projection_sequence"]),
                "status": "passed" if passed else "failed",
                "checked_at": checked_at,
            }
        )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url-env", default="STAGING_POSTGRES_URL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_url = os.environ.get(args.db_url_env)
    if not database_url:
        print("projection verification failed: database environment is not configured", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url) as connection:
            results = verify_database(connection)
        print(json.dumps({"passed": all(row["status"] == "passed" for row in results), "checks": results}))
        return 0 if all(row["status"] == "passed" for row in results) else 1
    except (ProjectionError, psycopg.Error):
        print("projection verification failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
