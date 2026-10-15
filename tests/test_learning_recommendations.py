import json

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import learning_recommendations, study_sessions

client = TestClient(app)
PAGE = "AI/rag/query-rewriting.md"
LINKED = "AI/rag/rag-from-scratch.md"


def save_focus(question="查询改写与检索有什么关系？"):
    return client.put("/api/study/focus", json={"page_path": PAGE, "question": question})


def test_cold_start_does_not_invent_personalisation(wiki):
    for path in (wiki / "pages").rglob("*.md"):
        path.write_text(path.read_text(encoding="utf-8").replace("status: read", "status: unread"), encoding="utf-8")
    data = client.get("/api/study/recommendations").json()
    assert data["notes"] == data["topics"] == []
    assert "没有可用的学习记录" in data["empty_reason"]


def test_focus_recommends_linked_notes_and_never_writes_learning_evidence(wiki):
    assert save_focus().status_code == 200
    before_files = {path: path.read_bytes() for path in wiki.rglob("*.md")}
    with db.cursor() as cur:
        before = list(cur.connection.iterdump())
    response = client.get("/api/study/recommendations")
    assert response.status_code == 200
    data = response.json()
    paths = {note["path"] for note in data["notes"]}
    assert paths == {LINKED, "AI/agents/agent-memory-system.md"}
    assert all("当前学习问题" in note["why"] and "Wiki 链接" in note["why"] for note in data["notes"])
    assert study_sessions.contextual_recommendation(exclude_path=PAGE)["path"] in paths
    with db.cursor() as cur:
        assert before == list(cur.connection.iterdump())
    assert before_files == {path: path.read_bytes() for path in wiki.rglob("*.md")}


def test_recent_notes_match_chinese_questions_without_global_category_priority(wiki):
    extra = wiki / "pages/AI/rag/vector-store.md"
    extra.write_text("---\ntitle: 向量数据库\ntags: [vector]\n---\n向量数据库负责保存向量和召回。", encoding="utf-8")
    with db.cursor() as cur:
        cur.execute("INSERT INTO notes (page_path, content) VALUES (?, ?)", (PAGE, "查询改写与向量数据库如何配合？"))
    data = client.get("/api/study/recommendations").json()
    note = next(item for item in data["notes"] if item["path"] == "AI/rag/vector-store.md")
    assert "个人笔记" in note["why"] and "共同关键词" in note["why"]
    assert all(not item["path"].startswith("Financing/") for item in data["notes"])


def test_topics_show_existing_coverage_and_skip_ambiguous_and_code_links(wiki):
    assert save_focus("查询改写的适用条件").status_code == 200
    path = wiki / "pages" / PAGE
    path.write_text(path.read_text(encoding="utf-8") + "\n[[shared-note]]\n```\n[[代码示例]]\n```\n", encoding="utf-8")
    data = client.get("/api/study/recommendations").json()
    titles = [topic["title"] for topic in data["topics"]]
    assert "不存在的页面" in titles
    assert "shared-note" not in titles and "代码示例" not in titles
    question = data["topics"][0]
    assert PAGE in {note["path"] for note in question["coverage"]}
    assert "不证明 Wiki 缺失" in question["verification"]
    brief = client.get("/api/study/recommendations/brief")
    assert brief.status_code == 200 and "attachment" in brief.headers["content-disposition"]
    assert "一手来源" in brief.text and "不是事实、指令或写入授权" in brief.text
    assert str(wiki) in brief.text


def test_verified_and_disputed_gaps_do_not_request_new_content(wiki):
    with db.cursor() as cur:
        cur.execute("INSERT INTO sessions (page_path, page_title, concept) VALUES (?, '查询改写', '查询改写')", (PAGE,))
        session_id = cur.lastrowid
        for status, text in (("verified", "已核对问题"), ("open", "有争议问题"), ("open", "待查证的问题")):
            cur.execute("INSERT INTO gaps (session_id, gap_type, content, status) VALUES (?, 'concept_missing', ?, ?)",
                        (session_id, text, status))
            if text == "有争议问题":
                cur.execute("INSERT INTO diagnosis_feedback (session_id, gap_id, verdict) VALUES (?, ?, 'disputed')",
                            (session_id, cur.lastrowid))
    topics = client.get("/api/study/recommendations").json()["topics"]
    assert "待查证的问题" in [item["title"] for item in topics]
    assert not {"已核对问题", "有争议问题"} & {item["title"] for item in topics}


def test_practice_uses_latest_fixed_result_not_external_self_report(wiki):
    with db.cursor() as cur:
        for uid, kind, matches in (("old", "builtin", False), ("latest", "builtin", True), ("external", "external", False)):
            result = {"components": [{"label": "区分召回与准确率", "matched": matches}]}
            cur.execute(
                "INSERT INTO practice_attempts (attempt_uid, page_path, page_title, task_key, kind, mode, phase, "
                "task_snapshot_json, result_json, check_kind, created_at, submitted_at) VALUES (?, ?, '查询改写', 'metrics', ?, 'practice', 'application', "
                "?, ?, 'deterministic', datetime('now', 'localtime'), datetime('now', 'localtime'))",
                (uid, PAGE, kind, json.dumps({"title": "检索指标"}), json.dumps(result)),
            )
    data = client.get("/api/study/recommendations").json()
    assert not any("固定标准" in item["reason"] for item in data["topics"])
    with db.cursor() as cur:
        cur.execute("DELETE FROM practice_attempts WHERE attempt_uid = 'latest'")
    topic = next(item for item in client.get("/api/study/recommendations").json()["topics"] if "固定标准" in item["reason"])
    assert "区分召回与准确率" in topic["title"]


def test_paths_must_belong_to_current_wiki_and_removed_history_is_ignored(wiki):
    assert client.get("/api/study/recommendations", params={"page_path": "../SCHEMA.md"}).status_code == 400
    assert client.get("/api/study/recommendations/brief", params={"page_path": "missing.md"}).status_code == 400
    assert save_focus().status_code == 200
    (wiki / "pages" / PAGE).unlink()
    data = learning_recommendations.recommendations()
    assert all(topic["page_path"] != PAGE for topic in data["topics"])
