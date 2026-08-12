"""学习闭环 API 测试：讲解会话、笔记和复习卡。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PAGE = "AI/rag/query-rewriting.md"


def test_create_session_persists_explanation_gaps_and_cards(wiki):
    response = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写是在检索前把用户的问题变得更清楚。因为原问题往往不完整，所以系统通过补充关键词来改善召回。例如用户问它有什么用时，可以改写成包含具体对象的检索问题。",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["session"]["page_path"] == PAGE
    assert data["turns"][0]["role"] == "user"
    assert data["turns"][1]["role"] == "tutor"
    assert data["cards"]

    detail = client.get(f"/api/study/sessions/{data['session']['id']}")
    assert detail.status_code == 200
    assert detail.json()["session"]["id"] == data["session"]["id"]


def test_short_explanation_rejected(wiki):
    response = client.post("/api/study/sessions", json={"page_path": PAGE, "explanation": "太短"})
    assert response.status_code == 400


def test_note_round_trip(wiki):
    saved = client.put("/api/study/notes", params={"page_path": PAGE}, json={"content": "需要比较改写前后召回率。"})
    assert saved.status_code == 200
    assert saved.json()["content"] == "需要比较改写前后召回率。"
    fetched = client.get("/api/study/notes", params={"page_path": PAGE})
    assert fetched.status_code == 200
    assert fetched.json()["content"] == "需要比较改写前后召回率。"


def test_due_card_can_be_reviewed(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写通过把不完整的问题改成检索友好的表达来提高召回。它很重要，因为检索结果决定后续回答质量。比如把模糊提问补上上下文和关键词。",
    }).json()
    due = client.get("/api/study/reviews/due")
    assert due.status_code == 200
    card_id = created["cards"][0]["id"]
    review = client.post(f"/api/study/reviews/{card_id}", json={"rating": "good"})
    assert review.status_code == 200
    assert review.json()["interval"] >= 1
    assert review.json()["reps"] == 1


def test_history_and_gap_revision_are_persisted(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写先识别用户问题里缺失的条件，再补充具体对象和上下文，以便检索系统找到更匹配的资料。例如询问用途时，可以补上要比较的产品和使用场景。",
    }).json()
    history = client.get("/api/study/history")
    assert history.status_code == 200
    assert history.json()["sessions"][0]["id"] == created["session"]["id"]
    gaps = client.get("/api/study/gaps", params={"status": "open"}).json()["gaps"]
    assert gaps
    revised = client.post(f"/api/study/gaps/{gaps[0]['id']}/revision", json={
        "revision": "我会先列出问题缺少的对象、限制条件和上下文，再将这些信息改写为更完整的检索表达，并用实际任务检查召回内容是否更贴近需要。",
    })
    assert revised.status_code == 200
    assert revised.json()["status"] == "revised"
    assert revised.json()["revision"].startswith("我会先列出")
    assert client.get("/api/study/gaps", params={"status": "revised"}).json()["gaps"]


def test_review_schedule_changes_by_rating(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写会补全用户提问中的关键词和上下文，所以检索能找到更匹配的资料。例如把模糊问题改为包含对象、任务和约束条件的查询。",
    }).json()
    card_id = created["cards"][0]["id"]
    good = client.post(f"/api/study/reviews/{card_id}", json={"rating": "good"}).json()
    assert (good["interval"], good["reps"], good["ease"]) == (1, 1, 2.5)
    easy = client.post(f"/api/study/reviews/{card_id}", json={"rating": "easy"}).json()
    assert (easy["interval"], easy["reps"]) == (7, 2)
    again = client.post(f"/api/study/reviews/{card_id}", json={"rating": "again"}).json()
    assert (again["interval"], again["reps"]) == (1, 0)


def test_gap_revision_validation_and_missing_gap(wiki):
    assert client.post("/api/study/gaps/999/revision", json={"revision": "足够长的补充说明，但这个盲区并不存在，因此不应保存。"}).status_code == 404
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写通过补充上下文改善检索效果。例如在问题中加入明确对象和约束条件，使后续召回结果更接近真正要解决的任务。",
    }).json()
    gap_id = created["gaps"][0]["id"]
    assert client.post(f"/api/study/gaps/{gap_id}/revision", json={"revision": "太短"}).status_code == 400


def test_export_and_relink_orphaned_records(wiki):
    client.put("/api/study/notes", params={"page_path": PAGE}, json={"content": "需要在项目里继续验证。"})
    original = wiki / "pages" / "AI" / "rag" / "query-rewriting.md"
    moved = wiki / "pages" / "AI" / "rag" / "query-rewriting-moved.md"
    original.rename(moved)
    orphans = client.get("/api/study/orphans")
    assert orphans.status_code == 200
    assert orphans.json()["orphans"][0]["page_path"] == PAGE
    relink = client.post("/api/study/relink", json={
        "old_path": PAGE,
        "new_path": "AI/rag/query-rewriting-moved.md",
    })
    assert relink.status_code == 200
    assert relink.json()["new_path"].endswith("moved.md")
    assert client.get("/api/study/orphans").json()["orphans"] == []
    exported = client.get("/api/study/export")
    assert exported.status_code == 200
    assert exported.json()["format"] == "feynman-workbench-export"
    assert exported.json()["notes"][0]["page_path"].endswith("moved.md")
