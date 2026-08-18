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


def test_session_can_be_simplified_into_learning_outcome(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写会补足问题中的对象、场景和约束，使检索更接近真实任务。例如把模糊问题改成包含上下文的查询。",
    }).json()
    completed = client.post(f"/api/study/sessions/{created['session']['id']}/simplify", json={
        "explanation": "查询改写就是先补全问题条件，再让检索系统用更明确的查询找资料。例如给问题加上对象和场景。",
    })
    assert completed.status_code == 200
    outcome = completed.json()["outcome"]
    assert outcome["first_explanation"]
    assert outcome["second_explanation"].startswith("查询改写就是")
    assert outcome["next_review_date"]
    assert "improvements" in outcome
    assert "reassessment_source" in outcome


def test_learning_outcome_explains_quality_gains_when_structure_is_unchanged(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": (
            "查询改写会补足问题中的对象、场景和约束条件，所以检索能更准确地返回资料。"
            "系统还会检查输入限制和异常情况，以避免不完整的问题误导后续判断。"
        ),
    }).json()
    completed = client.post(f"/api/study/sessions/{created['session']['id']}/simplify", json={
        "explanation": "查询改写像给问题填一张申请单：补上对象和场景，再交给检索系统找资料。",
    })

    assert completed.status_code == 200
    outcome = completed.json()["outcome"]
    assert any("申请单" in item for item in outcome["improvements"])
    assert any("更紧凑" in item for item in outcome["improvements"])


def test_second_expression_reassesses_local_structure_and_persists_feedback(wiki):
    first = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "elapsed_seconds": 75,
        "explanation": "查询改写就是把问题说得更清楚，让检索更容易找到相关资料。",
    })
    assert first.status_code == 200
    created = first.json()
    assert created["gaps"]
    session_id = created["session"]["id"]
    feedback = client.post(f"/api/study/sessions/{session_id}/diagnosis-feedback", json={"verdict": "disputed"})
    assert feedback.status_code == 200
    completed = client.post(f"/api/study/sessions/{session_id}/simplify", json={
        "elapsed_seconds": 125,
        "explanation": "查询改写先补上对象和约束，再让检索系统查找资料。例如把模糊问题改成带场景的查询。",
    })
    assert completed.status_code == 200
    outcome = completed.json()["outcome"]
    assert outcome["remaining_gaps"] == []
    assert any("新增" in item for item in outcome["improvements"])
    detail = client.get(f"/api/study/sessions/{session_id}").json()
    assert detail["diagnosis_feedback"][0]["verdict"] == "disputed"
    summary = client.get("/api/study/today-summary").json()
    assert summary["elapsed_seconds"] >= 125


def test_home_action_prioritizes_a_startable_concept(wiki):
    response = client.get("/api/study/home")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "start"
    assert data["page_path"].endswith(".md")
    assert data["reason"] == "mastery_state"
    assert "尚未留下" in data["detail"] or "已读完" in data["detail"]
    assert data["alternatives"]


def test_workspace_preview_and_demo_setup_activate_a_first_run_experience(wiki):
    preview = client.get("/api/concepts/preview", params={"path": str(wiki)})
    assert preview.status_code == 200
    assert preview.json()["page_count"] >= 1
    settings = client.put("/api/study/workspace", json={
        "mode": "demo", "wiki_path": None, "diagnostic_mode": "local", "daily_review_goal": 7,
    })
    assert settings.status_code == 200
    assert settings.json()["mode"] == "local"
    assert settings.json()["uses_environment_path"] is True
    assert settings.json()["configured"] is True
    assert settings.json()["daily_review_goal"] == 7
    assert settings.json()["learning_goal"] == "long_term"


def test_local_llm_profiles_override_environment_fallback_and_can_switch(wiki, monkeypatch):
    initial = client.get("/api/study/llm-settings")
    assert initial.status_code == 200
    assert initial.json()["configured"] is False
    assert "api_key" not in initial.json()
    assert client.post("/api/study/llm-settings/test").status_code == 400

    saved = client.put("/api/study/llm-settings", json={
        "api_key": "local-test-secret-1234",
        "base_url": "http://127.0.0.1:11434/v1/",
        "model": "example-local-model",
        "profile_name": "本地模型",
        "profile_id": None,
    })
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["source"] == "local"
    assert saved.json()["active_profile_name"] == "本地模型"
    assert saved.json()["api_key_masked"] != "local-test-secret-1234"
    assert saved.json()["base_url"] == "http://127.0.0.1:11434/v1"

    from app import config
    assert config.get_llm_config()["api_key"] == "local-test-secret-1234"
    first_id = saved.json()["active_profile_id"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-fallback-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://fallback.example/v1")
    monkeypatch.setenv("FEYNMAN_LLM_MODEL", "fallback-model")
    assert config.get_llm_config()["api_key"] == "local-test-secret-1234"

    second = client.put("/api/study/llm-settings", json={
        "api_key": "second-test-secret-5678",
        "base_url": "https://example.test/v1",
        "model": "example-cloud-model",
        "profile_name": "云端模型",
        "profile_id": None,
    })
    assert second.status_code == 200
    assert second.json()["active_profile_name"] == "云端模型"
    assert len(second.json()["profiles"]) == 2

    activated = client.post(f"/api/study/llm-settings/{first_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["active_profile_name"] == "本地模型"
    assert config.get_llm_config()["api_key"] == "local-test-secret-1234"

    removed = client.delete(f"/api/study/llm-settings/{first_id}")
    assert removed.status_code == 200
    assert removed.json()["active_profile_name"] == "云端模型"
    assert config.get_llm_config()["api_key"] == "second-test-secret-5678"
    second_id = removed.json()["active_profile_id"]
    fallback = client.delete(f"/api/study/llm-settings/{second_id}")
    assert fallback.status_code == 200
    assert fallback.json()["source"] == "environment"
    assert config.get_llm_config()["api_key"] == "environment-fallback-key"


def test_llm_connection_result_is_saved_on_the_active_profile(wiki, monkeypatch):
    saved = client.put("/api/study/llm-settings", json={
        "api_key": "profile-test-secret",
        "base_url": "https://example.test/v1",
        "model": "example-model",
        "profile_name": "可测试连接",
        "profile_id": None,
    })
    assert saved.status_code == 200

    import openai

    class FakeCompletions:
        @staticmethod
        def create(**_): return object()

    class FakeOpenAI:
        def __init__(self, **_): self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    checked = client.post("/api/study/llm-settings/test")
    assert checked.status_code == 200
    assert checked.json()["ok"] is True
    profile = client.get("/api/study/llm-settings").json()["profiles"][0]
    assert profile["last_test"]["ok"] is True
    assert profile["last_test"]["tested_at"]


def test_folder_picker_can_be_cancelled_without_touching_workspace(wiki, monkeypatch):
    class FakeRoot:
        def withdraw(self): pass
        def attributes(self, *_): pass
        def destroy(self): pass

    import tkinter
    from tkinter import filedialog

    monkeypatch.setattr(tkinter, "Tk", FakeRoot)
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_: "")
    response = client.post("/api/study/workspace/pick-folder")
    assert response.status_code == 200
    assert response.json() == {"cancelled": True}


def test_review_summary_report_and_export_import_are_additive(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写会补全任务对象和上下文条件，所以检索系统能把模糊问题变得更可执行。例如给问题补上目标用户和使用场景。",
    }).json()
    session_id = created["session"]["id"]
    client.post(f"/api/study/sessions/{session_id}/simplify", json={
        "explanation": "查询改写就是把缺少条件的问题说完整，让检索用更清楚的说法找资料。例如加入对象、目标和场景。",
    })
    summary = client.get("/api/study/reviews/summary")
    assert summary.status_code == 200
    assert summary.json()["goal"] >= 1
    report = client.get("/api/study/weekly-report")
    assert report.status_code == 200
    assert report.json()["summary"]["completed_sessions"] >= 1
    exported = client.get("/api/study/export").json()
    preview = client.post("/api/study/import/preview", json={"payload": exported})
    assert preview.status_code == 200
    assert preview.json()["incoming"]["sessions"] >= 1
    imported = client.post("/api/study/import", json={"payload": exported})
    assert imported.status_code == 200
    assert imported.json()["imported"]["sessions"] == 0


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


def test_recall_brief_uses_selected_persona_and_local_learning_evidence(wiki):
    client.put("/api/study/notes", params={"page_path": PAGE}, json={
        "content": "我知道改写要补关键词，但还不确定什么时候应该补场景与约束。",
    })
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写会让问题更清楚，但我还没有说明它如何改变检索结果。",
    })
    assert created.status_code == 200
    response = client.post("/api/study/recall-brief", json={"page_path": PAGE, "persona": "direct"})
    assert response.status_code == 200
    brief = response.json()
    assert brief["persona"] == "direct"
    assert brief["persona_label"] == "直率追问"
    assert brief["question"]
    assert len(brief["follow_ups"]) >= 2
    assert brief["source"] == "local"


def test_knowledge_update_requires_review_then_supports_safe_undo(wiki):
    source = wiki / "pages" / "AI" / "rag" / "query-rewriting.md"
    before = source.read_text(encoding="utf-8")
    created = client.post("/api/study/knowledge-updates", json={
        "page_path": PAGE,
        "persona": "reflective",
        "content": "我发现查询改写不只是补关键词，还要补足对象、场景和约束；下次要比较改写前后的召回结果。",
    })
    assert created.status_code == 200
    draft = created.json()
    assert draft["status"] == "draft"
    assert draft["persona"] == "reflective"
    assert draft["evidence"]
    assert source.read_text(encoding="utf-8") == before

    applied = client.post(f"/api/study/knowledge-updates/{draft['id']}/apply", json={
        "target_mode": "append_current",
        "proposal": "- 学习记录：改写前后应比较召回结果，并说明对象、场景与约束。",
        "proposed_title": "",
    })
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    written = source.read_text(encoding="utf-8")
    assert "## 学习增量" in written
    assert f"feynman-workbench:update:{draft['id']}" in written

    undone = client.post(f"/api/study/knowledge-updates/{draft['id']}/undo")
    assert undone.status_code == 200
    assert undone.json()["status"] == "undone"
    assert source.read_text(encoding="utf-8") == before


def test_reflections_are_timestamped_exported_and_can_be_summarized(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写需要补足对象、场景和约束条件，才能让检索返回更贴近真实任务的资料。例如将模糊问题改成带有用户目标和上下文的查询。",
    }).json()
    reflection = client.post("/api/study/reflections", json={
        "content": "我原来只记得补关键词，现在理解了对象和场景也会改变检索结果。下次要用一个实际问题验证。",
        "page_path": PAGE,
        "session_id": created["session"]["id"],
    })
    assert reflection.status_code == 200
    assert reflection.json()["source"] == "session"
    assert reflection.json()["page_path"] == PAGE

    listed = client.get("/api/study/reflections")
    assert listed.status_code == 200
    assert listed.json()["reflections"][0]["content"].startswith("我原来只记得")

    updated = client.put(f"/api/study/reflections/{reflection.json()['id']}", json={
        "content": "我现在理解对象、场景和约束都会影响检索结果。下次要用一个实际问题验证。",
    })
    assert updated.status_code == 200
    assert updated.json()["content"].startswith("我现在理解")

    summary = client.post("/api/study/reflections/summary", json={"reflection_ids": [reflection.json()["id"]]})
    assert summary.status_code == 200
    assert summary.json()["source"] == "summary"
    assert summary.json()["summary_source"] in {"local", "llm"}

    exported = client.get("/api/study/export").json()
    assert exported["version"] == 3
    assert len(exported["reflections"]) == 2
    assert exported["reflections"][0]["session_id"] == created["session"]["id"]
    preview = client.post("/api/study/import/preview", json={"payload": exported})
    assert preview.status_code == 200
    assert preview.json()["incoming"]["reflections"] == 2


def test_due_card_can_be_reviewed(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写通过把不完整的问题改成检索友好的表达来提高召回。它很重要，因为检索结果决定后续回答质量。比如把模糊提问补上上下文和关键词。",
    }).json()
    due = client.get("/api/study/reviews/due")
    assert due.status_code == 200
    assert due.json()["cards"] == []
    cram = client.get("/api/study/reviews/queue", params={"mode": "cram"})
    assert cram.status_code == 200
    assert cram.json()["total"] >= 1
    card_id = created["cards"][0]["id"]
    review = client.post(f"/api/study/reviews/{card_id}", json={"rating": "good"})
    assert review.status_code == 200
    assert review.json()["interval"] >= 1
    assert review.json()["reps"] == 1
    assert review.json()["interval"] == 3


def test_strict_review_attempt_is_persisted_and_queue_supports_cram(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写会补足问题中的对象、任务与上下文，使检索系统能返回更贴近用户目标的内容。例如把模糊问题改成包含场景和约束条件的查询。",
    }).json()
    card_id = created["cards"][0]["id"]
    attempt = client.post(f"/api/study/reviews/{card_id}/attempt", json={
        "agent": "strict",
        "answer": "查询改写的目的是让检索条件更明确，通过补上对象、场景和约束来改善召回。例如把泛泛的问题改成可执行的查询。",
    })
    assert attempt.status_code == 200
    data = attempt.json()
    assert data["agent"] == "strict"
    assert data["agent_name"] == "突击教练"
    assert data["verdict"] in {"pass", "retry"}
    exported = client.get("/api/study/export").json()
    assert exported["review_attempts"][0]["card_id"] == card_id


def test_history_and_gap_revision_are_persisted(wiki):
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写能让检索系统找到更匹配资料，但我还说不清它具体如何做到。",
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
    assert (good["interval"], good["reps"], good["ease"]) == (3, 1, 2.5)
    easy = client.post(f"/api/study/reviews/{card_id}", json={"rating": "easy"}).json()
    assert (easy["interval"], easy["reps"]) == (14, 2)
    again = client.post(f"/api/study/reviews/{card_id}", json={"rating": "again"}).json()
    assert (again["interval"], again["reps"]) == (1, 0)


def test_gap_revision_validation_and_missing_gap(wiki):
    assert client.post("/api/study/gaps/999/revision", json={"revision": "足够长的补充说明，但这个盲区并不存在，因此不应保存。"}).status_code == 404
    created = client.post("/api/study/sessions", json={
        "page_path": PAGE,
        "explanation": "查询改写通过补充上下文改善检索效果，使后续召回结果更接近真正要解决的任务。",
    }).json()
    gap_id = created["gaps"][0]["id"]
    assert client.post(f"/api/study/gaps/{gap_id}/revision", json={"revision": "太短"}).status_code == 400


def test_export_and_relink_orphaned_records(wiki):
    client.put("/api/study/notes", params={"page_path": PAGE}, json={"content": "需要在项目里继续验证。"})
    client.post("/api/study/reflections", json={"content": "这条心得也应跟随知识点的新路径。", "page_path": PAGE})
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
    assert client.get("/api/study/reflections").json()["reflections"][0]["page_path"].endswith("moved.md")
    exported = client.get("/api/study/export")
    assert exported.status_code == 200
    assert exported.json()["format"] == "feynman-workbench-export"
    assert exported.json()["notes"][0]["page_path"].endswith("moved.md")
