"""费曼学习诊断器：优先使用已配置的模型，离线时退回可解释的本地规则。"""
from __future__ import annotations

import json
import re

from app.config import get_llm_config


def _sentences(text: str) -> list[str]:
    """Keep the actual learner sentences as explainable local-rule evidence."""
    return [part.strip() for part in re.split(r"(?<=[。！？!?])|\n+", text) if part.strip()]


def _sentence_count(text: str) -> int:
    return len(_sentences(text))


STRUCTURE_CHECKS = (
    {
        "key": "core",
        "label": "概念或流程主体",
        "pattern": r"是|指|用于|用来|解决|目标|作用|流程|系统|应用|模型|工具",
        "suggestion": "先用一句话说明：它是什么，或它要解决什么问题。",
    },
    {
        "key": "mechanism",
        "label": "步骤或因果关系",
        # Arrows and ordered verbs are deliberate evidence too. A learner should not
        # be penalized merely because they did not type the exact word "机制".
        "pattern": r"→|->|⇒|先.{0,30}(再|然后)|(?:提供|返回|执行|校验|调用|回传|传入|输出).{0,36}(?:返回|执行|校验|调用|回传|结果|模型|工具)|通过.{0,36}(使|让|来|从而)|因为.{0,36}(所以|因此|从而)|步骤|流程|机制",
        "suggestion": "把关键步骤用“先…再…”或“因为…所以…”连成一条链。",
    },
    {
        "key": "boundary_or_example",
        "label": "例子、条件或边界",
        "pattern": r"例如|比如|举例|场景|权限|非法|超时|异常|注入|边界|失败|限制|风险|条件|反例",
        "suggestion": "补一个真实场景、边界条件或反例，检验这套说法何时成立。",
    },
)


# These terms are not a subject-matter rubric.  They are concrete details that
# often disappear when a learner turns a full explanation into a short one.
# Keeping the list deliberately small lets the offline comparison explain its
# wording instead of pretending to judge factual correctness.
IMPORTANT_DETAIL_TERMS = (
    "schema", "tool_calls", "权限", "非法参数", "超时", "提示注入",
    "边界", "异常", "风险", "限制", "约束", "校验", "输入", "输出",
)
ANALOGY_PATTERN = re.compile(r"(?:就像|像|好比|类比|比作|当作|例如|比如)")


def _character_count(text: str) -> int:
    return len(re.sub(r"\s+|[，。；、：！？,.!?;:]", "", text))


def _important_terms(text: str) -> list[str]:
    lowered = text.lower()
    return [term for term in IMPORTANT_DETAIL_TERMS if term.lower() in lowered]


def compare_expression_quality(first: str, second: str) -> dict:
    """Describe observable differences between two learner expressions.

    Local mode can compare the learner's own wording without claiming that the
    explanation is factually correct.  The result is intentionally split into
    new points, simplification, and omitted details so the learning outcome
    does not reduce progress to a few boolean structure checks.
    """
    first_sentences = _sentences(first)
    second_sentences = _sentences(second)
    first_length = _character_count(first)
    second_length = _character_count(second)
    first_terms = _important_terms(first)
    second_terms = _important_terms(second)
    kept_terms = [term for term in first_terms if term in second_terms]
    first_has_analogy = any(ANALOGY_PATTERN.search(sentence) for sentence in first_sentences)
    analogy_sentences = [sentence[:180] for sentence in second_sentences if ANALOGY_PATTERN.search(sentence)]

    new_points: list[str] = []
    if analogy_sentences and not first_has_analogy:
        new_points.append(f"新增了类比或例子：{analogy_sentences[0]}")

    first_structure = explain_structure(first)
    second_structure = explain_structure(second)
    first_checks = {item["key"]: item for item in first_structure["checks"]}
    second_checks = {item["key"]: item for item in second_structure["checks"]}
    retained_labels = [
        second_checks[key]["label"]
        for key in second_checks
        if second_checks[key]["passed"] and first_checks[key]["passed"]
    ]

    simplified: list[str] = []
    if first_length and second_length <= first_length * 0.9:
        retained = "、".join(retained_labels) or "已检测到的表达结构"
        simplified.append(
            f"表达更紧凑：字数由约 {first_length} 减至约 {second_length}，仍保留{retained}。"
        )
    first_average = first_length / max(len(first_sentences), 1)
    second_average = second_length / max(len(second_sentences), 1)
    first_term_density = len(first_terms) / max(first_length, 1)
    second_term_density = len(second_terms) / max(second_length, 1)
    if first_average and second_average <= first_average * 0.82 and not simplified:
        simplified.append(
            f"句子更易扫读：平均句长由约 {first_average:.0f} 降至约 {second_average:.0f} 个字。"
        )

    omitted_terms = [term for term in first_terms if term not in second_terms]
    omitted_important = []
    if omitted_terms:
        omitted_important.append(
            f"省略了仍重要的细节：{'、'.join(omitted_terms[:4])}。下次复习可补回这些条件或校验点。"
        )

    return {
        "new_points": new_points,
        "simplified": simplified,
        "omitted_important": omitted_important,
        "metrics": {
            "first_characters": first_length,
            "second_characters": second_length,
            "first_average_sentence_length": round(first_average, 1),
            "second_average_sentence_length": round(second_average, 1),
            "first_technical_term_density": round(first_term_density, 3),
            "second_technical_term_density": round(second_term_density, 3),
            "important_term_coverage": {
                "kept": len(kept_terms),
                "total": len(first_terms),
            },
        },
    }


def _evidence_sentences(sentences: list[str], pattern: str) -> list[str]:
    matcher = re.compile(pattern)
    return [sentence[:180] for sentence in sentences if matcher.search(sentence)][:2]


def explain_structure(text: str) -> dict:
    """Return structural signals, never a claim that the facts are correct.

    Offline feedback has to be useful without pretending that a keyword matcher can
    understand a subject.  Each check therefore carries the learner's own sentence
    as evidence, and a missing signal is a prompt rather than a factual verdict.
    """
    normalized = text.strip()
    sentences = _sentences(normalized)
    checks: list[dict] = []
    for definition in STRUCTURE_CHECKS:
        evidence = _evidence_sentences(sentences, definition["pattern"])
        if definition["key"] == "core" and not evidence and (len(normalized) >= 40 or len(sentences) >= 2):
            evidence = [sentences[0][:180]] if sentences else []
        checks.append({
            "key": definition["key"],
            "label": definition["label"],
            "passed": bool(evidence),
            "evidence": evidence,
            "suggestion": definition["suggestion"],
        })
    strengths = [f"{item['label']}：{item['evidence'][0]}" for item in checks if item["passed"]]
    missing = [item["suggestion"] for item in checks if not item["passed"]]
    return {
        "checks": checks,
        "strengths": strengths,
        "missing": missing,
        "confidence": "structure_only",
        "is_complete": not missing,
    }


def local_diagnosis(explanation: str, title: str) -> tuple[list[dict], str]:
    """Generate explainable expression prompts for an offline learning session."""
    structure = explain_structure(explanation)
    gaps = [
        {
            "gap_type": "vague" if check["key"] == "core" else "missing",
            "content": check["suggestion"],
            "check_key": check["key"],
            "evidence": check["evidence"],
        }
        for check in structure["checks"] if not check["passed"]
    ]
    question = f"不用术语重述「{title}」：它解决什么问题，关键步骤怎样衔接？"
    return gaps, question


def _clean_json(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("模型没有返回对象")
    return value


def _normalize_gaps(raw_gaps: object) -> list[dict]:
    normalized = []
    if not isinstance(raw_gaps, list):
        return normalized
    for gap in raw_gaps[:3]:
        if not isinstance(gap, dict):
            continue
        content = str(gap.get("content", "")).strip()
        gap_type = str(gap.get("gap_type", "vague")).strip()
        if content and gap_type in {"missing", "wrong", "vague"}:
            normalized.append({"gap_type": gap_type, "content": content[:500]})
    return normalized


def _call_llm(config: dict, messages: list[dict]) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=20)
    response = client.chat.completions.create(
        model=config["model"], temperature=0.2, max_tokens=900, messages=messages,
    )
    return response.choices[0].message.content or "{}"


def diagnose(explanation: str, title: str, reference_html: str) -> tuple[list[dict], str, str]:
    """返回 (盲区, 下一问, 来源)，模型异常时不影响学习流程。"""
    config = get_llm_config()
    if config.get("mode") != "ai" or not config["api_key"]:
        gaps, question = local_diagnosis(explanation, title)
        return gaps, question, "local"
    try:
        content = _call_llm(config, [
                {"role": "system", "content": "你是费曼学习教练。只依据参考资料检查学习者讲解，不要编造事实。返回纯 JSON：{\"gaps\":[{\"gap_type\":\"missing|wrong|vague\",\"content\":\"简洁、可行动的中文反馈\"}],\"question\":\"一个下一步追问\"}。盲区最多 3 条。"},
                {"role": "user", "content": f"知识点：{title}\n\n参考资料：\n{reference_html[:12000]}\n\n学习者讲解：\n{explanation[:10000]}"},
            ])
        payload = _clean_json(content)
        gaps = _normalize_gaps(payload.get("gaps"))
        question = str(payload.get("question", "")).strip()[:500]
        if not question:
            raise ValueError("模型没有给出下一问")
        return gaps, question, "llm"
    except Exception:
        gaps, question = local_diagnosis(explanation, title)
        return gaps, question, "local"


def assess_gap_revision(revision: str, gap_content: str, title: str, reference_html: str) -> tuple[str, str, str]:
    """核对用户对单个盲区的补充。离线模式只记录为已补充，避免伪造“已验证”。"""
    config = get_llm_config()
    if config.get("mode") != "ai" or not config["api_key"]:
        return "revised", "补充已保存。配置学习助手后，可依据参考资料进一步核对是否已澄清。", "local"
    try:
        content = _call_llm(config, [
            {"role": "system", "content": "你是费曼学习教练。只依据参考资料核对学习者对一个盲区的补充。返回纯 JSON：{\"status\":\"verified|revised\",\"feedback\":\"简短中文反馈\"}。只有补充准确且真正回应盲区时才用 verified。"},
            {"role": "user", "content": f"知识点：{title}\n\n参考资料：\n{reference_html[:12000]}\n\n原盲区：{gap_content}\n\n学习者补充：{revision[:10000]}"},
        ])
        payload = _clean_json(content)
        status = str(payload.get("status", "revised")).strip()
        feedback = str(payload.get("feedback", "补充已保存，建议在下一次回顾中再次验证。")).strip()[:500]
        if status not in {"verified", "revised"}:
            status = "revised"
        return status, feedback or "补充已保存，建议在下一次回顾中再次验证。", "llm"
    except Exception:
        return "revised", "补充已保存。学习助手暂不可用，建议在下一次回顾中再次验证。", "local"
