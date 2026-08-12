"""费曼学习工作台 — Wiki 读取层（对 D:\\LLM wiki 只读，绝不写入）"""
import re
import unicodedata
from pathlib import Path

import markdown as md

from app.config import get_wiki_path

# 管理页/非概念页：不进入概念库
EXCLUDED_FILES = {"dashboard.md"}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
CODE_FENCE_RE = re.compile(r"^```")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta, body)。frontmatter 缺失时返回 ({}, 全文)。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in ("tags", "sources", "contradictions"):
            value = [v.strip().strip("'\"") for v in re.findall(r"[-\s]*([^\s,\[\]\"']+)", value) if v.strip()]
        else:
            value = value.strip("'\"")
        meta[key] = value
    return meta, m.group(2).strip()


def _section_of(rel_path: Path) -> str:
    """分区名 = pages/ 下第一层目录名（如 AI/rag/xxx.md → AI）。"""
    parts = rel_path.parts
    return parts[0] if len(parts) > 1 else ""


def scan_concepts() -> list[dict]:
    """扫描 pages/ 全部概念页。返回按分区+标题排序的列表，字典含：
    path / title / section / type / status / tags / created / updated / line_count"""
    wiki = get_wiki_path()
    pages_dir = wiki / "pages"
    if not pages_dir.is_dir():
        return []
    concepts = []
    for f in sorted(pages_dir.rglob("*.md")):
        if f.name in EXCLUDED_FILES:
            continue
        rel = f.relative_to(pages_dir).as_posix()
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        meta, body = parse_frontmatter(text)
        concepts.append({
            "path": rel,
            "title": meta.get("title") or f.stem,
            "section": _section_of(Path(rel)),
            "type": meta.get("type", ""),
            "status": meta.get("status", "unread"),
            "importance": meta.get("importance", ""),
            "tags": meta.get("tags", []),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "line_count": body.count("\n") + 1,
        })
    return concepts


def _link_target_exists(target: str, wiki: Path) -> bool:
    """wikilink 目标按 basename 匹配（与 Obsidian 规则一致）。"""
    for f in (wiki / "pages").rglob("*.md"):
        if f.stem == target:
            return True
    return False


def extract_wikilinks(body: str) -> list[str]:
    """提取正文中的 [[wikilink]] 目标（跳过 ``` 代码块，避免示例/向量片段误报）。"""
    targets: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if CODE_FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in WIKILINK_RE.finditer(line):
            targets.append(m.group(1))
    return targets


def build_graph() -> dict:
    """构建知识图谱：nodes = 概念页；links = 页间 wikilink 出链（无向去重，仅存在目标）。"""
    wiki = get_wiki_path()
    pages_dir = wiki / "pages"
    if not pages_dir.is_dir():
        return {"nodes": [], "links": []}
    concepts = scan_concepts()
    by_stem: dict[str, str] = {}
    for c in concepts:
        by_stem[c["path"].rsplit("/", 1)[-1][:-3]] = c["path"]
    links: set[tuple[str, str]] = set()
    for c in concepts:
        f = pages_dir / c["path"]
        try:
            _, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        for target in extract_wikilinks(body):
            t_path = by_stem.get(target)
            if not t_path or t_path == c["path"]:
                continue
            pair = tuple(sorted((c["path"], t_path)))
            links.add(pair)
    return {
        "nodes": [
            {"id": c["path"], "title": c["title"], "section": c["section"],
             "status": c["status"], "importance": c["importance"]}
            for c in concepts
        ],
        "links": [{"source": a, "target": b} for a, b in sorted(links)],
    }


def render_page_html(path: str) -> tuple[dict, str]:
    """读取页面 → (meta, 正文 HTML)。wikilink 转成可点击样式（内部概念跳转）或不存在的目标转灰显。"""
    wiki = get_wiki_path()
    p = Path(path)
    if p.suffix != ".md" or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"非法页面路径: {path}")
    f = wiki / "pages" / p
    if not f.is_file():
        raise FileNotFoundError(path)
    text = f.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    def repl(m: re.Match) -> str:
        target, label = m.group(1), m.group(2)
        label = label or target
        cls = "wl-ok" if _link_target_exists(target, wiki) else "wl-missing"
        return f'<span class="wikilink {cls}" data-target="{target}">{label}</span>'

    body_html = WIKILINK_RE.sub(repl, body)
    html = md.markdown(body_html, extensions=["fenced_code", "tables", "sane_lists"])
    return meta, html


def display_size(line_count: int) -> str:
    """粗略阅读时长提示（约 1 行/秒，中文按字数修正）。"""
    return f"约 {max(1, line_count // 60)} 分钟"


def slugify(text: str) -> str:
    """概念名 → 文件名友好 slug（供 review 草稿命名）。"""
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text)
    return text.strip("-")[:40] or "concept"
