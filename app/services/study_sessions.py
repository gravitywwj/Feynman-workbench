"""费曼学习会话服务：持久化讲解、笔记、盲区与复习卡。

没有配置 LLM 密钥时，服务仍返回可执行的费曼检查结果；这样本地学习闭环不会退化成占位界面。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app import config, db
from app.services import (
    learning_recommendations,
    learning_lab,
    mastery,
    practice_sessions,
    review_coach,
    review_schedule,
    tutor,
    wiki_reader,
    wiki_writer,
)

MIN_REVIEW_ANSWER_LENGTH = 24
MIN_GAP_REVISION_LENGTH = 24


def _gap_learning_path(gap: dict) -> dict:
    """Expose a small, stateful correction path without treating a first rewrite as proof."""
    gap_type = tutor.normalize_gap_type(gap.get("gap_type"))
    meta = tutor.gap_type_meta(gap_type)
    practice_at = gap.get("practice_completed_at")
    retest_due = str(gap.get("retest_due") or "")
    retest_at = gap.get("retest_completed_at")
    status = gap.get("status") or "open"
    today = date.today().isoformat()
    if status == "verified":
        stage = "verified"
        next_action = "已完成来源核对；按间隔复习继续保持。"
    elif retest_at:
        stage = "retest_recorded"
        next_action = "异情境复测已保存，仍待依据原文或学习助手完成核对。"
    elif practice_at and retest_due and retest_due <= today:
        stage = "retest_ready"
        next_action = "今天可用新情境复测，检验是否真的理解而不是记住原答案。"
    elif practice_at:
        stage = "retest_wait"
        next_action = f"已完成两分钟微练习；{retest_due or '明天'}再换一个情境复测。"
    else:
        stage = "practice_ready"
        next_action = "先完成一次两分钟微练习，再安排隔天异情境复测。"
    return {
        "type": gap_type,
        "label": meta["label"],
        "goal": meta["goal"],
        "practice_minutes": 2,
        "practice_prompt": meta["practice_prompt"],
        "retest_prompt": str(gap.get("retest_prompt") or meta["retest_prompt"]),
        "retest_due": retest_due or None,
        "stage": stage,
        "next_action": next_action,
    }


def _decorate_gap(row: object) -> dict:
    gap = dict(row)
    gap["gap_type"] = tutor.normalize_gap_type(gap.get("gap_type"))
    gap["learning_path"] = _gap_learning_path(gap)
    gap["evidence"] = {
        "concept_missing": "先回到当前资料，补出概念的对象、目标和原文依据。",
        "causal_error": "请回到当前资料，核对每一步怎样导致下一步，而不是只写结论。",
        "boundary_missing": "请回到当前资料，补出成立条件、限制或反例。",
        "transfer_failure": "请换一个场景检验规则能否迁移，不要只重复原例。",
    }[gap["gap_type"]]
    return gap


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
        focus = cur.execute("SELECT question FROM learning_focus WHERE page_path = ?", (page_path,)).fetchone()
    return tutor.build_recall_brief(
        title=title,
        reference=reference,
        note=(note_row["content"] if note_row else ""),
        gaps=[row["content"] for row in gap_rows],
        reflections=[row["content"] for row in reflection_rows],
        persona=persona,
        focus_question=focus["question"] if focus else "",
    )


def _card_source_material(blocks: list[dict], title: str, question: str) -> dict:
    """Bind a card to one uniquely relevant Wiki block, never to arbitrary page text."""
    terms = wiki_reader._search_terms(f"{title} {question}")
    candidates = []
    for block in blocks:
        if block["kind"] == "heading" or not block["excerpt"] or len(block["excerpt"]) < 3:
            continue
        content = block["excerpt"].lower()
        heading = block["heading"].lower()
        score = sum(min(content.count(term), 3) + 2 * bool(term in heading) for term in terms)
        if score:
            candidates.append((score, block))
    candidates.sort(key=lambda item: (-item[0], item[1]["index"]))
    if not candidates or (len(candidates) > 1 and candidates[0][0] == candidates[1][0]):
        return {
            "reference_status": "needs_review", "source_anchor": "相关原文待核对",
            "source_index": None, "source_context": "", "source_context_before": "", "source_context_after": "", "source_fingerprint": "",
            "required_points": [], "reference_excerpt": "",
        }
    block = candidates[0][1]
    context = "\n".join(value for value in (block["context_before"], block["context_after"]) if value)
    return {
        "reference_status": "source", "source_anchor": block["heading"] or title,
        "source_index": block["index"], "source_context": context,
        "source_context_before": block["context_before"], "source_context_after": block["context_after"],
        "source_fingerprint": block["fingerprint"],
        "required_points": [block["excerpt"]], "reference_excerpt": block["excerpt"],
    }


def _cards_for(page_path: str, title: str, gaps: list[dict]) -> list[dict]:
    try:
        _, body = wiki_reader.read_page_markdown(page_path)
        blocks = wiki_reader.source_blocks(body)
    except (FileNotFoundError, ValueError, OSError, UnicodeDecodeError):
        blocks = []
    questions = [f"用自己的话说明：{title} 的定义或目标，以及关键机制。"]
    for gap in gaps[:2]:
        questions.append(f"依据「{title}」的资料，回应这个待核对点：{gap['content']}")
    cards = []
    for question in questions:
        material = _card_source_material(blocks, title, question)
        source_suffix = f"（依据「{material['source_anchor']}」）" if material["reference_status"] == "source" else "（资料待核对）"
        cards.append({"question": f"{question}{source_suffix}", "answer": material["reference_excerpt"], **material})
    return cards


def create_session(
    page_path: str, explanation: str, elapsed_seconds: int = 0, persona: str = "feynman",
    evidence_keys: list[str] | None = None, uncertainty: str = "",
) -> dict:
    """创建一轮讲解会话，并保存本地诊断、追问和初始复习卡。"""
    explanation = explanation.strip()
    if not explanation:
        raise ValueError("请先写下你的回忆表达，再开始诊断。")
    # Earlier local clients did not send an evidence selection.  Preserve their
    # records while requiring the current UI to submit selected observable proof.
    evidence = (
        tutor.require_understanding_evidence(explanation, evidence_keys)
        if evidence_keys is not None else tutor.inspect_understanding_evidence(explanation)
    )
    uncertainty = uncertainty.strip()[:300]
    meta, body = wiki_reader.read_page_markdown(page_path)
    title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    initial_source = _card_source_material(
        wiki_reader.source_blocks(body), title,
        f"用自己的话说明：{title} 的定义或目标，以及关键机制。",
    )
    reference_excerpt = initial_source["reference_excerpt"] if initial_source["reference_status"] == "source" else ""
    gaps, question, diagnosis_source = tutor.diagnose(
        explanation, title, reference_excerpt, persona, uncertainty,
    )
    today = date.today()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (page_path, page_title, concept, status, duration_seconds, evidence_json, uncertainty, followup_stage) "
            "VALUES (?, ?, ?, 'gaps', ?, ?, ?, 'clarify')",
            (page_path, title, title, max(0, elapsed_seconds), json.dumps(evidence["selected"], ensure_ascii=False), uncertainty),
        )
        session_id = cur.lastrowid
        cur.execute("INSERT INTO turns (session_id, role, content) VALUES (?, 'user', ?)", (session_id, explanation.strip()))
        cur.execute(
            "INSERT INTO turns (session_id, role, content, kind, metadata_json) VALUES (?, 'tutor', ?, 'prompt', ?)",
            (session_id, question, json.dumps({"stage": "clarify"}, ensure_ascii=False)),
        )
        first_gap_id = None
        for gap in gaps:
            cur.execute(
                "INSERT INTO gaps (session_id, gap_type, content) VALUES (?, ?, ?)",
                (session_id, gap["gap_type"], gap["content"]),
            )
            if first_gap_id is None:
                first_gap_id = cur.lastrowid
        if first_gap_id is not None:
            cur.execute("UPDATE sessions SET target_gap_id = ? WHERE id = ?", (first_gap_id, session_id))
        for card in _cards_for(page_path, title, gaps):
            cur.execute(
                "INSERT INTO cards (session_id, question, answer, reference_status, source_anchor, source_index, source_context, "
                "source_context_before, source_context_after, source_fingerprint, required_points_json, reference_excerpt, due) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, card["question"], card["answer"], card["reference_status"], card["source_anchor"],
                    card["source_index"], card["source_context"], card["source_context_before"], card["source_context_after"], card["source_fingerprint"],
                    json.dumps(card["required_points"], ensure_ascii=False), card["reference_excerpt"], review_schedule.initial_due(today),
                ),
            )
    detail = session_detail(session_id)
    detail["diagnosis_source"] = diagnosis_source
    structure = tutor.explain_structure(explanation)
    detail["diagnosis"] = {
        "strengths": structure["strengths"],
        "checks": structure["checks"],
        "understanding_evidence": evidence,
        "uncertainty": uncertainty,
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
        turns = cur.execute(
            "SELECT id, role, content, kind, metadata_json, idempotency_key, created_at FROM turns WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        gaps = cur.execute(
            "SELECT id, gap_type, content, status, revision, practice_completed_at, retest_due, retest_prompt, retest_answer, retest_completed_at "
            "FROM gaps WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        cards = cur.execute(
            "SELECT cards.id, cards.question, cards.answer, cards.reference_status, cards.source_anchor, cards.source_index, "
            "cards.source_context, cards.source_context_before, cards.source_context_after, cards.source_fingerprint, "
            "cards.required_points_json, cards.reference_excerpt, cards.due, "
            "cards.interval, cards.reps, cards.micro_recall_id, sessions.page_path FROM cards JOIN sessions ON sessions.id = cards.session_id "
            "WHERE cards.session_id = ? ORDER BY cards.id", (session_id,)
        ).fetchall()
        feedback = cur.execute(
            "SELECT gap_id, verdict FROM diagnosis_feedback WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    gap_items = [_decorate_gap(row) for row in gaps]
    card_items = _decorate_cards(cards)
    session_item = dict(session)
    session_item["evidence_keys"] = _decode_json(session_item.pop("evidence_json", "[]"), [])
    turn_items = db.rows_to_dicts(turns)
    for turn in turn_items:
        turn["metadata"] = _decode_json(turn.pop("metadata_json", "{}"), {})
    latest_prompt = next((turn for turn in reversed(turn_items) if turn["role"] == "tutor"), None)
    latest_task = latest_prompt if latest_prompt and latest_prompt["kind"] in {"prompt", "followup_task"} else None
    user_keys = [turn["idempotency_key"] for turn in reversed(turn_items)
                 if turn["role"] == "user" and turn.get("idempotency_key")]
    response_keys = {turn.get("idempotency_key") for turn in turn_items if turn.get("idempotency_key")}
    pending_key = next((key for key in user_keys if f"{key}:response" not in response_keys), None)
    latest_feedback = next((turn["metadata"] for turn in reversed(turn_items)
                            if turn["role"] == "tutor" and turn["metadata"].get("feedback")), {})
    followup = {
        "stage": session_item.get("followup_stage", ""),
        "target_gap_id": session_item.get("target_gap_id"),
        "help_level": session_item.get("followup_help_level", 0),
        "task": latest_task["content"] if latest_task and session_item.get("followup_stage") not in {"complete", "limit"} else "",
        "pending_turn_id": pending_key,
        "feedback": latest_feedback.get("feedback", ""),
        "finding": latest_feedback.get("finding", ""),
        "source": latest_feedback.get("source", ""),
        "evidence_excerpt": latest_feedback.get("evidence_excerpt", ""),
        "active": session_item.get("followup_stage") not in {"", "complete"},
    }
    target_position = next((index for index, gap in enumerate(gaps) if gap["id"] == session_item.get("target_gap_id")), -1)
    source_card = card_items[min(len(card_items) - 1, target_position + 1)] if card_items and target_position >= 0 else card_items[0] if card_items else None
    if source_card:
        followup.update({
            "source_anchor": source_card.get("source_anchor", ""),
            "source_excerpt": source_card.get("reference_excerpt", ""),
            "source_index": source_card.get("source_index"),
            "source_status": source_card.get("current_source_status", "needs_review"),
            "source_label": source_card.get("current_source_label", "待核对"),
            "source_message": source_card.get("current_source_message", "没有可验证的原文定位。"),
        })
    return {
        "session": session_item, "turns": turn_items, "gaps": gap_items,
        "cards": card_items, "diagnosis_feedback": db.rows_to_dicts(feedback), "followup": followup,
    }


def _micro_recall_item(row) -> dict:
    item = dict(row)
    current = wiki_reader.source_status(
        item["page_path"], item["source_index"], item["source_excerpt"], item["source_fingerprint"],
        item["source_context_before"], item["source_context_after"],
    )
    item["current_source_status"] = current["status"]
    item["current_source_label"] = current["label"]
    item["current_source_message"] = current["message"]
    return item


def create_micro_recall(
    recall_uid: str, page_path: str, source_index: int, excerpt: str, question: str, answer: str = "",
) -> dict:
    try:
        UUID(recall_uid)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("问题编号格式不正确") from None
    question = question.strip()
    if not question or len(question) > 300 or len(answer) > 5000:
        raise ValueError("问题须为 1–300 个字符，回答最多 5000 个字符。")
    source = wiki_reader.capture_source_block(page_path, source_index, excerpt)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        existing = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall_uid,)).fetchone()
        if existing:
            if (existing["page_path"] != page_path or existing["source_index"] != source_index
                    or existing["source_excerpt"] != source["excerpt"] or existing["question"] != question):
                raise ValueError("该问题编号已用于其他草稿内容。")
            return _micro_recall_item(existing)
        open_item = cur.execute(
            "SELECT page_title FROM micro_recalls WHERE status IN ('draft', 'active') LIMIT 1",
        ).fetchone()
        if open_item:
            raise ValueError(f"当前已有未完成的问题；请先接续「{open_item['page_title']}」中的问题。")
        cur.execute(
            "INSERT INTO micro_recalls (recall_uid, page_path, page_title, source_index, source_heading, source_excerpt, "
            "source_context_before, source_context_after, source_fingerprint, question, answer, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
            (recall_uid, page_path, source["page_title"], source_index, source["heading"], source["excerpt"],
             source["context_before"], source["context_after"], source["fingerprint"], question, answer, now, now),
        )
        row = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall_uid,)).fetchone()
    return _micro_recall_item(row)


def get_micro_recall(recall_uid: str) -> dict:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall_uid,)).fetchone()
    if not row:
        raise LookupError("微回忆记录不存在")
    return _micro_recall_item(row)


def list_micro_recalls(page_path: str) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM micro_recalls WHERE page_path = ? ORDER BY updated_at DESC, id DESC LIMIT 20", (page_path,),
        ).fetchall()
    return [_micro_recall_item(row) for row in rows]


def update_micro_recall(recall_uid: str, question: str, answer: str) -> dict:
    question, answer = question.strip(), answer
    if not question or len(question) > 300 or len(answer) > 5000:
        raise ValueError("问题须为 1–300 个字符，回答最多 5000 个字符。")
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall_uid,)).fetchone()
        if not row:
            raise LookupError("微回忆记录不存在")
        if row["status"] == "draft":
            cur.execute(
                "UPDATE micro_recalls SET question = ?, answer = ?, updated_at = datetime('now', 'localtime') WHERE recall_uid = ?",
                (question, answer, recall_uid),
            )
        elif row["status"] == "active":
            if question != row["question"]:
                raise ValueError("首次作答后，问题已进入学习记录，不能再修改。")
            cur.execute(
                "UPDATE micro_recalls SET answer = ?, updated_at = datetime('now', 'localtime') WHERE recall_uid = ?",
                (answer, recall_uid),
            )
        else:
            raise ValueError("已完成的问题不能再修改。")
        row = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall_uid,)).fetchone()
    return _micro_recall_item(row)


def submit_micro_recall_answer(recall_uid: str, answer: str, idempotency_key: str) -> dict:
    answer = answer.strip()
    if not answer or len(answer) > 5000:
        raise ValueError("请先填写回答，最多 5000 个字符。")
    try:
        UUID(idempotency_key)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("提交编号格式不正确") from None
    item = get_micro_recall(recall_uid)
    if item["status"] == "draft":
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with db.cursor() as cur:
            cur.execute("BEGIN IMMEDIATE")
            current = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall_uid,)).fetchone()
            if current["status"] == "draft" and current["session_id"] is None:
                cur.execute(
                    "INSERT INTO sessions (page_path, page_title, concept, status, tutor_turns, followup_stage, uncertainty) "
                    "VALUES (?, ?, ?, 'gaps', 3, 'clarify', '')",
                    (current["page_path"], current["page_title"], current["page_title"]),
                )
                session_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO gaps (session_id, gap_type, content) VALUES (?, 'concept_missing', ?)",
                    (session_id, current["question"]),
                )
                gap_id = cur.lastrowid
                cur.execute("UPDATE sessions SET target_gap_id = ? WHERE id = ?", (gap_id, session_id))
                cur.execute(
                    "INSERT INTO turns (session_id, role, content, kind, metadata_json) VALUES (?, 'tutor', ?, 'prompt', ?)",
                    (session_id, current["question"], json.dumps({"stage": "clarify", "micro_recall_uid": recall_uid}, ensure_ascii=False)),
                )
                cur.execute(
                    "INSERT INTO cards (session_id, question, answer, reference_status, source_anchor, source_index, source_context, "
                    "source_context_before, source_context_after, source_fingerprint, micro_recall_id, required_points_json, reference_excerpt, due) "
                    "VALUES (?, ?, ?, 'source', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, current["question"], current["source_excerpt"], current["source_heading"] or current["page_title"],
                     current["source_index"], "\n".join(value for value in (current["source_context_before"], current["source_context_after"]) if value),
                     current["source_context_before"], current["source_context_after"], current["source_fingerprint"], current["id"], json.dumps([current["source_excerpt"]], ensure_ascii=False),
                     current["source_excerpt"], review_schedule.initial_due(date.today())),
                )
                cur.execute(
                    "UPDATE micro_recalls SET status = 'active', session_id = ?, answer = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'draft' AND session_id IS NULL",
                    (session_id, answer, now, current["id"]),
                )
            else:
                cur.execute("UPDATE micro_recalls SET answer = ? WHERE recall_uid = ?", (answer, recall_uid))
    else:
        with db.cursor() as cur:
            cur.execute("UPDATE micro_recalls SET answer = ?, updated_at = datetime('now', 'localtime') WHERE recall_uid = ?", (answer, recall_uid))
    item = get_micro_recall(recall_uid)
    if not item.get("session_id"):
        raise ValueError("无法恢复此问题对应的学习会话。")
    return advance_followup(item["session_id"], answer, idempotency_key)


def advance_followup(session_id: int, answer: str, idempotency_key: str) -> dict:
    answer = answer.strip()
    if not answer or len(answer) > 5000:
        raise ValueError("请先填写回答，最多 5000 个字符。")
    try:
        UUID(idempotency_key)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("提交编号格式不正确") from None
    response_key = f"{idempotency_key}:response"
    target_id = None
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        session = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("学习会话不存在")
        existing = cur.execute("SELECT * FROM turns WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing and (existing["session_id"] != session_id or existing["content"] != answer):
            raise ValueError("该提交编号已用于其他回答。")
        if cur.execute("SELECT 1 FROM turns WHERE idempotency_key = ?", (response_key,)).fetchone():
            return session_detail(session_id)
        if not existing:
            if session["followup_stage"] in {"", "complete", "limit"}:
                raise ValueError("当前会话没有可继续的追问任务。")
            prior_keys = cur.execute(
                "SELECT idempotency_key FROM turns WHERE session_id = ? AND role = 'user' "
                "AND kind = 'followup_answer' AND idempotency_key IS NOT NULL ORDER BY id DESC",
                (session_id,),
            ).fetchall()
            if any(not cur.execute(
                "SELECT 1 FROM turns WHERE idempotency_key = ?", (f"{row['idempotency_key']}:response",),
            ).fetchone() for row in prior_keys):
                raise ValueError("上一轮反馈尚未完成；使用原提交继续重试。")
            prompt = cur.execute(
                "SELECT * FROM turns WHERE session_id = ? AND role = 'tutor' AND kind IN ('prompt', 'followup_task') "
                "ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
            if not prompt:
                raise ValueError("会话没有可恢复的追问任务。")
            cur.execute(
                "INSERT OR IGNORE INTO turns (session_id, role, content, kind, metadata_json, idempotency_key) "
                "VALUES (?, 'user', ?, 'followup_answer', ?, ?)",
                (session_id, answer, json.dumps({"stage": session["followup_stage"], "help_level": session["followup_help_level"]}), idempotency_key),
            )
            existing = cur.execute("SELECT * FROM turns WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if not existing or existing["session_id"] != session_id or existing["content"] != answer:
                raise ValueError("该提交编号已用于其他回答。")
            cur.execute(
                "UPDATE micro_recalls SET answer = ?, updated_at = datetime('now', 'localtime') WHERE session_id = ?",
                (answer, session_id),
            )
            target = cur.execute("SELECT * FROM gaps WHERE id = ? AND session_id = ?", (session["target_gap_id"], session_id)).fetchone()
            if not target:
                target = cur.execute("SELECT * FROM gaps WHERE session_id = ? ORDER BY id LIMIT 1", (session_id,)).fetchone()
            target_id = target["id"] if target else None
            gap_text = target["content"] if target else session["page_title"]
            micro = cur.execute("SELECT * FROM micro_recalls WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
            card = None
            if micro:
                card = cur.execute("SELECT * FROM cards WHERE micro_recall_id = ?", (micro["id"],)).fetchone()
            if not card:
                gap_position = 0
                if target:
                    gap_position = cur.execute(
                        "SELECT COUNT(*) FROM gaps WHERE session_id = ? AND id < ?", (session_id, target["id"]),
                    ).fetchone()[0]
                card = cur.execute(
                    "SELECT * FROM cards WHERE session_id = ? ORDER BY id LIMIT 1 OFFSET ?",
                    (session_id, gap_position + (1 if target else 0)),
                ).fetchone()
                if not card:
                    card = cur.execute("SELECT * FROM cards WHERE session_id = ? ORDER BY id LIMIT 1", (session_id,)).fetchone()
            source_card = dict(card) if card else {}
            current_stage = session["followup_stage"]
            current_task = prompt["content"]
            source_status = wiki_reader.source_status(
                session["page_path"], source_card.get("source_index"), source_card.get("reference_excerpt", ""),
                source_card.get("source_fingerprint", ""), source_card.get("source_context_before", ""),
                source_card.get("source_context_after", ""),
            ) if source_card.get("reference_status") == "source" else {"status": "needs_review", "message": "沒有可驗證的摘录定位。"}
            reference_text = "\n".join((source_card.get("source_context") or "", source_card.get("reference_excerpt") or ""))
        else:
            target = cur.execute("SELECT * FROM gaps WHERE id = ? AND session_id = ?", (session["target_gap_id"], session_id)).fetchone()
            if not target:
                target = cur.execute("SELECT * FROM gaps WHERE session_id = ? ORDER BY id LIMIT 1", (session_id,)).fetchone()
            gap_text = target["content"] if target else session["page_title"]
            prompt = cur.execute(
                "SELECT * FROM turns WHERE session_id = ? AND role = 'tutor' AND kind IN ('prompt', 'followup_task') "
                "ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
            current_stage = session["followup_stage"]
            current_task = prompt["content"] if prompt else ""
            source_card = {}
            micro = cur.execute("SELECT * FROM micro_recalls WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
            if micro:
                source_card = dict(cur.execute("SELECT * FROM cards WHERE micro_recall_id = ?", (micro["id"],)).fetchone() or {})
            if not source_card:
                source_card = dict(cur.execute("SELECT * FROM cards WHERE session_id = ? ORDER BY id LIMIT 1", (session_id,)).fetchone() or {})
            source_status = wiki_reader.source_status(
                session["page_path"], source_card.get("source_index"), source_card.get("reference_excerpt", ""),
                source_card.get("source_fingerprint", ""), source_card.get("source_context_before", ""),
                source_card.get("source_context_after", ""),
            ) if source_card.get("reference_status") == "source" else {"status": "needs_review", "message": "没有可验证的摘录定位。"}
            reference_text = "\n".join((source_card.get("source_context") or "", source_card.get("reference_excerpt") or ""))
    if existing and not current_task:
        raise ValueError("会话没有可恢复的追问任务。")
    if existing and existing["role"] == "user":
        turn_stage = _decode_json(existing["metadata_json"], {}).get("stage")
        if turn_stage and turn_stage != current_stage:
            raise ValueError("原提交所属阶段已改变；重新载入当前任务后再提交。")

    try:
        assessment = tutor.assess_followup(
            answer=answer, question=current_task, gap=gap_text, stage=current_stage,
            source_excerpt=reference_text, reference_status=source_status["status"],
        )
    except Exception as exc:
        raise RuntimeError(f"追问反馈无效或暂不可用：{exc}") from exc
    finding = assessment["finding"]
    next_stage = finding
    if finding == "ambiguous":
        next_stage = "clarify"
        next_task = f"请具体说明“{gap_text[:100]}”涉及的对象，以及一个关键步骤或成立条件。"
    elif finding == "misconception":
        next_stage = "counterexample"
        next_task = f"资料依据：“{assessment['evidence_excerpt']}”。请依据这条内容重新判断：{gap_text[:100]}"
    elif finding == "stuck":
        next_stage = "hint"
        next_task = f"一级提示：先指出「{source_card.get('source_anchor') or session['page_title']}」中的对象，再说出一个关键步骤或条件。"
    else:
        if current_stage == "transfer":
            next_stage = "complete"
            next_task = ""
        else:
            next_stage = "transfer"
            next_task = assessment["task"]

    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        current = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if cur.execute("SELECT 1 FROM turns WHERE idempotency_key = ?", (response_key,)).fetchone():
            return session_detail(session_id)
        if current["followup_stage"] != current_stage:
            raise ValueError("追问阶段已改变；重新载入当前任务后再提交。")
        prompt_count = cur.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ? AND role = 'tutor' AND kind IN ('prompt', 'followup_task')",
            (session_id,),
        ).fetchone()[0]
        if next_stage not in {"complete", "limit"} and prompt_count >= current["tutor_turns"]:
            next_stage = "limit"
            next_task = ""
        feedback = assessment["feedback"]
        if next_stage == "limit":
            feedback += " 追问轮数已到上限；这个待核对点仍保留。"
        if source_status["status"] != "locatable":
            feedback += f" 原文状态：{source_status['message']}"
        help_level = max(current["followup_help_level"], 1 if finding == "stuck" else 0)
        response_kind = "followup_task" if next_task else "assessment"
        metadata = {
            "stage": next_stage, "finding": finding, "feedback": feedback,
            "evidence_excerpt": assessment["evidence_excerpt"], "source": assessment["source"],
            "source_status": source_status["status"], "help_level": help_level,
            "answer_turn_id": existing["id"],
        }
        cur.execute(
            "INSERT OR IGNORE INTO turns (session_id, role, content, kind, metadata_json, idempotency_key) VALUES (?, 'tutor', ?, ?, ?, ?)",
            (session_id, next_task or feedback, response_kind, json.dumps(metadata, ensure_ascii=False), response_key),
        )
        if cur.rowcount:
            cur.execute(
                "UPDATE sessions SET followup_stage = ?, followup_help_level = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (next_stage, help_level, session_id),
            )
            if next_stage == "complete":
                cur.execute("UPDATE gaps SET status = 'revised' WHERE id = ? AND status = 'open'", (target_id,))
                cur.execute("UPDATE sessions SET status = 'done' WHERE id = ?", (session_id,))
                cur.execute("UPDATE micro_recalls SET status = 'complete' WHERE session_id = ?", (session_id,))
    return session_detail(session_id)


def complete_session(session_id: int, explanation: str, elapsed_seconds: int = 0) -> dict:
    """Store a second, simpler expression and return an honest learning outcome."""
    explanation = explanation.strip()
    if not explanation:
        raise ValueError("请先写下第二次表达，再保存学习结果。")
    with db.cursor() as cur:
        session = cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise LookupError("学习会话不存在")
        meta, reference_html = wiki_reader.render_page_html(session["page_path"])
        title = meta.get("title") or session["page_title"]
        reevaluated_gaps, _, reevaluation_source = tutor.diagnose(
            explanation, title, reference_html, uncertainty=str(session["uncertainty"] or ""),
        )
        cur.execute("INSERT INTO turns (session_id, role, content, kind) VALUES (?, 'revision', ?, 'revision')", (session_id, explanation))
        cur.execute(
            "UPDATE gaps SET status = 'revised', revision = ? WHERE session_id = ? AND status = 'open'",
            (explanation, session_id),
        )
        for gap in reevaluated_gaps:
            cur.execute(
                "INSERT INTO gaps (session_id, gap_type, content) VALUES (?, ?, ?)",
                (session_id, gap["gap_type"], gap["content"]),
            )
        cur.execute(
            "UPDATE sessions SET status = 'done', followup_stage = 'complete', duration_seconds = MAX(duration_seconds, ?), "
            "updated_at = datetime('now', 'localtime') WHERE id = ?",
            (max(0, elapsed_seconds), session_id),
        )
        cur.execute("UPDATE micro_recalls SET status = 'complete' WHERE session_id = ? AND status = 'active'", (session_id,))
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
    recommended = contextual_recommendation(exclude_path=detail["session"]["page_path"])
    return {
        **detail,
        "outcome": {
            "strengths": second_structure["strengths"],
            "remaining_gaps": [gap for gap in detail["gaps"] if gap["status"] == "open"],
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


def _recent_gap_patterns(days: int = 14) -> dict[str, list[dict]]:
    """Recent correction needs, grouped by page; it is evidence, not a mastery score."""
    since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT sessions.page_path, gaps.gap_type, COUNT(*) AS total FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id "
            "WHERE substr(gaps.created_at, 1, 10) >= ? AND gaps.status != 'verified' "
            "GROUP BY sessions.page_path, gaps.gap_type",
            (since,),
        ).fetchall()
    result: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        item["gap_type"] = tutor.normalize_gap_type(item["gap_type"])
        result.setdefault(item["page_path"], []).append(item)
    return result


def _recommendation_candidates(*, exclude_path: str | None = None, limit: int = 4) -> list[dict]:
    workspace = config.get_workspace_settings()
    concepts = wiki_reader.scan_concepts()
    patterns = _recent_gap_patterns()
    level_score = {"unseen": 42, "read": 46, "recalled": 36, "checked": 16, "maintaining": 4}
    try:
        exam_days = (date.fromisoformat(workspace["exam_date"]) - date.today()).days if workspace.get("exam_date") else None
    except ValueError:
        exam_days = None
    candidates = []
    for concept in concepts:
        if concept["path"] == exclude_path:
            continue
        mastery_info = concept.get("mastery", {})
        level = mastery_info.get("level", "unseen")
        open_gaps = int(mastery_info.get("open_gaps") or 0)
        due_cards = int(mastery_info.get("due_cards") or 0)
        confidence = mastery_info.get("self_confidence")
        section_weight = int(workspace.get("section_weights", {}).get(concept.get("section", ""), 3))
        recent = patterns.get(concept["path"], [])
        recent_total = sum(int(item["total"]) for item in recent)
        score = level_score.get(level, 30) + open_gaps * 14 + due_cards * 18 + (section_weight - 1) * 7 + recent_total * 9
        if confidence:
            score += (6 - int(confidence)) * 7
        if workspace.get("learning_goal") == "presentation" and concept.get("importance") == "high":
            score += 16
        if workspace.get("learning_goal") == "exam":
            score += 8 if concept.get("importance") == "high" else 0
            if exam_days is not None:
                score += max(0, 28 - exam_days)
        reasons = []
        state_reason = {
            "unseen": "还没有学习证据",
            "read": "已阅读但未完成回忆表达",
            "recalled": "有回忆表达但尚未来源核对",
            "checked": "已核对，适合巩固保持",
            "maintaining": "正在保持中",
        }.get(level, "需要补充学习证据")
        reasons.append(state_reason)
        if open_gaps:
            reasons.append(f"有 {open_gaps} 个待修复盲区")
        if confidence:
            reasons.append(f"自评把握度 {confidence}/5")
        else:
            reasons.append("尚未标记把握度")
        if recent:
            labels = "、".join(dict.fromkeys(tutor.gap_type_meta(item["gap_type"])["label"] for item in recent))
            reasons.append(f"近两周出现过{labels}")
        if section_weight >= 4:
            reasons.append(f"章节权重 {section_weight}/5")
        if workspace.get("learning_goal") == "exam" and exam_days is not None:
            reasons.append(f"距考试 {exam_days} 天")
        estimated = min(22, 6 + min(open_gaps, 2) * 3 + (2 if level in {"unseen", "read"} else 0))
        available = int(workspace.get("available_minutes") or 60)
        benefit = (
            f"完成后会留下{('一次回忆表达' if level in {'unseen', 'read'} else '一次可核对的复述')}，"
            f"并把下一步缩小为{'盲区微练习' if open_gaps else '一次间隔复习'}。"
        )
        candidates.append({
            "path": concept["path"], "title": concept["title"], "section": concept.get("section", ""),
            "score": score, "why": "；".join(reasons) + "。", "recommendation_reason": "；".join(reasons) + "。",
            "estimated_minutes": estimated, "fits_today": estimated <= available, "available_minutes": available,
            "benefit": benefit, "learning_goal": workspace.get("learning_goal"),
        })
    return sorted(candidates, key=lambda item: (-item["score"], item["title"], item["path"]))[:limit]


def contextual_recommendation(*, exclude_path: str | None = None) -> dict | None:
    if exclude_path:
        notes = learning_recommendations.recommendations(exclude_path)["notes"]
        return {**notes[0], "recommendation_reason": notes[0]["why"]} if notes else None
    candidates = _recommendation_candidates(exclude_path=exclude_path, limit=1)
    return candidates[0] if candidates else None


def get_learning_focus() -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM learning_focus WHERE id = 1").fetchone()
    return dict(row) if row else None


def _focus_reflection(cur, focus: dict) -> bool:
    content = f"学习问题：{focus['question']}\n\n理解变化与下次起点：\n{focus['checkpoint'] or '尚未记录'}"
    if not cur.execute(
        "SELECT 1 FROM reflections WHERE source = 'focus' AND page_path = ? AND content = ?",
        (focus["page_path"], content),
    ).fetchone():
        cur.execute(
            "INSERT INTO reflections (page_path, page_title, source, content) VALUES (?, ?, 'focus', ?)",
            (focus["page_path"], focus["page_title"], content),
        )
        return True
    return False


def save_learning_focus(
    page_path: str, question: str, checkpoint: str = "", available_minutes: int = 60,
    expected_updated_at: str | None = None,
) -> dict:
    question, checkpoint = question.strip(), checkpoint.strip()
    if not question or len(question) > 300 or len(checkpoint) > 3000 or not 5 <= available_minutes <= 480:
        raise ValueError("请填写具体学习问题，并将参考时间设为 5 到 480 分钟。")
    meta, _ = wiki_reader.render_page_html(page_path)
    title = meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md")
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT * FROM learning_focus WHERE id = 1").fetchone()
        if (row["updated_at"] if row else None) != expected_updated_at:
            raise RuntimeError("学习重点已在其他页面更新。重新载入后再保存；当前输入仍保留。")
        if row and row["page_path"] != page_path:
            _focus_reflection(cur, dict(row))
        cur.execute(
            "INSERT INTO learning_focus (id, page_path, page_title, question, checkpoint, available_minutes, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "page_path = excluded.page_path, page_title = excluded.page_title, question = excluded.question, "
            "checkpoint = excluded.checkpoint, available_minutes = excluded.available_minutes, updated_at = excluded.updated_at",
            (page_path, title, question, checkpoint, available_minutes, datetime.now().isoformat(timespec="microseconds")),
        )
        saved = dict(cur.execute("SELECT * FROM learning_focus WHERE id = 1").fetchone())
    return saved


def archive_learning_focus(expected_updated_at: str) -> dict:
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT * FROM learning_focus WHERE id = 1").fetchone()
        if not row or row["updated_at"] != expected_updated_at:
            raise RuntimeError("学习重点已更新。重新载入后再归档。")
        _focus_reflection(cur, dict(row))
        cur.execute("DELETE FROM learning_focus WHERE id = 1")
    return {"focus": None, "message": "已保存到学习心得，可以开始下一个问题。"}


def today_action() -> dict:
    """Return one transparent action based on evidence and user-supplied study context."""
    workspace = config.get_workspace_settings()
    if not workspace["configured"]:
        return {
            "type": "configure", "title": "先连接你的学习资料",
            "detail": "选择一个包含 pages 的本地 Wiki，或先用两分钟示例体验一次回忆表达。",
        }
    concepts = wiki_reader.scan_concepts()
    available_paths = {item["path"] for item in concepts}
    with db.cursor() as cur:
        draft = cur.execute(
            "SELECT * FROM micro_recalls WHERE status = 'draft' ORDER BY updated_at DESC, id DESC LIMIT 1",
        ).fetchone()
    if draft and draft["page_path"] in available_paths:
        return {
            "type": "micro_recall", "title": draft["question"], "page_path": draft["page_path"],
            "recall_uid": draft["recall_uid"], "source_index": draft["source_index"],
            "detail": f"接续「{draft['source_heading'] or draft['page_title']}」中的未完成问题。",
            "estimated_minutes": 3, "reason": "saved_micro_recall",
        }
    with db.cursor() as cur:
        active_session = cur.execute(
            "SELECT sessions.id, sessions.page_path, sessions.page_title, sessions.followup_stage, sessions.target_gap_id, "
            "gaps.content AS gap FROM sessions LEFT JOIN gaps ON gaps.id = sessions.target_gap_id "
            "WHERE sessions.followup_stage IN ('clarify', 'counterexample', 'hint', 'transfer', 'limit') "
            "ORDER BY sessions.updated_at DESC, sessions.id DESC LIMIT 1",
        ).fetchone()
    if active_session and active_session["page_path"] in available_paths:
        stage_labels = {"clarify": "澄清", "counterexample": "反例核对", "hint": "一级提示", "transfer": "迁移检验", "limit": "保留未解决点"}
        return {
            "type": "follow_up", "title": f"继续追问：{active_session['page_title']}",
            "detail": f"当前阶段：{stage_labels[active_session['followup_stage']]}。{active_session['gap'] or '继续处理当前问题。'}",
            "session_id": active_session["id"], "page_path": active_session["page_path"],
            "estimated_minutes": 3, "reason": "saved_followup",
        }
    with db.cursor() as cur:
        attempts = cur.execute(
            "SELECT attempt_uid, page_path, page_title, task_key FROM practice_attempts "
            "WHERE kind = 'builtin' AND submitted_at IS NULL ORDER BY created_at DESC, id DESC",
        ).fetchall()
    active_practice = next((row for row in attempts if row["page_path"] in available_paths), None)
    if active_practice:
        attempt = practice_sessions.get_attempt(active_practice["attempt_uid"])
        task_title = attempt["task"].get("title") or active_practice["task_key"]
        return {
            "type": "practice_resume", "title": f"继续自测：{task_title}",
            "detail": f"「{active_practice['page_title']}」中有一道未提交题目，已保存的答案会一并恢复。",
            "page_path": active_practice["page_path"], "attempt_uid": active_practice["attempt_uid"],
            "estimated_minutes": attempt["task"].get("estimated_minutes", 3), "reason": "saved_practice",
        }
    focus = get_learning_focus()
    if focus and any(item["path"] == focus["page_path"] for item in wiki_reader.scan_concepts()):
        return {
            "type": "focus", "title": focus["question"], "page_path": focus["page_path"],
            "detail": focus["checkpoint"] or "继续阅读、回忆或应用，按这个问题的进展决定下一步。",
            "estimated_minutes": focus["available_minutes"], "reason": "saved_learning_focus",
        }
    due_cards = list_due_cards(1)
    if due_cards:
        summary = review_summary()
        if not summary["today_plan_count"]:
            return {
                "type": "review_complete", "title": "今天的复习计划已完成",
                "detail": f"今天已完成 {summary['completed']} 张。让记忆先间隔，明天再继续。",
                "reason": "daily_review_goal_reached", "estimated_minutes": 0,
                "benefit": "已完成的卡片会按本次判断安排下一次回忆。",
            }
        next_minutes = summary["next_card_estimated_minutes"] or 2
        return {
            "type": "review", "title": "先完成今天的下一张复习",
            "detail": f"今天计划 {summary['today_plan_count'] + summary['completed']} 张，已完成 {summary['completed']} 张。先用约 {next_minutes} 分钟完成这一张。",
            "reason": "scheduled_review", "estimated_minutes": min(summary["estimated_minutes"], workspace["available_minutes"]),
            "benefit": "完成后会更新间隔安排，并为保持状态留下新的回忆证据。",
        }
    with db.cursor() as cur:
        session = cur.execute(
            "SELECT id, page_path, page_title FROM sessions WHERE status != 'done' ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
    if session:
        return {
            "type": "continue", "title": f"继续处理：{session['page_title']}",
            "detail": "上次已经完成第一次表达。现在补充并用更简单的话再讲一次。",
            "session_id": session["id"], "page_path": session["page_path"], "estimated_minutes": 6,
            "benefit": "完成后会留下第二次表达与待修复盲区，方便后续核对。",
        }
    choices = _recommendation_candidates(limit=4)
    if choices:
        concept = choices[0]
        alternatives = choices[1:]
        return {
            "type": "start", "title": f"从「{concept['title']}」开始",
            "detail": f"为什么是它：{concept['why']} 预计 {concept['estimated_minutes']} 分钟。完成后：{concept['benefit']}",
            "page_path": concept["path"], "reason": "contextual_evidence", "learning_goal": workspace.get("learning_goal"),
            "estimated_minutes": concept["estimated_minutes"], "benefit": concept["benefit"], "why": concept["why"],
            "alternatives": [{key: item[key] for key in ("path", "title", "why", "estimated_minutes", "benefit")} for item in alternatives],
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
        card["required_points"] = _decode_json(card.pop("required_points_json", "[]"), [])
        status = card.get("reference_status") or "legacy"
        card["reference_status"] = status
        current = wiki_reader.source_status(
            card.get("page_path", ""), card.get("source_index"), card.get("reference_excerpt", ""),
            card.get("source_fingerprint", ""), card.get("source_context_before", ""), card.get("source_context_after", ""),
        ) if status == "source" else {
            "status": "needs_review", "label": "待核对", "message": "这条记录没有可验证的原文定位。",
        }
        card["current_source_status"] = current["status"]
        card["current_source_label"] = current["label"]
        card["current_source_message"] = current["message"]
        card["reference_label"] = {
            "source": f"Wiki 摘录 · {card.get('source_anchor') or '当前资料'}",
            "needs_review": "资料待核对",
            "legacy": "旧卡待回原文核对",
        }.get(status, "资料待核对")
        card["can_show_reference"] = status == "source" and bool(card.get("reference_excerpt"))
        card["can_verify_reference"] = card["can_show_reference"] and current["status"] == "locatable"
        cards.append(card)
    return cards


def list_due_cards(limit: int = 20) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT cards.*, sessions.page_title, sessions.page_path FROM cards JOIN sessions ON sessions.id = cards.session_id "
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
            "SELECT cards.*, sessions.page_title, sessions.page_path FROM cards JOIN sessions ON sessions.id = cards.session_id "
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
    goal = settings["daily_review_goal"]
    remaining_goal = max(0, goal - completed)
    today_plan = cards[:remaining_goal]
    return {
        "total": len(cards),
        "completed": completed,
        "goal": goal,
        "remaining_goal": remaining_goal,
        "today_plan_count": len(today_plan),
        "today_plan_estimated_minutes": sum(card["estimated_minutes"] for card in today_plan),
        "next_card_estimated_minutes": today_plan[0]["estimated_minutes"] if today_plan else None,
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
    if len(answer) < MIN_REVIEW_ANSWER_LENGTH:
        raise ValueError(f"请至少写 {MIN_REVIEW_ANSWER_LENGTH} 个字符，再交给复习教练检查。")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT cards.*, sessions.page_title, sessions.page_path FROM cards "
            "JOIN sessions ON sessions.id = cards.session_id WHERE cards.id = ?", (card_id,),
        ).fetchone()
    if not row:
        raise LookupError("复习卡不存在")
    card = dict(row)
    current = wiki_reader.source_status(
        card["page_path"], card.get("source_index"), card.get("reference_excerpt", ""),
        card.get("source_fingerprint", ""), card.get("source_context_before", ""), card.get("source_context_after", ""),
    ) if card.get("reference_status") == "source" else {"status": "needs_review", "message": "无可验证定位"}
    reference_html = "\n".join((card.get("source_context") or "", card.get("reference_excerpt") or ""))
    required_points = _decode_json(card.get("required_points_json"), [])
    verdict, feedback, follow_up, source, evidence_level = review_coach.assess(
        answer, question=card["question"], expected=card["answer"], required_points=required_points,
        reference_status="source" if current["status"] == "locatable" else "needs_review", title=card["page_title"],
        reference_html=reference_html, agent=agent,
    )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO review_attempts (card_id, agent, answer, verdict, feedback, follow_up, source, evidence_level) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (card_id, agent, answer, verdict, feedback, follow_up, source, evidence_level),
        )
        attempt_id = cur.lastrowid
    return {
        "id": attempt_id, "card_id": card_id, "agent": agent, "agent_name": review_coach.agent_profile(agent)["name"],
        "verdict": verdict, "feedback": feedback, "follow_up": follow_up, "source": source,
        "evidence_level": evidence_level, "reference_status": card.get("reference_status") or "legacy",
        "current_source_status": current["status"], "current_source_message": current["message"],
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


def review_knowledge_update(update_id: int, proposal: str) -> dict:
    row = _knowledge_update_row(update_id)
    clean = proposal.strip()
    if row["status"] != "draft" or not clean:
        raise ValueError("只能审核尚未处理的非空草稿。")
    raw = wiki_reader.raw_evidence_for_page(row["page_path"], clean)
    verdict = tutor.review_wiki_proposal(content=clean, raw_evidence=raw)
    kind = "verified" if verdict["verdict"] == "supported" else "tentative"
    preview = None
    if verdict["verdict"] != "problematic":
        preview = wiki_writer.preview_reviewed_update(
            row["page_path"], clean, update_id, kind=kind, citations=verdict["citations"],
        )
    analysis = _decode_json(row["analysis_json"], {})
    analysis["review"] = {**verdict, "content": clean, "target_path": row["page_path"],
                          "kind": kind if preview else None, "base_hash": preview["base_hash"] if preview else None,
                          "diff": preview["diff"] if preview else "", "raw_evidence": raw}
    with db.cursor() as cur:
        cur.execute("UPDATE knowledge_updates SET proposal = ?, analysis_json = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (clean, json.dumps(analysis, ensure_ascii=False), update_id))
    return get_knowledge_update(update_id)


def apply_knowledge_update(
    update_id: int, *, target_mode: str, proposal: str, proposed_title: str = "",
) -> dict:
    """Apply an approved proposal with a full pre-write snapshot for later undo."""
    if target_mode not in {"append_current", "append_pending", "keep_local"}:
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
    review = _decode_json(row["analysis_json"], {}).get("review") or {}
    expected_mode = "append_current" if review.get("kind") == "verified" else "append_pending"
    if (review.get("verdict") == "problematic" or review.get("content") != clean_proposal
            or review.get("target_path") != row["page_path"] or target_mode != expected_mode
            or not review.get("base_hash")):
        raise ValueError("草稿尚未通过最终审核，或审核后被修改；请重新审核。")
    if wiki_reader.raw_evidence_for_page(row["page_path"], clean_proposal) != review.get("raw_evidence"):
        raise ValueError("原始资料已变化，请重新审核草稿。")
    change = wiki_writer.apply_reviewed_update(
        row["page_path"], clean_proposal, update_id, kind=review["kind"],
        citations=review.get("citations", []), expected_hash=review["base_hash"],
    )
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO wiki_revisions (knowledge_update_id, page_path, before_content, after_content, created_page, change_json) VALUES (?, ?, ?, ?, ?, ?)",
                (update_id, change["path"], change["before_content"], change["after_content"],
                 int(change["created_page"]), json.dumps(change["manifest"], ensure_ascii=False)),
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
                created_page=bool(change["created_page"]), manifest=change["manifest"],
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
        created_page=bool(revision["created_page"]), manifest=_decode_json(revision["change_json"], {}),
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
    return [_decorate_gap(row) for row in rows]


def revise_gap(gap_id: int, revision: str) -> dict:
    revision = revision.strip()
    if len(revision) < MIN_GAP_REVISION_LENGTH:
        raise ValueError(f"请至少写 {MIN_GAP_REVISION_LENGTH} 个字符，说明你如何补全这个问题。")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
        if not row:
            raise LookupError("待澄清点不存在")
        gap = dict(row)
    gap_type = tutor.normalize_gap_type(gap["gap_type"])
    retest_due = (date.today() + timedelta(days=1)).isoformat()
    retest_prompt = tutor.gap_type_meta(gap_type)["retest_prompt"]
    with db.cursor() as cur:
        cur.execute(
            "UPDATE gaps SET revision = ?, status = 'revised', practice_completed_at = datetime('now', 'localtime'), "
            "retest_due = ?, retest_prompt = ?, retest_answer = NULL, retest_completed_at = NULL WHERE id = ?",
            (revision, retest_due, retest_prompt, gap_id),
        )
        cur.execute("UPDATE sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?", (gap["session_id"],))
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('gap_practiced', ?, ?)",
            (gap["page_path"], gap_id),
        )
        updated = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
    result = _decorate_gap(updated)
    result.update({
        "feedback": f"两分钟微练习已保存。明天（{retest_due}）会换一个情境再测；第一次补充不会被当成已核对答案。",
        "assessment_source": "learning_path",
    })
    return result


def retest_gap(gap_id: int, answer: str) -> dict:
    """Check the second, deliberately different-situation response against the source."""
    answer = answer.strip()
    if len(answer) < MIN_GAP_REVISION_LENGTH:
        raise ValueError(f"请至少写 {MIN_GAP_REVISION_LENGTH} 个字符，说明新情境下如何判断。")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
        if not row:
            raise LookupError("待澄清点不存在")
        gap = dict(row)
    if not gap.get("practice_completed_at"):
        raise ValueError("请先完成两分钟微练习，再进行异情境复测。")
    due = str(gap.get("retest_due") or "")
    if due and due > date.today().isoformat():
        raise ValueError(f"异情境复测将在 {due} 开放，先让记忆间隔一天。")
    try:
        _, reference_html = wiki_reader.render_page_html(gap["page_path"])
    except (FileNotFoundError, ValueError):
        reference_html = ""
    status, feedback, source = tutor.assess_gap_revision(
        answer, gap["content"], gap["page_title"], reference_html, gap.get("retest_prompt") or "",
    )
    with db.cursor() as cur:
        cur.execute(
            "UPDATE gaps SET retest_answer = ?, retest_completed_at = datetime('now', 'localtime'), status = ? WHERE id = ?",
            (answer, status, gap_id),
        )
        cur.execute("UPDATE sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?", (gap["session_id"],))
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('gap_retested', ?, ?)",
            (gap["page_path"], gap_id),
        )
        if status == "verified":
            cur.execute(
                "INSERT INTO learning_events (event_type, page_path, entity_id) VALUES ('gap_verified', ?, ?)",
                (gap["page_path"], gap_id),
            )
        updated = cur.execute(
            "SELECT gaps.*, sessions.page_path, sessions.page_title FROM gaps "
            "JOIN sessions ON sessions.id = gaps.session_id WHERE gaps.id = ?", (gap_id,),
        ).fetchone()
    result = _decorate_gap(updated)
    result.update({"feedback": feedback, "assessment_source": source})
    return result


def set_self_confidence(page_path: str, confidence: int) -> dict:
    """Persist a learner-owned confidence signal; it influences ordering, never mastery labels."""
    if not 1 <= confidence <= 5:
        raise ValueError("把握度需要在 1 到 5 之间")
    _validate_page(page_path)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO self_assessments (page_path, confidence, updated_at) VALUES (?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(page_path) DO UPDATE SET confidence = excluded.confidence, updated_at = excluded.updated_at",
            (page_path, confidence),
        )
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path) VALUES ('confidence_set', ?)", (page_path,)
        )
    return {"page_path": page_path, "confidence": confidence}


def export_learning_data() -> dict:
    """导出工作台学习记录与已确认写入的安全回档快照，不改写 Wiki 原文。"""
    tables = (
        "notes", "reflections", "sessions", "turns", "gaps", "cards", "reviews",
        "review_attempts", "learning_events", "diagnosis_feedback", "knowledge_updates", "self_assessments",
        "wiki_revisions", "practice_attempts", "learning_focus", "micro_recalls",
        "learning_lab_progress", "learning_lab_attempts", "learning_lab_rewards",
    )
    order_by = {
        "self_assessments": "page_path",
        "learning_lab_progress": "course_id, course_version, mission_key, mission_version",
        "learning_lab_attempts": "attempt_uid",
        "learning_lab_rewards": "reward_key",
    }
    with db.cursor() as cur:
        payload = {
            table: db.rows_to_dicts(cur.execute(f"SELECT * FROM {table} ORDER BY {order_by.get(table, 'id')}").fetchall())
            for table in tables
        }
    return {"format": "feynman-workbench-export", "version": 9, "exported_at": datetime.now().isoformat(timespec="seconds"), **payload}


def import_learning_data(payload: dict, *, dry_run: bool = False) -> dict:
    """Merge a user-selected local export without deleting current-device records.

    Sessions have no globally stable identity, so their natural key is page, title and
    creation time. Notes use their page path and only accept a strictly newer remote
    version. This keeps an import additive and safe enough for a local-first workflow.
    """
    if not isinstance(payload, dict) or payload.get("format") != "feynman-workbench-export":
        raise ValueError("这不是费曼学习工作台导出的学习数据")
    version = payload.get("version")
    if type(version) is not int or version not in {1, 2, 3, 4, 5, 6, 7, 8, 9}:
        raise ValueError("暂不支持该导出版本")
    names = (
        "notes", "reflections", "sessions", "turns", "gaps", "cards", "reviews",
        "review_attempts", "learning_events", "diagnosis_feedback", "knowledge_updates", "self_assessments",
        "wiki_revisions", "practice_attempts", "learning_focus", "micro_recalls",
        "learning_lab_progress", "learning_lab_attempts", "learning_lab_rewards",
    )
    source = {name: payload.get(name, []) for name in names}
    if any(not isinstance(value, list) for value in source.values()):
        raise ValueError("导出数据的结构不正确")
    learning_lab.validate_import_rows(
        source["learning_lab_progress"], source["learning_lab_attempts"], source["learning_lab_rewards"],
    )
    for item in source["micro_recalls"]:
        path = item.get("page_path") if isinstance(item, dict) else None
        try:
            UUID(item.get("recall_uid", ""))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("备份中的微回忆编号格式不正确。") from None
        if (
            not isinstance(path, str) or not path or len(path) > 2000 or Path(path).is_absolute()
            or ".." in Path(path).parts or Path(path).suffix != ".md"
            or type(item.get("source_index")) is not int or item["source_index"] < 0
            or not isinstance(item.get("page_title"), str) or len(item["page_title"]) > 2000
            or not isinstance(item.get("source_excerpt"), str) or not item["source_excerpt"] or len(item["source_excerpt"]) > 5000
            or not isinstance(item.get("question"), str) or not item["question"].strip() or len(item["question"]) > 300
            or not isinstance(item.get("answer"), str) or len(item["answer"]) > 5000
            or not isinstance(item.get("source_fingerprint"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["source_fingerprint"])
            or item.get("status") not in {"draft", "active", "complete"}
        ):
            raise ValueError("备份中的微回忆内容格式不正确。")
    focus_rows = source["learning_focus"]
    if len(focus_rows) > 1:
        raise ValueError("备份中只能有一个当前学习重点。")
    if focus_rows:
        focus = focus_rows[0]
        limits = {"page_path": 2000, "page_title": 2000, "question": 300, "checkpoint": 3000, "updated_at": 40}
        if not isinstance(focus, dict) or any(
            not isinstance(focus.get(key), str) or len(focus[key]) > limit
            or (key != "checkpoint" and not focus[key].strip()) for key, limit in limits.items()
        ):
            raise ValueError("备份中的学习问题或断点格式不正确。")
        if type(focus.get("available_minutes")) is not int or not 5 <= focus["available_minutes"] <= 480:
            raise ValueError("备份中的参考时间应为 5 到 480 分钟。")
        try:
            datetime.fromisoformat(focus["updated_at"])
        except ValueError:
            raise ValueError("备份中的学习重点更新时间不正确。") from None
    for revision in source["wiki_revisions"]:
        try:
            manifest = json.loads(revision.get("change_json", "{}"))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("备份中的 Wiki 撤销信息格式不正确。") from None
        if not isinstance(manifest, dict) or (manifest and any(
            not isinstance(manifest.get(key), str) for key in ("wiki_root", "index_before", "index_after", "kind")
        )):
            raise ValueError("备份中的 Wiki 撤销信息不完整。")
    counts = {
        "notes": 0, "reflections": 0, "sessions": 0, "cards": 0, "events": 0,
        "knowledge_updates": 0, "wiki_revisions": 0, "self_assessments": 0,
        "practice_attempts": 0, "learning_focus": 0, "micro_recalls": 0,
        "learning_lab_progress": 0, "learning_lab_attempts": 0, "learning_lab_rewards": 0,
    }
    with db.cursor() as cur:
        practice_preview = practice_sessions.import_preview(cur, source["practice_attempts"])
        learning_lab_preview = learning_lab.import_preview(
            cur, source["learning_lab_progress"], source["learning_lab_attempts"], source["learning_lab_rewards"],
        )
    if dry_run:
        return {
            "dry_run": True,
            "incoming": {name: len(value) for name, value in source.items()},
            "practice": practice_preview,
            "learning_lab": learning_lab_preview,
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
        target_gap_sources: dict[int, int] = {}
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
            followup_stage = str(session.get("followup_stage") or "")
            if followup_stage not in {"", "clarify", "counterexample", "hint", "transfer", "complete", "limit"}:
                followup_stage = ""
            try:
                help_level = max(0, min(3, int(session.get("followup_help_level") or 0)))
            except (TypeError, ValueError):
                help_level = 0
            target_gap = session.get("target_gap_id")
            if isinstance(target_gap, int):
                target_gap_sources[old_id] = target_gap
            cur.execute(
                "INSERT INTO sessions (page_path, page_title, concept, status, tutor_turns, duration_seconds, evidence_json, uncertainty, "
                "followup_stage, followup_help_level, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (page_path, page_title, str(session.get("concept") or page_title),
                 str(session.get("status") or "done"), int(session.get("tutor_turns") or 3), int(session.get("duration_seconds") or 0),
                 json.dumps(session.get("evidence_keys", session.get("evidence_json", [])), ensure_ascii=False)
                 if not isinstance(session.get("evidence_json"), str) else str(session.get("evidence_json") or "[]"),
                 str(session.get("uncertainty") or "")[:300],
                 followup_stage, help_level,
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
                metadata = turn.get("metadata_json", "{}")
                if not isinstance(metadata, str):
                    metadata = json.dumps(metadata, ensure_ascii=False)
                key = turn.get("idempotency_key")
                cur.execute(
                    "INSERT OR IGNORE INTO turns (session_id, role, content, kind, metadata_json, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_session, str(turn.get("role") or "user"), str(turn["content"]),
                     str(turn.get("kind") or "message"), metadata, key, str(turn.get("created_at") or datetime.now().isoformat(timespec="seconds"))),
                )
        gap_map: dict[int, int] = {}
        for gap in source["gaps"]:
            new_session = session_map.get(gap.get("session_id")) if gap.get("session_id") in new_source_session_ids else None
            if new_session and str(gap.get("content", "")).strip():
                cur.execute(
                    "INSERT INTO gaps (session_id, gap_type, content, status, revision, practice_completed_at, retest_due, retest_prompt, retest_answer, retest_completed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_session, str(gap.get("gap_type") or "vague"), str(gap["content"]),
                     str(gap.get("status") or "open"), gap.get("revision"), gap.get("practice_completed_at"),
                     gap.get("retest_due"), str(gap.get("retest_prompt") or "")[:1000], gap.get("retest_answer"),
                     gap.get("retest_completed_at")),
                )
                if isinstance(gap.get("id"), int):
                    gap_map[gap["id"]] = cur.lastrowid
        for old_session_id, old_gap_id in target_gap_sources.items():
            new_gap_id = gap_map.get(old_gap_id)
            if new_gap_id and old_session_id in new_source_session_ids:
                cur.execute("UPDATE sessions SET target_gap_id = ? WHERE id = ?", (new_gap_id, session_map[old_session_id]))
        micro_recall_map: dict[int, int] = {}
        for recall in source["micro_recalls"]:
            new_session = session_map.get(recall.get("session_id")) if isinstance(recall.get("session_id"), int) else None
            if recall.get("status") in {"active", "complete"} and not new_session:
                continue
            existing = cur.execute("SELECT * FROM micro_recalls WHERE recall_uid = ?", (recall["recall_uid"],)).fetchone()
            if existing:
                if any(existing[key] != recall.get(key) for key in (
                    "page_path", "source_index", "source_excerpt", "source_fingerprint", "question", "answer", "status",
                )):
                    raise ValueError("备份中的微回忆编号与现有记录冲突。")
                if isinstance(recall.get("id"), int):
                    micro_recall_map[recall["id"]] = existing["id"]
                continue
            cur.execute(
                "INSERT INTO micro_recalls (recall_uid, page_path, page_title, source_index, source_heading, source_excerpt, "
                "source_context_before, source_context_after, source_fingerprint, question, answer, status, session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (recall["recall_uid"], recall["page_path"], recall["page_title"], recall["source_index"],
                 str(recall.get("source_heading") or "")[:300], recall["source_excerpt"],
                 str(recall.get("source_context_before") or "")[:5000], str(recall.get("source_context_after") or "")[:5000],
                 recall["source_fingerprint"], recall["question"], recall["answer"], recall["status"], new_session,
                 str(recall.get("created_at") or datetime.now().isoformat(timespec="seconds")),
                 str(recall.get("updated_at") or recall.get("created_at") or datetime.now().isoformat(timespec="seconds"))),
            )
            new_recall_id = cur.lastrowid
            if isinstance(recall.get("id"), int):
                micro_recall_map[recall["id"]] = new_recall_id
            counts["micro_recalls"] += 1
        for card in source["cards"]:
            recall_id = micro_recall_map.get(card.get("micro_recall_id")) if isinstance(card.get("micro_recall_id"), int) else None
            old_session = card.get("session_id")
            new_session = session_map.get(old_session) if isinstance(old_session, int) and (
                old_session in new_source_session_ids or recall_id is not None
            ) else None
            old_id = card.get("id")
            if not new_session or not isinstance(old_id, int) or not str(card.get("question", "")).strip():
                continue
            if recall_id:
                existing_card = cur.execute("SELECT id FROM cards WHERE micro_recall_id = ?", (recall_id,)).fetchone()
                if existing_card:
                    card_map[old_id] = existing_card["id"]
                    continue
            cur.execute(
                "INSERT INTO cards (session_id, question, answer, reference_status, source_anchor, source_index, source_context, "
                "source_context_before, source_context_after, source_fingerprint, micro_recall_id, required_points_json, reference_excerpt, interval, ease, due, reps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_session, str(card["question"]), str(card.get("answer") or ""),
                 str(card.get("reference_status") or "legacy"), str(card.get("source_anchor") or "")[:200],
                 card.get("source_index"), str(card.get("source_context") or "")[:10000],
                 str(card.get("source_context_before") or "")[:5000], str(card.get("source_context_after") or "")[:5000],
                 str(card.get("source_fingerprint") or ""), recall_id,
                 json.dumps(card.get("required_points", card.get("required_points_json", [])), ensure_ascii=False)
                 if not isinstance(card.get("required_points_json"), str) else str(card.get("required_points_json") or "[]"),
                 str(card.get("reference_excerpt") or "")[:5000], int(card.get("interval") or 0), float(card.get("ease") or 2.5),
                 str(card.get("due") or date.today().isoformat()), int(card.get("reps") or 0)),
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
                    "INSERT INTO review_attempts (card_id, agent, answer, verdict, feedback, follow_up, source, evidence_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_card, str(attempt.get("agent") or "feynman"), str(attempt["answer"]),
                     str(attempt.get("verdict") or "retry"), str(attempt.get("feedback") or ""),
                     str(attempt.get("follow_up") or ""), str(attempt.get("source") or "local"),
                     str(attempt.get("evidence_level") or "unverified")),
                )
        for event in source["learning_events"]:
            path = str(event.get("page_path", ""))
            if path:
                event_type = str(event.get("event_type") or "imported")
                if event_type == "practice_submitted":
                    continue
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
        for assessment in source["self_assessments"]:
            page_path = str(assessment.get("page_path") or "")
            try:
                confidence = int(assessment.get("confidence"))
            except (TypeError, ValueError):
                continue
            if not page_path or not 1 <= confidence <= 5:
                continue
            incoming_at = str(assessment.get("updated_at") or "")
            existing = cur.execute("SELECT updated_at FROM self_assessments WHERE page_path = ?", (page_path,)).fetchone()
            if not existing or (incoming_at and incoming_at > str(existing["updated_at"] or "")):
                cur.execute(
                    "INSERT INTO self_assessments (page_path, confidence, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(page_path) DO UPDATE SET confidence = excluded.confidence, updated_at = excluded.updated_at",
                    (page_path, confidence, incoming_at or datetime.now().isoformat(timespec="seconds")),
                )
                counts["self_assessments"] += 1
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
                "INSERT INTO wiki_revisions (knowledge_update_id, page_path, before_content, after_content, created_page, created_at, undone_at, change_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_update, page_path, before_content, after_content, int(bool(revision.get("created_page"))),
                 created_at or datetime.now().isoformat(timespec="seconds"), revision.get("undone_at"), revision.get("change_json", "{}")),
            )
            counts["wiki_revisions"] += 1
        imported_practice = practice_sessions.import_attempts(cur, source["practice_attempts"])
        counts["practice_attempts"] = imported_practice["attempts"]
        counts["events"] += imported_practice["events"]
        if focus_rows:
            focus = focus_rows[0]
            current = cur.execute("SELECT * FROM learning_focus WHERE id = 1").fetchone()
            if not current:
                cur.execute(
                    "INSERT INTO learning_focus (id, page_path, page_title, question, checkpoint, available_minutes, updated_at) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?)",
                    tuple(focus[key] for key in ("page_path", "page_title", "question", "checkpoint", "available_minutes", "updated_at")),
                )
                counts["learning_focus"] = 1
            elif any(current[key] != focus[key] for key in ("page_path", "question", "checkpoint")):
                # Keep the current problem; recover a conflicting checkpoint in the existing reflection history.
                counts["reflections"] += int(_focus_reflection(cur, focus))
        imported_lab = learning_lab.import_records(
            cur, source["learning_lab_progress"], source["learning_lab_attempts"], source["learning_lab_rewards"],
        )
        counts["learning_lab_progress"] = imported_lab["progress"]
        counts["learning_lab_attempts"] = imported_lab["attempts"]
        counts["learning_lab_rewards"] = imported_lab["rewards"]
    return {"dry_run": False, "imported": counts, "message": "已合并导入数据，当前设备既有记录未被删除。"}


def weekly_report(today: date | None = None) -> dict:
    """A diagnostic weekly report: recurring patterns, checked progress, and few next actions."""
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
            "SELECT gaps.content, gaps.gap_type, gaps.status, sessions.page_title, sessions.page_path, gaps.created_at "
            "FROM gaps JOIN sessions ON sessions.id = gaps.session_id "
            "ORDER BY gaps.id DESC LIMIT 100"
        ).fetchall()
        source_check_rows = cur.execute(
            "SELECT sessions.page_path, MAX(sessions.page_title) AS page_title, COUNT(*) AS total "
            "FROM review_attempts JOIN cards ON cards.id = review_attempts.card_id "
            "JOIN sessions ON sessions.id = cards.session_id "
            "WHERE review_attempts.verdict = 'pass' "
            "AND review_attempts.source = 'llm' AND review_attempts.evidence_level = 'source_reviewed' "
            "AND substr(review_attempts.created_at, 1, 10) BETWEEN ? AND ? "
            "GROUP BY sessions.page_path",
            (start, end),
        ).fetchall()
        historical_keyword_matches = cur.execute(
            "SELECT COUNT(*) FROM review_attempts WHERE evidence_level = 'source_standard'"
        ).fetchone()[0]
        practice_rows = cur.execute(
            "SELECT kind, mode, phase, task_key, result_json FROM practice_attempts "
            "WHERE submitted_at IS NOT NULL AND substr(submitted_at, 1, 10) BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    counter = Counter()
    by_path: dict[str, int] = Counter()
    for event in events:
        counter[event["event_type"]] += event["total"]
        by_path[event["page_path"]] += event["total"]
    repeated = Counter()
    clusters = Counter()
    cluster_titles: dict[tuple[str, str], str] = {}
    for gap in gap_rows:
        if gap["status"] != "verified":
            repeated[(gap["page_title"], gap["content"])] += 1
        if str(gap["created_at"] or "")[:10] >= start:
            normalized_type = tutor.normalize_gap_type(gap["gap_type"])
            clusters[(gap["page_path"], normalized_type)] += 1
            cluster_titles[(gap["page_path"], normalized_type)] = gap["page_title"]
    concepts = wiki_reader.scan_concepts()
    states = mastery.overview(concepts)
    maintaining = [concept for concept in concepts if states[concept["path"]]["level"] == "maintaining"]
    corrections = [
        {"title": title, "gap": gap, "times": times}
        for (title, gap), times in repeated.most_common(5)
    ]
    recurring_clusters = [
        {
            "path": path,
            "title": cluster_titles[(path, gap_type)],
            "gap_type": gap_type,
            "label": tutor.gap_type_meta(gap_type)["label"],
            "times": times,
            "next_action": tutor.gap_type_meta(gap_type)["practice_prompt"],
        }
        for (path, gap_type), times in clusters.most_common(5)
    ]
    prior_gap_counts = Counter(gap["page_path"] for gap in gap_rows)
    source_checks = {row["page_path"]: dict(row) for row in source_check_rows}
    practice_summary = {
        phase: {"assessments": 0, "matches": 0, "needs_review": 0, "practice_runs": 0}
        for phase in ("application", "transfer", "delayed")
    }
    practice_summary["external"] = {"self_reported": 0}
    practice_summary["by_family"] = {
        family: {
            phase: {"assessments": 0, "matches": 0, "needs_review": 0, "practice_runs": 0}
            for phase in practice_sessions.PHASES
        }
        for family in sorted({task["family"] for task in practice_sessions.TASKS})
    }
    for row in practice_rows:
        result = _decode_json(row["result_json"], {})
        if row["kind"] == "external":
            practice_summary["external"]["self_reported"] += 1
        elif row["phase"] in practice_sessions.PHASES:
            family = practice_sessions._TASKS_BY_KEY.get(row["task_key"], {}).get("family", "unknown")
            family_summary = practice_summary["by_family"].setdefault(
                family, {
                    phase: {"assessments": 0, "matches": 0, "needs_review": 0, "practice_runs": 0}
                    for phase in practice_sessions.PHASES
                },
            )
            for phase_summary in (practice_summary[row["phase"]], family_summary[row["phase"]]):
                if row["mode"] == "assessment":
                    phase_summary["assessments"] += 1
                    if result.get("eligible") and result.get("matches") is True:
                        phase_summary["matches"] += 1
                    elif result.get("eligible"):
                        phase_summary["needs_review"] += 1
                else:
                    phase_summary["practice_runs"] += 1
    progress_evidence = []
    for concept in concepts:
        state = states[concept["path"]]
        check = source_checks.get(concept["path"])
        if check and prior_gap_counts[concept["path"]]:
            progress_evidence.append({
                "path": concept["path"], "title": concept["title"],
                "evidence": f"此前记录过 {prior_gap_counts[concept['path']]} 个待澄清点；本周有 {check['total']} 次回答依据原文或明确要点通过核对。",
                "level": state["label"],
            })
    priorities = [
        {
            "path": item["path"], "title": item["title"], "why": item["why"],
            "estimated_minutes": item["estimated_minutes"], "benefit": item["benefit"],
        }
        for item in _recommendation_candidates(limit=3)
    ]
    evidence_total = (
        counter["session_done"] + counter["gap_revised"] + counter["gap_practiced"]
        + counter["gap_retested"] + counter["review_rated"] + counter["practice_submitted"]
    )
    return {
        "range": {"start": start, "end": end},
        "has_evidence": bool(evidence_total),
        "evidence_total": evidence_total,
        "summary": {
            "completed_sessions": counter["session_done"],
            "revised_gaps": counter["gap_revised"] + counter["gap_practiced"],
            "reviews": counter["review_rated"],
            "maintaining_concepts": len(maintaining),
            "historical_keyword_matches": int(historical_keyword_matches),
            "source_checks": sum(int(row["total"]) for row in source_check_rows),
            "practice_attempts": sum(
                practice_summary[phase]["assessments"] + practice_summary[phase]["practice_runs"]
                for phase in practice_sessions.PHASES
            ),
            "external_practice": practice_summary["external"]["self_reported"],
        },
        "practice": practice_summary,
        "history_notice": (
            "历史关键词匹配记录，未完成事实核验；这些记录不计入来源核对，旧掌握状态可能回退。"
            if historical_keyword_matches else ""
        ),
        "corrected_misconceptions": corrections,
        "repeated_gaps": [item for item in corrections if item["times"] > 1],
        "maintaining_concepts": [{"title": item["title"], "path": item["path"]} for item in maintaining[:8]],
        "diagnostic": {
            "recurring_error_clusters": recurring_clusters,
            "progress_evidence": progress_evidence[:5],
            "next_week_priorities": priorities,
        },
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
            " UNION ALL SELECT page_path, page_path AS page_title, updated_at AS last_activity FROM self_assessments"
            " UNION ALL SELECT page_path, page_title, COALESCE(submitted_at, created_at) AS last_activity FROM practice_attempts"
            " UNION ALL SELECT page_path, page_title, updated_at AS last_activity FROM micro_recalls"
            " UNION ALL SELECT page_path, page_title, updated_at AS last_activity FROM learning_focus"
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
            "OR EXISTS(SELECT 1 FROM knowledge_updates WHERE target_path = ?) OR EXISTS(SELECT 1 FROM wiki_revisions WHERE page_path = ?) "
            "OR EXISTS(SELECT 1 FROM self_assessments WHERE page_path = ?) OR EXISTS(SELECT 1 FROM practice_attempts WHERE page_path = ?) "
            "OR EXISTS(SELECT 1 FROM micro_recalls WHERE page_path = ?) "
            "OR EXISTS(SELECT 1 FROM learning_focus WHERE page_path = ?)",
            (old_path, old_path, old_path, old_path, old_path, old_path, old_path, old_path, old_path, old_path),
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
        cur.execute("UPDATE self_assessments SET page_path = ? WHERE page_path = ?", (new_path, old_path))
        cur.execute(
            "UPDATE practice_attempts SET page_path = ?, page_title = ? WHERE page_path = ?",
            (new_path, title, old_path),
        )
        cur.execute("UPDATE micro_recalls SET page_path = ?, page_title = ? WHERE page_path = ?", (new_path, title, old_path))
        cur.execute("UPDATE learning_events SET page_path = ? WHERE page_path = ?", (new_path, old_path))
        cur.execute(
            "UPDATE learning_focus SET page_path = ?, page_title = ?, updated_at = ? WHERE page_path = ?",
            (new_path, title, datetime.now().isoformat(timespec="microseconds"), old_path),
        )
    return {"old_path": old_path, "new_path": new_path, "title": title}
