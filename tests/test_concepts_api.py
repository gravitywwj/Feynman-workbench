"""概念库 API 集成测试（TestClient，fixture wiki 注入）"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_list_concepts(wiki):
    r = client.get("/api/concepts")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 7
    assert data["sections"] == {"AI": 4, "Financing": 3}
    titles = {c["title"] for c in data["concepts"]}
    assert "Query Rewriting 查询改写" in titles


def test_list_filter_section(wiki):
    r = client.get("/api/concepts", params={"section": "Financing"})
    data = r.json()
    assert data["total"] == 3
    assert {c["path"] for c in data["concepts"]} == {
        "Financing/cashflow/budget-and-savings.md",
        "Financing/investing/investment-basics.md",
        "Financing/references/shared-note.md",
    }


def test_list_search(wiki):
    r = client.get("/api/concepts", params={"q": "memory"})
    data = r.json()
    assert data["total"] == 1
    assert data["concepts"][0]["title"] == "Agent Memory System"


def test_get_page(wiki):
    r = client.get("/api/concepts/page", params={"path": "AI/rag/query-rewriting.md"})
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["title"] == "Query Rewriting 查询改写"
    assert "<h1" in data["html"]


def test_get_page_404(wiki):
    r = client.get("/api/concepts/page", params={"path": "nope.md"})
    assert r.status_code == 404


def test_get_page_rejects_path_traversal(wiki):
    r = client.get("/api/concepts/page", params={"path": "../SCHEMA.md"})
    assert r.status_code == 400


def test_get_graph(wiki):
    r = client.get("/api/concepts/graph")
    assert r.status_code == 200
    data = r.json()
    assert len(data["nodes"]) == 7
    assert any(
        link["source"].endswith("query-rewriting.md") and link["target"].endswith("rag-from-scratch.md")
        for link in data["links"]
    )


def test_put_meta_updates_file(wiki):
    r = client.put("/api/concepts/meta", json={
        "path": "AI/rag/query-rewriting.md", "status": "read", "importance": "high"})
    assert r.status_code == 200
    assert r.json()["updated"] == {"status": "read", "importance": "high"}
    # 文件确实被改了，且重新扫描能看到
    r2 = client.get("/api/concepts", params={"q": "Query"})
    assert r2.json()["concepts"][0]["status"] == "read"
    assert r2.json()["concepts"][0]["importance"] == "high"


def test_put_meta_invalid_value(wiki):
    r = client.put("/api/concepts/meta", json={"path": "AI/rag/query-rewriting.md", "status": "done"})
    assert r.status_code == 400


def test_put_meta_can_clear_importance(wiki):
    path = "AI/rag/query-rewriting.md"
    client.put("/api/concepts/meta", json={"path": path, "importance": "high"})
    r = client.put("/api/concepts/meta", json={"path": path, "importance": ""})
    assert r.status_code == 200
    assert r.json()["updated"]["importance"] == ""
    listed = client.get("/api/concepts", params={"q": "Query"}).json()["concepts"][0]
    assert listed["importance"] == ""


def test_put_meta_traversal(wiki):
    r = client.put("/api/concepts/meta", json={"path": "../SCHEMA.md", "status": "read"})
    assert r.status_code == 400
