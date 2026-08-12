"""费曼学习工作台 — 配置"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "feynman.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_wiki_path() -> Path:
    """Wiki 根目录（默认 D:\\LLM wiki，可用环境变量 FEYNMAN_WIKI_PATH 覆盖，便于测试注入 fixture）。"""
    return Path(os.environ.get("FEYNMAN_WIKI_PATH", r"D:\LLM wiki"))


def get_llm_config() -> dict:
    return {
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("FEYNMAN_LLM_MODEL", "deepseek-v4-flash"),
    }
