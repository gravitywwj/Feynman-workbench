"""Application configuration and user-owned local workspace settings."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Local-only convenience: .env stays available as a deployment fallback.
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.environ.get("FEYNMAN_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("FEYNMAN_DB_PATH", DATA_DIR / "feynman.db"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEMO_WIKI_DIR = Path(__file__).resolve().parent / "demo_wiki"
SETTINGS_PATH = DATA_DIR / "workspace-settings.json"
LLM_SETTINGS_PATH = DATA_DIR / "llm-settings.json"
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"


def _stored_settings() -> dict:
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _stored_llm_settings() -> dict:
    """Read local-only LLM settings. The secret is never returned to the browser."""
    try:
        value = json.loads(LLM_SETTINGS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "已保存"
    return f"{value[:3]}{'•' * 8}{value[-4:]}"


def _environment_llm_values() -> dict:
    return {
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", ""),
        "model": os.environ.get("FEYNMAN_LLM_MODEL", ""),
    }


def _llm_profiles(stored: dict) -> list[dict]:
    """Normalize current profiles and migrate the first single-profile format in memory."""
    raw_profiles = stored.get("profiles")
    profiles = raw_profiles if isinstance(raw_profiles, list) else []
    if not profiles and stored.get("api_key"):
        profiles = [{
            "id": "legacy-local", "name": "本机默认连接", "api_key": stored.get("api_key", ""),
            "base_url": stored.get("base_url", DEFAULT_LLM_BASE_URL),
            "model": stored.get("model", DEFAULT_LLM_MODEL),
        }]
    normalized: list[dict] = []
    used_ids: set[str] = set()
    for raw in profiles:
        if not isinstance(raw, dict):
            continue
        profile_id = str(raw.get("id") or "").strip()
        if not profile_id or profile_id in used_ids:
            profile_id = uuid4().hex
        used_ids.add(profile_id)
        key = str(raw.get("api_key") or "").strip()
        if not key:
            continue
        normalized.append({
            "id": profile_id,
            "name": str(raw.get("name") or "未命名连接").strip()[:60] or "未命名连接",
            "api_key": key,
            "base_url": str(raw.get("base_url") or DEFAULT_LLM_BASE_URL).strip().rstrip("/"),
            "model": str(raw.get("model") or DEFAULT_LLM_MODEL).strip(),
            "last_test": raw.get("last_test") if isinstance(raw.get("last_test"), dict) else None,
        })
    return normalized


def _active_local_profile(stored: dict) -> tuple[dict | None, list[dict]]:
    profiles = _llm_profiles(stored)
    active_id = str(stored.get("active_profile_id") or "")
    active = next((profile for profile in profiles if profile["id"] == active_id), None)
    if not active and len(profiles) == 1:
        active = profiles[0]
    return active, profiles


def _write_llm_settings(profiles: list[dict], active_profile_id: str | None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "profiles": profiles,
        "active_profile_id": active_profile_id or "",
    }
    temporary = LLM_SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(LLM_SETTINGS_PATH)


def _effective_llm_values() -> tuple[dict, dict, dict | None]:
    stored = _stored_llm_settings()
    active, _ = _active_local_profile(stored)
    environment = _environment_llm_values()
    # An intentionally selected web profile wins. .env remains a useful fallback
    # for first launch, deployment and headless use when no profile is active.
    if active:
        return {
            "api_key": active["api_key"],
            "base_url": active["base_url"],
            "model": active["model"],
            "source": "local",
        }, environment, active
    effective = {
        "api_key": environment["api_key"],
        "base_url": environment["base_url"] or DEFAULT_LLM_BASE_URL,
        "model": environment["model"] or DEFAULT_LLM_MODEL,
        "source": "environment" if environment["api_key"] else "none",
    }
    return effective, environment, None


def get_workspace_settings() -> dict:
    """Return the effective local workspace configuration and its readiness."""
    stored = _stored_settings()
    explicit_wiki = os.environ.get("FEYNMAN_WIKI_PATH")
    mode = "local" if explicit_wiki else stored.get("mode", "local")
    raw_path = explicit_wiki or stored.get("wiki_path") or r"D:\LLM wiki"
    wiki_path = DEMO_WIKI_DIR if mode == "demo" else Path(raw_path)
    llm, _, _ = _effective_llm_values()
    diagnostic_mode = stored.get("diagnostic_mode") or ("ai" if llm["api_key"] else "local")
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
        "ai_available": bool(llm["api_key"]),
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
    llm, _, profile = _effective_llm_values()
    return {
        **llm,
        "profile_id": profile["id"] if profile else None,
        "mode": settings["diagnostic_mode"],
    }


def get_llm_settings() -> dict:
    """Return profile metadata while deliberately never returning any raw key."""
    effective, environment, active = _effective_llm_values()
    stored = _stored_llm_settings()
    _, profiles = _active_local_profile(stored)
    public_profiles = [
        {
            "id": profile["id"], "name": profile["name"], "base_url": profile["base_url"],
            "model": profile["model"], "api_key_masked": _mask_secret(profile["api_key"]),
            "last_test": profile.get("last_test"), "active": bool(active and profile["id"] == active["id"]),
        }
        for profile in profiles
    ]
    return {
        "configured": bool(effective["api_key"]),
        "source": effective["source"],
        "api_key_masked": _mask_secret(effective["api_key"]),
        "base_url": effective["base_url"],
        "model": effective["model"],
        "active_profile_id": active["id"] if active else None,
        "active_profile_name": active["name"] if active else "",
        "profiles": public_profiles,
        "environment_fallback_available": bool(environment["api_key"]),
    }


def _valid_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("服务地址需要是完整的 http 或 https URL")
    return url


def save_llm_settings(
    *, api_key: str, base_url: str, model: str, profile_name: str = "", profile_id: str | None = None,
) -> dict:
    """Save and activate one user-owned provider profile without exposing its key."""
    key = api_key.strip()
    if "\n" in key or "\r" in key:
        raise ValueError("API Key 不能包含换行符")
    clean_base_url = _valid_base_url(base_url)
    clean_model = model.strip()
    if not clean_model or "\n" in clean_model or "\r" in clean_model:
        raise ValueError("请填写服务提供的模型标识")
    clean_name = profile_name.strip() or "未命名连接"
    if "\n" in clean_name or "\r" in clean_name:
        raise ValueError("配置名称不能包含换行符")
    if len(key) > 1000 or len(clean_model) > 160 or len(clean_base_url) > 2000 or len(clean_name) > 60:
        raise ValueError("API 设置长度超出限制")
    stored = _stored_llm_settings()
    _, profiles = _active_local_profile(stored)
    target = next((profile for profile in profiles if profile["id"] == profile_id), None) if profile_id else None
    saved_key = key or (target["api_key"] if target else "")
    if not saved_key:
        raise ValueError("新连接需要填写 API Key；编辑已保存连接时可留空以保留原密钥。")
    if target:
        target.update({"name": clean_name, "base_url": clean_base_url, "model": clean_model, "api_key": saved_key})
    else:
        target = {
            "id": uuid4().hex, "name": clean_name, "base_url": clean_base_url,
            "model": clean_model, "api_key": saved_key, "last_test": None,
        }
        profiles.append(target)
    _write_llm_settings(profiles, target["id"])
    return get_llm_settings()


def activate_llm_profile(profile_id: str) -> dict:
    stored = _stored_llm_settings()
    _, profiles = _active_local_profile(stored)
    if not any(profile["id"] == profile_id for profile in profiles):
        raise LookupError("要启用的连接不存在")
    _write_llm_settings(profiles, profile_id)
    return get_llm_settings()


def delete_llm_profile(profile_id: str) -> dict:
    stored = _stored_llm_settings()
    _, profiles = _active_local_profile(stored)
    if not any(profile["id"] == profile_id for profile in profiles):
        raise LookupError("要删除的连接不存在")
    remaining = [profile for profile in profiles if profile["id"] != profile_id]
    active_id = str(stored.get("active_profile_id") or "")
    if active_id == profile_id:
        active_id = remaining[0]["id"] if remaining else ""
    _write_llm_settings(remaining, active_id)
    return get_llm_settings()


def _record_llm_test(profile_id: str, ok: bool, message: str) -> None:
    stored = _stored_llm_settings()
    _, profiles = _active_local_profile(stored)
    profile = next((item for item in profiles if item["id"] == profile_id), None)
    if not profile:
        return
    profile["last_test"] = {
        "ok": ok, "message": message[:300], "tested_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_llm_settings(profiles, str(stored.get("active_profile_id") or profile_id))


def test_llm_connection() -> dict:
    """Perform one minimal chat request only after the learner explicitly asks to test."""
    llm = get_llm_config()
    if not llm["api_key"]:
        raise ValueError("请先保存 API Key，再测试连接。")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=llm["api_key"], base_url=llm["base_url"], timeout=20)
        client.chat.completions.create(
            model=llm["model"],
            temperature=0,
            max_tokens=2,
            messages=[{"role": "user", "content": "Reply with OK."}],
        )
        message = f"已连接到 {llm['model']}。"
        if llm["profile_id"]:
            _record_llm_test(llm["profile_id"], True, message)
        return {"ok": True, "message": message}
    except Exception as exc:
        message = str(exc).replace(llm["api_key"], "[已隐藏]")[:300]
        result = f"连接失败：{message or '服务未返回有效响应'}"
        if llm["profile_id"]:
            _record_llm_test(llm["profile_id"], False, result)
        return {"ok": False, "message": result}
