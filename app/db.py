"""费曼学习工作台 — 数据库层（sqlite3 标准库，风格对齐个人工作台）"""
import sqlite3
from contextlib import contextmanager

from app.config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_path TEXT NOT NULL,            -- wiki pages/ 相对路径，如 AI/rag/query-rewriting.md
    page_title TEXT NOT NULL,
    concept TEXT NOT NULL,              -- 本次学习的概念名（默认=页面 title）
    status TEXT NOT NULL DEFAULT 'teaching',  -- teaching | gaps | simplifying | done
    tutor_turns INTEGER NOT NULL DEFAULT 3,   -- AI 追问轮数上限
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                 -- user | tutor
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_path TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    gap_type TEXT NOT NULL,             -- missing | wrong | vague
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- open | revised | verified
    revision TEXT,                      -- 用户的简化修订稿
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    interval INTEGER NOT NULL DEFAULT 0,  -- 当前间隔（天），0=未复习过
    ease REAL NOT NULL DEFAULT 2.5,       -- 难度系数（SM-2）
    due TEXT NOT NULL,                    -- 下次到期日 YYYY-MM-DD
    reps INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    rating TEXT NOT NULL,               -- again | hard | good | easy
    reviewed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_notes_page_path ON notes(page_path);
CREATE INDEX IF NOT EXISTS idx_gaps_session ON gaps(session_id);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due);
CREATE INDEX IF NOT EXISTS idx_cards_session ON cards(session_id);
"""


def get_conn() -> sqlite3.Connection:
    """返回裸连接（sqlite3 的 with 只提交不关闭，用完需手动 close）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor():
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
