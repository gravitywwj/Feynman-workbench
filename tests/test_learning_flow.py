"""跨资料、微回忆、追问和备份的学习闭环。"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import study_sessions, tutor, wiki_reader

client = TestClient(app)
PAGE = "AI/rag/query-rewriting.md"


def source_block(page_path=PAGE, predicate=lambda block: block["kind"] == "paragraph"):
    _, body = wiki_reader.read_page_markdown(page_path)
    return next(block for block in wiki_reader.source_blocks(body) if predicate(block))


def test_source_anchor_is_exact_and_reports_change_deletion_and_duplicates(wiki):
    block = source_block(predicate=lambda item: item["kind"] == "paragraph" and "查询改写" in item["excerpt"])
    _, rendered = wiki_reader.render_page_html(PAGE)
    assert f'id="wiki-source-{block["index"]}" data-source-block="{block["index"]}"' in rendered
    assert wiki_reader.capture_source_block(PAGE, block["index"], block["excerpt"])["excerpt"] == block["excerpt"]
    with pytest.raises(ValueError, match="不一致"):
        wiki_reader.capture_source_block(PAGE, block["index"], "另一段无关内容")

    assert wiki_reader.source_status(
        PAGE, block["index"], block["excerpt"], block["fingerprint"],
        block["context_before"], block["context_after"],
    )["status"] == "locatable"
    page = wiki / "pages" / PAGE
    original = page.read_text(encoding="utf-8")
    changed = original.replace("RAG", "Graph", 1)
    assert changed != original
    page.write_text(changed, encoding="utf-8")
    assert wiki_reader.source_status(
        PAGE, block["index"], block["excerpt"], block["fingerprint"],
        block["context_before"], block["context_after"],
    )["status"] == "changed"

    page.write_text(original.replace(block["raw"] + "\n\n", "", 1), encoding="utf-8")
    assert wiki_reader.source_status(
        PAGE, block["index"], block["excerpt"], block["fingerprint"],
        block["context_before"], block["context_after"],
    )["status"] == "missing"

    page.write_text(original + f"\n\n{block['excerpt']}\n", encoding="utf-8")
    assert wiki_reader.source_status(
        PAGE, block["index"], block["excerpt"], block["fingerprint"],
        block["context_before"], block["context_after"],
    )["status"] == "ambiguous"


def test_question_at_page_end_selects_its_own_source_block(wiki):
    path = "AI/rag/late-evidence.md"
    page = wiki / "pages" / path
    page.write_text(
        "---\ntitle: Retrieval Boundaries\n---\n\n# Retrieval Boundaries\n\n"
        "General background with no matching terms.\n\nA separate unrelated paragraph.\n\n"
        "The late-only marker glacial-jaguar describes the final retrieval boundary.\n",
        encoding="utf-8",
    )
    cards = study_sessions._cards_for(path, "Retrieval Boundaries", [{"content": "glacial-jaguar retrieval boundary"}])
    assert cards[1]["reference_status"] == "source"
    assert "glacial-jaguar" in cards[1]["reference_excerpt"]
    assert "unrelated paragraph" not in cards[1]["reference_excerpt"]


def test_micro_recall_draft_creates_learning_evidence_only_after_answer(wiki):
    block = source_block(predicate=lambda item: item["kind"] == "paragraph" and "查询改写" in item["excerpt"])
    uid = str(uuid4())
    created = client.post("/api/study/micro-recalls", json={
        "recall_uid": uid, "page_path": PAGE, "source_index": block["index"],
        "excerpt": block["excerpt"], "question": "这段中的关键环节是什么？",
    })
    assert created.status_code == 200, created.text
    assert created.json()["recall"]["status"] == "draft"
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0

    updated = client.put(f"/api/study/micro-recalls/{uid}", json={
        "question": "为什么查询改写是关键环节？", "answer": "",
    })
    assert updated.status_code == 200
    key = str(uuid4())
    answered = client.post(f"/api/study/micro-recalls/{uid}/answer", json={
        "answer": "不知道。", "idempotency_key": key,
    })
    assert answered.status_code == 200, answered.text
    details = answered.json()
    assert details["followup"]["stage"] == "hint"
    assert details["followup"]["help_level"] == 1
    assert details["followup"]["task"].startswith("一级提示：")
    assert len(details["cards"]) == 1 and details["cards"][0]["micro_recall_id"]
    assert client.get(f"/api/study/micro-recalls/{uid}").json()["recall"]["status"] == "active"

    before = len(details["turns"])
    duplicate = client.post(f"/api/study/micro-recalls/{uid}/answer", json={
        "answer": "不知道。", "idempotency_key": key,
    })
    assert duplicate.status_code == 200
    assert len(duplicate.json()["turns"]) == before


def test_followup_transitions_keep_help_and_gap_history(wiki, monkeypatch):
    created = study_sessions.create_session(
        PAGE,
        "查询改写先补足对象和场景，因为检索需要明确目标。例如给模糊问题补充约束条件。",
    )
    session_id = created["session"]["id"]
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET tutor_turns = 9 WHERE id = ?", (session_id,))
    quote = created["cards"][0]["reference_excerpt"]
    steps = iter([
        ("ambiguous", "请说明具体对象。", "对象和步骤分别是什么？", ""),
        ("misconception", "这与资料中的说法不同。", "依据反例重新判断。", quote),
        ("stuck", "先用一级提示。", "先说对象和一个关键步骤。", ""),
        ("sound", "当前回答与摘录一致。", "换到图书馆检索场景，说明同一规则如何工作。", quote),
        ("sound", "迁移回答与摘录一致。", "", quote),
    ])

    def assess(**_kwargs):
        finding, feedback, task, evidence = next(steps)
        return {"finding": finding, "feedback": feedback, "task": task, "evidence_excerpt": evidence, "source": "llm"}

    monkeypatch.setattr(tutor, "assess_followup", assess)
    stages = ["clarify", "counterexample", "hint", "transfer", "complete"]
    last_key = ""
    last_answer = ""
    for index, stage in enumerate(stages):
        last_key = str(uuid4())
        last_answer = f"回答阶段 {index} 的具体内容。"
        details = study_sessions.advance_followup(session_id, last_answer, last_key)
        assert details["followup"]["stage"] == stage
        if stage == "hint":
            assert details["followup"]["help_level"] == 1
        if stage == "transfer":
            assert "图书馆检索" in details["followup"]["task"]
    assert details["session"]["status"] == "done"
    assert next(gap for gap in details["gaps"] if gap["id"] == details["session"]["target_gap_id"])["status"] == "revised"
    before = len(details["turns"])
    repeated = study_sessions.advance_followup(session_id, last_answer, last_key)
    assert len(repeated["turns"]) == before
    assert sum(turn["kind"] == "followup_answer" for turn in repeated["turns"]) == 5


def test_invalid_followup_feedback_is_saved_for_retry_without_pass(wiki, monkeypatch):
    created = study_sessions.create_session(
        PAGE,
        "查询改写会补足检索问题中的对象和场景，因为这样能明确检索目标。例如增加时间和约束。",
    )
    session_id = created["session"]["id"]
    key = str(uuid4())
    payload = {"answer": "我会先指出对象，再说明查询如何补足。", "idempotency_key": key}
    monkeypatch.setattr(tutor, "assess_followup", lambda **_kwargs: (_ for _ in ()).throw(ValueError("invalid model json")))
    failed = client.post(f"/api/study/sessions/{session_id}/follow-ups", json=payload)
    assert failed.status_code == 503
    pending = client.get(f"/api/study/sessions/{session_id}").json()
    assert pending["followup"]["stage"] == "clarify"
    assert pending["followup"]["pending_turn_id"] == key
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM review_attempts").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM turns WHERE idempotency_key = ?", (key,)).fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM turns WHERE idempotency_key = ?", (f"{key}:response",)).fetchone()[0] == 0

    monkeypatch.setattr(tutor, "assess_followup", lambda **_kwargs: {
        "finding": "ambiguous", "feedback": "请补充对象。", "task": "说明一个对象和关键步骤。",
        "evidence_excerpt": "", "source": "llm",
    })
    retried = client.post(f"/api/study/sessions/{session_id}/follow-ups", json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["followup"]["stage"] == "clarify"
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM turns WHERE idempotency_key = ?", (key,)).fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM turns WHERE idempotency_key = ?", (f"{key}:response",)).fetchone()[0] == 1


def test_backup_v8_restores_micro_recall_session_source_and_stage(wiki, monkeypatch):
    block = source_block(predicate=lambda item: item["kind"] == "paragraph" and "查询改写" in item["excerpt"])
    uid = str(uuid4())
    created = study_sessions.create_micro_recall(
        uid, PAGE, block["index"], block["excerpt"], "为什么要补充检索问题？",
    )
    response = study_sessions.submit_micro_recall_answer(uid, "不知道。", str(uuid4()))
    assert response["followup"]["stage"] == "hint"
    exported = study_sessions.export_learning_data()
    assert exported["version"] == 8
    assert exported["micro_recalls"][0]["source_context_before"] == created["source_context_before"]
    assert exported["sessions"][0]["followup_stage"] == "hint"

    restored_db = wiki / "restored.db"
    monkeypatch.setattr(db, "DB_PATH", restored_db)
    db.init_db()
    db.init_db()
    result = study_sessions.import_learning_data(exported)
    assert result["imported"]["micro_recalls"] == 1
    assert result["imported"]["sessions"] == 1
    assert result["imported"]["cards"] == 1
    restored = study_sessions.get_micro_recall(uid)
    details = study_sessions.session_detail(restored["session_id"])
    assert details["followup"]["stage"] == "hint"
    assert details["cards"][0]["micro_recall_id"] == restored["id"]
    assert details["cards"][0]["source_context_before"] == block["context_before"]
    assert [turn["kind"] for turn in details["turns"] if turn["role"] == "tutor"] == ["prompt", "followup_task"]
    repeated = study_sessions.import_learning_data(exported)
    assert repeated["imported"]["micro_recalls"] == 0
    assert repeated["imported"]["sessions"] == 0


def test_orphaned_micro_recall_can_be_relinked_without_losing_source(wiki):
    block = source_block(predicate=lambda item: item["kind"] == "paragraph" and "查询改写" in item["excerpt"])
    uid = str(uuid4())
    study_sessions.create_micro_recall(uid, PAGE, block["index"], block["excerpt"], "为什么需要补充检索问题？")
    old_page = wiki / "pages" / PAGE
    new_path = "AI/rag/query-rewriting-moved.md"
    old_page.rename(wiki / "pages" / new_path)

    assert any(item["page_path"] == PAGE for item in study_sessions.list_orphaned_records())
    result = study_sessions.relink_page(PAGE, new_path)
    recalled = study_sessions.get_micro_recall(uid)

    assert result["new_path"] == new_path
    assert recalled["page_path"] == new_path
    assert recalled["current_source_status"] == "locatable"
    assert not any(item["page_path"] == PAGE for item in study_sessions.list_orphaned_records())
