"""Print one bounded weekly portfolio-audit packet; perform no writes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import db
from lib.weekly_audit import build_packet


def main():
    packet = build_packet(
        holdings=db.get_holdings(),
        transactions=db.get_recent_transactions(limit=50),
        suggestions=db.get_recent_suggestions(limit=50),
        grades=db.get_recent_grades(limit=100),
        lessons=db.get_lessons(limit=40),
        snapshots=db.get_recent_snapshots(limit=150),
        generated_at=datetime.now(timezone.utc),
    )
    print(json.dumps(packet, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
