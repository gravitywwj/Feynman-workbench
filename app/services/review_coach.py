"""复习教练：费曼式梳理与直截了当的突击检查共用同一份事实边界。"""
from __future__ import annotations

import json
import re

from app.config import get_llm_config

AGENTS = {
    "feynman": {
        "name": "费曼教练",
        "label": "梳理模式",
        "instruction": "语气平静、具体。帮助学习者用自己的话补足机制和例子。",
    },
    "strict": {
        "name": "突击教练",
        "label": "突击检查",
        "instruction": "语气直接、克制、像严格的审计者。先给结论，再指出一个最重要的缺口和下一步。不羞辱、不贴人格或能力标签。",
    },
}


def agent_profile(agent: str) -> dict:
    if agent not in AGENTS:
        raise ValueError("复习教练必须为 feynman 或 strict")
    return AGENTS[agent]


def _plain_text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ")


def local_assessment(
    answer: str, required_points: list[str], agent: str, *, reference_ready: bool,
) -> tuple[str, str, str, bool]:
    """Offline feedback can guide expression, but cannot verify facts."""
    feedback = (
        "待核对：目前没有可用的可靠参考资料，无法判断回答是否符合事实。"
        if not reference_ready or not required_points
        else "待核对：本地反馈只能整理表达线索，无法确认回答是否符合参考资料。"
    )
    return "retry", feedback, "对照资料后标出一条明确依据，再决定是否重试。", False


def _clean_json(content: str) -> dict:
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("复习教练没有返回对象")
    return value


def assess(
    answer: str, *, question: str, expected: str, required_points: list[str], reference_status: str,
    title: str, reference_html: str, agent: str,
) -> tuple[str, str, str, str, str]:
    """Return assessment plus the strength of its source-grounded evidence."""
    profile = agent_profile(agent)
    reference_ready = reference_status == "source" and bool(required_points) and bool(reference_html.strip())
    if not reference_ready:
        verdict, feedback, follow_up, _ = local_assessment(
            answer, required_points, agent, reference_ready=False,
        )
        return verdict, feedback, follow_up, "local", "unverified"
    config = get_llm_config()
    if config.get("mode") != "ai" or not config["api_key"]:
        verdict, feedback, follow_up, _ = local_assessment(
            answer, required_points, agent, reference_ready=True,
        )
        return verdict, feedback, follow_up, "local", "unverified"
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=20)
        response = client.chat.completions.create(
            model=config["model"], temperature=0.2, max_tokens=500,
            messages=[
                {"role": "system", "content": (
                    f"你是{profile['name']}。{profile['instruction']}只依据参考资料判断，不编造。"
                    "只有学习者回答覆盖必备要点且与资料一致，才可通过。"
                    "返回纯 JSON：{\"verdict\":\"pass|retry\",\"feedback\":\"不超过90字\",\"follow_up\":\"一个下一问\"}。"
                )},
                {"role": "user", "content": (
                    f"知识点：{title}\n参考资料：{_plain_text(reference_html)[:10000]}\n"
                    f"复习题：{question}\n来源摘要：{expected[:1200]}\n必备要点：{'；'.join(required_points[:4])}\n学习者回答：{answer[:5000]}"
                )},
            ],
        )
        payload = _clean_json(response.choices[0].message.content or "{}")
        verdict = payload.get("verdict", "retry")
        feedback = payload.get("feedback", "")
        follow_up = payload.get("follow_up", "")
        if not all(isinstance(value, str) for value in (verdict, feedback, follow_up)):
            raise ValueError("复习教练反馈格式无效")
        feedback = feedback.strip()[:300]
        follow_up = follow_up.strip()[:300]
        if verdict not in {"pass", "retry"} or not feedback or not follow_up:
            raise ValueError("复习教练反馈不完整")
        return verdict, feedback, follow_up, "llm", "source_reviewed" if verdict == "pass" else "unverified"
    except Exception:
        verdict, feedback, follow_up, _ = local_assessment(
            answer, required_points, agent, reference_ready=True,
        )
        return verdict, feedback, follow_up, "local", "unverified"
