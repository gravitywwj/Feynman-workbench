from app.services import tutor


def test_diagnose_uses_local_rules_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    gaps, question, source = tutor.diagnose("这是一个很短的解释。", "查询改写", "<p>reference</p>")
    assert source == "local"
    assert gaps
    assert question


def test_diagnose_falls_back_when_llm_call_fails(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(tutor, "_call_llm", lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    _, _, source = tutor.diagnose("这是一个足够长的解释，包含原因和一个例子，例如用于检索任务。", "查询改写", "<p>reference</p>")
    assert source == "local"


def test_diagnose_accepts_fenced_json(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(tutor, "_call_llm", lambda *_: "```json\n{\"gaps\":[{\"gap_type\":\"missing\",\"content\":\"补一个例子\"}],\"question\":\"下一问\"}\n```")
    gaps, question, source = tutor.diagnose("足够长的讲解，包含原因和场景，例如用于检索任务。", "查询改写", "<p>reference</p>")
    assert source == "llm"
    assert gaps == [{"gap_type": "missing", "content": "补一个例子"}]
    assert question == "下一问"


def test_local_gap_revision_is_not_marked_verified(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    status, _, source = tutor.assess_gap_revision("这是足够长的补充说明，解释了机制、原因并包含一个具体例子。", "补充例子", "查询改写", "<p>reference</p>")
    assert (status, source) == ("revised", "local")
