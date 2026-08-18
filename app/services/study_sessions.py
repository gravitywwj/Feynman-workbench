"""费曼学习会话服务：持久化讲解、笔记、盲区与复习卡。

没有配置 LLM 密钥时，服务仍返回可执行的费曼检查结果；这样本地学习闭环不会退化成占位界面。
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import json
from pathlib import Path

from app import config, db
from app.services import mastery, review_coach, review_schedule, tutor, wiki_reader, wiki_writer

MIN_EXPLANATION_LENGTH = 24


def _validate_page(path: str) -> dict:
    meta, _ = wiki_reader.render_page_html(path)
    return meta


def build_recall_brief(page_path: str, persona: str = "feynman") -> dict:
    """Build the first recall prompt from the selected page and learner evidence."""
    meta, reference = wiki_reader.read_page_markdown(page_path)
    title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    with db.cursor() as cur:
        note_row = cur.execute("SELECT content FROM notes WHERE page_path = ?", (page_path,)).fetchone()
        gap_rows = cur.execute(
            "SELECT gaps.content FROM gaps JOIN sessions ON sessions.id = gaps.session_id "
            "WHERE sessions.page_path = ? AND gaps.status != 'verified' ORDER BY gaps.id DESC LIMIT 3",
            (page_path,),
        ).fetchall()
        reflection_rows = cur.execute(
            "SELECT content FROM reflections WHERE page_path = ? ORDER BY id DESC LIMIT 2", (page_path,)
        ).fetchall()
    return tutor.build_recall_brief(
        title=title,
        reference=reference,
        note=(note_row["content"] if note_row else ""),
        gaps=[row["content"] for row in gap_rows],
        reflections=[row["content"] for row in reflection_rows],
        persona=persona,
    )


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


def create_session(page_path: str, explanation: str, elapsed_seconds: int = 0, persona: str = "feynman") -> dict:
    """创建一轮讲解会话，并保存本地诊断、追问和初始复习卡。"""
    if not explanation or len(explanation.strip()) < MIN_EXPLANATION_LENGTH:
        raise ValueError(f"请至少写 {MIN_EXPLANATION_LENGTH} 个字符，再开始诊断。")
    meta, reference_html = wiki_reader.render_page_html(page_path)
    title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    gaps, question, diagnosis_source = tutor.diagnose(explanation, title, reference_html, persona)
    today = date.today()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (page_path, page_title, concept, status, duration_seconds) VALUES (?, ?, ?, 'gaps', ?)",
            (page_path, title, title, max(0, elapsed_seconds)),
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
                (session_id, card["question"], card["answer"], review_schedule.initial_due(today)),
            )
    detail = session_detail(session_id)
    detail["diagnosis_source"] = diagnosis_source
    structure = tutor.explain_structure(explanation)
    detail["diagnosis"] = {
        "strengths": structure["strengths"],
        "checks": structure["checks"],
        "next_task": question,
        "source": diagnosis_source,
        "confidence": "reference_checked" if diagnosis_source == "llm" else "structure_only",
    }
    return detail


def session_detail(session_id: int) -> dict:
    with db.cursor() as cur:
        session = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("学习会话不存在")
        turns = cur.execute("SELECT id, role, content, created_at FROM turns WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        gaps = cur.execute("SELECT id, gap_type, content, status, revision FROM gaps WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        cards = cur.execute("SELECT id, question, answer, due, interval, reps FROM cards WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        feedback = cur.execute(
            "SELECT gap_id, verdict FROM diagnosis_feedback WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    gap_items = db.rows_to_dicts(gaps)
    for gap in gap_items:
        gap["evidence"] = {
            "missing": "回答中缺少这个检查项，请回到当前学习资料核对。",
            "wrong": "请回到当前学习资料核对这处可能的误解。",
            "vague": "回答结构不足以核对，请回到当前学习资料补足解释。",
        }.get(gap["gap_type"], "请回到当前学习资料核对。")
    return {
        "session": dict(session), "turns": db.rows_to_dicts(turns), "gaps": gap_items,
        "cards": db.rows_to_dicts(cards), "diagnosis_feedback": db.rows_to_dicts(feedback),
    }


def complete_session(session_id: int, explanation: str, elapsed_seconds: int = 0) -> dict:
    """Store a second, simpler expression and return an honest learning outcome."""
    explanation = explanation.strip()
    if len(explanation) < MIN_EXPLANATION_LENGTH:
        raise ValueError(f"请至少写 {MIN_EXPLANATION_LENGTH} 个字符，再保存第二次表达。")
    with db.cursor() as cur:
        session = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("学习会话不存在")
        meta, reference_html = wiki_reader.render_page_html(session["page_path"])
        title = meta.get("title") or session["page_title"]
        reevaluated_gaps, _, reevaluation_source = tutor.diagnose(explanation, title, reference_html)
        cur.execute("INSERT INTO turns (session_id, role, content) VALUES (?, 'revision', ?)", (session_id, explanation))
        cur.execute("DELETE FROM gaps WHERE session_id = ? AND status = 'open'", (session_id,))
        for gap in reevaluated_gaps:
            cur.execute(
                "INSERT INTO gaps (session_id, gap_type, content) VALUES (?, ?, ?)",
                (session_id, gap["gap_type"], gap["content"]),
            )
        cur.execute(
            "UPDATE sessions SET status = 'done', duration_seconds = MAX(duration_seconds, ?), "
            "updated_at = datetime('now', 'localtime') WHERE id = ?",
            (max(0, elapsed_seconds), session_id),
        )
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('session_done', ?, ?)",
            (session["page_path"], session_id),
        )
    detail = session_detail(session_id)
    first = next((turn["content"] for turn in detail["turns"] if turn["role"] == "user"), "")
    second = next((turn["content"] for turn in detail["turns"] if turn["role"] == "revision"), explanation)
    first_structure = tutor.explain_structure(first)
    second_structure = tutor.explain_structure(second)
    first_by_key = {item["key"]: item for item in first_structure["checks"]}
    second_by_key = {item["key"]: item for item in second_structure["checks"]}
    added = [
        f"新增{second_by_key[key]['label']}：{second_by_key[key]['evidence'][0]}"
        for key in second_by_key
        if second_by_key[key]["passed"] and not first_by_key[key]["passed"]
    ]
    removed = [
        f"第二次未再展开{first_by_key[key]['label']}，下次复习可补回。"
        for key in first_by_key
        if first_by_key[key]["passed"] and not second_by_key[key]["passed"]
    ]
    quality = tutor.compare_expression_quality(first, second)
    improvements = [*added, *quality["new_points"], *quality["simplified"]]
    tradeoffs = [*removed, *quality["omitted_important"]]
    next_due = min((card["due"] for card in detail["cards"]), default=None)
    recommended = wiki_reader.recommend_next_concept(detail["session"]["page_path"])
    return {
        **detail,
        "outcome": {
            "strengths": second_structure["strengths"],
            "remaining_gaps": [gap for gap in detail["gaps"] if gap["status"] != "verified"],
            "first_explanation": first,
            "second_explanation": second,
            "improvements": improvements or ["结构信号没有新增；下一次回忆可继续检验是否更清楚、完整。"],
            "tradeoffs": tradeoffs,
            "comparison": quality,
            "reassessment_source": reevaluation_source,
            "next_review_date": next_due,
            "recommended_next": recommended,
            "confidence": "structure_only",
        },
    }


def record_diagnosis_feedback(session_id: int, gap_id: int | None, verdict: str) -> dict:
    """Persist user feedback so local prompts can be evaluated, not silently trusted."""
    if verdict not in {"helpful", "disputed"}:
        raise ValueError("反馈必须为 helpful 或 disputed")
    with db.cursor() as cur:
        session = cur.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("学习会话不存在")
        if gap_id is not None:
            gap = cur.execute("SELECT 1 FROM gaps WHERE id = ? AND session_id = ?", (gap_id, session_id)).fetchone()
            if not gap:
                raise ValueError("该提示不属于当前学习会话")
        cur.execute(
            "INSERT INTO diagnosis_feedback (session_id, gap_id, verdict) VALUES (?, ?, ?)",
            (session_id, gap_id, verdict),
        )
        feedback_id = cur.lastrowid
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) "
            "SELECT ?, page_path, ? FROM sessions WHERE id = ?",
            (f"diagnosis_{verdict}", feedback_id, session_id),
        )
    return {"id": feedback_id, "session_id": session_id, "gap_id": gap_id, "verdict": verdict}


def today_action() -> dict:
    """Return the one learning action that deserves the home screen."""
    workspace = config.get_workspace_settings()
    if not workspace["configured"]:
        return {
            "type": "configure",
            "title": "先连接你的学习资料",
            "detail": "选择一个包含 pages 的本地 Wiki，或先用两分钟示例体验一次回忆表达。",
        }
    due_cards = list_due_cards(1)
    if due_cards:
        summary = review_summary()
        return {
            "type": "review",
            "title": "完成今天的间隔复习",
            "detail": f"今天目标 {summary['goal']} 张，已完成 {summary['completed']} 张；还有 {summary['total']} 张需要回忆。",
        }
    with db.cursor() as cur:
        session = cur.execute(
            "SELECT id, page_path, page_title FROM sessions WHERE status != 'done' ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
    if session:
        return {"type": "continue", "title": f"继续处理：{session['page_title']}", "detail": "上次已经完成第一次表达。现在补充并用更简单的话再讲一次。", "session_id": session["id"], "page_path": session["page_path"]}
    concepts = wiki_reader.scan_concepts()
    choices = mastery.weakest_first(concepts)
    if choices:
        offset = 0
        if workspace.get("learning_goal") == "presentation":
            high_priority = [item for item in choices if item.get("importance") == "high"]
            concept = high_priority[0] if high_priority else choices[0]
        elif workspace.get("learning_goal") == "exam" and len(choices) > 1:
            offset = 1
            concept = choices[offset]
        else:
            concept = choices[0]
        reason = {
            "unseen": "它尚未留下阅读或回忆证据，因此从这里开始。",
            "read": "它已读完但还没有回忆表达，现在适合合上资料尝试重建。",
            "recalled": "它已有第一次表达，下一步应继续完成二次复述。",
            "revised": "它已完成二次表达，等待复习计划安排巩固。",
            "stable": "目前没有更紧急的待处理概念。",
        }[concept.get("mastery", {}).get("level", "unseen")]
        alternatives = [item for item in choices if item["path"] != concept["path"]][:3]
        return {
            "type": "start", "title": f"从「{concept['title']}」开始", "detail": f"{reason} 阅读后合上资料，用自己的话完成一次回忆表达。",
            "page_path": concept["path"], "reason": "mastery_state", "learning_goal": workspace.get("learning_goal"),
            "alternatives": [{"path": item["path"], "title": item["title"]} for item in alternatives],
        }
    return {"type": "empty", "title": "还没有可学习的概念", "detail": "连接 Wiki 后，这里会给出今天最合适的下一步。"}


def today_study_summary(today: date | None = None) -> dict:
    """Return completed, client-measured study time rather than a decorative number."""
    current = today or date.today()
    with db.cursor() as cur:
        seconds = cur.execute(
            "SELECT COALESCE(SUM(duration_seconds), 0) FROM sessions "
            "WHERE substr(updated_at, 1, 10) = ?",
            (current.isoformat(),),
        ).fetchone()[0]
        sessions = cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE substr(updated_at, 1, 10) = ?", (current.isoformat(),)
        ).fetchone()[0]
    return {"date": current.isoformat(), "elapsed_seconds": int(seconds or 0), "sessions": int(sessions or 0)}


def _decorate_cards(rows: list) -> list[dict]:
    today = date.today()
    cards = []
    for row in rows:
        card = dict(row)
        due_date = date.fromisoformat(card["due"])
        card["overdue_days"] = max(0, (today - due_date).days)
        card["stage"] = review_schedule.stage_label(card["reps"], card["interval"])
        card["estimated_minutes"] = 2 if card["reps"] <= 1 else 1
        card["why_today"] = (
            f"比计划晚了 {card['overdue_days']} 天"
            if card["overdue_days"]
            else "今天是本次间隔复习日"
        )
        cards.append(card)
    return cards


def list_due_cards(limit: int = 20) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT cards.*, sessions.page_title FROM cards JOIN sessions ON sessions.id = cards.session_id "
            "WHERE due <= ? ORDER BY due, cards.id LIMIT ?",
            (date.today().isoformat(), limit),
        ).fetchall()
    return _decorate_cards(rows)


def list_review_queue(mode: str = "scheduled", limit: int = 20) -> list[dict]:
    """scheduled 只展示到期卡；cram 则给出最值得立即抽查的卡。"""
    if mode not in {"scheduled", "cram"}:
        raise ValueError("复习模式必须为 scheduled 或 cram")
    if mode == "scheduled":
        return list_due_cards(limit)
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT cards.*, sessions.page_title FROM cards JOIN sessions ON sessions.id = cards.session_id "
            "ORDER BY CASE WHEN due <= ? THEN 0 ELSE 1 END, due, reps, cards.id LIMIT ?",
            (date.today().isoformat(), limit),
        ).fetchall()
    cards = _decorate_cards(rows)
    for index, card in enumerate(cards):
        if card["overdue_days"]:
            card["why_today"] = f"已超过计划 {card['overdue_days']} 天，优先收回"
        elif card["reps"] == 0:
            card["why_today"] = "刚完成学习，需要做一次突击检查"
        else:
            card["why_today"] = "距离下次计划较近，适合现在抽查"
        card["priority"] = index + 1
    return cards


def review_summary() -> dict:
    """A compact daily plan with an explainable estimate, not a gamified score."""
    cards = list_due_cards(100)
    settings = config.get_workspace_settings()
    today_text = date.today().isoformat()
    with db.cursor() as cur:
        completed = cur.execute(
            "SELECT COUNT(*) FROM reviews WHERE substr(reviewed_at, 1, 10) = ?", (today_text,)
        ).fetchone()[0]
    return {
        "total": len(cards),
        "completed": completed,
        "goal": settings["daily_review_goal"],
        "estimated_minutes": sum(card["estimated_minutes"] for card in cards),
        "cards": cards,
        "has_evidence": bool(cards or completed),
    }


def review_card(card_id: int, rating: str) -> dict:
    if rating not in {"again", "hard", "good", "easy"}:
        raise ValueError("评分必须为 again、hard、good 或 easy")
    with db.cursor() as cur:
        card = cur.execute(
            "SELECT cards.*, sessions.page_path FROM cards JOIN sessions ON sessions.id = cards.session_id WHERE cards.id = ?",
            (card_id,),
        ).fetchone()
        if not card:
            raise LookupError("复习卡不存在")
        schedule = review_schedule.next_schedule(
            interval=card["interval"], reps=card["reps"], ease=card["ease"], rating=rating,
        )
        cur.execute(
            "UPDATE cards SET interval = ?, reps = ?, ease = ?, due = ? WHERE id = ?",
            (schedule["interval"], schedule["reps"], schedule["ease"], schedule["due"], card_id),
        )
        cur.execute("INSERT INTO reviews (card_id, rating) VALUES (?, ?)", (card_id, rating))
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('review_rated', ?, ?)",
            (card["page_path"], card_id),
        )
        updated = cur.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    result = dict(updated)
    result["next_review_reason"] = {
        "again": "记忆尚不稳定，明天再回忆一次",
        "hard": "需要更短间隔巩固",
        "good": "可以拉长间隔，再检查是否仍能回忆",
        "easy": "掌握较稳，安排更长的间隔",
    }[rating]
    return result


def assess_review_attempt(card_id: int, answer: str, agent: str) -> dict:
    """让指定教练检查一次主动回忆，并保存可追溯的反馈。"""
    answer = answer.strip()
    if len(answer) < MIN_EXPLANATION_LENGTH:
        raise ValueError(f"请至少写 {MIN_EXPLANATION_LENGTH} 个字符，再交给复习教练检查。")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT cards.*, sessions.page_title, sessions.page_path FROM cards "
            "JOIN sessions ON sessions.id = cards.session_id WHERE cards.id = ?", (card_id,),
        ).fetchone()
    if not row:
        raise LookupError("复习卡不存在")
    card = dict(row)
    try:
        _, reference_html = wiki_reader.render_page_html(card["page_path"])
    except (FileNotFoundError, ValueError):
        reference_html = ""
    verdict, feedback, follow_up, source = review_coach.assess(
        answer, question=card["question"], expected=card["answer"], title=card["page_title"],
        reference_html=reference_html, agent=agent,
    )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO review_attempts (card_id, agent, answer, verdict, feedback, follow_up, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (card_id, agent, answer, verdict, feedback, follow_up, source),
        )
        attempt_id = cur.lastrowid
    return {
        "id": attempt_id, "card_id": card_id, "agent": agent, "agent_name": review_coach.agent_profile(agent)["name"],
        "verdict": verdict, "feedback": feedback, "follow_up": follow_up, "source": source,
    }


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


def _decode_json(value: str, fallback):
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, type(fallback)) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _knowledge_update_payload(row, revision=None) -> dict:
    item = dict(row)
    item["analysis"] = _decode_json(item.pop("analysis_json"), {})
    item["evidence"] = _decode_json(item.pop("evidence_json"), [])
    item["revision"] = dict(revision) if revision else None
    return item


def _knowledge_update_row(update_id: int):
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM knowledge_updates WHERE id = ?", (update_id,)).fetchone()
    if not row:
        raise LookupError("知识库更新草案不存在")
    return row


def _current_page_evidence(page_path: str, title: str) -> dict:
    _, body = wiki_reader.read_page_markdown(page_path)
    return {
        "path": page_path,
        "title": title,
        "section": page_path.split("/", 1)[0] if "/" in page_path else "",
        "excerpt": " ".join(body.split())[:320],
        "matched_terms": ["当前学习页"],
    }


def create_knowledge_update(content: str, page_path: str, persona: str = "feynman") -> dict:
    """Analyze one learner note and persist an editable, not-yet-written proposal."""
    note = content.strip()
    if len(note) < 4:
        raise ValueError("请先写下一条具体的理解、疑问或想法，再让 Agent 整理。")
    meta = _validate_page(page_path)
    title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    evidence = wiki_reader.search_wiki(note, limit=5)
    if not any(item["path"] == page_path for item in evidence):
        evidence.insert(0, _current_page_evidence(page_path, title))
    analysis = tutor.analyze_knowledge_note(note=note, title=title, evidence=evidence, persona=persona)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO knowledge_updates (page_path, page_title, persona, source_content, analysis_json, evidence_json, proposal, proposed_title) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                page_path, title, tutor.normalize_persona(persona), note,
                json.dumps({key: value for key, value in analysis.items() if key not in {"proposal", "proposed_title"}}, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False), analysis["proposal"], analysis["proposed_title"],
            ),
        )
        update_id = cur.lastrowid
    return get_knowledge_update(update_id)


def get_knowledge_update(update_id: int) -> dict:
    row = _knowledge_update_row(update_id)
    with db.cursor() as cur:
        revision = cur.execute(
            "SELECT id, page_path, created_page, created_at, undone_at FROM wiki_revisions "
            "WHERE knowledge_update_id = ? ORDER BY id DESC LIMIT 1", (update_id,)
        ).fetchone()
    return _knowledge_update_payload(row, revision)


def list_knowledge_updates(limit: int = 50, page_path: str | None = None) -> list[dict]:
    where = "WHERE page_path = ?" if page_path else ""
    params = (page_path, limit) if page_path else (limit,)
    with db.cursor() as cur:
        rows = cur.execute(
            f"SELECT * FROM knowledge_updates {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
        revisions = {
            row["knowledge_update_id"]: row
            for row in cur.execute(
                "SELECT r.id, r.knowledge_update_id, r.page_path, r.created_page, r.created_at, r.undone_at "
                "FROM wiki_revisions r WHERE r.id IN (SELECT MAX(id) FROM wiki_revisions GROUP BY knowledge_update_id)"
            ).fetchall()
        }
    return [_knowledge_update_payload(row, revisions.get(row["id"])) for row in rows]


def apply_knowledge_update(
    update_id: int, *, target_mode: str, proposal: str, proposed_title: str = "",
) -> dict:
    """Apply an approved proposal with a full pre-write snapshot for later undo."""
    if target_mode not in {"append_current", "create_idea", "keep_local"}:
        raise ValueError("不支持的知识库写入方式")
    clean_proposal = proposal.strip()
    if not clean_proposal:
        raise ValueError("请先保留或编辑草案内容，再确认操作。")
    row = _knowledge_update_row(update_id)
    if row["status"] != "draft":
        raise ValueError("这条草案已经处理，不能重复写入。")
    title = proposed_title.strip()[:120] or row["proposed_title"] or f"关于{row['page_title']}的学习想法"
    if target_mode == "keep_local":
        with db.cursor() as cur:
            cur.execute(
                "UPDATE knowledge_updates SET proposal = ?, proposed_title = ?, target_mode = ?, status = 'kept_local', "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (clean_proposal, title, target_mode, update_id),
            )
        return get_knowledge_update(update_id)
    if target_mode == "append_current":
        change = wiki_writer.append_learning_update(row["page_path"], clean_proposal, update_id)
    else:
        change = wiki_writer.create_linked_idea_page(row["page_path"], title, clean_proposal, update_id)
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO wiki_revisions (knowledge_update_id, page_path, before_content, after_content, created_page) VALUES (?, ?, ?, ?, ?)",
                (update_id, change["path"], change["before_content"], change["after_content"], int(change["created_page"])),
            )
            revision_id = cur.lastrowid
            cur.execute(
                "UPDATE knowledge_updates SET proposal = ?, proposed_title = ?, target_mode = ?, target_path = ?, status = 'applied', "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (clean_proposal, title, target_mode, change["path"], update_id),
            )
            cur.execute(
                "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('knowledge_update_applied', ?, ?)",
                (change["path"], revision_id),
            )
    except Exception:
        # Filesystem succeeded but database bookkeeping did not: immediately try to
        # restore the exact snapshot so no untracked Wiki edit is left behind.
        try:
            wiki_writer.restore_revision(
                change["path"], change["before_content"], change["after_content"],
                created_page=bool(change["created_page"]),
            )
        except (FileNotFoundError, ValueError, OSError):
            pass
        raise
    return get_knowledge_update(update_id)


def undo_knowledge_update(update_id: int) -> dict:
    """Restore the exact pre-write snapshot when no later edit conflicts with it."""
    row = _knowledge_update_row(update_id)
    if row["status"] != "applied":
        raise ValueError("只有已写入 Wiki 的草案可以撤销。")
    with db.cursor() as cur:
        revision = cur.execute(
            "SELECT * FROM wiki_revisions WHERE knowledge_update_id = ? AND undone_at IS NULL ORDER BY id DESC LIMIT 1",
            (update_id,),
        ).fetchone()
    if not revision:
        raise LookupError("没有可撤销的 Wiki 快照")
    wiki_writer.restore_revision(
        revision["page_path"], revision["before_content"], revision["after_content"],
        created_page=bool(revision["created_page"]),
    )
    with db.cursor() as cur:
        cur.execute("UPDATE wiki_revisions SET undone_at = datetime('now', 'localtime') WHERE id = ?", (revision["id"],))
        cur.execute(
            "UPDATE knowledge_updates SET status = 'undone', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (update_id,),
        )
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('knowledge_update_undone', ?, ?)",
            (revision["page_path"], revision["id"]),
        )
    return get_knowledge_update(update_id)


def _reflection_by_id(reflection_id: int) -> dict:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM reflections WHERE id = ?", (reflection_id,)).fetchone()
    if not row:
        raise LookupError("这条学习心得不存在或已被移除。")
    return dict(row)


def create_reflection(content: str, *, page_path: str | None = None, session_id: int | None = None) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("请写下心得后再保存。")
    page_title = None
    if page_path:
        meta, _ = wiki_reader.render_page_html(page_path)
        page_title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    if session_id:
        with db.cursor() as cur:
            session = cur.execute("SELECT page_path, page_title FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("关联的学习会话不存在。")
        if page_path and page_path != session["page_path"]:
            raise ValueError("心得关联的知识点与学习会话不一致。")
        page_path, page_title = session["page_path"], session["page_title"]
    source = "session" if session_id else "manual"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO reflections (page_path, page_title, session_id, source, content) VALUES (?, ?, ?, ?, ?)",
            (page_path, page_title, session_id, source, content),
        )
        reflection_id = cur.lastrowid
        if page_path:
            cur.execute(
                "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('reflection_saved', ?, ?)",
                (page_path, reflection_id),
            )
    return _reflection_by_id(reflection_id)


def update_reflection(reflection_id: int, content: str) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("心得不能保存为空。")
    with db.cursor() as cur:
        existing = cur.execute("SELECT id FROM reflections WHERE id = ?", (reflection_id,)).fetchone()
        if not existing:
            raise LookupError("这条学习心得不存在或已被移除。")
        cur.execute(
            "UPDATE reflections SET content = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (content, reflection_id),
        )
    return _reflection_by_id(reflection_id)


def list_reflections(limit: int = 50, page_path: str | None = None) -> list[dict]:
    sql = "SELECT * FROM reflections "
    params: list[object] = []
    if page_path:
        sql += "WHERE page_path = ? "
        params.append(page_path)
    sql += "ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with db.cursor() as cur:
        return db.rows_to_dicts(cur.execute(sql, params).fetchall())


def create_reflection_summary(reflection_ids: list[int]) -> dict:
    ids = list(dict.fromkeys(reflection_ids))
    if not ids:
        raise ValueError("请至少选择一条学习心得。")
    placeholders = ", ".join("?" for _ in ids)
    with db.cursor() as cur:
        rows = cur.execute(
            f"SELECT * FROM reflections WHERE id IN ({placeholders}) ORDER BY created_at ASC, id ASC", ids,
        ).fetchall()
    reflections = db.rows_to_dicts(rows)
    if len(reflections) != len(ids):
        raise LookupError("所选心得中有内容已不存在，请刷新后再试。")
    content, source = tutor.summarize_reflections(reflections)
    linked_paths = {item["page_path"] for item in reflections if item.get("page_path")}
    page_path = next(iter(linked_paths)) if len(linked_paths) == 1 else None
    page_title = next((item["page_title"] for item in reflections if item.get("page_title")), None)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO reflections (page_path, page_title, source, content) VALUES (?, ?, 'summary', ?)",
            (page_path, page_title if page_path else "阶段总结", content),
        )
        reflection_id = cur.lastrowid
        if page_path:
            cur.execute(
                "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('reflection_summary_saved', ?, ?)",
                (page_path, reflection_id),
            )
    result = _reflection_by_id(reflection_id)
    result["summary_source"] = source
    result["source_reflection_ids"] = ids
    return result


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
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('gap_revised', ?, ?)",
            (gap["page_path"], gap_id),
        )
        updated = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
    result = dict(updated)
    result.update({"feedback": feedback, "assessment_source": source})
    return result


def export_learning_data() -> dict:
    """导出工作台学习记录与已确认写入的安全回档快照，不改写 Wiki 原文。"""
    tables = (
        "notes", "reflections", "sessions", "turns", "gaps", "cards", "reviews",
        "review_attempts", "learning_events", "diagnosis_feedback", "knowledge_updates",
        "wiki_revisions",
    )
    with db.cursor() as cur:
        payload = {table: db.rows_to_dicts(cur.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()) for table in tables}
    return {"format": "feynman-workbench-export", "version": 3, "exported_at": datetime.now().isoformat(timespec="seconds"), **payload}


def import_learning_data(payload: dict, *, dry_run: bool = False) -> dict:
    """Merge a user-selected local export without deleting current-device records.

    Sessions have no globally stable identity, so their natural key is page, title and
    creation time. Notes use their page path and only accept a strictly newer remote
    version. This keeps an import additive and safe enough for a local-first workflow.
    """
    if payload.get("format") != "feynman-workbench-export":
        raise ValueError("这不是费曼学习工作台导出的学习数据")
    if payload.get("version") not in {1, 2, 3}:
        raise ValueError("暂不支持该导出版本")
    names = (
        "notes", "reflections", "sessions", "turns", "gaps", "cards", "reviews",
        "review_attempts", "learning_events", "diagnosis_feedback", "knowledge_updates",
        "wiki_revisions",
    )
    source = {name: payload.get(name, []) for name in names}
    if any(not isinstance(value, list) for value in source.values()):
        raise ValueError("导出数据的结构不正确")
    counts = {
        "notes": 0, "reflections": 0, "sessions": 0, "cards": 0, "events": 0,
        "knowledge_updates": 0, "wiki_revisions": 0,
    }
    if dry_run:
        return {
            "dry_run": True,
            "incoming": {name: len(value) for name, value in source.items()},
            "message": "导入只会补充不存在的会话，并在导入笔记更新更晚时合并笔记。",
        }
    with db.cursor() as cur:
        for note in source["notes"]:
            path = str(note.get("page_path", ""))
            content = str(note.get("content", ""))[:10000]
            if not path:
                continue
            existing = cur.execute("SELECT updated_at FROM notes WHERE page_path = ?", (path,)).fetchone()
            incoming_at = str(note.get("updated_at") or "")
            if not existing:
                cur.execute("INSERT INTO notes (page_path, content) VALUES (?, ?)", (path, content))
                counts["notes"] += 1
            elif incoming_at and incoming_at > (existing["updated_at"] or ""):
                cur.execute("UPDATE notes SET content = ?, updated_at = ? WHERE page_path = ?", (content, incoming_at, path))
                counts["notes"] += 1

        session_map: dict[int, int] = {}
        new_source_session_ids: set[int] = set()
        for session in source["sessions"]:
            old_id = session.get("id")
            page_path = str(session.get("page_path", ""))
            page_title = str(session.get("page_title", ""))
            created_at = str(session.get("created_at") or "")
            if not isinstance(old_id, int) or not page_path or not page_title:
                continue
            existing = cur.execute(
                "SELECT id FROM sessions WHERE page_path = ? AND page_title = ? AND created_at = ?",
                (page_path, page_title, created_at),
            ).fetchone()
            if existing:
                session_map[old_id] = existing["id"]
                continue
            cur.execute(
                "INSERT INTO sessions (page_path, page_title, concept, status, tutor_turns, duration_seconds, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (page_path, page_title, str(session.get("concept") or page_title),
                 str(session.get("status") or "done"), int(session.get("tutor_turns") or 3), int(session.get("duration_seconds") or 0),
                 created_at or datetime.now().isoformat(timespec="seconds"),
                 str(session.get("updated_at") or created_at or datetime.now().isoformat(timespec="seconds"))),
            )
            session_map[old_id] = cur.lastrowid
            new_source_session_ids.add(old_id)
            counts["sessions"] += 1

        for reflection in source["reflections"]:
            content = str(reflection.get("content") or "").strip()[:10000]
            created_at = str(reflection.get("created_at") or "")
            if not content or not created_at:
                continue
            existing = cur.execute(
                "SELECT 1 FROM reflections WHERE content = ? AND created_at = ?", (content, created_at),
            ).fetchone()
            if existing:
                continue
            old_session_id = reflection.get("session_id")
            session_id = session_map.get(old_session_id) if isinstance(old_session_id, int) else None
            cur.execute(
                "INSERT INTO reflections (page_path, page_title, session_id, source, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (reflection.get("page_path"), reflection.get("page_title"), session_id,
                 str(reflection.get("source") or "manual"), content, created_at,
                 str(reflection.get("updated_at") or created_at)),
            )
            counts["reflections"] += 1

        card_map: dict[int, int] = {}
        for turn in source["turns"]:
            new_session = session_map.get(turn.get("session_id")) if turn.get("session_id") in new_source_session_ids else None
            if new_session and str(turn.get("content", "")).strip():
                cur.execute("INSERT INTO turns (session_id, role, content) VALUES (?, ?, ?)", (new_session, str(turn.get("role") or "user"), str(turn["content"])))
        for gap in source["gaps"]:
            new_session = session_map.get(gap.get("session_id")) if gap.get("session_id") in new_source_session_ids else None
            if new_session and str(gap.get("content", "")).strip():
                cur.execute(
                    "INSERT INTO gaps (session_id, gap_type, content, status, revision) VALUES (?, ?, ?, ?, ?)",
                    (new_session, str(gap.get("gap_type") or "vague"), str(gap["content"]),
                     str(gap.get("status") or "open"), gap.get("revision")),
                )
        for card in source["cards"]:
            new_session = session_map.get(card.get("session_id")) if card.get("session_id") in new_source_session_ids else None
            old_id = card.get("id")
            if not new_session or not isinstance(old_id, int) or not str(card.get("question", "")).strip():
                continue
            cur.execute(
                "INSERT INTO cards (session_id, question, answer, interval, ease, due, reps) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_session, str(card["question"]), str(card.get("answer") or ""), int(card.get("interval") or 0),
                 float(card.get("ease") or 2.5), str(card.get("due") or date.today().isoformat()), int(card.get("reps") or 0)),
            )
            card_map[old_id] = cur.lastrowid
            counts["cards"] += 1
        for review in source["reviews"]:
            new_card = card_map.get(review.get("card_id"))
            if new_card and str(review.get("rating", "")) in {"again", "hard", "good", "easy"}:
                cur.execute("INSERT INTO reviews (card_id, rating) VALUES (?, ?)", (new_card, review["rating"]))
        for attempt in source["review_attempts"]:
            new_card = card_map.get(attempt.get("card_id"))
            if new_card and str(attempt.get("answer", "")).strip():
                cur.execute(
                    "INSERT INTO review_attempts (card_id, agent, answer, verdict, feedback, follow_up, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_card, str(attempt.get("agent") or "feynman"), str(attempt["answer"]),
                     str(attempt.get("verdict") or "retry"), str(attempt.get("feedback") or ""),
                     str(attempt.get("follow_up") or ""), str(attempt.get("source") or "local")),
                )
        for event in source["learning_events"]:
            path = str(event.get("page_path", ""))
            if path:
                event_type = str(event.get("event_type") or "imported")
                created_at = str(event.get("created_at") or "")
                existing = cur.execute(
                    "SELECT 1 FROM learning_events WHERE event_type = ? AND page_path = ? AND created_at = ?",
                    (event_type, path, created_at),
                ).fetchone() if created_at else None
                if not existing:
                    if created_at:
                        cur.execute(
                            "INSERT INTO learning_events (event_type, page_path, entity_id, created_at) VALUES (?, ?, ?, ?)",
                            (event_type, path, event.get("entity_id"), created_at),
                        )
                    else:
                        cur.execute("INSERT INTO learning_events (event_type, page_path, entity_id) VALUES (?, ?, ?)", (event_type, path, event.get("entity_id")))
                    counts["events"] += 1
        for feedback in source["diagnosis_feedback"]:
            new_session = session_map.get(feedback.get("session_id")) if feedback.get("session_id") in new_source_session_ids else None
            verdict = str(feedback.get("verdict") or "")
            if new_session and verdict in {"helpful", "disputed"}:
                cur.execute("INSERT INTO diagnosis_feedback (session_id, verdict) VALUES (?, ?)", (new_session, verdict))
        update_map: dict[int, int] = {}
        for update in source["knowledge_updates"]:
            old_id = update.get("id")
            page_path = str(update.get("page_path") or "")
            source_content = str(update.get("source_content") or "").strip()[:10000]
            created_at = str(update.get("created_at") or "")
            if not isinstance(old_id, int) or not page_path or not source_content:
                continue
            existing = cur.execute(
                "SELECT id FROM knowledge_updates WHERE page_path = ? AND source_content = ? AND created_at = ?",
                (page_path, source_content, created_at),
            ).fetchone() if created_at else None
            if existing:
                update_map[old_id] = existing["id"]
                continue
            status = str(update.get("status") or "draft")
            if status not in {"draft", "applied", "kept_local", "undone"}:
                status = "draft"
            analysis = update.get("analysis_json", "{}")
            evidence = update.get("evidence_json", "[]")
            analysis_json = analysis if isinstance(analysis, str) else json.dumps(analysis, ensure_ascii=False)
            evidence_json = evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False)
            cur.execute(
                "INSERT INTO knowledge_updates (page_path, page_title, persona, source_content, analysis_json, evidence_json, proposal, proposed_title, target_mode, target_path, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    page_path, str(update.get("page_title") or Path(page_path).stem),
                    tutor.normalize_persona(str(update.get("persona") or "feynman")), source_content,
                    analysis_json, evidence_json, str(update.get("proposal") or "")[:5000],
                    str(update.get("proposed_title") or "")[:120], update.get("target_mode"),
                    update.get("target_path"), status,
                    created_at or datetime.now().isoformat(timespec="seconds"),
                    str(update.get("updated_at") or created_at or datetime.now().isoformat(timespec="seconds")),
                ),
            )
            update_map[old_id] = cur.lastrowid
            counts["knowledge_updates"] += 1
        for revision in source["wiki_revisions"]:
            new_update = update_map.get(revision.get("knowledge_update_id"))
            page_path = str(revision.get("page_path") or "")
            before_content = revision.get("before_content")
            after_content = revision.get("after_content")
            if not new_update or not page_path or not isinstance(before_content, str) or not isinstance(after_content, str):
                continue
            created_at = str(revision.get("created_at") or "")
            existing = cur.execute(
                "SELECT 1 FROM wiki_revisions WHERE knowledge_update_id = ? AND page_path = ? AND created_at = ?",
                (new_update, page_path, created_at),
            ).fetchone() if created_at else None
            if existing:
                continue
            cur.execute(
                "INSERT INTO wiki_revisions (knowledge_update_id, page_path, before_content, after_content, created_page, created_at, undone_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_update, page_path, before_content, after_content, int(bool(revision.get("created_page"))),
                 created_at or datetime.now().isoformat(timespec="seconds"), revision.get("undone_at")),
            )
            counts["wiki_revisions"] += 1
    return {"dry_run": False, "imported": counts, "message": "已合并导入数据，当前设备既有记录未被删除。"}


def weekly_report(today: date | None = None) -> dict:
    """A learning report focused on corrections and durable understanding evidence."""
    current = today or date.today()
    start = (current - timedelta(days=6)).isoformat()
    end = current.isoformat()
    with db.cursor() as cur:
        events = cur.execute(
            "SELECT event_type, page_path, COUNT(*) AS total FROM learning_events "
            "WHERE substr(created_at, 1, 10) BETWEEN ? AND ? GROUP BY event_type, page_path",
            (start, end),
        ).fetchall()
        gap_rows = cur.execute(
            "SELECT gaps.content, gaps.status, sessions.page_title, sessions.page_path "
            "FROM gaps JOIN sessions ON sessions.id = gaps.session_id "
            "ORDER BY gaps.id DESC LIMIT 100"
        ).fetchall()
    counter = Counter()
    by_path: dict[str, int] = Counter()
    for event in events:
        counter[event["event_type"]] += event["total"]
        by_path[event["page_path"]] += event["total"]
    repeated = Counter()
    for gap in gap_rows:
        if gap["status"] != "verified":
            repeated[(gap["page_title"], gap["content"])] += 1
    concepts = wiki_reader.scan_concepts()
    states = mastery.overview(concepts)
    stable = [concept for concept in concepts if states[concept["path"]]["level"] == "stable"]
    corrections = [
        {"title": title, "gap": gap, "times": times}
        for (title, gap), times in repeated.most_common(5)
    ]
    evidence_total = counter["session_done"] + counter["gap_revised"] + counter["review_rated"]
    return {
        "range": {"start": start, "end": end},
        "has_evidence": bool(evidence_total),
        "evidence_total": evidence_total,
        "summary": {
            "completed_sessions": counter["session_done"],
            "revised_gaps": counter["gap_revised"],
            "reviews": counter["review_rated"],
            "stable_concepts": len(stable),
        },
        "corrected_misconceptions": corrections,
        "repeated_gaps": [item for item in corrections if item["times"] > 1],
        "stable_concepts": [{"title": item["title"], "path": item["path"]} for item in stable[:8]],
        "active_concepts": [
            {"path": path, "activity": total} for path, total in sorted(by_path.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
    }


def list_orphaned_records() -> list[dict]:
    """识别原 Wiki 中已不存在的页面路径，以便用户在重命名/移动后手动重新关联。"""
    with db.cursor() as cur:
        paths = cur.execute(
            "SELECT page_path, MAX(page_title) AS page_title, MAX(last_activity) AS last_activity FROM ("
            "SELECT page_path, page_title, updated_at AS last_activity FROM sessions "
            "UNION ALL SELECT page_path, page_path AS page_title, updated_at AS last_activity FROM notes "
            "UNION ALL SELECT page_path, page_title, updated_at AS last_activity FROM reflections WHERE page_path IS NOT NULL "
            "UNION ALL SELECT page_path, page_title, updated_at AS last_activity FROM knowledge_updates "
            "UNION ALL SELECT target_path, page_title, updated_at AS last_activity FROM knowledge_updates WHERE target_path IS NOT NULL"
            ") GROUP BY page_path"
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
            "SELECT EXISTS(SELECT 1 FROM sessions WHERE page_path = ?) OR EXISTS(SELECT 1 FROM notes WHERE page_path = ?) "
            "OR EXISTS(SELECT 1 FROM reflections WHERE page_path = ?) OR EXISTS(SELECT 1 FROM knowledge_updates WHERE page_path = ?) "
            "OR EXISTS(SELECT 1 FROM knowledge_updates WHERE target_path = ?) OR EXISTS(SELECT 1 FROM wiki_revisions WHERE page_path = ?)",
            (old_path, old_path, old_path, old_path, old_path, old_path),
        ).fetchone()[0]
        if not has_records:
            raise LookupError("旧页面没有可重新关联的学习记录。")
        note_conflict = cur.execute("SELECT 1 FROM notes WHERE page_path = ?", (new_path,)).fetchone()
        old_note = cur.execute("SELECT 1 FROM notes WHERE page_path = ?", (old_path,)).fetchone()
        if note_conflict and old_note:
            raise ValueError("目标页面已有笔记，为避免覆盖，请先在学习记录中手动合并笔记。")
        cur.execute("UPDATE sessions SET page_path = ?, page_title = ?, concept = ? WHERE page_path = ?", (new_path, title, title, old_path))
        cur.execute("UPDATE notes SET page_path = ? WHERE page_path = ?", (new_path, old_path))
        cur.execute("UPDATE reflections SET page_path = ?, page_title = ? WHERE page_path = ?", (new_path, title, old_path))
        cur.execute("UPDATE knowledge_updates SET page_path = ?, page_title = ? WHERE page_path = ?", (new_path, title, old_path))
        cur.execute("UPDATE knowledge_updates SET target_path = ? WHERE target_path = ?", (new_path, old_path))
        cur.execute("UPDATE wiki_revisions SET page_path = ? WHERE page_path = ?", (new_path, old_path))
    return {"old_path": old_path, "new_path": new_path, "title": title}
