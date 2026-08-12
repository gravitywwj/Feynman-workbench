"""费曼学习工作台 — Wiki 读取层（对 D:\\LLM wiki 只读，绝不写入）"""
import html
import re
import unicodedata
from pathlib import Path

import bleach
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


SAFE_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "img", "li", "ol", "p", "pre", "span", "strong", "table", "tbody", "td", "th",
    "thead", "tr", "ul",
}
SAFE_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "span": ["class", "data-path", "data-target"],
    "code": ["class"],
}


def _wikilink_index(concepts: list[dict]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """建立精确路径和文件名索引，文件名重名时保留全部候选。"""
    by_path: dict[str, str] = {}
    by_stem: dict[str, list[str]] = {}
    for concept in concepts:
        path = concept["path"]
        by_path[path.removesuffix(".md")] = path
        stem = Path(path).stem
        by_stem.setdefault(stem, []).append(path)
    return by_path, by_stem


def _resolve_wikilink(target: str, by_path: dict[str, str], by_stem: dict[str, list[str]]) -> str | None:
    normalized = target.strip().replace("\\", "/").removesuffix(".md")
    if normalized in by_path:
        return by_path[normalized]
    candidates = by_stem.get(Path(normalized).name, [])
    return candidates[0] if len(candidates) == 1 else None


def resolve_wikilink(target: str, concepts: list[dict]) -> str | None:
    """解析 wikilink：优先精确相对路径；仅有唯一同名文件时才按文件名跳转。"""
    by_path, by_stem = _wikilink_index(concepts)
    return _resolve_wikilink(target, by_path, by_stem)


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
    by_path, by_stem = _wikilink_index(concepts)
    links: set[tuple[str, str]] = set()
    for c in concepts:
        f = pages_dir / c["path"]
        try:
            _, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        for target in extract_wikilinks(body):
            t_path = _resolve_wikilink(target, by_path, by_stem)
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

    concepts = scan_concepts()
    by_path, by_stem = _wikilink_index(concepts)

    def repl(m: re.Match) -> str:
        target, label = m.group(1), m.group(2)
        label = label or target
        resolved_path = _resolve_wikilink(target, by_path, by_stem)
        cls = "wl-ok" if resolved_path else "wl-missing"
        data_path = f' data-path="{html.escape(resolved_path, quote=True)}"' if resolved_path else ""
        return (
            f'<span class="wikilink {cls}" data-target="{html.escape(target, quote=True)}"{data_path}>'
            f'{html.escape(label)}</span>'
        )

    body_html = WIKILINK_RE.sub(repl, body)
    rendered = md.markdown(body_html, extensions=["fenced_code", "tables", "sane_lists"])
    sanitized_html = bleach.clean(
        rendered, tags=SAFE_TAGS, attributes=SAFE_ATTRIBUTES, protocols=["http", "https", "mailto"]
    )
    return meta, sanitized_html


def display_size(line_count: int) -> str:
    """粗略阅读时长提示（约 1 行/秒，中文按字数修正）。"""
    return f"约 {max(1, line_count // 60)} 分钟"


def slugify(text: str) -> str:
    """概念名 → 文件名友好 slug（供 review 草稿命名）。"""
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text)
    return text.strip("-")[:40] or "concept"
