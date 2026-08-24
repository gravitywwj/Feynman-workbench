"""费曼学习诊断器：优先使用已配置的模型，离线时退回可解释的本地规则。"""
from __future__ import annotations

import json
import re

from app.config import get_llm_config


PERSONAS = {
    "feynman": {
        "label": "费曼教练",
        "intro": "用自己的话重建理解。先说最确定的一点，再补上因果和例子。",
        "instruction": "语气平静、具体，以苏格拉底式追问帮助学习者自己说清楚。",
    },
    "direct": {
        "label": "直率追问",
        "intro": "直接回答问题。不要用空泛词，说明对象、机制和边界。",
        "instruction": "语气直接、克制，不表扬，不绕弯；指出缺口后立刻给出下一问。",
    },
    "exam": {
        "label": "考试冲刺",
        "intro": "先抓最容易失分的链条：定义、关键步骤、条件和一个例子。",
        "instruction": "像考前口试教练，聚焦高频关键点与易混边界，问题短而明确。",
    },
    "reflective": {
        "label": "温和复盘",
        "intro": "回看你原来的想法，再找出现在仍不确定的一点。",
        "instruction": "语气温和但不空泛，连接学习笔记与已知盲区，帮助形成下一步行动。",
    },
}


def normalize_persona(persona: str | None) -> str:
    return persona if persona in PERSONAS else "feynman"


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


# These are deliberately observable expression signals, not a claim that a
# keyword match proves factual understanding.  The learner chooses two or
# three before submitting; the service then verifies that those signals are
# actually present in the expression.
UNDERSTANDING_EVIDENCE = (
    {
        "key": "definition",
        "label": "定义或目标",
        "pattern": r"是|指|用于|用来|解决|目标|作用|流程|系统|应用|模型|工具",
        "suggestion": "用一句话说明：它是什么，或它要解决什么问题。",
    },
    {
        "key": "mechanism",
        "label": "因果机制或步骤",
        "pattern": r"→|->|⇒|先.{0,30}(再|然后)|(?:提供|返回|执行|校验|调用|回传|传入|输出).{0,36}(?:返回|执行|校验|调用|回传|结果|模型|工具)|通过.{0,36}(使|让|来|从而)|因为.{0,36}(所以|因此|从而)|步骤|流程|机制",
        "suggestion": "把关键步骤用“先…再…”或“因为…所以…”连成一条链。",
    },
    {
        "key": "example",
        "label": "具体例子或场景",
        "pattern": r"例如|比如|举例|场景|案例|好比|就像",
        "suggestion": "补一个真实场景或例子，说明它具体怎样发挥作用。",
    },
    {
        "key": "boundary",
        "label": "适用边界或条件",
        "pattern": r"权限|非法|超时|异常|注入|边界|失败|限制|风险|条件|反例|不适用|前提",
        "suggestion": "写出一个条件、限制或反例，说明它不总是在所有情况下成立。",
    },
)


# These names describe the *kind of repair to practise*, rather than claiming a
# diagnosis is factually correct.  Older exports used missing/wrong/vague; keep
# accepting them so a local knowledge base can be upgraded without migration of
# user records.
GAP_TYPE_ALIASES = {
    "missing": "concept_missing",
    "wrong": "causal_error",
    "vague": "concept_missing",
    "concept_missing": "concept_missing",
    "causal_error": "causal_error",
    "boundary_missing": "boundary_missing",
    "transfer_failure": "transfer_failure",
}

GAP_TYPE_META = {
    "concept_missing": {
        "label": "概念缺失",
        "goal": "补出对象、目标和一句可核对的定义。",
        "practice_prompt": "用一句话补全：它是什么、解决什么问题；再指出资料中支持这句话的一处原文。",
        "retest_prompt": "换一个没有出现在原笔记里的场景：它仍然解决什么问题？先说对象和目标，再说明为什么。",
    },
    "causal_error": {
        "label": "因果链错误",
        "goal": "把输入、关键步骤和结果连成可检验的因果链。",
        "practice_prompt": "写出一条“因为…所以…”或“先…再…”链：每一步如何导致下一步？用资料中的一处依据核对。",
        "retest_prompt": "换一个输入或约束条件：关键步骤会怎样变化，结果为什么会随之变化？请写完整因果链。",
    },
    "boundary_missing": {
        "label": "边界条件遗漏",
        "goal": "说明这套说法何时成立、何时需要额外条件。",
        "practice_prompt": "补一个成立条件、限制或反例：少了它会发生什么？回原文找出对应条件。",
        "retest_prompt": "换一个更苛刻的条件或反例：原结论还能直接成立吗？说明需要补上的限制。",
    },
    "transfer_failure": {
        "label": "例子迁移失败",
        "goal": "把规则迁移到一个新场景，而不是只复述原例。",
        "practice_prompt": "选择一个与你资料不同的真实小场景，说明这条规则怎样应用，以及哪一步最关键。",
        "retest_prompt": "再换一个领域或任务场景：用同一规则作判断，并说明为什么不是简单套用原例。",
    },
}


def normalize_gap_type(value: object) -> str:
    return GAP_TYPE_ALIASES.get(str(value or "").strip(), "concept_missing")


def gap_type_meta(value: object) -> dict:
    return GAP_TYPE_META[normalize_gap_type(value)]


def _normalise_evidence_keys(evidence_keys: list[str] | None) -> list[str]:
    allowed = {item["key"] for item in UNDERSTANDING_EVIDENCE}
    selected: list[str] = []
    for value in evidence_keys or []:
        key = str(value).strip()
        if key in allowed and key not in selected:
            selected.append(key)
    return selected


def inspect_understanding_evidence(text: str, evidence_keys: list[str] | None = None) -> dict:
    """Return selected evidence alongside the learner sentences that show it."""
    sentences = _sentences(text.strip())
    selected = _normalise_evidence_keys(evidence_keys)
    checks = []
    for definition in UNDERSTANDING_EVIDENCE:
        evidence = _evidence_sentences(sentences, definition["pattern"])
        if definition["key"] == "definition" and not evidence and (len(text.strip()) >= 8 or sentences):
            evidence = [sentences[0][:180]] if sentences else []
        checks.append({
            "key": definition["key"],
            "label": definition["label"],
            "selected": definition["key"] in selected,
            "passed": bool(evidence),
            "evidence": evidence,
            "suggestion": definition["suggestion"],
        })
    return {"selected": selected, "checks": checks}


def require_understanding_evidence(text: str, evidence_keys: list[str]) -> dict:
    """Require two to three learner-chosen, visible forms of understanding."""
    selected = _normalise_evidence_keys(evidence_keys)
    if not 2 <= len(selected) <= 3:
        raise ValueError("请任选 2 到 3 项理解证据：定义或目标、因果机制、具体例子、适用边界。")
    result = inspect_understanding_evidence(text, selected)
    missing = [item["label"] for item in result["checks"] if item["selected"] and not item["passed"]]
    if missing:
        raise ValueError(f"你已选择“{'、'.join(missing)}”，但表达中还没有可观察到的对应内容。请补充后再诊断。")
    return result


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


def local_diagnosis(explanation: str, title: str, uncertainty: str = "") -> tuple[list[dict], str]:
    """Generate explainable expression prompts for an offline learning session."""
    structure = explain_structure(explanation)
    check_types = {
        "core": "concept_missing",
        "mechanism": "causal_error",
        "boundary_or_example": "transfer_failure",
    }
    gaps = [
        {
            "gap_type": check_types.get(check["key"], "concept_missing"),
            "content": check["suggestion"],
            "check_key": check["key"],
            "evidence": check["evidence"],
        }
        for check in structure["checks"] if not check["passed"]
    ]
    focus = uncertainty.strip()[:300]
    if focus:
        focus_type = (
            "boundary_missing" if re.search(r"边界|条件|限制|风险|异常|反例", focus)
            else "transfer_failure" if re.search(r"例子|场景|迁移|应用", focus)
            else "causal_error" if re.search(r"原因|机制|步骤|为什么|因果", focus)
            else "concept_missing"
        )
        gaps.insert(0, {
            "gap_type": focus_type,
            "content": f"你标记的最不确定点是「{focus}」。先回到当前资料，找出支持或反驳它的原文依据。",
            "check_key": "uncertainty",
            "evidence": [],
        })
        question = f"先处理你标记的不确定点「{focus}」：资料中的哪句话、步骤或条件能帮助你核对它？"
    else:
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
        raw_type = str(gap.get("gap_type", "concept_missing")).strip()
        if content and raw_type in GAP_TYPE_ALIASES:
            normalized.append({"gap_type": normalize_gap_type(raw_type), "content": content[:500]})
    return normalized


def _call_llm(config: dict, messages: list[dict]) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=20)
    response = client.chat.completions.create(
        model=config["model"], temperature=0.2, max_tokens=900, messages=messages,
    )
    return response.choices[0].message.content or "{}"


def _json_string_list(value: object, limit: int = 3) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item).strip()][:limit]


def _reference_headings(reference: str) -> list[str]:
    headings = re.findall(r"^#{1,3}\s+(.+?)\s*$", reference, flags=re.M)
    return [re.sub(r"\s+", " ", heading).strip()[:100] for heading in headings][:3]


def build_recall_brief(
    *, title: str, reference: str, note: str = "", gaps: list[str] | None = None,
    reflections: list[str] | None = None, persona: str | None = None,
) -> dict:
    """Prepare a source-grounded first question before a recall session begins."""
    persona_key = normalize_persona(persona)
    persona_spec = PERSONAS[persona_key]
    gaps = [item.strip() for item in gaps or [] if item.strip()][:3]
    reflections = [item.strip() for item in reflections or [] if item.strip()][:2]
    headings = _reference_headings(reference)
    why_bits = []
    if note.strip():
        why_bits.append("你已为这个知识点留下笔记")
    if gaps:
        why_bits.append(f"还有 {len(gaps)} 个待澄清点")
    if reflections:
        why_bits.append("你曾记录过相关心得")
    why_now = "；".join(why_bits) or "这是一次从记忆重建理解的练习"
    fallback = {
        "persona": persona_key,
        "persona_label": persona_spec["label"],
        "opening": persona_spec["intro"],
        "question": f"不看资料，说明「{title}」具体要解决什么问题？它接收什么信息，最后得到什么结果？",
        "follow_ups": [
            f"关键机制或步骤是怎样衔接的？{('可先从「' + headings[0] + '」相关内容回忆。') if headings else ''}",
            "它在什么条件下不适用、容易出错，或需要额外验证？",
        ],
        "hint": (f"你之前写过：{note.strip()[:160]}" if note.strip() else "先说定义或目标，再补一条因果链，最后给一个场景。"),
        "why_now": why_now,
        "source": "local",
    }
    config = get_llm_config()
    if config.get("mode") != "ai" or not config.get("api_key"):
        return fallback
    try:
        payload = _clean_json(_call_llm(config, [
            {
                "role": "system",
                "content": (
                    "你是个人知识库中的回忆教练。只依据给出的 Wiki 资料、学习笔记和历史盲区提出问题，"
                    "不要补充资料中没有的事实。" + persona_spec["instruction"] +
                    "返回纯 JSON：{\"opening\":\"一句开场\",\"question\":\"一个具体主问题\","
                    "\"follow_ups\":[\"递进问题1\",\"递进问题2\"],\"hint\":\"不直接给答案的提示\",\"why_now\":\"一句理由\"}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"知识点：{title}\n\nWiki 资料：\n{reference[:12000]}\n\n"
                    f"学习笔记：\n{note[:3000] or '（无）'}\n\n"
                    f"待澄清点：\n{'；'.join(gaps) or '（无）'}\n\n"
                    f"学习心得：\n{'；'.join(reflections) or '（无）'}"
                ),
            },
        ]))
        question = str(payload.get("question") or "").strip()[:500]
        follow_ups = _json_string_list(payload.get("follow_ups"), 2)
        if not question or not follow_ups:
            raise ValueError("回忆引导不完整")
        return {
            "persona": persona_key,
            "persona_label": persona_spec["label"],
            "opening": str(payload.get("opening") or persona_spec["intro"]).strip()[:500],
            "question": question,
            "follow_ups": follow_ups,
            "hint": str(payload.get("hint") or fallback["hint"]).strip()[:600],
            "why_now": str(payload.get("why_now") or why_now).strip()[:500],
            "source": "llm",
        }
    except Exception:
        return fallback


def analyze_knowledge_note(
    *, note: str, title: str, evidence: list[dict], persona: str | None = None,
) -> dict:
    """Turn a learner note into an inspectable, editable Wiki-update proposal."""
    persona_key = normalize_persona(persona)
    persona_spec = PERSONAS[persona_key]
    clean_note = note.strip()
    local = {
        "summary": "已将这条记录整理为待审核的知识库草案。",
        "answer": (
            "本地模式只列出命中的 Wiki 页面，不对知识事实作出自动判断。"
            if evidence else "本地 Wiki 中暂未找到足以回答这条记录的页面，建议保留为待查问题。"
        ),
        "open_questions": ["这条理解需要用哪个真实例子或原始资料继续验证？"],
        "proposal": f"- 学习记录：{clean_note[:1200]}",
        "proposed_title": f"关于{title}的学习想法",
        "source": "local",
    }
    config = get_llm_config()
    if config.get("mode") != "ai" or not config.get("api_key"):
        return local
    sources = "\n\n".join(
        f"[{item.get('title', '')} | {item.get('path', '')}]\n{item.get('excerpt', '')}"
        for item in evidence[:5]
    ) or "（没有命中页面）"
    try:
        payload = _clean_json(_call_llm(config, [
            {
                "role": "system",
                "content": (
                    "你负责把学习者笔记整理成个人 Wiki 的可审阅草案。只依据提供的本地 Wiki 证据，"
                    "不能把推测写成事实；若证据不足，要明确为待查。" + persona_spec["instruction"] +
                    "返回纯 JSON：{\"summary\":\"简短分析\",\"answer\":\"基于证据的回答或证据不足说明\","
                    "\"open_questions\":[\"待查问题\"],\"proposal\":\"可直接写入 Markdown 的简短内容\","
                    "\"proposed_title\":\"新想法页标题\"}。proposal 不超过 900 字，不要输出 Markdown 标题。"
                ),
            },
            {
                "role": "user",
                "content": f"当前知识点：{title}\n\n学习者笔记：\n{clean_note[:6000]}\n\n可用 Wiki 证据：\n{sources[:12000]}",
            },
        ]))
        proposal = str(payload.get("proposal") or "").strip()[:3000]
        if not proposal:
            raise ValueError("没有生成草案")
        return {
            "summary": str(payload.get("summary") or local["summary"]).strip()[:1000],
            "answer": str(payload.get("answer") or local["answer"]).strip()[:1500],
            "open_questions": _json_string_list(payload.get("open_questions"), 3),
            "proposal": proposal,
            "proposed_title": str(payload.get("proposed_title") or local["proposed_title"]).strip()[:120],
            "source": "llm",
        }
    except Exception:
        return local


IDEA_VERDICTS = {
    "supported": "有本地依据",
    "plausible": "有一定合理性",
    "uncertain": "待验证",
    "problematic": "存在明显疑点",
}


def _idea_evidence_text(evidence: list[dict]) -> str:
    return "\n\n".join(
        f"[{item.get('title', '')} | {item.get('path', '')}]\n{item.get('excerpt', '')}"
        for item in evidence[:5]
    ) or "（没有命中本地 Wiki 页面）"


def _idea_local_assessment(note: str, evidence: list[dict]) -> dict:
    """Keep offline mode honest: retrieval is evidence, not a truth verdict."""
    supporting = [
        f"本地 Wiki 命中：{item.get('title', '')}（{item.get('path', '')}）"
        for item in evidence[:3]
    ]
    return {
        "verdict": "uncertain",
        "verdict_label": IDEA_VERDICTS["uncertain"],
        "confidence": "低",
        "summary": "这是一条值得继续拆解的想法，但当前本地模式不能判断它在事实或实践上是否正确。",
        "supporting_points": supporting or ["暂未找到可直接对照的本地 Wiki 依据。"],
        "concerns": ["还没有把想法拆成可检验的前提、机制和边界。"],
        "open_questions": ["如果这个想法成立，最小的可观察证据或反例是什么？"],
        "next_question": "你认为这个想法成立的关键前提是什么？请给一个具体场景或例子。",
        "source": "local",
    }


def assess_idea(*, note: str, evidence: list[dict], persona: str | None = None) -> dict:
    """Give an evidence-bounded first assessment for a standalone learner idea."""
    persona_key = normalize_persona(persona)
    local = _idea_local_assessment(note.strip(), evidence)
    config = get_llm_config()
    if config.get("mode") != "ai" or not config.get("api_key"):
        return local
    persona_spec = PERSONAS[persona_key]
    try:
        payload = _clean_json(_call_llm(config, [
            {
                "role": "system",
                "content": (
                    "你是个人知识库中的想法评估伙伴。先判断学习者的想法目前处于什么证据状态，"
                    "不要把推测包装成事实。只能使用提供的本地 Wiki 片段；若证据不足必须标为 uncertain。"
                    + persona_spec["instruction"] +
                    "返回纯 JSON：{\"verdict\":\"supported|plausible|uncertain|problematic\","
                    "\"confidence\":\"高|中|低\",\"summary\":\"一句判断\","
                    "\"supporting_points\":[\"依据\"],\"concerns\":[\"疑点\"],"
                    "\"open_questions\":[\"待验证问题\"],\"next_question\":\"下一轮具体问题\"}。"
                ),
            },
            {
                "role": "user",
                "content": f"学习者的想法：\n{note[:8000]}\n\n本地 Wiki 依据：\n{_idea_evidence_text(evidence)[:12000]}",
            },
        ]))
        verdict = str(payload.get("verdict") or "uncertain").strip()
        if verdict not in IDEA_VERDICTS:
            verdict = "uncertain"
        return {
            "verdict": verdict,
            "verdict_label": IDEA_VERDICTS[verdict],
            "confidence": str(payload.get("confidence") or "低").strip()[:10],
            "summary": str(payload.get("summary") or local["summary"]).strip()[:1200],
            "supporting_points": _json_string_list(payload.get("supporting_points"), 4) or local["supporting_points"],
            "concerns": _json_string_list(payload.get("concerns"), 4) or local["concerns"],
            "open_questions": _json_string_list(payload.get("open_questions"), 4) or local["open_questions"],
            "next_question": str(payload.get("next_question") or local["next_question"]).strip()[:600],
            "source": "llm",
        }
    except Exception:
        return local


def discuss_idea(
    *, idea: str, history: list[dict], evidence: list[dict], persona: str | None = None,
) -> dict:
    """Respond to one new idea turn while keeping the assessment provisional."""
    local = _idea_local_assessment(idea.strip(), evidence)
    local["summary"] = "我先把这一轮内容放回原来的想法中看：目前可以继续推演，但还不能把它当成已验证结论。"
    local["reply"] = (
        "这一步补充了一个值得检查的方向。为了避免过早下结论，请把它拆成："
        "前提是什么、机制如何发生、以及什么情况会让它失效。"
    )
    local["next_question"] = "如果只能设计一个最小验证，你会观察什么结果来支持或否定这个想法？"
    config = get_llm_config()
    if config.get("mode") != "ai" or not config.get("api_key"):
        return local
    persona_spec = PERSONAS[normalize_persona(persona)]
    transcript = "\n\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history[-10:]
    ) or "（这是第一轮补充）"
    try:
        payload = _clean_json(_call_llm(config, [
            {
                "role": "system",
                "content": (
                    "你是学习者的想法讨论伙伴。基于整段对话推进讨论：先指出这一轮补充改变了什么，"
                    "再给出暂定判断、依据、隐含前提和一个下一步问题。只依据本地 Wiki 片段，"
                    "无法确认时必须明确说待验证。" + persona_spec["instruction"] +
                    "返回纯 JSON：{\"verdict\":\"supported|plausible|uncertain|problematic\","
                    "\"confidence\":\"高|中|低\",\"summary\":\"一句判断\","
                    "\"reply\":\"给学习者的自然语言回应\",\"supporting_points\":[\"依据\"],"
                    "\"concerns\":[\"隐含前提或风险\"],\"open_questions\":[\"待验证问题\"],"
                    "\"next_question\":\"下一轮具体问题\"}。"
                ),
            },
            {
                "role": "user",
                "content": f"已有对话：\n{transcript[:16000]}\n\n本轮新内容：\n{idea[:8000]}\n\n本地 Wiki 依据：\n{_idea_evidence_text(evidence)[:10000]}",
            },
        ]))
        verdict = str(payload.get("verdict") or "uncertain").strip()
        if verdict not in IDEA_VERDICTS:
            verdict = "uncertain"
        return {
            "verdict": verdict,
            "verdict_label": IDEA_VERDICTS[verdict],
            "confidence": str(payload.get("confidence") or "低").strip()[:10],
            "summary": str(payload.get("summary") or local["summary"]).strip()[:1200],
            "reply": str(payload.get("reply") or local["reply"]).strip()[:3000],
            "supporting_points": _json_string_list(payload.get("supporting_points"), 4) or local["supporting_points"],
            "concerns": _json_string_list(payload.get("concerns"), 4) or local["concerns"],
            "open_questions": _json_string_list(payload.get("open_questions"), 4) or local["open_questions"],
            "next_question": str(payload.get("next_question") or local["next_question"]).strip()[:600],
            "source": "llm",
        }
    except Exception:
        return local


def summarize_idea_conversation(
    *, title: str, initial_content: str, turns: list[dict], evidence: list[dict], persona: str | None = None,
) -> dict:
    """Compress a complete idea discussion into an editable Wiki draft."""
    local = {
        "summary": "已把想法讨论整理为待审核草案；其中仍需验证的部分会保留为开放问题。",
        "draft_title": title.strip()[:120] or "未命名学习想法",
        "draft_content": (
            f"## 想法\n\n{initial_content.strip()[:1800]}\n\n"
            "## 讨论结论\n\n"
            "当前讨论尚不足以确认事实正确性。请在继续实践或查阅原始资料后更新这条记录。\n\n"
            "## 待验证问题\n\n- 设计一个最小例子，观察什么结果能够支持或否定这个想法？"
        ),
        "open_questions": ["设计一个最小验证，明确支持或否定该想法的观察结果。"],
        "source": "local",
    }
    config = get_llm_config()
    if config.get("mode") != "ai" or not config.get("api_key"):
        return local
    transcript = "\n\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}" for item in turns
    )
    try:
        payload = _clean_json(_call_llm(config, [
            {
                "role": "system",
                "content": (
                    "你负责把一段学习者与 Agent 的想法讨论整理成个人 Wiki 草稿。"
                    "只使用对话和提供的本地 Wiki 片段，不新增外部事实。把已形成的判断、仍不确定的边界、"
                    "可执行的验证动作分开写。返回纯 JSON：{\"summary\":\"一句总结\","
                    "\"draft_title\":\"页面标题\",\"draft_content\":\"不含一级标题的 Markdown 正文\","
                    "\"open_questions\":[\"开放问题\"]}。draft_content 不超过 3000 字。"
                ),
            },
            {
                "role": "user",
                "content": f"想法标题：{title}\n\n初始想法：\n{initial_content[:7000]}\n\n讨论记录：\n{transcript[:18000]}\n\n本地 Wiki 依据：\n{_idea_evidence_text(evidence)[:9000]}",
            },
        ]))
        draft_content = str(payload.get("draft_content") or "").strip()[:5000]
        if not draft_content:
            raise ValueError("没有生成 Wiki 草稿")
        return {
            "summary": str(payload.get("summary") or local["summary"]).strip()[:1200],
            "draft_title": str(payload.get("draft_title") or local["draft_title"]).strip()[:120],
            "draft_content": draft_content,
            "open_questions": _json_string_list(payload.get("open_questions"), 5) or local["open_questions"],
            "source": "llm",
        }
    except Exception:
        return local


def diagnose(
    explanation: str, title: str, reference_html: str, persona: str | None = None, uncertainty: str = "",
) -> tuple[list[dict], str, str]:
    """返回 (盲区, 下一问, 来源)，模型异常时不影响学习流程。"""
    persona_spec = PERSONAS[normalize_persona(persona)]
    config = get_llm_config()
    if config.get("mode") != "ai" or not config["api_key"]:
        gaps, question = local_diagnosis(explanation, title, uncertainty)
        return gaps, question, "local"
    try:
        content = _call_llm(config, [
                {"role": "system", "content": "你是费曼学习教练。只依据参考资料检查学习者讲解，不要编造事实。" + persona_spec["instruction"] + "如果学习者标记了最不确定点，第一条反馈和下一问必须直接帮助他核对该点。返回纯 JSON：{\"gaps\":[{\"gap_type\":\"concept_missing|causal_error|boundary_missing|transfer_failure\",\"content\":\"简洁、可行动的中文反馈\"}],\"question\":\"一个下一步追问\"}。盲区最多 3 条。"},
                {"role": "user", "content": f"知识点：{title}\n\n参考资料：\n{reference_html[:12000]}\n\n学习者标记的最不确定点：\n{uncertainty.strip()[:300] or '未标记'}\n\n学习者讲解：\n{explanation[:10000]}"},
            ])
        payload = _clean_json(content)
        gaps = _normalize_gaps(payload.get("gaps"))
        question = str(payload.get("question", "")).strip()[:500]
        if not question:
            raise ValueError("模型没有给出下一问")
        return gaps, question, "llm"
    except Exception:
        gaps, question = local_diagnosis(explanation, title, uncertainty)
        return gaps, question, "local"


def summarize_reflections(reflections: list[dict]) -> tuple[str, str]:
    """Summarize only the reflection text deliberately selected by the learner."""
    entries = [str(item.get("content") or "").strip() for item in reflections]
    entries = [entry for entry in entries if entry]
    if not entries:
        raise ValueError("Please select at least one reflection before summarizing.")
    config = get_llm_config()
    if config.get("mode") == "ai" and config.get("api_key"):
        try:
            result = _call_llm(config, [
                {"role": "system", "content": "You summarize only the learner's selected reflections. Do not add external facts. Return up to three concise Chinese paragraphs covering formed understanding, open questions, and one next action."},
                {"role": "user", "content": "\n\n".join(f"- {entry}" for entry in entries)[:18000]},
            ]).strip()
            if result:
                return result[:3000], "llm"
        except Exception:
            pass
    excerpts = [entry.replace("\n", " ")[:180] for entry in entries[:3]]
    result = "学习心得摘录：\n" + "\n".join(f"• {entry}" for entry in excerpts)
    if len(entries) > 3:
        result += f"\n另有 {len(entries) - 3} 条心得待你继续归纳。"
    result += "\n\n这是基于已选心得的本地摘录。开启 AI 深度诊断后，可生成更结构化的阶段总结。"
    return result[:3000], "local"


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
