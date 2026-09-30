"""独立的想法讨论会话。

学习会话负责复习阶段的费曼式回忆；本模块记录学习之后随时产生的想法、
多轮讨论和最终 Wiki 草稿，二者不共享会话数据。
"""
from __future__ import annotations

import json
from pathlib import Path

from app import db
from app.services import tutor, wiki_reader, wiki_writer


def _decode_json(value: str, fallback):
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, type(fallback)) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _title_from_content(content: str) -> str:
    first_line = next((line.strip("# -*\t") for line in content.splitlines() if line.strip()), "")
    return (first_line or "未命名学习想法")[:120]


def _evidence_for(content: str, related_page_path: str | None = None) -> list[dict]:
    evidence = wiki_reader.search_wiki(content, limit=5)
    if related_page_path:
        meta, body = wiki_reader.read_page_markdown(related_page_path)
        title = meta.get("title") or Path(related_page_path).stem
        related = {
            "path": related_page_path,
            "title": title,
            "section": related_page_path.split("/", 1)[0] if "/" in related_page_path else "",
            "excerpt": " ".join(body.split())[:320],
            "matched_terms": ["关联学习页"],
        }
        evidence = [related] + [item for item in evidence if item.get("path") != related_page_path]
    return evidence[:5]


def _turn_payload(row) -> dict:
    item = dict(row)
    item["metadata"] = _decode_json(item.pop("metadata_json", "{}"), {})
    return item


def _revision_payload(row) -> dict | None:
    return dict(row) if row else None


def _idea_payload(row, turns, revision=None) -> dict:
    item = dict(row)
    item["evidence"] = _decode_json(item.pop("evidence_json", "[]"), [])
    item["assessment"] = _decode_json(item.pop("assessment_json", "{}"), {})
    item["turns"] = [_turn_payload(turn) for turn in turns]
    item["revision"] = _revision_payload(revision)
    return item


def _row(idea_id: int):
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM idea_sessions WHERE id = ?", (idea_id,)).fetchone()
    if not row:
        raise LookupError("想法讨论不存在")
    return row


def get_idea(idea_id: int) -> dict:
    with db.cursor() as cur:
        idea = cur.execute("SELECT * FROM idea_sessions WHERE id = ?", (idea_id,)).fetchone()
        if not idea:
            raise LookupError("想法讨论不存在")
        turns = cur.execute(
            "SELECT id, role, kind, content, metadata_json, created_at FROM idea_turns WHERE idea_id = ? ORDER BY id",
            (idea_id,),
        ).fetchall()
        revision = cur.execute(
            "SELECT id, idea_id, page_path, created_page, created_at, undone_at FROM idea_revisions "
            "WHERE idea_id = ? ORDER BY id DESC LIMIT 1",
            (idea_id,),
        ).fetchone()
    return _idea_payload(idea, turns, revision)


def list_ideas(limit: int = 50) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM idea_sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "initial_content": row["initial_content"],
            "status": row["status"],
            "assessment": _decode_json(row["assessment_json"], {}),
            "draft_title": row["draft_title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def create_idea(
    content: str, *, title: str = "", persona: str = "feynman", related_page_path: str | None = None,
) -> dict:
    clean_content = content.strip()
    if len(clean_content) < 4:
        raise ValueError("请先写下一条具体的想法，再开始讨论。")
    clean_title = title.strip()[:120] or _title_from_content(clean_content)
    clean_related = related_page_path.strip() if related_page_path else None
    evidence = _evidence_for(clean_content, clean_related)
    assessment = tutor.assess_idea(note=clean_content, evidence=evidence, persona=persona)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO idea_sessions (title, initial_content, persona, related_page_path, evidence_json, assessment_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                clean_title, clean_content[:10000], tutor.normalize_persona(persona), clean_related,
                json.dumps(evidence, ensure_ascii=False), json.dumps(assessment, ensure_ascii=False),
            ),
        )
        idea_id = cur.lastrowid
        cur.execute(
            "INSERT INTO idea_turns (idea_id, role, kind, content, metadata_json) VALUES (?, 'user', 'assessment', ?, '{}')",
            (idea_id, clean_content[:10000]),
        )
        cur.execute(
            "INSERT INTO idea_turns (idea_id, role, kind, content, metadata_json) VALUES (?, 'agent', 'assessment', ?, ?)",
            (idea_id, assessment["summary"], json.dumps(assessment, ensure_ascii=False)),
        )
    return get_idea(idea_id)


def send_message(idea_id: int, content: str) -> dict:
    clean_content = content.strip()
    if len(clean_content) < 2:
        raise ValueError("请先写下这一轮想补充或追问的内容。")
    idea = _row(idea_id)
    if idea["status"] not in {"open"}:
        raise ValueError("这条想法已经生成草稿或完成入库，请先新建一轮讨论。")
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT role, content FROM idea_turns WHERE idea_id = ? ORDER BY id", (idea_id,)
        ).fetchall()
    history = [dict(row) for row in rows]
    evidence = _decode_json(idea["evidence_json"], [])
    response = tutor.discuss_idea(
        idea=clean_content, history=history, evidence=evidence, persona=idea["persona"],
    )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO idea_turns (idea_id, role, kind, content, metadata_json) VALUES (?, 'user', 'discussion', ?, '{}')",
            (idea_id, clean_content[:10000]),
        )
        cur.execute(
            "INSERT INTO idea_turns (idea_id, role, kind, content, metadata_json) VALUES (?, 'agent', 'discussion', ?, ?)",
            (idea_id, response.get("reply") or response.get("summary") or "已记录这一轮讨论。", json.dumps(response, ensure_ascii=False)),
        )
        cur.execute(
            "UPDATE idea_sessions SET assessment_json = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (json.dumps(response, ensure_ascii=False), idea_id),
        )
    return get_idea(idea_id)


def generate_draft(idea_id: int) -> dict:
    idea = _row(idea_id)
    if idea["status"] not in {"open", "draft"}:
        raise ValueError("这条想法已经完成处理，不能重复生成草稿。")
    if idea["status"] == "draft" and idea["draft_content"]:
        return get_idea(idea_id)
    with db.cursor() as cur:
        turns = cur.execute(
            "SELECT role, kind, content, metadata_json, created_at FROM idea_turns WHERE idea_id = ? ORDER BY id",
            (idea_id,),
        ).fetchall()
    turn_items = [_turn_payload(turn) for turn in turns]
    evidence = _decode_json(idea["evidence_json"], [])
    draft = tutor.summarize_idea_conversation(
        title=idea["title"], initial_content=idea["initial_content"], turns=turn_items,
        evidence=evidence, persona=idea["persona"],
    )
    assessment = _decode_json(idea["assessment_json"], {})
    assessment.update(draft)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO idea_turns (idea_id, role, kind, content, metadata_json) VALUES (?, 'agent', 'summary', ?, ?)",
            (idea_id, draft["summary"], json.dumps(draft, ensure_ascii=False)),
        )
        cur.execute(
            "UPDATE idea_sessions SET draft_title = ?, draft_content = ?, status = 'draft', "
            "assessment_json = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (draft["draft_title"], draft["draft_content"], json.dumps(assessment, ensure_ascii=False), idea_id),
        )
    return get_idea(idea_id)


def review_idea(idea_id: int, *, content: str, page_path: str) -> dict:
    idea = _row(idea_id)
    clean = content.strip()
    evidence = _decode_json(idea["evidence_json"], [])
    candidates = {item.get("path") for item in evidence}
    if idea["status"] != "draft" or not clean or page_path not in candidates:
        raise ValueError("请选择已有的关联学习页，并审核非空草稿。")
    raw = wiki_reader.raw_evidence_for_page(page_path, clean)
    verdict = tutor.review_wiki_proposal(content=clean, raw_evidence=raw)
    kind = "verified" if verdict["verdict"] == "supported" else "tentative"
    preview = None
    if verdict["verdict"] != "problematic":
        preview = wiki_writer.preview_reviewed_update(
            page_path, clean, idea_id, kind=kind, citations=verdict["citations"],
        )
    assessment = _decode_json(idea["assessment_json"], {})
    assessment["review"] = {**verdict, "content": clean, "target_path": page_path,
                            "kind": kind if preview else None, "base_hash": preview["base_hash"] if preview else None,
                            "diff": preview["diff"] if preview else "", "raw_evidence": raw}
    with db.cursor() as cur:
        cur.execute("UPDATE idea_sessions SET draft_content = ?, assessment_json = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (clean, json.dumps(assessment, ensure_ascii=False), idea_id))
    return get_idea(idea_id)


def apply_idea(idea_id: int, *, mode: str, title: str, content: str) -> dict:
    if mode not in {"append_current", "append_pending", "keep_local"}:
        raise ValueError("独立想法只支持写入关联学习页或保留在本地")
    idea = _row(idea_id)
    if idea["status"] != "draft":
        raise ValueError("请先生成可审核的 Wiki 草稿。")
    clean_content = content.strip()
    if not clean_content:
        raise ValueError("Wiki 草稿不能为空")
    clean_title = title.strip()[:120] or idea["draft_title"] or idea["title"]
    if mode == "keep_local":
        with db.cursor() as cur:
            cur.execute(
                "UPDATE idea_sessions SET draft_title = ?, draft_content = ?, status = 'kept_local', "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (clean_title, clean_content[:5000], idea_id),
            )
        return get_idea(idea_id)
    review = _decode_json(idea["assessment_json"], {}).get("review") or {}
    expected_mode = "append_current" if review.get("kind") == "verified" else "append_pending"
    if (review.get("verdict") == "problematic" or review.get("content") != clean_content
            or mode != expected_mode or not review.get("target_path") or not review.get("base_hash")):
        raise ValueError("草稿尚未通过最终审核，或审核后被修改；请重新审核。")
    if wiki_reader.raw_evidence_for_page(review["target_path"], clean_content) != review.get("raw_evidence"):
        raise ValueError("原始资料已变化，请重新审核草稿。")
    change = wiki_writer.apply_reviewed_update(
        review["target_path"], clean_content, idea_id, kind=review["kind"],
        citations=review.get("citations", []), expected_hash=review["base_hash"],
    )
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO idea_revisions (idea_id, page_path, before_content, after_content, created_page, change_json) VALUES (?, ?, ?, ?, 0, ?)",
                (idea_id, change["path"], change["before_content"], change["after_content"],
                 json.dumps(change["manifest"], ensure_ascii=False)),
            )
            cur.execute(
                "UPDATE idea_sessions SET draft_title = ?, draft_content = ?, status = 'applied', wiki_path = ?, "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (clean_title, clean_content[:5000], change["path"], idea_id),
            )
    except Exception:
        try:
            wiki_writer.restore_revision(
                change["path"], change["before_content"], change["after_content"],
                created_page=False, manifest=change["manifest"],
            )
        except (FileNotFoundError, ValueError, OSError):
            pass
        raise
    return get_idea(idea_id)


def undo_idea(idea_id: int) -> dict:
    idea = _row(idea_id)
    if idea["status"] != "applied":
        raise ValueError("只有已写入 Wiki 的想法可以撤销。")
    with db.cursor() as cur:
        revision = cur.execute(
            "SELECT * FROM idea_revisions WHERE idea_id = ? AND undone_at IS NULL ORDER BY id DESC LIMIT 1",
            (idea_id,),
        ).fetchone()
    if not revision:
        raise LookupError("没有可撤销的 Wiki 快照")
    wiki_writer.restore_revision(
        revision["page_path"], revision["before_content"], revision["after_content"],
        created_page=bool(revision["created_page"]), manifest=_decode_json(revision["change_json"], {}),
    )
    with db.cursor() as cur:
        cur.execute("UPDATE idea_revisions SET undone_at = datetime('now', 'localtime') WHERE id = ?", (revision["id"],))
        cur.execute(
            "UPDATE idea_sessions SET status = 'undone', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (idea_id,),
        )
    return get_idea(idea_id)
