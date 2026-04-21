"""
One-time migration: enable SLA for all existing spaces.

Background
----------
After changing sla_enabled DEFAULT from 1 → 0, new spaces are opt-in.
Spaces already in the DB before this migration should remain active,
so we set sla_enabled = 1 for any space whose first_seen_at is in the past.

Idempotency
-----------
The WHERE clause limits updates to rows still at 0, so re-running is safe.

Usage
-----
    cd tracker/
    python ../scripts/migrate_enable_existing_spaces.py

Run once after deploying the DEFAULT 0 schema change, then archive this script.
"""
import os
import sys
import logging
import sqlite3

# Allow importing from tracker/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tracker"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "tracker", ".env"))

DB_PATH = os.environ.get("DB_PATH", "./data/tracking.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def run():
    log.info("Migration: connecting to %s", DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    pre_count   = con.execute("SELECT COUNT(*) FROM spaces").fetchone()[0]
    pre_enabled = con.execute("SELECT COUNT(*) FROM spaces WHERE sla_enabled = 1").fetchone()[0]
    log.info("[migration] spaces before: total=%d, enabled=%d", pre_count, pre_enabled)

    result = con.execute(
        """UPDATE spaces
           SET sla_enabled = 1
           WHERE first_seen_at < strftime('%Y-%m-%dT%H:%M:%S', 'now')
             AND sla_enabled = 0"""
    )
    con.commit()

    post_enabled = con.execute("SELECT COUNT(*) FROM spaces WHERE sla_enabled = 1").fetchone()[0]
    log.info(
        "[migration] spaces after: enabled=%d (migrated %d rows)",
        post_enabled, result.rowcount,
    )

    con.close()
    log.info("Migration complete.")


if __name__ == "__main__":
    run()
