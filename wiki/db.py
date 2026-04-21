"""Wiki-specific SQLite operations. Tables are initialised by tracker.db.init_db()."""
from __future__ import annotations

import json
import logging
import time

from tracker.db import get_conn

log = logging.getLogger(__name__)


def save_pending(pending_id: str, payload: dict, user_id: str = "") -> None:
    con = get_conn()
    con.execute(
        "INSERT OR REPLACE INTO wiki_pending (id, payload, created_at, user_id) VALUES (?,?,?,?)",
        (pending_id, json.dumps(payload), int(time.time()), user_id),
    )
    con.commit()
    con.close()


def get_pending(pending_id: str) -> dict | None:
    con = get_conn()
    row = con.execute(
        "SELECT payload FROM wiki_pending WHERE id=?", (pending_id,)
    ).fetchone()
    con.close()
    return json.loads(row["payload"]) if row else None


def delete_pending(pending_id: str) -> None:
    con = get_conn()
    con.execute("DELETE FROM wiki_pending WHERE id=?", (pending_id,))
    con.commit()
    con.close()


def set_awaiting_edit(thread_id: str, pending_id: str) -> None:
    con = get_conn()
    con.execute(
        "INSERT OR REPLACE INTO wiki_awaiting_edit (thread_id, pending_id, created_at) VALUES (?,?,?)",
        (thread_id, pending_id, int(time.time())),
    )
    con.commit()
    con.close()


def get_awaiting_edit(thread_id: str) -> str | None:
    con = get_conn()
    row = con.execute(
        "SELECT pending_id FROM wiki_awaiting_edit WHERE thread_id=?", (thread_id,)
    ).fetchone()
    con.close()
    return row["pending_id"] if row else None


def delete_awaiting_edit(thread_id: str) -> None:
    con = get_conn()
    con.execute("DELETE FROM wiki_awaiting_edit WHERE thread_id=?", (thread_id,))
    con.commit()
    con.close()


def cleanup_expired_pending(max_age_seconds: int = 86400) -> int:
    """Delete wiki_pending rows older than max_age_seconds. Returns count deleted."""
    cutoff = int(time.time()) - max_age_seconds
    con = get_conn()
    cur = con.execute("DELETE FROM wiki_pending WHERE created_at <= ?", (cutoff,))
    count = cur.rowcount
    # Also remove orphaned awaiting_edit rows whose pending entry is gone
    con.execute(
        "DELETE FROM wiki_awaiting_edit WHERE pending_id NOT IN (SELECT id FROM wiki_pending)"
    )
    con.commit()
    con.close()
    if count:
        log.info("[wiki.db] cleanup_expired_pending: removed %d rows", count)
    return count
