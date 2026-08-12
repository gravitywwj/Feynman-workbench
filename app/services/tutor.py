"""费曼学习诊断器：优先使用已配置的模型，离线时退回可解释的本地规则。"""
from __future__ import annotations

import json
import re

from app.config import get_llm_config


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？!?\n]+", text) if part.strip()])


def local_diagnosis(explanation: str, title: str) -> tuple[list[dict], str]:
    """不依赖网络的基础诊断：重点检查结构、机制与例子是否出现。"""
    normalized = explanation.strip()
    gaps: list[dict] = []
    if len(normalized) < 100 or _sentence_count(normalized) < 3:
        gaps.append({"gap_type": "vague", "content": "讲解还比较短。补充“它是什么、如何起作用、为什么重要”三部分，能更容易暴露理解缺口。"})
    if not re.search(r"例如|比如|举例|场景|比作|好比", normalized):
        gaps.append({"gap_type": "missing", "content": "还没有具体例子。尝试用一个真实任务、项目场景或类比来验证自己是否能把它讲清楚。"})
    if not re.search(r"因为|所以|导致|通过|从而|机制|步骤|首先|然后", normalized):
        gaps.append({"gap_type": "missing", "content": "关键机制还不够明确。请说明各步骤之间的因果关系，而不仅是罗列术语。"})
    question = f"如果不使用术语，你会如何向一位刚接触「{title}」的同事说明它为什么有用？"
    return gaps[:3], question


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
    if not config["api_key"]:
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
    if not config["api_key"]:
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
