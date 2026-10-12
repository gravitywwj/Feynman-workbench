"""Fixed, offline application checks and user-reported external practice."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app import db

VERSION = 1
HELP_LEVELS = ("none", "allowed_docs", "hint", "solution", "unknown")
FAMILIARITY = ("unseen", "seen", "unknown")
PHASES = ("application", "transfer", "delayed")

_METRICS_FIELDS = [
    {"key": "tp", "label": "TP", "type": "integer"},
    {"key": "fp", "label": "FP", "type": "integer"},
    {"key": "fn", "label": "FN", "type": "integer"},
    {"key": "precision", "label": "Precision", "type": "number"},
    {"key": "recall", "label": "Recall", "type": "number"},
]
_VALIDATION_FIELDS = [
    {"key": "json_parseable", "label": "参数文本可解析", "type": "boolean"},
    {"key": "params_schema_valid", "label": "参数本身符合 schema", "type": "boolean"},
    {"key": "tool_allowed", "label": "工具在白名单", "type": "boolean"},
    {"key": "execution_blocked", "label": "执行被拦截", "type": "boolean"},
    {"key": "blocked_at", "label": "拦截阶段", "type": "choice", "options": ["schema", "whitelist"]},
    {"key": "violation", "label": "具体违规", "type": "choice", "options": ["query_type", "unknown_tool", "unknown_field"]},
]

def _metric_task(key: str, phase: str, title: str, prompt: str, expected: dict) -> dict:
    return {
        "task_key": key, "family": "metrics", "phase": phase, "title": title,
        "prompt": prompt + " Precision = TP / (TP + FP)，Recall = TP / (TP + FN)。",
        "scope": "计算 precision 与 recall；不证明代码已执行。",
        "allowed_resources": "不查资料、不看解答；预计 3 分钟。",
        "estimated_minutes": 3, "page_paths": [
            "AI/foundation/evaluation-basics.md", "AI/foundation/ai-practice-lab.md",
        ],
        "fields": _METRICS_FIELDS, "expected": expected,
    }


def _validation_task(key: str, phase: str, title: str, prompt: str, expected: dict) -> dict:
    return {
        "task_key": key, "family": "validation", "phase": phase, "title": title,
        "prompt": prompt + " 白名单只有 search_docs；arguments 必须是只含非空白字符串 query 的 JSON 对象；执行控制先查白名单，再解析并校验参数。",
        "scope": "区分 schema 校验与工具白名单检查；不会执行工具或代码。",
        "allowed_resources": "不查资料、不看解答；预计 4 分钟。",
        "estimated_minutes": 4, "page_paths": [
            "AI/foundation/python-api-basics.md", "AI/foundation/ai-practice-lab.md",
        ],
        "fields": _VALIDATION_FIELDS, "expected": expected,
    }


TASKS = (
    _metric_task("metrics-apply", "application", "精确率与召回率：应用", "应找到 {A,B}，实际返回 {A,C,D}。", {
        "tp": 1, "fp": 2, "fn": 1, "precision": 1 / 3, "recall": 1 / 2,
    }),
    _metric_task("metrics-transfer", "transfer", "精确率与召回率：病例检索变式", "应找到 {K,L,M,N}，实际返回 {L,M,P}。", {
        "tp": 2, "fp": 1, "fn": 2, "precision": 2 / 3, "recall": 1 / 2,
    }),
    _metric_task("metrics-delayed", "delayed", "精确率与召回率：延迟变式", "应找到 {R,S,T}，实际返回 {S,T,U,V}。", {
        "tp": 2, "fp": 2, "fn": 1, "precision": 1 / 2, "recall": 2 / 3,
    }),
    _validation_task("validation-apply", "application", "工具调用校验：应用", '工具 search_docs；参数文本 {"query":7}。', {
        "json_parseable": True, "params_schema_valid": False, "tool_allowed": True,
        "execution_blocked": True, "blocked_at": "schema", "violation": "query_type",
    }),
    _validation_task("validation-transfer", "transfer", "工具调用校验：白名单变式", '白名单只有 search_docs，请求 erase_docs，参数 {"query":"借阅"}。', {
        "json_parseable": True, "params_schema_valid": True, "tool_allowed": False,
        "execution_blocked": True, "blocked_at": "whitelist", "violation": "unknown_tool",
    }),
    _validation_task("validation-delayed", "delayed", "工具调用校验：schema 变式", '工具 search_docs；参数文本 {"query":"借阅","admin":true}。', {
        "json_parseable": True, "params_schema_valid": False, "tool_allowed": True,
        "execution_blocked": True, "blocked_at": "schema", "violation": "unknown_field",
    }),
)
_TASKS_BY_KEY = {task["task_key"]: task for task in TASKS}
_PHASE_LABELS = {"application": "应用结果", "transfer": "首次变式结果", "delayed": "延迟结果"}


class ConflictError(ValueError):
    """A stable attempt UID was reused with different submitted content."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode(value: str | None, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _iso_now() -> str:
    return _now().isoformat(timespec="seconds")


def _local_date(value: str) -> date:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.date()


def _validate_page(page_path: str) -> dict:
    from app.services import wiki_reader

    meta, _ = wiki_reader.render_page_html(page_path)
    return {"path": page_path, "title": str(meta.get("title") or page_path.rsplit("/", 1)[-1].removesuffix(".md"))}


def _snapshot(task: dict) -> dict:
    return {
        "task_key": task["task_key"], "task_version": VERSION, "family": task["family"],
        "phase": task["phase"], "title": task["title"], "prompt": task["prompt"],
        "scope": task["scope"], "allowed_resources": task["allowed_resources"],
        "estimated_minutes": task["estimated_minutes"], "fields": task["fields"],
        "expected": task["expected"],
    }


def _public_task(snapshot: dict) -> dict:
    return {key: snapshot[key] for key in (
        "task_key", "task_version", "family", "phase", "title", "prompt", "scope",
        "allowed_resources", "estimated_minutes", "fields",
    ) if key in snapshot}


def _task_for_page(page_path: str, task: dict, cur) -> bool:
    if page_path in task["page_paths"]:
        return True
    keys = [item["task_key"] for item in TASKS if item["family"] == task["family"]]
    row = cur.execute(
        "SELECT 1 FROM practice_attempts WHERE page_path = ? AND task_key IN ("
        + ",".join("?" for _ in keys) + ") LIMIT 1",
        (page_path, *keys),
    ).fetchone()
    return bool(row)


def _row_json(row, key: str, fallback):
    return _decode(row[key], fallback)


def _attempt_row(cur, uid: str):
    try:
        UUID(uid)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("尝试编号格式不正确") from None
    return cur.execute("SELECT * FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone()


def _find_precursor(cur, page_path: str, family: str):
    application = next(task for task in TASKS if task["family"] == family and task["phase"] == "application")
    rows = cur.execute(
        "SELECT * FROM practice_attempts WHERE page_path = ? AND task_key = ? AND kind = 'builtin' "
        "AND submitted_at IS NOT NULL ORDER BY submitted_at DESC, id DESC",
        (page_path, application["task_key"]),
    ).fetchall()
    for row in rows:
        result = _row_json(row, "result_json", {})
        if result.get("eligible"):
            return row if result.get("matches") is True else None
    return None


def _phase_state(cur, page_path: str, task: dict, today: date | None = None) -> dict:
    if task["phase"] == "application":
        return {"open": True, "due_on": None, "reason": "可开始应用题"}
    precursor = _find_precursor(cur, page_path, task["family"])
    if not precursor:
        return {"open": False, "due_on": None, "reason": "先完成同页同题组的独立应用验收"}
    if task["phase"] == "transfer":
        return {"open": True, "due_on": None, "reason": "已有同题组独立应用结果"}
    due_on = (_local_date(precursor["submitted_at"]) + timedelta(days=7)).isoformat()
    current = today or _now().date()
    return {
        "open": current.isoformat() >= due_on, "due_on": due_on,
        "reason": "已到七日间隔复测日期" if current.isoformat() >= due_on else f"延迟复测将在 {due_on} 开放",
    }


def list_tasks(page_path: str) -> dict:
    page = _validate_page(page_path)
    with db.cursor() as cur:
        tasks = []
        for task in TASKS:
            if not _task_for_page(page_path, task, cur):
                continue
            state = _phase_state(cur, page_path, task)
            tasks.append({
                "task_key": task["task_key"], "family": task["family"], "phase": task["phase"],
                "title": task["title"], "scope": task["scope"],
                "allowed_resources": task["allowed_resources"],
                "estimated_minutes": task["estimated_minutes"], **state,
            })
    return {"page_path": page_path, "page_title": page["title"], "tasks": tasks, "has_tasks": bool(tasks)}


def _present(row, *, include_solution: bool = False) -> dict:
    snapshot = _row_json(row, "task_snapshot_json", {})
    result = _row_json(row, "result_json", {})
    answer = _row_json(row, "answer_json", {})
    value = {
        "attempt_uid": row["attempt_uid"], "page_path": row["page_path"], "page_title": row["page_title"],
        "task_key": row["task_key"], "task_version": row["task_version"],
        "kind": row["kind"], "mode": row["mode"], "phase": row["phase"],
        "familiarity": row["familiarity"], "seen_before": bool(row["seen_before"]),
        "assistance": row["assistance"], "precursor_uid": row["precursor_uid"],
        "created_at": row["created_at"], "submitted_at": row["submitted_at"],
        "solution_viewed_at": row["solution_viewed_at"], "check_kind": row["check_kind"],
        "task": _public_task(snapshot) if row["kind"] == "builtin" else {"title": snapshot.get("title", "外部练习")},
        "source_url": row["source_url"], "artifact_text": row["artifact_text"],
        "answer": answer if row["submitted_at"] else None,
        "result": result if row["submitted_at"] else None,
    }
    if include_solution and row["kind"] == "builtin" and snapshot.get("expected") is not None:
        value["solution"] = snapshot["expected"]
    return value


def start_attempt(
    page_path: str, task_key: str, mode: str, familiarity: str, assistance: str,
) -> dict:
    if mode not in {"practice", "assessment"}:
        raise ValueError("练习模式无效")
    if familiarity not in FAMILIARITY or assistance not in HELP_LEVELS:
        raise ValueError("熟悉程度或帮助程度无效")
    task = _TASKS_BY_KEY.get(task_key)
    if not task:
        raise LookupError("未知的本地自测题")
    page = _validate_page(page_path)
    now = _iso_now()
    uid = str(uuid4())
    with db.cursor() as cur:
        if not _task_for_page(page_path, task, cur):
            raise LookupError("当前页面没有适配这道本地自测题")
        active = cur.execute(
            "SELECT 1 FROM practice_attempts WHERE page_path = ? AND kind = 'builtin' AND submitted_at IS NULL LIMIT 1",
            (page_path,),
        ).fetchone()
        if active:
            raise ValueError("当前页面还有一题未提交；先继续或完成这次尝试")
        state = _phase_state(cur, page_path, task)
        if not state["open"]:
            raise ValueError(state["reason"])
        if assistance == "allowed_docs" and "可查阅指定资料" not in task["allowed_resources"]:
            raise ValueError("这道题不允许查阅资料")
        prior = cur.execute(
            "SELECT COUNT(*) FROM practice_attempts WHERE page_path = ? AND task_key = ?",
            (page_path, task_key),
        ).fetchone()[0]
        precursor = _find_precursor(cur, page_path, task["family"]) if task["phase"] != "application" else None
        cur.execute(
            "INSERT INTO practice_attempts "
            "(attempt_uid, page_path, page_title, task_key, task_version, task_snapshot_json, kind, mode, phase, "
            "familiarity, seen_before, assistance, answer_json, result_json, check_kind, precursor_uid, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'builtin', ?, ?, ?, ?, ?, '{}', '{}', 'deterministic', ?, ?)",
            (uid, page_path, page["title"], task_key, VERSION, _json(_snapshot(task)), mode, task["phase"],
             familiarity, int(prior > 0), assistance, precursor["attempt_uid"] if precursor else None, now),
        )
        row = cur.execute("SELECT * FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone()
    return _present(row)


def get_attempt(uid: str) -> dict:
    with db.cursor() as cur:
        row = _attempt_row(cur, uid)
        if not row:
            raise LookupError("自测记录不存在")
        return _present(row, include_solution=bool(row["solution_viewed_at"] or row["submitted_at"]))


def view_solution(uid: str) -> dict:
    with db.cursor() as cur:
        row = _attempt_row(cur, uid)
        if not row:
            raise LookupError("自测记录不存在")
        if row["kind"] != "builtin":
            raise ValueError("外部练习没有本地标准答案")
        if not row["submitted_at"] and not row["solution_viewed_at"]:
            now = _iso_now()
            cur.execute(
                "UPDATE practice_attempts SET solution_viewed_at = ?, assistance = 'solution' "
                "WHERE attempt_uid = ? AND submitted_at IS NULL",
                (now, uid),
            )
            row = cur.execute("SELECT * FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone()
        result = _present(row, include_solution=True)
    result["before_submission_notice"] = (
        "本次将作为练习记录，不形成独立验收资格。"
        if not row["submitted_at"] else ""
    )
    return result


def _grade(task: dict, answer: object) -> dict:
    if not isinstance(answer, dict):
        raise ValueError("答案必须是结构化对象")
    expected = task["expected"]
    if set(answer) != set(expected):
        raise ValueError("答案字段必须与题目要求完全一致")
    components = []
    for field in task["fields"]:
        key = field["key"]
        actual = answer[key]
        target = expected[key]
        if field["type"] == "integer":
            if type(actual) is not int or actual < 0:
                raise ValueError(f"{field['label']} 必须是非负整数")
            matched = actual == target
        elif field["type"] == "number":
            if type(actual) not in {int, float} or not math.isfinite(actual) or not 0 <= actual <= 1:
                raise ValueError(f"{field['label']} 必须是 0–1 之间的有限数值")
            matched = abs(actual - target) <= 0.001
        elif field["type"] == "boolean":
            if type(actual) is not bool:
                raise ValueError(f"{field['label']} 必须是布尔值")
            matched = actual is target
        elif field["type"] == "choice":
            if not isinstance(actual, str) or actual not in field["options"]:
                raise ValueError(f"{field['label']} 选择值无效")
            matched = actual == target
        else:
            matched = False
        components.append({
            "field": key, "label": field["label"], "expected": target,
            "actual": actual, "matched": bool(matched),
        })
    misses = [item["label"] for item in components if not item["matched"]]
    return {
        "matches": not misses, "matched_components": len(components) - len(misses),
        "total_components": len(components), "components": components,
        "feedback": "所有检查项均匹配。" if not misses else f"需复查：{'、'.join(misses)}。",
    }


def _eligibility(cur, row, task: dict, assistance: str, solution_viewed_at: str | None) -> tuple[bool, str]:
    if row["mode"] != "assessment":
        return False, "练习模式不形成验收资格"
    if row["familiarity"] != "unseen":
        return False, "熟悉程度不是“未见过”（用户自述）"
    if row["seen_before"]:
        return False, "这是本工作台中的重做原题"
    if assistance != "none":
        return False, "记录了提示、解答或未知帮助"
    if solution_viewed_at:
        return False, "提交前查看了解答"
    if task["phase"] != "application":
        precursor_uid = row["precursor_uid"]
        precursor = _attempt_row(cur, precursor_uid) if precursor_uid else None
        if not precursor or precursor["page_path"] != row["page_path"]:
            return False, "缺少同页前置应用结果"
        if precursor["kind"] != "builtin" or precursor["phase"] != "application":
            return False, "前置记录不是本地应用题"
        precursor_snapshot = _row_json(precursor, "task_snapshot_json", {})
        precursor_result = _row_json(precursor, "result_json", {})
        if precursor_snapshot.get("family") != task["family"] or not precursor_result.get("eligible") or precursor_result.get("matches") is not True:
            return False, "前置应用结果不满足独立通过条件"
        if precursor["submitted_at"] > row["created_at"]:
            return False, "前置应用记录晚于当前题目"
        if task["phase"] == "delayed":
            due_on = _local_date(precursor["submitted_at"]) + timedelta(days=7)
            if _now().date() < due_on:
                return False, f"延迟复测将在 {due_on.isoformat()} 开放"
    return True, "符合本题的首次独立验收条件（熟悉程度与帮助为用户自述）"


def submit_attempt(uid: str, answer: dict, assistance: str | None = None) -> dict:
    if assistance is not None and assistance not in HELP_LEVELS:
        raise ValueError("帮助程度无效")
    answer_text = _json(answer)
    if len(answer_text) > 10000:
        raise ValueError("答案不能超过 10000 字符")
    with db.cursor() as cur:
        row = _attempt_row(cur, uid)
        if not row:
            raise LookupError("自测记录不存在")
        if row["kind"] != "builtin":
            raise ValueError("外部练习不能作为本地题提交")
        if row["submitted_at"]:
            if row["answer_json"] == answer_text:
                return _present(row, include_solution=True)
            raise ConflictError("这次尝试已经提交；相同编号不能替换答案。")
        task = _TASKS_BY_KEY.get(row["task_key"])
        snapshot = _row_json(row, "task_snapshot_json", {})
        if not task or row["task_version"] != VERSION or snapshot != _snapshot(task):
            raise ValueError("题目版本无法核验，只能保留未核对记录")
        scored = _grade(task, answer)
        help_order = {"none": 0, "allowed_docs": 1, "hint": 2, "unknown": 3, "solution": 4}
        requested_help = assistance or row["assistance"]
        help_level = "solution" if row["solution_viewed_at"] else max(
            (row["assistance"], requested_help), key=lambda value: help_order[value],
        )
        eligible, reason = _eligibility(cur, row, task, help_level, row["solution_viewed_at"])
        result = {
            **scored, "phase": task["phase"], "phase_label": _PHASE_LABELS[task["phase"]],
            "eligible": eligible, "eligibility_reason": reason,
            "repeated": bool(row["seen_before"]), "assistance": help_level,
        }
        submitted_at = _iso_now()
        updated = cur.execute(
            "UPDATE practice_attempts SET answer_json = ?, result_json = ?, assistance = ?, submitted_at = ? "
            "WHERE attempt_uid = ? AND submitted_at IS NULL",
            (answer_text, _json(result), help_level, submitted_at, uid),
        )
        if updated.rowcount != 1:
            latest = _attempt_row(cur, uid)
            if latest and latest["answer_json"] == answer_text:
                return _present(latest, include_solution=True)
            raise ConflictError("这次尝试已由另一请求提交，不能覆盖。")
        final = cur.execute("SELECT * FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone()
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id, created_at) "
            "SELECT 'practice_submitted', ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM learning_events WHERE event_type = 'practice_submitted' AND entity_id = ?)",
            (row["page_path"], final["id"], submitted_at, final["id"]),
        )
    return _present(final, include_solution=True)


def create_external_attempt(
    page_path: str, title: str, source_url: str, reported_result: str,
    artifact_text: str, familiarity: str, assistance: str,
) -> dict:
    page = _validate_page(page_path)
    title = title.strip()
    source_url = source_url.strip()
    if not title or len(title) > 160:
        raise ValueError("外部练习标题须为 1–160 个字符")
    if len(source_url) > 2000 or any(ord(char) < 32 for char in source_url):
        raise ValueError("练习链接长度或格式无效")
    if source_url:
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("练习链接只允许 http 或 https")
    if len(reported_result) > 4000 or len(artifact_text) > 10000:
        raise ValueError("练习结果最多 4000 字符，提交内容最多 10000 字符")
    if familiarity not in FAMILIARITY or assistance not in HELP_LEVELS:
        raise ValueError("熟悉程度或帮助程度无效")
    now = _iso_now()
    uid = str(uuid4())
    result = {"reported_result": reported_result.strip(), "label": "用户自报，未自动核验"}
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO practice_attempts "
            "(attempt_uid, page_path, page_title, task_key, task_version, task_snapshot_json, kind, mode, phase, "
            "familiarity, assistance, result_json, artifact_text, source_url, check_kind, created_at, submitted_at) "
            "VALUES (?, ?, ?, '', 1, ?, 'external', 'practice', 'application', ?, ?, ?, ?, ?, "
            "'self_reported', ?, ?)",
            (uid, page_path, page["title"], _json({"title": title, "family": "external", "phase": "application"}),
             familiarity, assistance, _json(result), artifact_text, source_url, now, now),
        )
        row = cur.execute("SELECT * FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone()
        cur.execute(
            "INSERT INTO learning_events (event_type, page_path, entity_id, created_at) "
            "VALUES ('practice_submitted', ?, ?, ?)",
            (page_path, row["id"], now),
        )
    return _present(row)


def history(page_path: str, limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("历史数量须为 1–100")
    _validate_page(page_path)
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM practice_attempts WHERE page_path = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (page_path, limit),
        ).fetchall()
    return {"page_path": page_path, "attempts": [
        _present(row, include_solution=bool(row["solution_viewed_at"] or row["submitted_at"])) for row in rows
    ]}


def import_preview(cur, attempts: list) -> dict:
    if not isinstance(attempts, list):
        raise ValueError("practice_attempts 必须是数组")
    incoming = set()
    added = 0
    for item in attempts:
        if not isinstance(item, dict):
            raise ValueError("自测记录必须是对象")
        try:
            uid = str(UUID(str(item.get("attempt_uid") or "")))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("自测记录缺少有效 attempt_uid") from None
        if uid in incoming:
            raise ValueError(f"导入数据中 attempt_uid 重复：{uid}")
        incoming.add(uid)
        for name, limit in (("page_path", 2000), ("page_title", 300), ("task_key", 160),
                            ("task_snapshot_json", 10000), ("answer_json", 10000),
                            ("result_json", 10000), ("artifact_text", 10000),
                            ("source_url", 2000), ("created_at", 50), ("submitted_at", 50),
                            ("solution_viewed_at", 50)):
            value = item.get(name)
            if value is not None and (not isinstance(value, (str, dict, list)) or len(value) > limit):
                raise ValueError(f"自测记录字段 {name} 格式无效或过长")
            if name not in {"task_snapshot_json", "answer_json", "result_json"} and value is not None and not isinstance(value, str):
                raise ValueError(f"自测记录字段 {name} 必须是文本")
        if not str(item.get("page_path") or "") or not str(item.get("created_at") or ""):
            raise ValueError("自测记录缺少页面路径或开始时间")
        parsed = {}
        for name in ("task_snapshot_json", "answer_json", "result_json"):
            value = item.get(name, "{}")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (ValueError, json.JSONDecodeError):
                    raise ValueError(f"自测记录字段 {name} 不是有效 JSON") from None
            if not isinstance(value, dict):
                raise ValueError(f"自测记录字段 {name} 必须是 JSON 对象")
            if len(_json(value)) > 10000:
                raise ValueError(f"自测记录字段 {name} 超过 10000 字符")
            parsed[name] = value
        candidate = _TASKS_BY_KEY.get(str(item.get("task_key") or ""))
        if (
            item.get("kind") == "builtin" and type(item.get("task_version")) is int
            and item["task_version"] == VERSION and candidate
            and parsed["task_snapshot_json"] == _snapshot(candidate)
            and item["phase"] != candidate["phase"]
        ):
            raise ValueError("自测记录阶段与题目定义不一致")
        if item.get("kind") not in {"builtin", "external"} or item.get("mode") not in {"practice", "assessment"}:
            raise ValueError("自测记录类型或模式无效")
        if item.get("phase") not in PHASES:
            raise ValueError("自测记录阶段无效")
        if not str(item.get("page_title") or "") or item.get("familiarity") not in FAMILIARITY or item.get("assistance") not in HELP_LEVELS:
            raise ValueError("自测记录缺少标题、熟悉程度或帮助程度")
        try:
            started = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00")).astimezone()
            submitted = datetime.fromisoformat(str(item["submitted_at"]).replace("Z", "+00:00")).astimezone() if item.get("submitted_at") else None
            viewed = datetime.fromisoformat(str(item["solution_viewed_at"]).replace("Z", "+00:00")).astimezone() if item.get("solution_viewed_at") else None
        except ValueError:
            raise ValueError("自测记录时间格式无效") from None
        if (submitted and submitted < started) or (viewed and viewed < started) or (submitted and viewed and viewed > submitted):
            raise ValueError("自测记录时间顺序无效")
        source_url = str(item.get("source_url") or "")
        if any(ord(char) < 32 for char in source_url):
            raise ValueError("导入的练习链接包含控制字符")
        if source_url:
            parsed_url = urlsplit(source_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise ValueError("导入的练习链接只允许 http 或 https")
        existing = cur.execute("SELECT * FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone()
        if existing:
            if not _same_import_content(dict(existing), item):
                raise ConflictError(f"自测编号 {uid} 已存在，但内容不同；未覆盖现有记录。")
        else:
            added += 1
    return {"incoming": len(attempts), "new": added}


def _same_import_content(existing: dict, incoming: dict) -> bool:
    fields = (
        "page_path", "page_title", "task_key", "task_version", "kind", "mode", "phase", "familiarity",
        "assistance", "answer_json", "artifact_text", "source_url", "precursor_uid",
        "created_at", "submitted_at", "solution_viewed_at",
    )
    for name in fields:
        left, right = existing.get(name), incoming.get(name)
        if name in {"answer_json"}:
            left, right = _json(_decode(left, {})), _json(_decode(right, {}) if isinstance(right, str) else right or {})
        if name == "task_version":
            right = right if type(right) is int else 0
        if left != right:
            return False
    left_snapshot = _decode(existing.get("task_snapshot_json"), {})
    right_snapshot = incoming.get("task_snapshot_json", {})
    if isinstance(right_snapshot, str):
        right_snapshot = _decode(right_snapshot, {})
    return left_snapshot == right_snapshot


def import_attempts(cur, attempts: list) -> dict:
    preview = import_preview(cur, attempts)
    imported = 0
    events = 0
    for item in sorted(attempts, key=lambda value: str(value.get("created_at") or "")):
        uid = str(UUID(str(item["attempt_uid"])))
        if cur.execute("SELECT 1 FROM practice_attempts WHERE attempt_uid = ?", (uid,)).fetchone():
            continue
        snapshot_value = item.get("task_snapshot_json", {})
        snapshot = _decode(snapshot_value, {}) if isinstance(snapshot_value, str) else snapshot_value
        answer_value = item.get("answer_json", {})
        answer = _decode(answer_value, {}) if isinstance(answer_value, str) else answer_value
        task_key = str(item.get("task_key") or "")[:160]
        task_version = item.get("task_version") if type(item.get("task_version")) is int else 0
        candidate = _TASKS_BY_KEY.get(task_key)
        task = candidate if item.get("kind") == "builtin" and candidate and task_version == VERSION and snapshot == _snapshot(candidate) else None
        # Unknown versions remain readable history, but their score and eligibility are discarded.
        known_kind = item.get("kind") if item.get("kind") in {"builtin", "external"} else "builtin"
        mode = item.get("mode") if item.get("mode") in {"practice", "assessment"} else "practice"
        phase = item.get("phase") if item.get("phase") in PHASES else str(snapshot.get("phase") or "application")
        if phase not in PHASES:
            phase = "application"
        familiarity = item.get("familiarity") if item.get("familiarity") in FAMILIARITY else "unknown"
        assistance = item.get("assistance") if item.get("assistance") in HELP_LEVELS else "unknown"
        page_path = str(item["page_path"])
        created_at = str(item["created_at"])
        submitted_at = item.get("submitted_at") if isinstance(item.get("submitted_at"), str) else None
        solution_viewed_at = item.get("solution_viewed_at") if isinstance(item.get("solution_viewed_at"), str) else None
        precursor_uid = item.get("precursor_uid") if isinstance(item.get("precursor_uid"), str) else None
        prior = cur.execute(
            "SELECT COUNT(*) FROM practice_attempts WHERE page_path = ? AND task_key = ? AND created_at <= ?",
            (page_path, task_key, created_at),
        ).fetchone()[0]
        seen_before = int(bool(prior))
        result = _decode(item.get("result_json") if isinstance(item.get("result_json"), str) else _json(item.get("result_json", {})), {})
        check_kind = "unverified"
        if task:
            check_kind = "deterministic"
            result = {"matches": None, "eligible": False, "unverified_reason": "not_submitted"}
            if submitted_at:
                try:
                    scored = _grade(task, answer)
                    provisional = {
                        "mode": mode, "familiarity": familiarity, "seen_before": seen_before,
                        "precursor_uid": precursor_uid, "page_path": page_path, "created_at": created_at,
                    }
                    eligible, reason = _eligibility(cur, provisional, task, assistance, solution_viewed_at)
                    result = {
                        **scored, "phase": phase, "phase_label": _PHASE_LABELS[phase],
                        "eligible": eligible, "eligibility_reason": reason,
                        "repeated": bool(seen_before), "assistance": assistance,
                    }
                except (ValueError, TypeError):
                    result = {"matches": None, "eligible": False, "unverified_reason": "invalid_answer"}
                    check_kind = "unverified"
        else:
            result = {"matches": None, "eligible": False, "unverified_reason": "unknown_task_version",
                      "reported_result": result.get("reported_result") if isinstance(result, dict) else None}
        if known_kind == "external":
            check_kind = "self_reported"
            result = {
                "reported_result": result.get("reported_result") if isinstance(result, dict) else "",
                "label": "用户自报，未自动核验",
            }
        cur.execute(
            "INSERT INTO practice_attempts "
            "(attempt_uid, page_path, page_title, task_key, task_version, task_snapshot_json, kind, mode, phase, "
            "familiarity, seen_before, assistance, answer_json, result_json, artifact_text, source_url, check_kind, "
            "precursor_uid, created_at, submitted_at, solution_viewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, page_path, str(item.get("page_title") or page_path.rsplit("/", 1)[-1])[:300],
             task_key, task_version, _json(snapshot), known_kind, mode, phase, familiarity, seen_before, assistance,
             _json(answer), _json(result), str(item.get("artifact_text") or "")[:10000],
             str(item.get("source_url") or "")[:2000], check_kind, precursor_uid,
             created_at, submitted_at, solution_viewed_at),
        )
        local_id = cur.lastrowid
        imported += 1
        if submitted_at:
            cur.execute(
                "INSERT INTO learning_events (event_type, page_path, entity_id, created_at) "
                "SELECT 'practice_submitted', ?, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM learning_events WHERE event_type = 'practice_submitted' AND entity_id = ?)",
                (page_path, local_id, submitted_at, local_id),
            )
            events += cur.rowcount
    return {"attempts": imported, "events": events, **preview}
