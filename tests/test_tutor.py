from app.services import tutor


def test_diagnose_uses_local_rules_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    gaps, question, source = tutor.diagnose("这是一个很短的解释。", "查询改写", "<p>reference</p>")
    assert source == "local"
    assert gaps
    assert question


def test_env_file_is_loaded_without_overriding_process_variables(tmp_path, monkeypatch):
    import app.config as config

    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config.load_dotenv(env_file)
    assert config.get_llm_config()["api_key"] == "from-file"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-process")
    config.load_dotenv(env_file)
    assert config.get_llm_config()["api_key"] == "from-process"


def test_diagnose_falls_back_when_llm_call_fails(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(tutor, "_call_llm", lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    _, _, source = tutor.diagnose("这是一个足够长的解释，包含原因和一个例子，例如用于检索任务。", "查询改写", "<p>reference</p>")
    assert source == "local"


def test_diagnose_accepts_fenced_json(monkeypatch):
    monkeypatch.setattr(tutor, "get_llm_config", lambda: {
        "api_key": "test-key", "base_url": "https://example.test", "model": "test", "mode": "ai",
    })
    monkeypatch.setattr(tutor, "_call_llm", lambda *_: "```json\n{\"gaps\":[{\"gap_type\":\"missing\",\"content\":\"补一个例子\"}],\"question\":\"下一问\"}\n```")
    gaps, question, source = tutor.diagnose("足够长的讲解，包含原因和场景，例如用于检索任务。", "查询改写", "<p>reference</p>")
    assert source == "llm"
    assert gaps == [{"gap_type": "missing", "content": "补一个例子"}]
    assert question == "下一问"


def test_local_gap_revision_is_not_marked_verified(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    status, _, source = tutor.assess_gap_revision("这是足够长的补充说明，解释了机制、原因并包含一个具体例子。", "补充例子", "查询改写", "<p>reference</p>")
    assert (status, source) == ("revised", "local")


def test_local_structure_prompt_accepts_explicit_function_calling_chain(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    explanation = (
        "应用先提供 schema，模型返回 tool_calls，应用校验权限、非法参数和超时后执行工具，"
        "再把结果回传模型。这样可以避免提示注入把未授权操作直接执行。"
    )
    structure = tutor.explain_structure(explanation)
    assert all(check["passed"] for check in structure["checks"])
    assert any("tool_calls" in item for item in structure["strengths"])
    gaps, _, source = tutor.diagnose(explanation, "Function calling", "<p>reference</p>")
    assert source == "local"
    assert gaps == []


def test_expression_comparison_detects_analogy_simplification_and_omitted_details():
    first = (
        "Function calling 中，应用先提供 schema，模型返回 tool_calls，应用校验权限、非法参数和超时后执行工具，"
        "再把结果回传模型，以避免提示注入触发未授权操作。"
    )
    second = "它像一张工具申请单：模型先填申请，应用核对后去办，再把结果交回。"

    comparison = tutor.compare_expression_quality(first, second)

    assert any("工具申请单" in item for item in comparison["new_points"])
    assert comparison["simplified"]
    assert any("权限" in item for item in comparison["omitted_important"])
