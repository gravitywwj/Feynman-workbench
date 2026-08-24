from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
PAGE = "AI/rag/query-rewriting.md"


def test_idea_discussion_is_independent_and_can_be_written_then_undone(wiki):
    created = client.post("/api/ideas", json={
        "title": "上下文可能改变检索策略",
        "content": "我突然想到：上下文越完整，检索系统可能不只是找到更多内容，而是会改变它选择检索策略的方式。",
        "related_page_path": PAGE,
    })
    assert created.status_code == 200
    idea = created.json()
    assert idea["status"] == "open"
    assert idea["related_page_path"] == PAGE
    assert idea["assessment"]["verdict"] == "uncertain"
    assert idea["assessment"]["source"] == "local"
    assert [turn["role"] for turn in idea["turns"]] == ["user", "agent"]

    continued = client.post(f"/api/ideas/{idea['id']}/messages", json={
        "content": "我可以先做一个最小实验：固定问题，只改变对象、场景和约束，再比较检索结果与查询改写过程。",
    })
    assert continued.status_code == 200
    assert len(continued.json()["turns"]) == 4
    assert continued.json()["turns"][-1]["role"] == "agent"

    draft = client.post(f"/api/ideas/{idea['id']}/draft")
    assert draft.status_code == 200
    draft_data = draft.json()
    assert draft_data["status"] == "draft"
    assert draft_data["draft_content"]
    assert any(turn["kind"] == "summary" for turn in draft_data["turns"])

    applied = client.post(f"/api/ideas/{idea['id']}/apply", json={
        "mode": "create_idea",
        "title": "上下文与检索策略",
        "content": draft_data["draft_content"],
    })
    assert applied.status_code == 200
    applied_data = applied.json()
    assert applied_data["status"] == "applied"
    wiki_file = wiki / "pages" / applied_data["wiki_path"]
    assert wiki_file.is_file()
    assert "type: learning-idea" in wiki_file.read_text(encoding="utf-8")

    undone = client.post(f"/api/ideas/{idea['id']}/undo")
    assert undone.status_code == 200
    assert undone.json()["status"] == "undone"
    assert not wiki_file.exists()


def test_idea_can_be_kept_local_without_writing_wiki(wiki):
    created = client.post("/api/ideas", json={
        "content": "我想验证一个学习方法是否真的能减少遗忘，但还没有设计实验。",
    }).json()
    draft = client.post(f"/api/ideas/{created['id']}/draft").json()
    kept = client.post(f"/api/ideas/{created['id']}/apply", json={
        "mode": "keep_local",
        "title": draft["draft_title"],
        "content": draft["draft_content"],
    })
    assert kept.status_code == 200
    assert kept.json()["status"] == "kept_local"
    assert not (wiki / "pages" / "学习想法").exists()
