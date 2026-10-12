import sys
from types import SimpleNamespace

import pytest

from app import config
from app.services import review_coach, tutor


@pytest.fixture(autouse=True)
def isolate_saved_llm_profiles(tmp_path, monkeypatch):
    """Tutor unit tests must not inherit the developer's local saved API profile."""
    monkeypatch.setattr(config, "LLM_SETTINGS_PATH", tmp_path / "llm-settings.json")


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
    monkeypatch.setattr(config, "LLM_SETTINGS_PATH", tmp_path / "llm-settings.json")
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


def test_recall_brief_and_knowledge_note_have_inspectable_local_fallbacks(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    brief = tutor.build_recall_brief(
        title="查询改写", reference="# 查询改写\n查询改写会补足对象与场景。",
        note="我不确定约束条件什么时候重要。", gaps=["需要说明机制"],
        reflections=["下次用真实问题验证。"], persona="exam",
    )
    assert brief["persona"] == "exam"
    assert brief["source"] == "local"
    assert brief["question"]

    update = tutor.analyze_knowledge_note(
        note="我想比较改写前后的召回结果。", title="查询改写",
        evidence=[{"title": "查询改写", "path": "AI/rag/query-rewriting.md", "excerpt": "补足对象和场景。"}],
        persona="direct",
    )
    assert update["source"] == "local"
    assert update["proposal"]
    assert update["open_questions"]


def test_diagnose_accepts_fenced_json(monkeypatch):
    monkeypatch.setattr(tutor, "get_llm_config", lambda: {
        "api_key": "test-key", "base_url": "https://example.test", "model": "test", "mode": "ai",
    })
    monkeypatch.setattr(tutor, "_call_llm", lambda *_: "```json\n{\"gaps\":[{\"gap_type\":\"missing\",\"content\":\"补一个例子\"}],\"question\":\"下一问\"}\n```")
    gaps, question, source = tutor.diagnose("足够长的讲解，包含原因和场景，例如用于检索任务。", "查询改写", "<p>reference</p>")
    assert source == "llm"
    assert gaps == [{"gap_type": "concept_missing", "content": "补一个例子"}]
    assert question == "下一问"


def test_local_gap_revision_is_not_marked_verified(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    status, _, source = tutor.assess_gap_revision("这是足够长的补充说明，解释了机制、原因并包含一个具体例子。", "补充例子", "查询改写", "<p>reference</p>")
    assert (status, source) == ("revised", "local")


def test_gap_revision_without_reference_never_calls_ai(monkeypatch):
    monkeypatch.setattr(tutor, "get_llm_config", lambda: {
        "api_key": "test-key", "base_url": "https://example.test", "model": "test", "mode": "ai",
    })
    monkeypatch.setattr(tutor, "_call_llm", lambda *_: (_ for _ in ()).throw(AssertionError("must not call AI")))
    status, feedback, source = tutor.assess_gap_revision("补充内容", "原盲区", "主题", "")
    assert (status, source) == ("revised", "local")
    assert "没有可用参考资料" in feedback


def test_gap_revision_passes_retest_prompt_to_ai(monkeypatch):
    monkeypatch.setattr(tutor, "get_llm_config", lambda: {
        "api_key": "test-key", "base_url": "https://example.test", "model": "test", "mode": "ai",
    })
    prompts = []

    def fake_call(_config, messages):
        prompts.append(messages[-1]["content"])
        return '{"status":"verified","feedback":"符合资料"}'

    monkeypatch.setattr(tutor, "_call_llm", fake_call)
    status, _, source = tutor.assess_gap_revision(
        "补充说明", "原盲区", "主题", "<p>参考</p>", "用另一个情境判断",
    )
    assert (status, source) == ("verified", "llm")
    assert "用另一个情境判断" in prompts[0]


@pytest.mark.parametrize(("content", "expected"), [
    ('{"verdict":"pass","feedback":"符合资料","follow_up":"继续"}', ("pass", "llm", "source_reviewed")),
    ('{"verdict":"retry","feedback":"有缺口","follow_up":"再核对"}', ("retry", "llm", "unverified")),
    ("not json", ("retry", "local", "unverified")),
    ('{"verdict":"pass","feedback":"缺少追问"}', ("retry", "local", "unverified")),
    ('{"verdict":"pass","feedback":[],"follow_up":"继续"}', ("retry", "local", "unverified")),
    ('{"verdict":"pass","feedback":"符合资料","follow_up":{}}', ("retry", "local", "unverified")),
    ('{"verdict":1,"feedback":"符合资料","follow_up":"继续"}', ("retry", "local", "unverified")),
    (None, ("retry", "local", "unverified")),
])
def test_review_coach_only_source_grounded_ai_pass_is_verified(monkeypatch, content, expected):
    monkeypatch.setattr(review_coach, "get_llm_config", lambda: {
        "api_key": "test-key", "base_url": "https://example.test", "model": "test", "mode": "ai",
    })
    def create_completion(**_):
        if content is None:
            raise TimeoutError("mock timeout")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion)))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **_: fake_client))
    result = review_coach.assess(
        "answer", question="question", expected="source summary", required_points=["point"],
        reference_status="source", title="topic", reference_html="<p>source</p>", agent="strict",
    )
    assert (result[0], result[3], result[4]) == expected


def test_local_feedback_never_verifies_keyword_or_long_text(monkeypatch):
    monkeypatch.setattr(review_coach, "get_llm_config", lambda: {"mode": "local", "api_key": ""})
    for agent in ("feynman", "strict"):
        for answer in (
            "Input validation is forbidden. Output verification is forbidden. Skip both checks entirely.",
            "Input validation Output verification Input validation Output verification " * 20,
            "x" * 3000,
            "Input validation is required. Output verification is required.",
        ):
            result = review_coach.assess(
                answer, question="question", expected="summary", required_points=["Input validation", "Output verification"],
                reference_status="source", title="topic", reference_html="<p>source</p>", agent=agent,
            )
            assert result[0] == "retry"
            assert result[3:] == ("local", "unverified")


def test_review_coach_without_reference_does_not_call_ai(monkeypatch):
    monkeypatch.setattr(review_coach, "get_llm_config", lambda: (_ for _ in ()).throw(AssertionError("no reference")))
    result = review_coach.assess(
        "answer", question="question", expected="", required_points=[], reference_status="needs_review",
        title="topic", reference_html="", agent="strict",
    )
    assert result[0] == "retry" and result[3:] == ("local", "unverified")


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
