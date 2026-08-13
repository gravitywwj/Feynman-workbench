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


def local_assessment(answer: str, expected: str, agent: str) -> tuple[str, str, str]:
    """离线时只判断回答的可检索性，不冒充事实核验。"""
    normalized = answer.strip()
    expected_words = set(_keywords(expected))
    answer_words = set(_keywords(normalized))
    overlap = len(expected_words & answer_words)
    has_example = bool(re.search(r"例如|比如|举例|场景|好比", normalized))
    complete = len(normalized) >= 60 and (overlap >= 2 or has_example)
    if agent == "strict":
        if complete:
            return "pass", "结论：可以继续。你给出了可核对的解释；现在用资料确认术语和因果链是否准确。", "给出一个反例，说明它在什么情况下不适用。"
        return "retry", "结论：不够。当前回答缺少可核对的机制、条件或例子，不能算掌握。不要重读整页，先补出其中一项。", "用两句话回答：它解决什么问题，靠什么机制做到？"
    if complete:
        return "pass", "你的回答已有可核对的结构。现在打开参考答案，补上最不确定的一处即可。", "尝试再用一个不同场景解释它。"
    return "retry", "先别急着看答案。把概念、机制或例子中的任意一项讲具体一点，再来核对。", "它解决什么问题，又为什么能解决？"


def _clean_json(content: str) -> dict:
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("复习教练没有返回对象")
    return value


def assess(answer: str, *, question: str, expected: str, title: str, reference_html: str, agent: str) -> tuple[str, str, str, str]:
    """返回 verdict、反馈、下一问与来源；模型不可用时退回本地规则。"""
    profile = agent_profile(agent)
    config = get_llm_config()
    if config.get("mode") != "ai" or not config["api_key"]:
        verdict, feedback, follow_up = local_assessment(answer, expected, agent)
        return verdict, feedback, follow_up, "local"
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=20)
        response = client.chat.completions.create(
            model=config["model"], temperature=0.2, max_tokens=500,
            messages=[
                {"role": "system", "content": (
                    f"你是{profile['name']}。{profile['instruction']}只依据参考资料判断，不编造。"
                    "返回纯 JSON：{\"verdict\":\"pass|retry\",\"feedback\":\"不超过90字\",\"follow_up\":\"一个下一问\"}。"
                )},
                {"role": "user", "content": (
                    f"知识点：{title}\n参考资料：{_plain_text(reference_html)[:10000]}\n"
                    f"复习题：{question}\n参考答案或学习记录：{expected[:1200]}\n学习者回答：{answer[:5000]}"
                )},
            ],
        )
        payload = _clean_json(response.choices[0].message.content or "{}")
        verdict = str(payload.get("verdict", "retry"))
        feedback = str(payload.get("feedback", "")).strip()[:300]
        follow_up = str(payload.get("follow_up", "")).strip()[:300]
        if verdict not in {"pass", "retry"} or not feedback or not follow_up:
            raise ValueError("复习教练反馈不完整")
        return verdict, feedback, follow_up, "llm"
    except Exception:
        verdict, feedback, follow_up = local_assessment(answer, expected, agent)
        return verdict, feedback, follow_up, "local"
