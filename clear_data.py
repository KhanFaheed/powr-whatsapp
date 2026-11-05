#!/usr/bin/env python
"""
POWR WhatsApp Bot - Clear Database Entries

Deletes every row from the three application models:
    - InboundMessage
    - ConversationState
    - LeadRecord

Usage:
    python clear_data.py                 # show counts, back up the DB, ask for confirmation
    python clear_data.py --yes           # no confirmation prompt
    python clear_data.py --dry-run       # only show what would be deleted (no changes)
    python clear_data.py --no-backup     # skip the SQLite backup copy
    python clear_data.py --keep-leads    # keep LeadRecord rows (delete only messages + state)

Notes:
    - Only the local database is touched. Odoo records created by the bot are NOT deleted.
    - Django's own tables (auth, sessions, admin, migrations) are left untouched.
"""
import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.db import transaction

from assistant.models import ConversationState, InboundMessage, LeadRecord


# ANSI colors (same palette as dev_test.py)
class C:
    H = '\033[95m'; B = '\033[94m'; CY = '\033[96m'; G = '\033[92m'
    Y = '\033[93m'; R = '\033[91m'; E = '\033[0m'; BD = '\033[1m'


def hdr(t): print(f"\n{C.H}{C.BD}{'=' * 60}\n{t:^60}\n{'=' * 60}{C.E}\n")
def info(t): print(f"{C.Y}ℹ️  {t}{C.E}")
def ok(t): print(f"{C.G}✅ {t}{C.E}")
def warn(t): print(f"{C.Y}⚠️  {t}{C.E}")
def err(t): print(f"{C.R}❌ {t}{C.E}")


def models_to_clear(keep_leads=False):
    """Return the (label, model) pairs to delete, in safe order."""
    pairs = [
        ("InboundMessage", InboundMessage),
        ("ConversationState", ConversationState),
    ]
    if not keep_leads:
        pairs.append(("LeadRecord", LeadRecord))
    return pairs


def backup_sqlite_db():
    """Copy the SQLite file next to itself with a timestamp (db.sqlite3.bak.YYYYmmdd-HHMMSS)."""
    db = settings.DATABASES.get("default", {})
    if db.get("ENGINE") != "django.db.backends.sqlite3":
        warn(f"Database engine is '{db.get('ENGINE')}' — skipping file backup (use your DB's own dump tool).")
        return None

    db_path = Path(db.get("NAME") or "")
    if not db_path.exists():
        warn(f"SQLite file not found at {db_path} — skipping backup.")
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak.{stamp}")
    shutil.copy2(db_path, backup_path)
    ok(f"Backup written: {backup_path}")
    return backup_path


def show_counts(pairs):
    total = 0
    for label, model in pairs:
        count = model.objects.count()
        total += count
        print(f"   {label:<20} {count:>6} row(s)")
    print(f"   {'TOTAL':<20} {total:>6} row(s)")
    return total


def clear(pairs):
    """Delete all rows of every model inside one transaction; return per-model counts."""
    deleted = {}
    with transaction.atomic():
        for label, model in pairs:
            count, _ = model.objects.all().delete()
            deleted[label] = count
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Delete all rows from the POWR assistant models.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="Only report counts, delete nothing")
    parser.add_argument("--no-backup", action="store_true", help="Do not copy the SQLite database first")
    parser.add_argument("--keep-leads", action="store_true", help="Keep LeadRecord rows")
    args = parser.parse_args()

    pairs = models_to_clear(keep_leads=args.keep_leads)

    hdr("Clear POWR Database Entries")
    db = settings.DATABASES.get("default", {})
    info(f"Database: {db.get('ENGINE')} · {db.get('NAME')}")
    info("Models  : " + ", ".join(label for label, _ in pairs))
    print()

    total = show_counts(pairs)
    print()

    if total == 0:
        ok("Nothing to delete — the tables are already empty.")
        return 0

    if args.dry_run:
        warn("Dry run — no rows were deleted.")
        return 0

    if not args.yes:
        answer = input(f"{C.R}This permanently deletes {total} row(s). Type 'yes' to continue: {C.E}").strip().lower()
        if answer not in {"yes", "y"}:
            warn("Aborted — nothing was deleted.")
            return 1

    if not args.no_backup:
        backup_sqlite_db()

    deleted = clear(pairs)

    print()
    for label, count in deleted.items():
        ok(f"{label}: deleted {count} object(s) (related rows included)")
    ok("Database entries cleared.")

    print()
    show_counts(pairs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
