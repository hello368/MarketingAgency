"""
Database layer — SQLite
All persistent state lives here so Cloud Run restarts don't lose data.
"""
import os
import time
import sqlite3
import logging
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo
from config import TEAM_MEMBERS, TIMEZONE

log = logging.getLogger(__name__)
DB_PATH = os.environ.get("DB_PATH", "./data/tracking.db")
TZ = ZoneInfo(TIMEZONE)


def get_conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = get_conn()

    con.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    UNIQUE NOT NULL,
            group_name          TEXT,
            google_chat_id      TEXT,
            google_display_name TEXT,
            telegram_chat_id    INTEGER,
            telegram_registered INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS checkins (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT NOT NULL,
            member_name  TEXT NOT NULL,
            checkin_time TEXT,
            goal_1       TEXT,
            goal_2       TEXT,
            goal_3       TEXT,
            raw_text     TEXT,
            status       TEXT DEFAULT 'on-time',
            UNIQUE(date, member_name)
        );

        CREATE TABLE IF NOT EXISTS eod_submissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            member_name TEXT NOT NULL,
            submit_time TEXT,
            link_1      TEXT,
            link_2      TEXT,
            link_3      TEXT,
            raw_text    TEXT,
            status      TEXT DEFAULT 'submitted',
            UNIQUE(date, member_name)
        );

        CREATE TABLE IF NOT EXISTS sla_timers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT,
            space_display    TEXT,
            space_name       TEXT,
            thread_key       TEXT,
            tagger_name      TEXT,
            tagged_name      TEXT,
            tagged_google_id TEXT,
            deadline         TEXT,
            resolved         INTEGER DEFAULT 0,
            telegram_pinged  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS ping_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pinged_at   TEXT,
            member_name TEXT,
            reason      TEXT,
            space_name  TEXT,
            thread_key  TEXT
        );

        CREATE TABLE IF NOT EXISTS bot_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS task_nag_timers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT    NOT NULL,
            thread_id        TEXT    NOT NULL,
            space_name       TEXT    NOT NULL,
            assignee         TEXT    NOT NULL,
            google_chat_id   TEXT    DEFAULT '',
            nag_level        INTEGER DEFAULT 0,
            acknowledged     INTEGER DEFAULT 0,
            deadline_l1      TEXT    NOT NULL,
            deadline_l2      TEXT    NOT NULL,
            deadline_l3      TEXT    NOT NULL,
            client           TEXT    DEFAULT '',
            city             TEXT    DEFAULT '',
            task_description TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS wiki_pending (
            id         TEXT    PRIMARY KEY,
            payload    TEXT    NOT NULL,
            created_at INTEGER NOT NULL,
            user_id    TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS wiki_awaiting_edit (
            thread_id  TEXT    PRIMARY KEY,
            pending_id TEXT    NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spaces (
            space_name    TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL,
            space_type    TEXT DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_active   TEXT NOT NULL,
            sla_enabled   INTEGER DEFAULT 1
        );
    """)

    # Migrate existing task_nag_timers table — add new columns if absent
    for col in (
        "client TEXT DEFAULT ''",
        "city TEXT DEFAULT ''",
        "task_description TEXT DEFAULT ''",
        "google_chat_id TEXT DEFAULT ''",
        "focus_mode INTEGER DEFAULT 0",
        "focus_deadline TEXT DEFAULT ''",
        "focus_check_sent INTEGER DEFAULT 0",
        "focus_no_reply_deadline TEXT DEFAULT ''",
    ):
        col_name = col.split()[0]
        try:
            con.execute(f"ALTER TABLE task_nag_timers ADD COLUMN {col}")
            log.info("Migration: added column %s to task_nag_timers", col_name)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Pre-populate team members (INSERT OR IGNORE = safe to re-run)
    for name, info in TEAM_MEMBERS.items():
        con.execute(
            "INSERT OR IGNORE INTO members (name, group_name) VALUES (?, ?)",
            (name, info["group"]),
        )

    con.commit()
    con.close()
    log.info("Database initialized at %s", DB_PATH)


# ─────────────────────────────────────────
# Bot State (persistent key-value store)
# Used to remember the check-in/EOD thread keys across restarts.
# ─────────────────────────────────────────
def set_state(key: str, value: str):
    con = get_conn()
    con.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))
    con.commit()
    con.close()


def get_state(key: str) -> str | None:
    con = get_conn()
    row = con.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
    con.close()
    return row["value"] if row else None


# ─────────────────────────────────────────
# Members
# ─────────────────────────────────────────
def get_member_by_google_id(google_id: str) -> sqlite3.Row | None:
    con = get_conn()
    row = con.execute("SELECT * FROM members WHERE google_chat_id=?", (google_id,)).fetchone()
    con.close()
    return row


def get_member_by_name(name: str) -> sqlite3.Row | None:
    """Case-insensitive partial match on stored name."""
    con = get_conn()
    rows = con.execute("SELECT * FROM members").fetchall()
    con.close()
    name_lower = name.lower()
    for row in rows:
        if row["name"].lower() in name_lower or name_lower in row["name"].lower():
            return row
    return None


def get_member_by_telegram_id(telegram_id: int) -> sqlite3.Row | None:
    con = get_conn()
    row = con.execute(
        "SELECT * FROM members WHERE telegram_chat_id=?", (telegram_id,)
    ).fetchone()
    con.close()
    return row


def upsert_google_chat_id(name: str, google_id: str, display_name: str):
    con = get_conn()
    con.execute(
        """UPDATE members
           SET google_chat_id=?, google_display_name=?
           WHERE name=? AND (google_chat_id IS NULL OR google_chat_id='')""",
        (google_id, display_name, name),
    )
    con.commit()
    con.close()


def register_telegram(name: str, telegram_chat_id: int) -> bool:
    """Returns True if the name matched a known team member."""
    member = get_member_by_name(name)
    if not member:
        return False
    con = get_conn()
    con.execute(
        "UPDATE members SET telegram_chat_id=?, telegram_registered=1 WHERE name=?",
        (telegram_chat_id, member["name"]),
    )
    con.commit()
    con.close()
    return True


def all_members_today_checkin(date: str) -> list[str]:
    """Returns names of members who have NOT checked in today."""
    con = get_conn()
    checked = {
        row[0]
        for row in con.execute(
            "SELECT member_name FROM checkins WHERE date=?", (date,)
        ).fetchall()
    }
    all_names = {row[0] for row in con.execute("SELECT name FROM members").fetchall()}
    con.close()
    return sorted(all_names - checked)


# ─────────────────────────────────────────
# Check-ins
# ─────────────────────────────────────────
def log_checkin(date: str, name: str, time_str: str, goals: list[str], raw: str, status: str):
    con = get_conn()
    con.execute(
        """INSERT OR IGNORE INTO checkins
           (date, member_name, checkin_time, goal_1, goal_2, goal_3, raw_text, status)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            date, name, time_str,
            goals[0] if len(goals) > 0 else "",
            goals[1] if len(goals) > 1 else "",
            goals[2] if len(goals) > 2 else "",
            raw, status,
        ),
    )
    con.commit()
    con.close()


def log_late_missing(date: str, name: str):
    con = get_conn()
    con.execute(
        """INSERT OR IGNORE INTO checkins
           (date, member_name, checkin_time, status)
           VALUES (?, ?, NULL, 'missing')""",
        (date, name),
    )
    con.commit()
    con.close()


# ─────────────────────────────────────────
# EOD Submissions
# ─────────────────────────────────────────
def log_eod(date: str, name: str, time_str: str, links: list[str], raw: str):
    con = get_conn()
    con.execute(
        """INSERT OR IGNORE INTO eod_submissions
           (date, member_name, submit_time, link_1, link_2, link_3, raw_text, status)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            date, name, time_str,
            links[0] if len(links) > 0 else "",
            links[1] if len(links) > 1 else "",
            links[2] if len(links) > 2 else "",
            raw, "submitted",
        ),
    )
    con.commit()
    con.close()


# ─────────────────────────────────────────
# SLA Timers
# ─────────────────────────────────────────
def create_sla_timer(
    space_display: str, space_name: str, thread_key: str,
    tagger_name: str, tagged_name: str, tagged_google_id: str, deadline: datetime
) -> int:
    con = get_conn()
    cur = con.execute(
        """INSERT INTO sla_timers
           (created_at, space_display, space_name, thread_key,
            tagger_name, tagged_name, tagged_google_id, deadline)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            datetime.now(TZ).isoformat(),
            space_display, space_name, thread_key,
            tagger_name, tagged_name, tagged_google_id,
            deadline.isoformat(),
        ),
    )
    timer_id = cur.lastrowid
    con.commit()
    con.close()
    return timer_id


def resolve_sla_timer(thread_key: str, responder_google_id: str) -> list[sqlite3.Row]:
    """Mark timers resolved when the tagged person replies in the same thread."""
    con = get_conn()
    timers = con.execute(
        """SELECT * FROM sla_timers
           WHERE thread_key=? AND tagged_google_id=? AND resolved=0""",
        (thread_key, responder_google_id),
    ).fetchall()
    for t in timers:
        con.execute("UPDATE sla_timers SET resolved=1 WHERE id=?", (t["id"],))
    con.commit()
    con.close()
    return timers


def get_expired_sla_timers() -> list[sqlite3.Row]:
    """Return unresolved timers past their deadline, not yet pinged."""
    now_iso = datetime.now(TZ).isoformat()
    con = get_conn()
    rows = con.execute(
        """SELECT * FROM sla_timers
           WHERE resolved=0 AND telegram_pinged=0 AND deadline <= ?""",
        (now_iso,),
    ).fetchall()
    con.close()
    return rows


def mark_sla_pinged(timer_id: int):
    con = get_conn()
    con.execute("UPDATE sla_timers SET telegram_pinged=1 WHERE id=?", (timer_id,))
    con.commit()
    con.close()


def log_ping(member_name: str, reason: str, space_name: str, thread_key: str):
    con = get_conn()
    con.execute(
        "INSERT INTO ping_log (pinged_at, member_name, reason, space_name, thread_key) VALUES (?,?,?,?,?)",
        (datetime.now(TZ).isoformat(), member_name, reason, space_name, thread_key),
    )
    con.commit()
    con.close()


# ─────────────────────────────────────────
# Task Nag Timers (Phase 5)
# ─────────────────────────────────────────

def create_task_nag_timer(
    thread_id: str, space_name: str, assignee: str,
    deadline_l1: str, deadline_l2: str, deadline_l3: str,
    client: str = "", city: str = "", task_description: str = "",
    google_chat_id: str = "",
) -> int:
    con = get_conn()
    cur = con.execute(
        """INSERT INTO task_nag_timers
           (created_at, thread_id, space_name, assignee, google_chat_id,
            deadline_l1, deadline_l2, deadline_l3,
            client, city, task_description)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now(TZ).isoformat(), thread_id, space_name, assignee, google_chat_id,
         deadline_l1, deadline_l2, deadline_l3,
         client, city, task_description),
    )
    timer_id = cur.lastrowid
    con.commit()
    con.close()
    return timer_id


def get_expired_nag_timers(now_iso: str) -> list[sqlite3.Row]:
    """
    Return timers that need their next escalation level fired.
    Each row includes a 'nag_level' field = the level that should be fired now
    (1, 2, or 3 — the one whose deadline has passed but hasn't been sent yet).
    Timers in focus_mode are excluded — they use a separate check path.
    """
    con = get_conn()
    rows = con.execute(
        """SELECT *,
               CASE
                 WHEN nag_level < 1 AND deadline_l1 <= :now THEN 1
                 WHEN nag_level < 2 AND deadline_l2 <= :now THEN 2
                 WHEN nag_level < 3 AND deadline_l3 <= :now THEN 3
                 ELSE 0
               END AS next_level
           FROM task_nag_timers
           WHERE acknowledged = 0
             AND focus_mode = 0
             AND nag_level < 3
             AND deadline_l1 <= :now""",
        {"now": now_iso},
    ).fetchall()
    con.close()
    # Only return rows that actually have a level to fire
    result = []
    for row in rows:
        d = dict(row)
        level = d.pop("next_level")
        if level > 0 and level > d["nag_level"]:
            d["nag_level"] = level
            result.append(d)
    return result


def enter_focus_mode(thread_id: str, assignee: str, focus_deadline_iso: str) -> int:
    """Set focus_mode=1 on active timers for this assignee in the thread. Returns row count."""
    con = get_conn()
    cur = con.execute(
        """UPDATE task_nag_timers
           SET focus_mode=1, focus_deadline=?, focus_check_sent=0, focus_no_reply_deadline=''
           WHERE thread_id=? AND lower(assignee)=lower(?) AND acknowledged=0 AND focus_mode=0""",
        (focus_deadline_iso, thread_id, assignee),
    )
    count = cur.rowcount
    con.commit()
    con.close()
    return count


def get_timers_needing_focus_check(now_iso: str) -> list[dict]:
    """Return focus timers whose 45-min deadline has passed but check message not yet sent."""
    con = get_conn()
    rows = con.execute(
        """SELECT * FROM task_nag_timers
           WHERE focus_mode=1 AND focus_check_sent=0 AND acknowledged=0
             AND focus_deadline != '' AND focus_deadline <= ?""",
        (now_iso,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def mark_focus_check_sent(timer_id: int, no_reply_deadline_iso: str) -> None:
    """Record that the 45-min check message was sent and set the 10-min no-reply deadline."""
    con = get_conn()
    con.execute(
        "UPDATE task_nag_timers SET focus_check_sent=1, focus_no_reply_deadline=? WHERE id=?",
        (no_reply_deadline_iso, timer_id),
    )
    con.commit()
    con.close()


def snooze_focus_timer(thread_id: str, assignee: str, new_focus_deadline_iso: str) -> int:
    """Reset the 45-min focus timer (user replied 'still working'). Returns row count."""
    con = get_conn()
    cur = con.execute(
        """UPDATE task_nag_timers
           SET focus_deadline=?, focus_check_sent=0, focus_no_reply_deadline=''
           WHERE thread_id=? AND lower(assignee)=lower(?) AND focus_mode=1 AND acknowledged=0""",
        (new_focus_deadline_iso, thread_id, assignee),
    )
    count = cur.rowcount
    con.commit()
    con.close()
    return count


def get_timers_focus_no_reply(now_iso: str) -> list[dict]:
    """Return focus timers where the 10-min no-reply window has expired."""
    con = get_conn()
    rows = con.execute(
        """SELECT * FROM task_nag_timers
           WHERE focus_mode=1 AND focus_check_sent=1 AND acknowledged=0
             AND focus_no_reply_deadline != '' AND focus_no_reply_deadline <= ?""",
        (now_iso,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def exit_focus_resume_nag(timer_id: int, now_iso: str) -> None:
    """Exit focus mode and force-escalate to L3 on the next nag check tick."""
    con = get_conn()
    con.execute(
        """UPDATE task_nag_timers
           SET focus_mode=0, focus_check_sent=0, focus_no_reply_deadline='',
               nag_level=CASE WHEN nag_level < 2 THEN 2 ELSE nag_level END,
               deadline_l3=?
           WHERE id=?""",
        (now_iso, timer_id),
    )
    con.commit()
    con.close()


def mark_nag_level_sent(timer_id: int, level: int) -> None:
    con = get_conn()
    con.execute(
        "UPDATE task_nag_timers SET nag_level=? WHERE id=?",
        (level, timer_id),
    )
    con.commit()
    con.close()


def acknowledge_task_nag_timers(thread_id: str, assignee_name: str) -> int:
    """Mark nag timers as acknowledged when the assignee responds in the thread."""
    con = get_conn()
    cur = con.execute(
        """UPDATE task_nag_timers SET acknowledged=1
           WHERE thread_id=? AND acknowledged=0
             AND lower(assignee)=lower(?)""",
        (thread_id, assignee_name),
    )
    count = cur.rowcount
    con.commit()
    con.close()
    return count


def close_task_nag_timers(thread_id: str) -> None:
    """Acknowledge all nag timers for a thread (task completed)."""
    con = get_conn()
    con.execute(
        "UPDATE task_nag_timers SET acknowledged=1 WHERE thread_id=?",
        (thread_id,),
    )
    con.commit()
    con.close()


def close_task_nag_timer_for_assignee(thread_id: str, assignee_name: str) -> int:
    """Acknowledge nag timers for one assignee only (partial task completion)."""
    con = get_conn()
    cur = con.execute(
        "UPDATE task_nag_timers SET acknowledged=1 WHERE thread_id=? AND lower(assignee)=lower(?)",
        (thread_id, assignee_name),
    )
    count = cur.rowcount
    con.commit()
    con.close()
    return count


# ─────────────────────────────────────────
# Spaces — multi-space SLA tracking
# ─────────────────────────────────────────

def upsert_space(space_name: str, display_name: str, space_type: str = "") -> None:
    """Register a new space. INSERT OR IGNORE — existing spaces are a no-op (no write)."""
    now = datetime.now(TZ).isoformat()
    con = get_conn()
    con.execute(
        """INSERT OR IGNORE INTO spaces
           (space_name, display_name, space_type, first_seen_at, last_active)
           VALUES (?, ?, ?, ?, ?)""",
        (space_name, display_name, space_type, now, now),
    )
    con.commit()
    con.close()


def get_all_spaces() -> list[sqlite3.Row]:
    """Return all registered spaces."""
    con = get_conn()
    rows = con.execute("SELECT * FROM spaces ORDER BY last_active DESC").fetchall()
    con.close()
    return rows


# ── touch_space in-memory write-back cache ──────────────────────────────────
# Batches last_active updates so every webhook message doesn't hit SQLite.
# flush_touch_cache() drains to DB; called by _flush_loop every 60s + at shutdown.
_touch_cache: dict[str, float] = {}
_touch_lock = Lock()


def touch_space(space_name: str) -> None:
    """Record last_active in memory. Flushed to SQLite by flush_touch_cache()."""
    with _touch_lock:
        _touch_cache[space_name] = time.time()


def flush_touch_cache() -> int:
    """Batch-write cached last_active timestamps to SQLite. Returns row count."""
    with _touch_lock:
        snapshot = dict(_touch_cache)
        _touch_cache.clear()
    if not snapshot:
        return 0
    con = get_conn()
    con.executemany(
        "UPDATE spaces SET last_active=? WHERE space_name=?",
        [
            (datetime.fromtimestamp(ts, tz=TZ).isoformat(), name)
            for name, ts in snapshot.items()
        ],
    )
    con.commit()
    con.close()
    return len(snapshot)
