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
