"""Pinned, read-only course adapter and local learning-lab records."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import UUID

from app import db
from app.config import AI_ENGINEERING_COURSE_PATH

COURSE_ID = "ai-engineering-from-scratch"
COURSE_VERSION = "467969391fee2b42f8c9d556a2be7802567a6a15"
COURSE_URL = "https://github.com/rohitg00/ai-engineering-from-scratch/tree/467969391fee2b42f8c9d556a2be7802567a6a15"
PROJECT_ID = "retrieval-evaluation-lab"
MAX_CODE_CHARS = 20_000
RUNNER_ENABLED = False
RUNNER_BLOCK_REASON = "本机 Windows 沙箱 P0 验证未通过；代码运行入口保持关闭。"

_PROJECT_ROOT = f"projects/{PROJECT_ID}"
_SOURCE_SHA256 = {
    f"{_PROJECT_ROOT}/project.json": "f7554882ed64a7fc8c7d00e6f68cff9465a7807dcb0f3dd85302625da1bf75c0",
    f"{_PROJECT_ROOT}/README.md": "567be2152761bb9ec3a62823a26ba4f525d803da059a71a64cce0e1e3ac08ac0",
    f"{_PROJECT_ROOT}/stages/01-validate-rankings/docs/en.md": "66bc538f1272af29fdc5f7a823faaa52850effb435ca54392ca821f88f475376",
    f"{_PROJECT_ROOT}/stages/01-validate-rankings/starter/main.py": "4d50aae176743ff15f3ca22af966354d4cb80a8d196df8c59313e030a7235cff",
    f"{_PROJECT_ROOT}/stages/02-precision-and-recall/docs/en.md": "6a03401c28acecdafc03aad9e449934dc41cb492f57b9c43ed4a6c70a5c08679",
    f"{_PROJECT_ROOT}/stages/03-rank-sensitive-metrics/docs/en.md": "12dfe33dcf2e1025b51f146d038915fdc603cdc0970dc53e1c2d057ac06b7adb",
    f"{_PROJECT_ROOT}/stages/04-compare-systems/docs/en.md": "e0db232df285e343a8fa0e69a30506cb05484423a5b296e73e76f226efb35c7e",
}
_SPECS = (
    {
        "mission_key": "mission-01-find-missing-evidence",
        "kind": "mission",
        "title": "Mission 01：找回遗漏的证据",
        "stage_id": "02-precision-and-recall",
        "previous": None,
        "docs": (
            f"{_PROJECT_ROOT}/stages/01-validate-rankings/docs/en.md",
            f"{_PROJECT_ROOT}/stages/02-precision-and-recall/docs/en.md",
        ),
        "question": "Top-K 改变后，哪些已标注的相关证据进入结果，precision 与 recall 分别如何变化？",
        "prediction_prompt": "先写出你预测的 precision、recall 和分母变化，再看资料或修改代码。",
        "acceptance": "实现课程 stage 2 的 precision@k 与 recall@k 约定；解释 k 分母、正相关标签数量、空相关集合和不足 k 条结果的处理。运行验收尚未启用。",
        "adaptation": "工作台任务适配。组合课程 stage 1 的输入校验与 stage 2 的指标实现；不是课程原有 Mission。",
    },
    {
        "mission_key": "mission-02-repair-ranking-metrics",
        "kind": "mission",
        "title": "Mission 02：修复失真的排名评价",
        "stage_id": "03-rank-sensitive-metrics",
        "previous": "mission-01-find-missing-evidence",
        "docs": (f"{_PROJECT_ROOT}/stages/03-rank-sensitive-metrics/docs/en.md",),
        "question": "相同的相关文档出现在不同名次时，为什么普通 precision 看不出排序质量差异？",
        "prediction_prompt": "预测把高相关文档从第 1 位移到更后位置时，reciprocal rank 与 NDCG 会怎样变化。",
        "acceptance": "按课程 stage 3 返回 reciprocal rank 与 NDCG@k；用固定边界例子解释排序位置与分级相关性的影响。运行验收尚未启用。",
        "adaptation": "工作台任务适配，对应课程 stage 3；不是课程原有 Mission。",
    },
    {
        "mission_key": "mission-03-detect-query-regression",
        "kind": "mission",
        "title": "Mission 03：识别平均分背后的退化",
        "stage_id": "04-compare-systems",
        "previous": "mission-02-repair-ranking-metrics",
        "docs": (f"{_PROJECT_ROOT}/stages/04-compare-systems/docs/en.md",),
        "question": "总体平均指标改善时，怎样找出某些查询实际退化？",
        "prediction_prompt": "预测宏平均与逐查询对比可能给出什么不同结论，并说明未标注文档会如何计数。",
        "acceptance": "按课程 stage 4 输出逐查询指标、系统宏平均和未标注结果数；指出回归查询并解释整体变化。运行验收尚未启用。",
        "adaptation": "工作台任务适配，对应课程 stage 4；不是课程原有 Mission。",
    },
    {
        "mission_key": "boss-retrieval-evaluation-lab",
        "kind": "boss",
        "title": "Boss：交付 Retrieval Evaluation Lab",
        "stage_id": "04-compare-systems",
        "previous": "mission-03-detect-query-regression",
        "docs": (
            f"{_PROJECT_ROOT}/README.md",
            f"{_PROJECT_ROOT}/stages/01-validate-rankings/docs/en.md",
            f"{_PROJECT_ROOT}/stages/02-precision-and-recall/docs/en.md",
            f"{_PROJECT_ROOT}/stages/03-rank-sensitive-metrics/docs/en.md",
            f"{_PROJECT_ROOT}/stages/04-compare-systems/docs/en.md",
        ),
        "question": "把输入校验、检索指标和逐查询回归分析组合成可复现的评估结果。",
        "prediction_prompt": "说明哪些输入、代码版本和逐项结果应进入最终对比报告。",
        "acceptance": "课程四阶段的固定 grader、回归检测和报告需逐项通过，并保存代码、输入、命令与结果。当前 grader 未在 Windows 沙箱下验证，因此 Boss 运行关闭。",
        "adaptation": "工作台 Boss 适配，将课程四个 stage 组合为一次项目交付；不扩展到需要外部模型或向量数据库的 RAG 服务。",
    },
)
MISSION_BY_KEY = {item["mission_key"]: item for item in _SPECS}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_REWARD_XP = {"mission": 100, "feynman": 80, "boss": 500}


class ConflictError(ValueError):
    """A local draft changed after the caller loaded it."""


class CourseUnavailable(RuntimeError):
    """The configured source is absent or is not the pinned course revision."""


class ExecutionUnavailable(RuntimeError):
    """The sandbox gate is closed."""


def _git_dir(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker.resolve(strict=True)
    raise CourseUnavailable("课程源没有可核验的 Git 元数据。")


def _head_commit(root: Path) -> str:
    git_dir = _git_dir(root)
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if _COMMIT_RE.fullmatch(head):
        return head
    if not head.startswith("ref: "):
        raise CourseUnavailable("课程源的 Git HEAD 格式无法识别。")
    ref = head[5:]
    if not ref or ".." in PurePosixPath(ref).parts or PurePosixPath(ref).is_absolute():
        raise CourseUnavailable("课程源的 Git HEAD 引用无效。")
    loose = git_dir.joinpath(*PurePosixPath(ref).parts)
    if loose.is_file():
        value = loose.read_text(encoding="ascii").strip()
        if _COMMIT_RE.fullmatch(value):
            return value
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="ascii").splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and _COMMIT_RE.fullmatch(parts[0]):
                return parts[0]
    raise CourseUnavailable("课程源的 Git HEAD 指向不存在的引用。")


def _checked_root() -> Path:
    try:
        root = Path(AI_ENGINEERING_COURSE_PATH).resolve(strict=True)
    except (OSError, RuntimeError):
        raise CourseUnavailable("本机未找到已固定版本的课程源。") from None
    if not root.is_dir():
        raise CourseUnavailable("课程源路径不是目录。")
    if _head_commit(root) != COURSE_VERSION:
        raise CourseUnavailable("课程源版本与已固定版本不一致。")
    return root


def _checked_file(root: Path, relative: str, *, max_bytes: int = 100_000) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise CourseUnavailable("课程资料路径无效。")
    try:
        target = root.joinpath(*rel.parts).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > max_bytes:
            raise CourseUnavailable("课程资料路径越界或文件大小超出限制。")
        expected_hash = _SOURCE_SHA256.get(relative)
        if not expected_hash:
            raise CourseUnavailable("课程资料不在固定版本的读取清单内。")
        content = target.read_text(encoding="utf-8")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
            raise CourseUnavailable("课程资料内容与固定版本不匹配。")
    except (OSError, RuntimeError):
        raise CourseUnavailable("固定版本缺少所需课程资料。") from None
    return target


def _validated_source() -> tuple[Path, dict]:
    root = _checked_root()
    try:
        project_file = _checked_file(root, f"{_PROJECT_ROOT}/project.json")
        project = json.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise CourseUnavailable("固定版本的课程项目清单无法读取。") from None
    if not isinstance(project, dict) or project.get("id") != PROJECT_ID:
        raise CourseUnavailable("固定版本中的课程项目标识不匹配。")
    stage_ids = {item.get("id") for item in project.get("stages", []) if isinstance(item, dict)}
    if stage_ids != {"01-validate-rankings", "02-precision-and-recall", "03-rank-sensitive-metrics", "04-compare-systems"}:
        raise CourseUnavailable("固定版本中的课程阶段与适配配置不匹配。")
    return root, project


def _source_status() -> dict:
    try:
        _validated_source()
        return {"available": True, "reason": "", "commit": COURSE_VERSION}
    except CourseUnavailable as exc:
        return {"available": False, "reason": str(exc), "commit": COURSE_VERSION}


def _mission_summary(spec: dict, progress: dict | None) -> dict:
    return {
        "mission_key": spec["mission_key"],
        "kind": spec["kind"],
        "title": spec["title"],
        "stage_id": spec["stage_id"],
        "status": (progress or {}).get("status", "not_started"),
        "updated_at": (progress or {}).get("updated_at"),
        "adaptation": spec["adaptation"],
        "locked": False,
    }


def _row_dict(row) -> dict | None:
    return dict(row) if row else None


def overview() -> dict:
    with db.cursor() as cur:
        progress_rows = cur.execute(
            "SELECT * FROM learning_lab_progress WHERE course_id = ? AND course_version = ?",
            (COURSE_ID, COURSE_VERSION),
        ).fetchall()
        reward_row = cur.execute(
            "SELECT COALESCE(SUM(xp), 0) AS xp, COALESCE(SUM(CASE WHEN source = 'imported' THEN xp ELSE 0 END), 0) AS imported_xp FROM learning_lab_rewards WHERE course_id = ?",
            (COURSE_ID,),
        ).fetchone()
    progress = {row["mission_key"]: dict(row) for row in progress_rows}
    xp = int(reward_row["xp"] or 0)
    imported_xp = int(reward_row["imported_xp"] or 0)
    return {
        "course": {
            "course_id": COURSE_ID,
            "course_version": COURSE_VERSION,
            "project_id": PROJECT_ID,
            "title": "AI Engineering Learning Lab",
            "project_title": "Retrieval Evaluation Lab",
            "source_url": COURSE_URL,
            "source_status": _source_status(),
            "license": "MIT; 保留课程仓库中的原许可与作者声明。",
        },
        "missions": [_mission_summary(spec, progress.get(spec["mission_key"])) for spec in _SPECS],
        "progress_count": sum(1 for item in _SPECS if progress.get(item["mission_key"], {}).get("status") == "completed"),
        "progress_total": len(_SPECS),
        "xp": xp,
        "imported_xp": imported_xp,
        "level": 1 + xp // 500,
        "level_xp": xp % 500,
        "xp_per_level": 500,
        "runner": {"enabled": RUNNER_ENABLED, "reason": RUNNER_BLOCK_REASON},
    }


def mission_detail(mission_key: str) -> dict:
    spec = MISSION_BY_KEY.get(mission_key)
    if not spec:
        raise LookupError("未找到该课程任务。")
    source_status = _source_status()
    documents = []
    starter_code = ""
    if source_status["available"]:
        root, _ = _validated_source()
        for relative in spec["docs"]:
            try:
                content = _checked_file(root, relative).read_text(encoding="utf-8")
            except (OSError, UnicodeError, CourseUnavailable) as exc:
                source_status = {**source_status, "available": False, "reason": str(exc)}
                documents = []
                break
            documents.append({"path": relative, "content": content})
        if source_status["available"]:
            try:
                starter_code = _checked_file(
                    root, f"{_PROJECT_ROOT}/stages/01-validate-rankings/starter/main.py", max_bytes=MAX_CODE_CHARS * 4,
                ).read_text(encoding="utf-8")[:MAX_CODE_CHARS]
            except (OSError, UnicodeError, CourseUnavailable) as exc:
                source_status = {**source_status, "available": False, "reason": str(exc)}
                documents = []
    with db.cursor() as cur:
        progress_row = cur.execute(
            "SELECT * FROM learning_lab_progress WHERE course_id = ? AND course_version = ? AND mission_key = ? AND mission_version = 1",
            (COURSE_ID, COURSE_VERSION, mission_key),
        ).fetchone()
        previous_code = None
        if spec["previous"]:
            previous = cur.execute(
                "SELECT code_text FROM learning_lab_progress WHERE course_id = ? AND course_version = ? AND mission_key = ? AND mission_version = 1",
                (COURSE_ID, COURSE_VERSION, spec["previous"]),
            ).fetchone()
            previous_code = previous["code_text"] if previous else None
    progress = _row_dict(progress_row)
    return {
        "mission": _mission_summary(spec, progress),
        "question": spec["question"],
        "prediction_prompt": spec["prediction_prompt"],
        "acceptance": spec["acceptance"],
        "adaptation": spec["adaptation"],
        "sources": [
            {"url": COURSE_URL, "commit": COURSE_VERSION, "path": item["path"]}
            for item in documents
        ],
        "documents": documents,
        "source_status": source_status,
        "initial_code": progress["code_text"] if progress else (previous_code if previous_code is not None else starter_code),
        "progress": progress,
        "runner": {"enabled": RUNNER_ENABLED, "reason": RUNNER_BLOCK_REASON},
    }


def save_draft(mission_key: str, *, prediction: str, observation: str, explanation: str, code_text: str,
               help_level: int, expected_updated_at: str | None) -> dict:
    if mission_key not in MISSION_BY_KEY:
        raise LookupError("未找到该课程任务。")
    fields = (prediction, observation, explanation, code_text)
    limits = (3000, 5000, 5000, MAX_CODE_CHARS)
    if any(not isinstance(value, str) or len(value) > limit for value, limit in zip(fields, limits)):
        raise ValueError("草稿内容超过字段长度限制。")
    if type(help_level) is not int or not 0 <= help_level <= 4:
        raise ValueError("帮助程度必须在 0 到 4 之间。")
    now = datetime.now().astimezone().isoformat(timespec="microseconds")
    if now == expected_updated_at:
        now = (datetime.fromisoformat(now) + timedelta(microseconds=1)).isoformat(timespec="microseconds")
    code_sha256 = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
    status = "in_progress" if any(value.strip() for value in fields) else "not_started"
    with db.cursor() as cur:
        values = (status, prediction, observation, explanation, code_text, code_sha256, help_level, now)
        if expected_updated_at is not None:
            cur.execute(
                "UPDATE learning_lab_progress SET status = ?, prediction = ?, observation = ?, explanation = ?, code_text = ?, code_sha256 = ?, help_level = ?, source = 'local', updated_at = ? "
                "WHERE course_id = ? AND course_version = ? AND mission_key = ? AND mission_version = 1 AND updated_at = ?",
                (*values, COURSE_ID, COURSE_VERSION, mission_key, expected_updated_at),
            )
        else:
            cur.execute(
                "INSERT INTO learning_lab_progress (course_id, course_version, mission_key, mission_version, status, prediction, observation, explanation, code_text, code_sha256, help_level, source, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'local', ?, ?) "
                "ON CONFLICT(course_id, course_version, mission_key, mission_version) DO NOTHING",
                (COURSE_ID, COURSE_VERSION, mission_key, status, prediction, observation, explanation, code_text, code_sha256, help_level, now, now),
            )
        if cur.rowcount != 1:
            raise ConflictError("该任务已在其他窗口更新。当前输入仍保留在编辑框中。")
        row = cur.execute(
            "SELECT * FROM learning_lab_progress WHERE course_id = ? AND course_version = ? AND mission_key = ? AND mission_version = 1",
            (COURSE_ID, COURSE_VERSION, mission_key),
        ).fetchone()
    return dict(row)


def run_mission(mission_key: str) -> None:
    if mission_key not in MISSION_BY_KEY:
        raise LookupError("未找到该课程任务。")
    raise ExecutionUnavailable(RUNNER_BLOCK_REASON)


def _parse_timestamp(value, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise ValueError(f"备份中的{label}时间格式不正确。")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"备份中的{label}时间格式不正确。") from None
    return value


def validate_import_rows(progress_rows: list, attempt_rows: list, reward_rows: list) -> None:
    max_rows = 5000
    if any(not isinstance(items, list) for items in (progress_rows, attempt_rows, reward_rows)):
        raise ValueError("备份中的实验记录格式不正确。")
    if any(len(items) > max_rows for items in (progress_rows, attempt_rows, reward_rows)):
        raise ValueError("备份中的实验记录数量超出限制。")
    total_code_chars = 0
    total_chars = 0
    for item in progress_rows:
        if not isinstance(item, dict):
            raise ValueError("备份中的课程进度格式不正确。")
        course_id, version, mission = (item.get(key) for key in ("course_id", "course_version", "mission_key"))
        if not all(isinstance(value, str) and _ID_RE.fullmatch(value) for value in (course_id, mission)):
            raise ValueError("备份中的课程或任务编号格式不正确。")
        if not isinstance(version, str) or not 7 <= len(version) <= 64 or not re.fullmatch(r"[a-f0-9-]+", version):
            raise ValueError("备份中的课程版本格式不正确。")
        if type(item.get("mission_version")) is not int or not 1 <= item["mission_version"] <= 1000:
            raise ValueError("备份中的任务版本格式不正确。")
        status = item.get("status")
        if not isinstance(status, str) or status not in {"not_started", "in_progress", "completed", "needs_review"}:
            raise ValueError("备份中的任务状态格式不正确。")
        if type(item.get("help_level")) is not int or not 0 <= item["help_level"] <= 4:
            raise ValueError("备份中的帮助程度格式不正确。")
        limits = {"prediction": 3000, "observation": 5000, "explanation": 5000, "code_text": MAX_CODE_CHARS}
        for field, limit in limits.items():
            if not isinstance(item.get(field), str) or len(item[field]) > limit:
                raise ValueError("备份中的任务草稿内容格式不正确。")
            total_chars += len(item[field])
        total_code_chars += len(item["code_text"])
        digest = hashlib.sha256(item["code_text"].encode("utf-8")).hexdigest()
        if not isinstance(item.get("code_sha256"), str) or not _SHA_RE.fullmatch(item["code_sha256"]) or item["code_sha256"] != digest:
            raise ValueError("备份中的代码快照哈希不匹配。")
        _parse_timestamp(item.get("created_at"), "课程进度创建")
        _parse_timestamp(item.get("updated_at"), "课程进度更新")
    for item in attempt_rows:
        if not isinstance(item, dict):
            raise ValueError("备份中的实验记录格式不正确。")
        try:
            UUID(item.get("attempt_uid", ""))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("备份中的实验记录编号格式不正确。") from None
        course_id, mission = item.get("course_id"), item.get("mission_key")
        if not all(isinstance(value, str) and _ID_RE.fullmatch(value) for value in (course_id, mission)):
            raise ValueError("备份中的课程或任务编号格式不正确。")
        version = item.get("course_version")
        if not isinstance(version, str) or not 7 <= len(version) <= 64 or not re.fullmatch(r"[a-f0-9-]+", version):
            raise ValueError("备份中的课程版本格式不正确。")
        if type(item.get("mission_version")) is not int or not 1 <= item["mission_version"] <= 1000:
            raise ValueError("备份中的任务版本格式不正确。")
        phase = item.get("phase")
        if not isinstance(phase, str) or phase not in {"practice", "challenge", "boss", "delayed"}:
            raise ValueError("备份中的实验阶段格式不正确。")
        status = item.get("status")
        if not isinstance(status, str) or status not in {"queued", "running", "succeeded", "failed", "timed_out", "cancelled", "interrupted", "imported"}:
            raise ValueError("备份中的实验状态格式不正确。")
        code = item.get("code_snapshot")
        digest = item.get("code_sha256")
        if not isinstance(code, str) or len(code) > MAX_CODE_CHARS or not isinstance(digest, str) or not _SHA_RE.fullmatch(digest):
            raise ValueError("备份中的运行代码快照格式不正确。")
        total_code_chars += len(code)
        if hashlib.sha256(code.encode("utf-8")).hexdigest() != digest:
            raise ValueError("备份中的运行代码哈希不匹配。")
        json_limits = {"input_json": 10_000, "command_json": 10_000, "result_json": 25_000}
        for field, limit in json_limits.items():
            value = item.get(field)
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("备份中的实验输入、命令或结果格式不正确。")
            total_chars += len(value)
            try:
                json.loads(value)
            except (ValueError, TypeError):
                raise ValueError("备份中的实验输入、命令或结果格式不正确。") from None
        if item.get("exit_code") is not None and (type(item["exit_code"]) is not int or not -255 <= item["exit_code"] <= 255):
            raise ValueError("备份中的实验退出码格式不正确。")
        if not isinstance(item.get("grader_version"), str) or len(item["grader_version"]) > 80:
            raise ValueError("备份中的 grader 版本格式不正确。")
        if type(item.get("help_level")) is not int or not 0 <= item["help_level"] <= 4:
            raise ValueError("备份中的帮助程度格式不正确。")
        _parse_timestamp(item.get("created_at"), "实验记录")
    for item in reward_rows:
        if not isinstance(item, dict):
            raise ValueError("备份中的 XP 记录格式不正确。")
        try:
            UUID(item.get("reward_uid", ""))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("备份中的 XP 记录编号格式不正确。") from None
        course_id, mission, reward_type = item.get("course_id"), item.get("mission_key"), item.get("reward_type")
        if not all(isinstance(value, str) and _ID_RE.fullmatch(value) for value in (course_id, mission)):
            raise ValueError("备份中的 XP 课程或任务编号格式不正确。")
        if not isinstance(reward_type, str) or reward_type not in _REWARD_XP or type(item.get("xp")) is not int or item["xp"] != _REWARD_XP[reward_type]:
            raise ValueError("备份中的 XP 奖励类型或数值不正确。")
        if item.get("reward_key") != f"{course_id}:{mission}:{reward_type}":
            raise ValueError("备份中的 XP 去重编号格式不正确。")
        if item.get("attempt_uid"):
            try:
                UUID(item["attempt_uid"])
            except (ValueError, TypeError):
                raise ValueError("备份中的 XP 关联实验编号格式不正确。") from None
        _parse_timestamp(item.get("created_at"), "XP 记录")
    if total_code_chars > 10_000_000 or total_chars > 50_000_000:
        raise ValueError("备份中的课程文本总量超出限制。")


def import_preview(cur, progress_rows: list, attempt_rows: list, reward_rows: list) -> dict:
    conflicts = {"progress": 0, "attempts": 0, "rewards": 0}
    for item in progress_rows:
        existing = cur.execute(
            "SELECT status, prediction, observation, explanation, code_text, code_sha256, help_level, created_at, updated_at FROM learning_lab_progress WHERE course_id = ? AND course_version = ? AND mission_key = ? AND mission_version = ?",
            (item["course_id"], item["course_version"], item["mission_key"], item["mission_version"]),
        ).fetchone()
        if existing and (
            existing["status"] not in {item["status"], "needs_review" if item["status"] == "completed" else item["status"]}
            or any(existing[key] != item.get(key) for key in (
                "prediction", "observation", "explanation", "code_text", "code_sha256", "help_level", "created_at", "updated_at",
            ))
        ):
            conflicts["progress"] += 1
    for item in attempt_rows:
        existing = cur.execute(
            "SELECT course_id, course_version, mission_key, mission_version, phase, status, code_sha256, code_snapshot, input_json, command_json, result_json, exit_code, grader_version, help_level, created_at FROM learning_lab_attempts WHERE attempt_uid = ?",
            (item["attempt_uid"],),
        ).fetchone()
        if existing and (
            existing["status"] not in {item["status"], "imported"}
            or any(existing[key] != item.get(key) for key in (
                "course_id", "course_version", "mission_key", "mission_version", "phase", "code_sha256", "code_snapshot",
                "input_json", "command_json", "result_json", "exit_code", "grader_version", "help_level", "created_at",
            ))
        ):
            conflicts["attempts"] += 1
    for item in reward_rows:
        existing = cur.execute(
            "SELECT reward_uid, course_id, mission_key, reward_type, xp, attempt_uid, created_at FROM learning_lab_rewards WHERE reward_key = ?",
            (item["reward_key"],),
        ).fetchone()
        if existing and any(existing[key] != item.get(key) for key in (
            "reward_uid", "course_id", "mission_key", "reward_type", "xp", "attempt_uid", "created_at",
        )):
            conflicts["rewards"] += 1
    return {"incoming": {"progress": len(progress_rows), "attempts": len(attempt_rows), "rewards": len(reward_rows)}, "conflicts": conflicts}


def import_records(cur, progress_rows: list, attempt_rows: list, reward_rows: list) -> dict:
    counts = {"progress": 0, "attempts": 0, "rewards": 0}
    for item in progress_rows:
        status = "needs_review" if item["status"] == "completed" else item["status"]
        cur.execute(
            "INSERT OR IGNORE INTO learning_lab_progress (course_id, course_version, mission_key, mission_version, status, prediction, observation, explanation, code_text, code_sha256, help_level, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported', ?, ?)",
            (item["course_id"], item["course_version"], item["mission_key"], item["mission_version"], status,
             item["prediction"], item["observation"], item["explanation"], item["code_text"], item["code_sha256"],
             item["help_level"], item["created_at"], item["updated_at"]),
        )
        counts["progress"] += cur.rowcount
    for item in attempt_rows:
        cur.execute(
            "INSERT OR IGNORE INTO learning_lab_attempts (attempt_uid, course_id, course_version, mission_key, mission_version, phase, status, code_sha256, code_snapshot, input_json, command_json, result_json, exit_code, grader_version, help_level, source, created_at) VALUES (?, ?, ?, ?, ?, ?, 'imported', ?, ?, ?, ?, ?, ?, ?, ?, 'imported', ?)",
            (item["attempt_uid"], item["course_id"], item["course_version"], item["mission_key"], item["mission_version"], item["phase"],
             item["code_sha256"], item["code_snapshot"], item["input_json"], item["command_json"], item["result_json"],
             item.get("exit_code"), item["grader_version"], item["help_level"], item["created_at"]),
        )
        counts["attempts"] += cur.rowcount
    for item in reward_rows:
        cur.execute(
            "INSERT OR IGNORE INTO learning_lab_rewards (reward_key, reward_uid, course_id, mission_key, reward_type, xp, attempt_uid, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'imported', ?)",
            (item["reward_key"], item["reward_uid"], item["course_id"], item["mission_key"], item["reward_type"], item["xp"], item.get("attempt_uid"), item["created_at"]),
        )
        counts["rewards"] += cur.rowcount
    return counts
