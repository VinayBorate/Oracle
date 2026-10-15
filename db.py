"""
SQLite database for deduplication and match history.
"""

import sqlite3
from datetime import datetime, timedelta
from loguru import logger

DB_PATH = "jobs.db"


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_id TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            url TEXT,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS matched_jobs (
            job_id TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            location TEXT,
            remote_type TEXT,
            url TEXT,
            match_score INTEGER,
            match_reason TEXT,
            posted_on TEXT,
            notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    return conn


def is_job_seen(conn: sqlite3.Connection, job_id: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen_jobs WHERE job_id = ?", (job_id,))
    return cur.fetchone() is not None


def mark_job_seen(conn: sqlite3.Connection, job: dict):
    try:
        conn.execute(
            "INSERT OR REPLACE INTO seen_jobs (job_id, company, title, url) VALUES (?, ?, ?, ?)",
            (job["id"], job["company"], job["title"], job["url"])
        )
        conn.commit()
    except Exception as e:
        logger.error(f"DB insert seen failed for {job['id']}: {e}")


def log_matched_job(conn: sqlite3.Connection, job: dict, result: dict):
    try:
        conn.execute(
            """INSERT OR REPLACE INTO matched_jobs
               (job_id, company, title, location, remote_type, url, match_score, match_reason, posted_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job["id"],
                job["company"],
                job["title"],
                job["location"],
                job.get("remote_type", ""),
                job["url"],
                result["score"],
                result["reason"],
                job["posted_on"]
            )
        )
        conn.commit()
    except Exception as e:
        logger.error(f"DB insert matched failed for {job['id']}: {e}")


def get_recent_matches(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    cutoff = datetime.now() - timedelta(days=days)
    cur = conn.execute(
        "SELECT * FROM matched_jobs WHERE notified_at >= ? ORDER BY notified_at DESC",
        (cutoff.isoformat(),)
    )
    return [dict(row) for row in cur.fetchall()]