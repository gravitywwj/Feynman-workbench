"""费曼学习会话服务：持久化讲解、笔记、盲区与复习卡。

没有配置 LLM 密钥时，服务仍返回可执行的费曼检查结果；这样本地学习闭环不会退化成占位界面。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

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
        old_interval = card["interval"] or 0
        old_reps = card["reps"] or 0
        old_ease = card["ease"] or 2.5
        if rating == "again":
            interval, reps, ease = 1, 0, max(1.3, old_ease - 0.2)
        elif rating == "hard":
            interval = 1 if old_reps == 0 else max(2, round(max(1, old_interval) * 1.2))
            reps, ease = old_reps + 1, max(1.3, old_ease - 0.15)
        elif rating == "good":
            interval = 1 if old_reps == 0 else 3 if old_reps == 1 else max(4, round(old_interval * old_ease))
            reps, ease = old_reps + 1, old_ease
        else:
            interval = 3 if old_reps == 0 else 7 if old_reps == 1 else max(7, round(old_interval * old_ease * 1.3))
            reps, ease = old_reps + 1, min(3.0, old_ease + 0.15)
        due = date.today() + timedelta(days=interval)
        cur.execute(
            "UPDATE cards SET interval = ?, reps = ?, ease = ?, due = ? WHERE id = ?",
            (interval, reps, ease, due.isoformat(), card_id),
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


def list_history(limit: int = 20, page_path: str | None = None) -> list[dict]:
    sql = (
        "SELECT sessions.*, COUNT(gaps.id) AS gap_total, "
        "COALESCE(SUM(CASE WHEN gaps.status = 'open' THEN 1 ELSE 0 END), 0) AS open_gap_total, "
        "COALESCE(SUM(CASE WHEN gaps.status = 'verified' THEN 1 ELSE 0 END), 0) AS verified_gap_total "
        "FROM sessions LEFT JOIN gaps ON gaps.session_id = sessions.id "
    )
    params: list[object] = []
    if page_path:
        sql += "WHERE sessions.page_path = ? "
        params.append(page_path)
    sql += "GROUP BY sessions.id ORDER BY sessions.updated_at DESC, sessions.id DESC LIMIT ?"
    params.append(limit)
    with db.cursor() as cur:
        rows = cur.execute(sql, params).fetchall()
    return db.rows_to_dicts(rows)


def list_gaps(limit: int = 50, status: str | None = None) -> list[dict]:
    sql = (
        "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
        "JOIN sessions ON sessions.id = gaps.session_id "
    )
    params: list[object] = []
    if status:
        sql += "WHERE gaps.status = ? "
        params.append(status)
    sql += "ORDER BY CASE gaps.status WHEN 'open' THEN 0 WHEN 'revised' THEN 1 ELSE 2 END, gaps.id DESC LIMIT ?"
    params.append(limit)
    with db.cursor() as cur:
        rows = cur.execute(sql, params).fetchall()
    return db.rows_to_dicts(rows)


def revise_gap(gap_id: int, revision: str) -> dict:
    revision = revision.strip()
    if len(revision) < MIN_EXPLANATION_LENGTH:
        raise ValueError(f"请至少写 {MIN_EXPLANATION_LENGTH} 个字符，说明你如何补全这个问题。")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
        if not row:
            raise LookupError("待澄清点不存在")
        gap = dict(row)
    try:
        _, reference_html = wiki_reader.render_page_html(gap["page_path"])
    except (FileNotFoundError, ValueError):
        reference_html = ""
    status, feedback, source = tutor.assess_gap_revision(
        revision, gap["content"], gap["page_title"], reference_html,
    )
    with db.cursor() as cur:
        cur.execute("UPDATE gaps SET revision = ?, status = ? WHERE id = ?", (revision, status, gap_id))
        cur.execute("UPDATE sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?", (gap["session_id"],))
        updated = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
    result = dict(updated)
    result.update({"feedback": feedback, "assessment_source": source})
    return result


def export_learning_data() -> dict:
    """导出工作台自身的学习记录，不包含或改写 Wiki 原文。"""
    tables = ("notes", "sessions", "turns", "gaps", "cards", "reviews")
    with db.cursor() as cur:
        payload = {table: db.rows_to_dicts(cur.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()) for table in tables}
    return {"format": "feynman-workbench-export", "version": 1, "exported_at": datetime.now().isoformat(timespec="seconds"), **payload}


def list_orphaned_records() -> list[dict]:
    """识别原 Wiki 中已不存在的页面路径，以便用户在重命名/移动后手动重新关联。"""
    with db.cursor() as cur:
        paths = cur.execute(
            "SELECT page_path, MAX(page_title) AS page_title, MAX(updated_at) AS last_activity "
            "FROM sessions GROUP BY page_path "
            "UNION "
            "SELECT notes.page_path, notes.page_path AS page_title, notes.updated_at AS last_activity "
            "FROM notes WHERE page_path NOT IN (SELECT page_path FROM sessions)"
        ).fetchall()
    orphaned = []
    for row in paths:
        path = row["page_path"]
        try:
            wiki_reader.render_page_html(path)
        except (FileNotFoundError, ValueError):
            orphaned.append(dict(row))
    return orphaned


def relink_page(old_path: str, new_path: str) -> dict:
    """将旧 Wiki 路径下的学习记录迁移到用户确认的新页面，冲突时拒绝覆盖。"""
    if old_path == new_path:
        raise ValueError("新旧页面路径相同，无需重新关联。")
    meta, _ = wiki_reader.render_page_html(new_path)
    title = meta.get("title") or new_path.rsplit("/", 1)[-1].removesuffix(".md")
    with db.cursor() as cur:
        has_records = cur.execute(
            "SELECT EXISTS(SELECT 1 FROM sessions WHERE page_path = ?) OR EXISTS(SELECT 1 FROM notes WHERE page_path = ?)",
            (old_path, old_path),
        ).fetchone()[0]
        if not has_records:
            raise LookupError("旧页面没有可重新关联的学习记录。")
        note_conflict = cur.execute("SELECT 1 FROM notes WHERE page_path = ?", (new_path,)).fetchone()
        old_note = cur.execute("SELECT 1 FROM notes WHERE page_path = ?", (old_path,)).fetchone()
        if note_conflict and old_note:
            raise ValueError("目标页面已有笔记，为避免覆盖，请先在学习记录中手动合并笔记。")
        cur.execute("UPDATE sessions SET page_path = ?, page_title = ?, concept = ? WHERE page_path = ?", (new_path, title, title, old_path))
        cur.execute("UPDATE notes SET page_path = ? WHERE page_path = ?", (new_path, old_path))
    return {"old_path": old_path, "new_path": new_path, "title": title}
