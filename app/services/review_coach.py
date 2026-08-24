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


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    ignored = {"这个", "那个", "可以", "通过", "因为", "所以", "什么", "一个", "the", "and", "with", "from"}
    return [word for word in words if word not in ignored][:18]


def _matched_required_points(answer: str, required_points: list[str]) -> list[str]:
    answer_words = set(_keywords(answer))
    matched = []
    for point in required_points:
        point_words = set(_keywords(point))
        if point_words and answer_words & point_words:
            matched.append(point)
    return matched


def local_assessment(
    answer: str, required_points: list[str], agent: str, *, reference_ready: bool,
) -> tuple[str, str, str, bool]:
    """Check explicit Wiki points offline without pretending to judge all facts."""
    normalized = answer.strip()
    if not reference_ready or not required_points:
        return (
            "retry",
            "这张卡没有保留足够的 Wiki 来源，不能把历史表达当作标准答案。请回原文核对后再评分。",
            "打开原文，找出定义、步骤或条件，再写下你能确认的一点。",
            False,
        )
    matched = _matched_required_points(normalized, required_points)
    required_count = min(2, len(required_points))
    complete = len(normalized) >= 24 and len(matched) >= required_count
    if agent == "strict":
        if complete:
            return "pass", f"结论：通过。你覆盖了 {len(matched)} 条 Wiki 要点；仍请回原文确认措辞与因果链。", "给出一个反例，说明它在什么情况下不适用。", True
        return "retry", f"结论：未通过。需要对上至少 {required_count} 条 Wiki 要点；当前命中 {len(matched)} 条。", "打开原文，补上定义或机制中的一项，再重新作答。", False
    if complete:
        return "pass", f"你已对上 {len(matched)} 条 Wiki 要点。打开原文，核对其中最不确定的一处。", "尝试再用一个不同场景解释它。", True
    return "retry", f"先别急着评分。当前只对上 {len(matched)} 条 Wiki 要点，还需要至少 {required_count} 条。", "它解决什么问题，又为什么能解决？", False


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
        verdict, feedback, follow_up, standard_met = local_assessment(
            answer, required_points, agent, reference_ready=True,
        )
        return verdict, feedback, follow_up, "local", "source_standard" if standard_met else "unverified"
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
        verdict = str(payload.get("verdict", "retry"))
        feedback = str(payload.get("feedback", "")).strip()[:300]
        follow_up = str(payload.get("follow_up", "")).strip()[:300]
        if verdict not in {"pass", "retry"} or not feedback or not follow_up:
            raise ValueError("复习教练反馈不完整")
        return verdict, feedback, follow_up, "llm", "source_reviewed" if verdict == "pass" else "unverified"
    except Exception:
        verdict, feedback, follow_up, standard_met = local_assessment(
            answer, required_points, agent, reference_ready=True,
        )
        return verdict, feedback, follow_up, "local", "source_standard" if standard_met else "unverified"
