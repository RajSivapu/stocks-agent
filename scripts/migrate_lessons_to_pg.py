"""
One-time migration: parse data/lessons.md and insert each entry into the lessons table.
Idempotent — skips rows that already exist (same entry_date + category + content).
Run once: python scripts/migrate_lessons_to_pg.py

NOTE: This migration has already been run. Script kept for reference only.
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import db, config
from supabase import create_client

LESSONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "lessons.md")


def parse_lessons(path):
    rows = []
    if not os.path.exists(path):
        print(f"  {path} not found — nothing to migrate")
        return rows
    with open(path) as f:
        text = f.read()
    current_category = "regime"
    for line in text.splitlines():
        line = line.strip()
        if "## Market regime log" in line:
            current_category = "regime"
        elif "## Lessons learned" in line:
            current_category = "lesson"
        elif "## Personalization" in line:
            current_category = "personalization"
        m = re.match(r"^-\s*`(\d{4}-\d{2}-\d{2})`\s*[—–-]\s*(.+)$", line)
        if not m:
            m = re.match(r"^-\s*(\d{4}-\d{2}-\d{2})\s*[—–-]\s*(.+)$", line)
        if m:
            entry_date, content = m.group(1), m.group(2).strip()
            rows.append({"entry_date": entry_date, "category": current_category, "content": content})
    return rows


def run():
    rows = parse_lessons(LESSONS_FILE)
    if not rows:
        print("No entries parsed — done.")
        return
    sb = create_client(config.secret("supabase_url"), config.secret("supabase_service_role_key"))
    inserted = 0
    for row in rows:
        existing = sb.table("lessons").select("id").eq("entry_date", row["entry_date"]) \
            .eq("category", row["category"]).eq("content", row["content"]).execute().data
        if not existing:
            db.insert_lesson(row)
            inserted += 1
    print(f"Migrated {inserted} entries ({len(rows) - inserted} already existed).")


if __name__ == "__main__":
    run()
