"""Flexible study focus: durable checkpoints without timed stages or mastery credit."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)
PAGE = "AI/rag/query-rewriting.md"
OTHER_PAGE = "AI/agents/agent-memory-system.md"


def save_focus(**changes):
    current = client.get("/api/study/focus").json()["focus"]
    payload = {
        "page_path": PAGE, "question": "什么时候应该改写查询？", "checkpoint": "下次比较保留原查询与改写的结果。",
        "available_minutes": 60, "expected_updated_at": current["updated_at"] if current else None,
    }
    payload.update(changes)
    response = client.put("/api/study/focus", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["focus"]


def test_focus_resumes_before_due_cards_and_time_can_change(wiki):
    with db.cursor() as cur:
        cur.execute("INSERT INTO sessions (page_path, page_title, concept) VALUES (?, 'Query', 'Query')", (PAGE,))
        cur.execute("INSERT INTO cards (session_id, question, answer, due) VALUES (?, 'Review', 'Answer', '2000-01-01')", (cur.lastrowid,))
    focus = save_focus()
    action = client.get("/api/study/home").json()
    assert action["type"] == "focus" and action["title"] == focus["question"]
    assert client.get("/api/study/reviews/due").json()["cards"]
    extended = save_focus(available_minutes=180)
    assert extended["available_minutes"] == 180 and extended["checkpoint"] == focus["checkpoint"]
    assert client.get("/api/study/home").json()["estimated_minutes"] == 180
    brief = client.post("/api/study/recall-brief", json={"page_path": PAGE}).json()
    assert focus["question"] in brief["question"]
    other_brief = client.post("/api/study/recall-brief", json={"page_path": OTHER_PAGE}).json()
    assert focus["question"] not in other_brief["question"]
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"question": "   "}, {"question": "x" * 301}, {"available_minutes": 0},
    {"available_minutes": 481}, {"page_path": "../outside.md"}, {"page_path": "missing.md"},
])
def test_focus_rejects_invalid_input_without_creating_records(wiki, changes):
    response = client.put("/api/study/focus", json={"page_path": PAGE, "question": "具体问题", **changes})
    assert response.status_code in {400, 422}
    assert client.get("/api/study/focus").json()["focus"] is None


def test_focus_preserves_previous_page_and_rejects_stale_edits(wiki):
    first = save_focus()
    second = save_focus(page_path=OTHER_PAGE, question="记忆应该何时更新？", checkpoint="下次找一个会失效的例子。")
    stale = client.put("/api/study/focus", json={
        "page_path": PAGE, "question": "过时的编辑", "expected_updated_at": first["updated_at"],
    })
    assert stale.status_code == 409
    assert client.post("/api/study/focus/archive", json={"expected_updated_at": first["updated_at"]}).status_code == 409
    assert client.get("/api/study/focus").json()["focus"] == second
    archived = client.post("/api/study/focus/archive", json={"expected_updated_at": second["updated_at"]})
    assert archived.status_code == 200 and archived.json()["focus"] is None
    reflections = client.get("/api/study/reflections").json()["reflections"]
    assert len(reflections) == 2
    assert all(item["source"] == "focus" for item in reflections)
    assert any(first["checkpoint"] in item["content"] for item in reflections)
    assert any(second["checkpoint"] in item["content"] for item in reflections)


def test_focus_backup_restores_without_overwriting_another_problem(wiki):
    original = save_focus()
    exported = client.get("/api/study/export").json()
    assert exported["version"] == 8 and exported["learning_focus"] == [original]
    with db.cursor() as cur:
        cur.execute("DELETE FROM learning_focus")
    assert client.post("/api/study/import/preview", json={"payload": exported}).json()["incoming"]["learning_focus"] == 1
    assert client.get("/api/study/focus").json()["focus"] is None
    result = client.post("/api/study/import", json={"payload": exported})
    assert result.status_code == 200 and result.json()["imported"]["learning_focus"] == 1
    assert client.get("/api/study/focus").json()["focus"] == original
    current = save_focus(page_path=OTHER_PAGE, question="另一个问题", checkpoint="当前断点")
    client.post("/api/study/import", json={"payload": exported})
    client.post("/api/study/import", json={"payload": exported})
    assert client.get("/api/study/focus").json()["focus"] == current
    reflections = client.get("/api/study/reflections").json()["reflections"]
    assert len(reflections) == 1 and original["checkpoint"] in reflections[0]["content"]
    legacy = {"format": "feynman-workbench-export", "version": 6}
    assert client.post("/api/study/import", json={"payload": legacy}).status_code == 200
    malformed = {**exported, "learning_focus": [{**original, "available_minutes": "180"}]}
    assert client.post("/api/study/import/preview", json={"payload": malformed}).status_code == 400
    assert client.get("/api/study/focus").json()["focus"] == current


def test_focus_only_orphan_can_be_relinked(wiki):
    first = save_focus()
    (wiki / "pages" / PAGE).unlink()
    assert any(item["page_path"] == PAGE for item in client.get("/api/study/orphans").json()["orphans"])
    response = client.post("/api/study/relink", json={"old_path": PAGE, "new_path": OTHER_PAGE})
    assert response.status_code == 200
    restored = client.get("/api/study/focus").json()["focus"]
    assert restored["page_path"] == OTHER_PAGE and restored["question"] == first["question"]
    assert restored["checkpoint"] == first["checkpoint"] and restored["updated_at"] != first["updated_at"]
