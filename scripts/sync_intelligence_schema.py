"""Mechanically consolidate the ordered post-intelligence migrations into fresh schema."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "-- Consolidated from sql/migrations/20260908_owner_dashboard_intelligence_read_role.sql"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    path = ROOT / "sql/schema.sql"
    current = path.read_text()
    prefix, separator, _ = current.partition(MARKER)
    if not separator:
        raise ValueError("schema consolidation marker unavailable")
    suffix = "\n".join(f"-- Consolidated from {migration.relative_to(ROOT)}\n{migration.read_text().rstrip()}\n"
        for migration in sorted((ROOT / "sql/migrations").glob("*.sql")) if migration.name >= "20260908")
    generated = prefix.rstrip() + "\n\n" + suffix
    if args.write:
        path.write_text(generated)
    elif generated != current:
        raise SystemExit("fresh intelligence schema is out of date")


if __name__ == "__main__":
    main()
