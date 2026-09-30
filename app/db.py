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
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]', -- 学习者选择并在表达中体现的理解证据
    uncertainty TEXT NOT NULL DEFAULT '',     -- 学习者主动标记的最不确定点
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

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_path TEXT,
    page_title TEXT,
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'manual', -- manual | session | summary
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS knowledge_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_path TEXT NOT NULL,
    page_title TEXT NOT NULL,
    persona TEXT NOT NULL DEFAULT 'feynman',
    source_content TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    proposal TEXT NOT NULL,
    proposed_title TEXT NOT NULL DEFAULT '',
    target_mode TEXT,
    target_path TEXT,
    status TEXT NOT NULL DEFAULT 'draft', -- draft | applied | kept_local | undone
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS wiki_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_update_id INTEGER NOT NULL REFERENCES knowledge_updates(id) ON DELETE CASCADE,
    page_path TEXT NOT NULL,
    before_content TEXT NOT NULL,
    after_content TEXT NOT NULL,
    created_page INTEGER NOT NULL DEFAULT 0,
    change_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    undone_at TEXT
);

CREATE TABLE IF NOT EXISTS gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    gap_type TEXT NOT NULL,             -- concept_missing | causal_error | boundary_missing | transfer_failure
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- open | revised | verified
    revision TEXT,                      -- 用户的简化修订稿
    practice_completed_at TEXT,         -- 第一步两分钟微练习完成时间
    retest_due TEXT,                    -- 异情境复测日期 YYYY-MM-DD
    retest_prompt TEXT NOT NULL DEFAULT '',
    retest_answer TEXT,
    retest_completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS self_assessments (
    page_path TEXT PRIMARY KEY,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS idea_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    initial_content TEXT NOT NULL,
    persona TEXT NOT NULL DEFAULT 'feynman',
    related_page_path TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    assessment_json TEXT NOT NULL DEFAULT '{}',
    draft_title TEXT NOT NULL DEFAULT '',
    draft_content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open', -- open | draft | applied | kept_local | undone
    wiki_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS idea_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES idea_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                 -- user | agent
    kind TEXT NOT NULL DEFAULT 'discussion', -- assessment | discussion | summary
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS idea_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES idea_sessions(id) ON DELETE CASCADE,
    page_path TEXT NOT NULL,
    before_content TEXT NOT NULL,
    after_content TEXT NOT NULL,
    created_page INTEGER NOT NULL DEFAULT 1,
    change_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    undone_at TEXT
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    reference_status TEXT NOT NULL DEFAULT 'legacy', -- source | needs_review | legacy
    source_anchor TEXT NOT NULL DEFAULT '',
    required_points_json TEXT NOT NULL DEFAULT '[]',
    reference_excerpt TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS review_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,                -- feynman | strict
    answer TEXT NOT NULL,
    verdict TEXT NOT NULL,              -- pass | retry
    feedback TEXT NOT NULL,
    follow_up TEXT NOT NULL,
    source TEXT NOT NULL,               -- local | llm
    evidence_level TEXT NOT NULL DEFAULT 'unverified', -- source_standard | source_reviewed | unverified
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    page_path TEXT NOT NULL,
    entity_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS diagnosis_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    gap_id INTEGER REFERENCES gaps(id) ON DELETE SET NULL,
    verdict TEXT NOT NULL,              -- helpful | disputed
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_notes_page_path ON notes(page_path);
CREATE INDEX IF NOT EXISTS idx_reflections_created ON reflections(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_page_path ON reflections(page_path);
CREATE INDEX IF NOT EXISTS idx_reflections_session ON reflections(session_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_updates_page_path ON knowledge_updates(page_path);
CREATE INDEX IF NOT EXISTS idx_knowledge_updates_created ON knowledge_updates(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wiki_revisions_update ON wiki_revisions(knowledge_update_id);
CREATE INDEX IF NOT EXISTS idx_idea_sessions_updated ON idea_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_idea_turns_session ON idea_turns(idea_id, id);
CREATE INDEX IF NOT EXISTS idx_idea_revisions_session ON idea_revisions(idea_id);
CREATE INDEX IF NOT EXISTS idx_gaps_session ON gaps(session_id);
CREATE INDEX IF NOT EXISTS idx_self_assessments_updated ON self_assessments(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due);
CREATE INDEX IF NOT EXISTS idx_cards_session ON cards(session_id);
CREATE INDEX IF NOT EXISTS idx_review_attempts_card ON review_attempts(card_id);
CREATE INDEX IF NOT EXISTS idx_learning_events_created ON learning_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_diagnosis_feedback_session ON diagnosis_feedback(session_id);
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
        # SQLite CREATE TABLE cannot add a column to existing local installs.
        # Keep this small migration here so an upgrade never discards records.
        session_columns = {row["name"] for row in cur.execute("PRAGMA table_info(sessions)").fetchall()}
        session_migrations = {
            "duration_seconds": "INTEGER NOT NULL DEFAULT 0",
            "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "uncertainty": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in session_migrations.items():
            if name not in session_columns:
                cur.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")

        gap_columns = {row["name"] for row in cur.execute("PRAGMA table_info(gaps)").fetchall()}
        gap_migrations = {
            "practice_completed_at": "TEXT",
            "retest_due": "TEXT",
            "retest_prompt": "TEXT NOT NULL DEFAULT ''",
            "retest_answer": "TEXT",
            "retest_completed_at": "TEXT",
        }
        for name, definition in gap_migrations.items():
            if name not in gap_columns:
                cur.execute(f"ALTER TABLE gaps ADD COLUMN {name} {definition}")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gaps_retest_due ON gaps(retest_due)")

        card_columns = {row["name"] for row in cur.execute("PRAGMA table_info(cards)").fetchall()}
        card_migrations = {
            "reference_status": "TEXT NOT NULL DEFAULT 'legacy'",
            "source_anchor": "TEXT NOT NULL DEFAULT ''",
            "required_points_json": "TEXT NOT NULL DEFAULT '[]'",
            "reference_excerpt": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in card_migrations.items():
            if name not in card_columns:
                cur.execute(f"ALTER TABLE cards ADD COLUMN {name} {definition}")

        attempt_columns = {row["name"] for row in cur.execute("PRAGMA table_info(review_attempts)").fetchall()}
        if "evidence_level" not in attempt_columns:
            cur.execute("ALTER TABLE review_attempts ADD COLUMN evidence_level TEXT NOT NULL DEFAULT 'unverified'")

        for table in ("wiki_revisions", "idea_revisions"):
            columns = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            if "change_json" not in columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN change_json TEXT NOT NULL DEFAULT '{{}}'")


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
