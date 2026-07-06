"""
database.py
------------
Core database layer for JEE-AI-PRO.

Uses SQLite by default (zero-config, file-based — good for local dev and
small deployments) with a thin abstraction so swapping to Postgres later
only means changing `_get_connection()` and the placeholder style.

Provides:
    - Schema creation / migration on startup (`init_db`)
    - Student CRUD
    - Doubt history (used by ai/ai_doubt_solver.py)
    - Mock test results
    - Activity log (used by pages/dashboard.py via models/progress.py)
    - Leaderboard queries
    - Generic helpers (execute, fetch_one, fetch_all) for other modules

Every public function is defensive: on failure it logs the error and
raises `DatabaseError` with a clear message, rather than leaking raw
sqlite3 exceptions up into Streamlit pages.
"""

from __future__ import annotations

import os
import json
import sqlite3
import logging
import datetime
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Iterator

logger = logging.getLogger("database")
logging.basicConfig(level=logging.INFO)

try:
    from config import DATABASE_PATH  # project config, e.g. "data/jee_ai_pro.db"
except ImportError:
    DATABASE_PATH = os.getenv("JEE_AI_PRO_DB_PATH", "jee_ai_pro.db")

_LOCK = threading.Lock()  # SQLite + Streamlit's multi-thread runtime needs this


class DatabaseError(Exception):
    """Raised for any database operation failure."""


# --------------------------------------------------------------------------
# Connection management
# --------------------------------------------------------------------------
@contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logger.error("Database error: %s", e)
        raise DatabaseError(str(e)) from e
    finally:
        if conn:
            conn.close()


def execute(query: str, params: tuple = ()) -> int:
    """Run an INSERT/UPDATE/DELETE. Returns lastrowid (for INSERT)."""
    with _LOCK, _get_connection() as conn:
        cur = conn.execute(query, params)
        return cur.lastrowid


def fetch_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    with _LOCK, _get_connection() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def fetch_all(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with _LOCK, _get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    target_exam     TEXT DEFAULT 'JEE Main',
    class_level     TEXT DEFAULT '12th',
    created_at      TEXT DEFAULT (datetime('now')),
    streak_days     INTEGER DEFAULT 0,
    last_active     TEXT
);

CREATE TABLE IF NOT EXISTS parents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    student_id      TEXT REFERENCES students(id) ON DELETE CASCADE,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS doubts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      TEXT REFERENCES students(id) ON DELETE CASCADE,
    subject         TEXT,
    topic           TEXT,
    difficulty      TEXT,
    question        TEXT,
    solution_json   TEXT,
    source          TEXT DEFAULT 'text',
    solved_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mock_tests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      TEXT REFERENCES students(id) ON DELETE CASCADE,
    test_name       TEXT,
    subject         TEXT,
    total_marks     INTEGER,
    scored_marks    INTEGER,
    total_questions INTEGER,
    correct_count   INTEGER,
    wrong_count     INTEGER,
    time_taken_sec  INTEGER,
    taken_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS progress (
    student_id      TEXT REFERENCES students(id) ON DELETE CASCADE,
    subject         TEXT,
    topic           TEXT,
    completion_pct  REAL DEFAULT 0,
    accuracy_pct    REAL DEFAULT 0,
    last_updated    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (student_id, subject, topic)
);

CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      TEXT REFERENCES students(id) ON DELETE CASCADE,
    activity_type   TEXT,   -- 'mock_test' | 'doubt_solved' | 'notes_read' | 'revision'
    detail          TEXT,
    metadata_json   TEXT,
    occurred_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS study_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      TEXT REFERENCES students(id) ON DELETE CASCADE,
    plan_date       TEXT,
    plan_json       TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leaderboard (
    student_id      TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    total_points    INTEGER DEFAULT 0,
    tests_taken     INTEGER DEFAULT 0,
    doubts_solved   INTEGER DEFAULT 0,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_doubts_student ON doubts(student_id);
CREATE INDEX IF NOT EXISTS idx_mock_tests_student ON mock_tests(student_id);
CREATE INDEX IF NOT EXISTS idx_activity_student ON activity_log(student_id);
CREATE INDEX IF NOT EXISTS idx_progress_student ON progress(student_id);
"""


def init_db() -> None:
    """Create all tables/indexes if they don't already exist. Call once at app startup."""
    with _LOCK, _get_connection() as conn:
        conn.executescript(_SCHEMA)
    logger.info("Database initialized at %s", DATABASE_PATH)


# --------------------------------------------------------------------------
# Students
# --------------------------------------------------------------------------
def create_student(student_id: str, name: str, email: str, password_hash: str,
                    target_exam: str = "JEE Main", class_level: str = "12th") -> None:
    execute(
        """INSERT INTO students (id, name, email, password_hash, target_exam, class_level)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (student_id, name, email, password_hash, target_exam, class_level),
    )
    execute("INSERT INTO leaderboard (student_id) VALUES (?)", (student_id,))


def get_student_by_id(student_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM students WHERE id = ?", (student_id,))


def get_student_by_email(email: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM students WHERE email = ?", (email,))


def update_student_streak(student_id: str, streak_days: int) -> None:
    execute(
        "UPDATE students SET streak_days = ?, last_active = datetime('now') WHERE id = ?",
        (streak_days, student_id),
    )


# --------------------------------------------------------------------------
# Doubts (used by ai/ai_doubt_solver.py)
# --------------------------------------------------------------------------
def save_doubt_record(student_id: str, solution: Dict[str, Any]) -> int:
    """
    Persist a solved doubt. `solution` is the dict produced by
    DoubtSolution.to_dict() in ai/ai_doubt_solver.py.
    """
    doubt_id = execute(
        """INSERT INTO doubts (student_id, subject, topic, difficulty, question,
                                solution_json, source, solved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            student_id,
            solution.get("subject"),
            solution.get("topic"),
            solution.get("difficulty"),
            solution.get("raw_question", solution.get("restated_question", "")),
            json.dumps(solution),
            solution.get("source", "text"),
            solution.get("solved_at", datetime.datetime.utcnow().isoformat()),
        ),
    )
    log_activity(student_id, "doubt_solved", solution.get("topic", "Doubt solved"))
    execute(
        "UPDATE leaderboard SET doubts_solved = doubts_solved + 1, updated_at = datetime('now') WHERE student_id = ?",
        (student_id,),
    )
    return doubt_id


def get_student_history(student_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    rows = fetch_all(
        "SELECT * FROM doubts WHERE student_id = ? ORDER BY solved_at DESC LIMIT ?",
        (student_id, limit),
    )
    for row in rows:
        if row.get("solution_json"):
            row["solution"] = json.loads(row["solution_json"])
    return rows


# --------------------------------------------------------------------------
# Mock tests
# --------------------------------------------------------------------------
def save_mock_test_result(student_id: str, test_name: str, subject: str, total_marks: int,
                           scored_marks: int, total_questions: int, correct_count: int,
                           wrong_count: int, time_taken_sec: int) -> int:
    test_id = execute(
        """INSERT INTO mock_tests (student_id, test_name, subject, total_marks, scored_marks,
                                    total_questions, correct_count, wrong_count, time_taken_sec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (student_id, test_name, subject, total_marks, scored_marks,
         total_questions, correct_count, wrong_count, time_taken_sec),
    )
    log_activity(student_id, "mock_test", f"{test_name} — {scored_marks}/{total_marks}")
    execute(
        """UPDATE leaderboard SET tests_taken = tests_taken + 1,
           total_points = total_points + ?, updated_at = datetime('now') WHERE student_id = ?""",
        (scored_marks, student_id),
    )
    return test_id


def get_mock_test_history(student_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM mock_tests WHERE student_id = ? ORDER BY taken_at DESC LIMIT ?",
        (student_id, limit),
    )


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
def upsert_progress(student_id: str, subject: str, topic: str,
                     completion_pct: float, accuracy_pct: float) -> None:
    execute(
        """INSERT INTO progress (student_id, subject, topic, completion_pct, accuracy_pct, last_updated)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(student_id, subject, topic)
           DO UPDATE SET completion_pct = excluded.completion_pct,
                         accuracy_pct = excluded.accuracy_pct,
                         last_updated = datetime('now')""",
        (student_id, subject, topic, completion_pct, accuracy_pct),
    )


def get_subject_progress(student_id: str) -> Dict[str, float]:
    """Average completion % per subject — used by models/progress.py + dashboard."""
    rows = fetch_all(
        """SELECT subject, AVG(completion_pct) as avg_pct
           FROM progress WHERE student_id = ? GROUP BY subject""",
        (student_id,),
    )
    return {row["subject"]: row["avg_pct"] for row in rows}


def get_weak_topics(student_id: str, threshold: float = 60.0, limit: int = 5) -> List[Dict[str, Any]]:
    return fetch_all(
        """SELECT subject, topic, accuracy_pct as accuracy FROM progress
           WHERE student_id = ? AND accuracy_pct < ?
           ORDER BY accuracy_pct ASC LIMIT ?""",
        (student_id, threshold, limit),
    )


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------
def log_activity(student_id: str, activity_type: str, detail: str,
                  metadata: Optional[Dict[str, Any]] = None) -> None:
    execute(
        """INSERT INTO activity_log (student_id, activity_type, detail, metadata_json)
           VALUES (?, ?, ?, ?)""",
        (student_id, activity_type, detail, json.dumps(metadata or {})),
    )


def get_recent_activity(student_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM activity_log WHERE student_id = ? ORDER BY occurred_at DESC LIMIT ?",
        (student_id, limit),
    )


# --------------------------------------------------------------------------
# Study plans
# --------------------------------------------------------------------------
def save_study_plan(student_id: str, plan_date: str, plan: List[Dict[str, str]]) -> None:
    execute(
        "INSERT INTO study_plans (student_id, plan_date, plan_json) VALUES (?, ?, ?)",
        (student_id, plan_date, json.dumps(plan)),
    )


def get_study_plan(student_id: str, plan_date: str) -> Optional[List[Dict[str, str]]]:
    row = fetch_one(
        """SELECT plan_json FROM study_plans WHERE student_id = ? AND plan_date = ?
           ORDER BY created_at DESC LIMIT 1""",
        (student_id, plan_date),
    )
    return json.loads(row["plan_json"]) if row else None


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------
def get_leaderboard(limit: int = 50) -> List[Dict[str, Any]]:
    return fetch_all(
        """SELECT s.name, l.total_points, l.tests_taken, l.doubts_solved
           FROM leaderboard l JOIN students s ON s.id = l.student_id
           ORDER BY l.total_points DESC LIMIT ?""",
        (limit,),
    )


# --------------------------------------------------------------------------
# Manual init (dev convenience)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    print(f"Database ready at: {os.path.abspath(DATABASE_PATH)}")
