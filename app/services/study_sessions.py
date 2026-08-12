"""费曼学习会话服务：持久化讲解、笔记、盲区与复习卡。

没有配置 LLM 密钥时，服务仍返回可执行的费曼检查结果；这样本地学习闭环不会退化成占位界面。
"""
from __future__ import annotations

from datetime import date, timedelta

from app import db
from app.services import tutor, wiki_reader


MIN_EXPLANATION_LENGTH = 24


def _validate_page(path: str) -> dict:
    meta, _ = wiki_reader.render_page_html(path)
    return meta


def _cards_for(title: str, explanation: str, gaps: list[dict]) -> list[dict]:
    cards = [{
        "question": f"用自己的话说明：{title} 是什么？",
        "answer": explanation.strip()[:500],
    }]
    for gap in gaps[:2]:
        cards.append({
            "question": f"针对「{title}」，补全这项检查：{gap['content']}",
            "answer": "下次复习时，用自己的例子或机制说明来补全。",
        })
    return cards


def create_session(page_path: str, explanation: str) -> dict:
    """创建一轮讲解会话，并保存本地诊断、追问和初始复习卡。"""
    if not explanation or len(explanation.strip()) < MIN_EXPLANATION_LENGTH:
        raise ValueError(f"请至少写 {MIN_EXPLANATION_LENGTH} 个字符，再开始诊断。")
    meta, reference_html = wiki_reader.render_page_html(page_path)
    title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    gaps, question, diagnosis_source = tutor.diagnose(explanation, title, reference_html)
    today = date.today()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (page_path, page_title, concept, status) VALUES (?, ?, ?, 'gaps')",
            (page_path, title, title),
        )
        session_id = cur.lastrowid
        cur.execute("INSERT INTO turns (session_id, role, content) VALUES (?, 'user', ?)", (session_id, explanation.strip()))
        cur.execute("INSERT INTO turns (session_id, role, content) VALUES (?, 'tutor', ?)", (session_id, question))
        for gap in gaps:
            cur.execute(
                "INSERT INTO gaps (session_id, gap_type, content) VALUES (?, ?, ?)",
                (session_id, gap["gap_type"], gap["content"]),
            )
        for card in _cards_for(title, explanation, gaps):
            cur.execute(
                "INSERT INTO cards (session_id, question, answer, due) VALUES (?, ?, ?, ?)",
                (session_id, card["question"], card["answer"], today.isoformat()),
            )
    detail = session_detail(session_id)
    detail["diagnosis_source"] = diagnosis_source
    return detail


def session_detail(session_id: int) -> dict:
    with db.cursor() as cur:
        session = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("学习会话不存在")
        turns = cur.execute("SELECT id, role, content, created_at FROM turns WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        gaps = cur.execute("SELECT id, gap_type, content, status, revision FROM gaps WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        cards = cur.execute("SELECT id, question, answer, due, interval, reps FROM cards WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    return {"session": dict(session), "turns": db.rows_to_dicts(turns), "gaps": db.rows_to_dicts(gaps), "cards": db.rows_to_dicts(cards)}


def list_due_cards(limit: int = 20) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT cards.*, sessions.page_title FROM cards JOIN sessions ON sessions.id = cards.session_id "
            "WHERE due <= ? ORDER BY due, cards.id LIMIT ?",
            (date.today().isoformat(), limit),
        ).fetchall()
    return db.rows_to_dicts(rows)


def review_card(card_id: int, rating: str) -> dict:
    if rating not in {"again", "hard", "good", "easy"}:
        raise ValueError("评分必须为 again、hard、good 或 easy")
    with db.cursor() as cur:
        card = cur.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise LookupError("复习卡不存在")
        intervals = {"again": 0, "hard": max(1, card["interval"] or 1), "good": max(1, (card["interval"] or 1) * 2), "easy": max(3, (card["interval"] or 1) * 3)}
        interval = intervals[rating]
        due = date.today() + timedelta(days=interval)
        cur.execute(
            "UPDATE cards SET interval = ?, reps = reps + 1, due = ? WHERE id = ?",
            (interval, due.isoformat(), card_id),
        )
        cur.execute("INSERT INTO reviews (card_id, rating) VALUES (?, ?)", (card_id, rating))
        updated = cur.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(updated)


def get_note(page_path: str) -> dict:
    _validate_page(page_path)
    with db.cursor() as cur:
        row = cur.execute("SELECT page_path, content, updated_at FROM notes WHERE page_path = ?", (page_path,)).fetchone()
    return dict(row) if row else {"page_path": page_path, "content": "", "updated_at": None}


def save_note(page_path: str, content: str) -> dict:
    _validate_page(page_path)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO notes (page_path, content) VALUES (?, ?) "
            "ON CONFLICT(page_path) DO UPDATE SET content = excluded.content, updated_at = datetime('now', 'localtime')",
            (page_path, content.strip()),
        )
    return get_note(page_path)
