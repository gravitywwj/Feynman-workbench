"""Application configuration and user-owned local workspace settings."""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FEYNMAN_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("FEYNMAN_DB_PATH", DATA_DIR / "feynman.db"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEMO_WIKI_DIR = Path(__file__).resolve().parent / "demo_wiki"
SETTINGS_PATH = DATA_DIR / "workspace-settings.json"


def _stored_settings() -> dict:
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def get_workspace_settings() -> dict:
    """Return the effective local workspace configuration and its readiness."""
    stored = _stored_settings()
    explicit_wiki = os.environ.get("FEYNMAN_WIKI_PATH")
    mode = "local" if explicit_wiki else stored.get("mode", "local")
    raw_path = explicit_wiki or stored.get("wiki_path") or r"D:\LLM wiki"
    wiki_path = DEMO_WIKI_DIR if mode == "demo" else Path(raw_path)
    diagnostic_mode = stored.get("diagnostic_mode") or ("ai" if os.environ.get("DEEPSEEK_API_KEY") else "local")
    if diagnostic_mode not in {"local", "ai"}:
        diagnostic_mode = "local"
    try:
        daily_review_goal = min(50, max(1, int(stored.get("daily_review_goal", 5))))
    except (TypeError, ValueError):
        daily_review_goal = 5
    learning_goal = stored.get("learning_goal", "long_term")
    if learning_goal not in {"exam", "presentation", "long_term"}:
        learning_goal = "long_term"
    return {
        "mode": mode,
        "wiki_path": str(wiki_path),
        "configured": (wiki_path / "pages").is_dir(),
        "diagnostic_mode": diagnostic_mode,
        "ai_available": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "daily_review_goal": daily_review_goal,
        "learning_goal": learning_goal,
        "uses_environment_path": bool(explicit_wiki),
    }


def save_workspace_settings(
    *, mode: str, wiki_path: str | None, diagnostic_mode: str, daily_review_goal: int, learning_goal: str = "long_term"
) -> dict:
    """Persist user choices. An explicit environment path remains deployment authority."""
    if mode not in {"local", "demo"}:
        raise ValueError("学习资料模式必须是 local 或 demo")
    if diagnostic_mode not in {"local", "ai"}:
        raise ValueError("诊断模式必须是 local 或 ai")
    if not 1 <= daily_review_goal <= 50:
        raise ValueError("每日复习目标应在 1 到 50 张之间")
    if learning_goal not in {"exam", "presentation", "long_term"}:
        raise ValueError("学习目标必须为 exam、presentation 或 long_term")
    if mode == "local":
        if not wiki_path:
            raise ValueError("请输入 Wiki 文件夹路径")
        candidate = Path(wiki_path).expanduser()
        if not candidate.is_absolute() or not (candidate / "pages").is_dir():
            raise ValueError("该文件夹需要是包含 pages 子目录的本地 Wiki 根目录")
        normalized_path = str(candidate)
    else:
        normalized_path = str(DEMO_WIKI_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "wiki_path": normalized_path,
        "diagnostic_mode": diagnostic_mode,
        "daily_review_goal": daily_review_goal,
        "learning_goal": learning_goal,
    }
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_PATH)
    return get_workspace_settings()


def get_wiki_path() -> Path:
    """Return the selected Wiki root. Environment injection remains test priority."""
    return Path(get_workspace_settings()["wiki_path"])


def get_llm_config() -> dict:
    settings = get_workspace_settings()
    return {
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("FEYNMAN_LLM_MODEL", "deepseek-v4-flash"),
        "mode": settings["diagnostic_mode"],
    }
