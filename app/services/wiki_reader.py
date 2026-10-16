"""费曼学习工作台 — Wiki 读取层（对 D:\\LLM wiki 只读，绝不写入）"""
import hashlib
import html
import re
import unicodedata
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import bleach
import markdown as md

from app.config import get_wiki_path
from app.services import mastery

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
    states = mastery.overview(concepts)
    for concept in concepts:
        concept["mastery"] = states.get(concept["path"], {"level": "unseen", "label": "未接触"})
    return concepts


def _parse_created_date(value: str) -> date | None:
    """解析笔记在 frontmatter 中声明的首次加入日期，不读取文件系统修改时间。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def recent_concepts(days: int = 14, today: date | None = None) -> list[dict]:
    """返回最近首次加入 Wiki 的页面，唯一依据是 frontmatter 的 created 字段。"""
    current_day = today or date.today()
    cutoff = current_day - timedelta(days=days - 1)
    recent = []
    for concept in scan_concepts():
        created_on = _parse_created_date(concept["created"])
        if created_on and cutoff <= created_on <= current_day:
            recent.append((created_on, concept))
    recent.sort(key=lambda item: (item[0], item[1]["title"]), reverse=True)
    return [concept for _, concept in recent]


def read_page_markdown(path: str) -> tuple[dict, str]:
    """Read one approved Wiki page as frontmatter and Markdown body.

    Agent features use this instead of rendered HTML so quoted evidence remains
    readable, inspectable and separate from presentation markup.
    """
    wiki = get_wiki_path()
    candidate = Path(path)
    if candidate.suffix != ".md" or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"非法页面路径: {path}")
    file_path = wiki / "pages" / candidate
    if not file_path.is_file():
        raise FileNotFoundError(path)
    return parse_frontmatter(file_path.read_text(encoding="utf-8"))


def _search_terms(query: str) -> list[str]:
    """Extract a small, explainable set of terms for local Wiki retrieval."""
    normalized = unicodedata.normalize("NFKC", query).lower()
    terms = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", normalized)
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
    return unique[:12]


def search_wiki(query: str, limit: int = 5) -> list[dict]:
    """Search the connected Wiki locally and return short evidence excerpts.

    This intentionally uses transparent lexical matching instead of pretending
    a local search is semantic retrieval. The Agent receives only these visible
    excerpts and must cite their pages in its answer.
    """
    terms = _search_terms(query)
    if not terms:
        return []
    candidates: list[tuple[int, dict]] = []
    for concept in scan_concepts():
        try:
            _, body = read_page_markdown(concept["path"])
        except (OSError, UnicodeDecodeError, ValueError, FileNotFoundError):
            continue
        haystack = f"{concept['title']}\n{body}".lower()
        title = concept["title"].lower()
        score = sum(haystack.count(term) + (3 if term in title else 0) for term in terms)
        if not score:
            continue
        excerpt = ""
        for line in body.splitlines():
            lowered = line.lower()
            if any(term in lowered for term in terms):
                excerpt = re.sub(r"\s+", " ", line).strip()
                break
        candidates.append((score, {
            "path": concept["path"],
            "title": concept["title"],
            "section": concept["section"],
            "excerpt": (excerpt or re.sub(r"\s+", " ", body).strip())[:320],
            "matched_terms": [term for term in terms if term in haystack],
        }))
    candidates.sort(key=lambda item: (-item[0], item[1]["title"], item[1]["path"]))
    return [item for _, item in candidates[:max(1, min(limit, 10))]]


def raw_evidence_for_page(page_path: str, query: str, limit: int = 3) -> list[dict]:
    """Read bounded excerpts from a page's declared immutable raw sources."""
    wiki = get_wiki_path().resolve()
    if wiki.name == "personal-wiki" or ((wiki.parent / "SCHEMA.md").is_file() and wiki.name != "learning-wiki"):
        raise ValueError("最终审核只能读取 learning-wiki，不能读取个人知识空间。")
    candidate = Path(page_path)
    if candidate.suffix != ".md" or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"非法页面路径: {page_path}")
    page = (wiki / "pages" / candidate).resolve()
    if not page.is_relative_to(wiki / "pages") or not page.is_file():
        raise FileNotFoundError(page_path)
    text = page.read_text(encoding="utf-8")
    frontmatter = FRONTMATTER_RE.match(text)
    if not frontmatter:
        return []
    field = re.search(r"(?m)^sources:[ \t]*(.*)$", frontmatter.group(1))
    if not field:
        return []
    inline = field.group(1).strip()
    if inline.startswith("["):
        paths = [part.strip().strip("'\"") for part in inline.strip("[]").split(",")]
    else:
        paths = []
        for line in frontmatter.group(1)[field.end():].lstrip("\r\n").splitlines():
            item = re.match(r"^[ \t]+-[ \t]+(.+)$", line)
            if not item:
                break
            paths.append(item.group(1).strip().strip("'\""))
    raw_root = (wiki / "raw").resolve()
    terms = _search_terms(query)
    result = []
    for path in paths:
        if not path.startswith("raw/") or not path.endswith(".md"):
            continue
        source = (wiki / path).resolve()
        if not source.is_relative_to(raw_root) or not source.is_file():
            continue
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            body = handle.read(150_000)
        _, body = parse_frontmatter(body)
        lowered = body.lower()
        positions = [lowered.find(term) for term in terms if len(term) >= 2]
        position = next((value for value in positions if value >= 0), 0)
        start = max(0, position - 250)
        result.append({"path": path, "excerpt": body[start:start + 2200].strip(), "kind": "raw"})
        if len(result) >= limit:
            break
    return result


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
    "p": ["id", "data-source-block"],
    "li": ["id", "data-source-block"],
    "pre": ["id", "data-source-block"],
    "table": ["id", "data-source-block"],
    "h1": ["id", "data-source-block"],
    "h2": ["id", "data-source-block"],
    "h3": ["id", "data-source-block"],
    "h4": ["id", "data-source-block"],
    "h5": ["id", "data-source-block"],
    "h6": ["id", "data-source-block"],
}


def _source_visible_text(source: str, kind: str) -> str:
    if kind == "code":
        return re.sub(r"\s+", " ", source).strip()
    source = WIKILINK_RE.sub(lambda match: match.group(2) or match.group(1), source)
    rendered = md.markdown(source, extensions=["fenced_code", "tables", "sane_lists"])
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", rendered))).strip()


def _source_fingerprint(index: int, heading: str, raw: str, before: str, after: str) -> str:
    value = "\n".join((str(index), heading, raw, before, after))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_blocks(body: str) -> list[dict]:
    """Split Wiki Markdown into stable, inspectable blocks used by cards and reading recall."""
    lines = body.splitlines()
    raw_blocks: list[dict] = []
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if match:
            heading = re.sub(r"\s+", " ", match.group(2)).strip()
            raw_blocks.append({"kind": "heading", "tag": f"h{len(match.group(1))}", "heading": heading,
                               "raw": match.group(2), "start_line": i + 1})
            i += 1
            continue
        fence = re.match(r"^\s*(```+|~~~+)", line)
        if fence:
            marker = fence.group(1)[0]
            start = i
            code = []
            i += 1
            while i < len(lines) and not re.match(rf"^\s*{re.escape(marker)}{{3,}}", lines[i]):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            raw_blocks.append({"kind": "code", "tag": "pre", "heading": heading,
                               "raw": "\n".join(code), "start_line": start + 1})
            continue
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[i + 1]):
            start = i
            table = [line]
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                table.append(lines[i])
                i += 1
            raw_blocks.append({"kind": "table", "tag": "table", "heading": heading,
                               "raw": "\n".join(table), "start_line": start + 1})
            continue
        if re.match(r"^\s*(?:[-+*]\s+|\d+[.)]\s+)", line):
            start = i
            item = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and (
                lines[i].startswith(("  ", "\t"))
                and not re.match(r"^\s*(?:[-+*]\s+|\d+[.)]\s+)", lines[i])
            ):
                item.append(lines[i])
                i += 1
            raw_blocks.append({"kind": "list_item", "tag": "li", "heading": heading,
                               "raw": "\n".join(item), "start_line": start + 1})
            continue
        start = i
        paragraph = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(?:#{1,6}\s|\s*(?:[-+*]\s+|\d+[.)]\s+|```+|~~~+))", lines[i],
        ) and not ("|" in lines[i] and i + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[i + 1],
        )):
            paragraph.append(lines[i])
            i += 1
        raw_blocks.append({"kind": "paragraph", "tag": "p", "heading": heading,
                           "raw": "\n".join(paragraph), "start_line": start + 1})

    for index, block in enumerate(raw_blocks):
        block["index"] = index
        block["excerpt"] = _source_visible_text(block["raw"], block["kind"])
    for index, block in enumerate(raw_blocks):
        block["context_before"] = raw_blocks[index - 1]["excerpt"] if index else ""
        block["context_after"] = raw_blocks[index + 1]["excerpt"] if index + 1 < len(raw_blocks) else ""
        block["fingerprint"] = _source_fingerprint(
            index, block["heading"], block["raw"], block["context_before"], block["context_after"],
        )
    return raw_blocks


def capture_source_block(page_path: str, source_index: int, submitted_excerpt: str) -> dict:
    """Return source text from disk after checking the browser's selected block."""
    meta, body = read_page_markdown(page_path)
    blocks = source_blocks(body)
    if not isinstance(source_index, int) or not 0 <= source_index < len(blocks):
        raise ValueError("资料位置已变化，请刷新页面后重新选择段落。")
    block = blocks[source_index]
    submitted = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", submitted_excerpt)).strip()
    current = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", block["excerpt"])).strip()
    if not submitted or submitted != current:
        raise ValueError("所选摘录与当前 Wiki 原文不一致，请刷新页面后重新选择。")
    return {"page_title": meta.get("title") or Path(page_path).stem, **block}


def source_status(
    page_path: str, source_index: int | None, excerpt: str, fingerprint: str,
    context_before: str = "", context_after: str = "",
) -> dict:
    """Resolve a saved source snapshot without guessing when its location has changed."""
    if source_index is None or not fingerprint or not excerpt:
        return {"status": "needs_review", "label": "待核对", "message": "这条记录没有可验证的原文定位。"}
    try:
        _, body = read_page_markdown(page_path)
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return {"status": "missing", "label": "原文缺失", "message": "保存摘录的 Wiki 页面已不可用。"}
    blocks = source_blocks(body)
    saved = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", excerpt)).strip()
    matches = [block for block in blocks if block["excerpt"] == saved]
    target = blocks[source_index] if 0 <= source_index < len(blocks) else None
    if len(matches) > 1:
        return {"status": "ambiguous", "label": "多处匹配", "message": "相同摘录出现在多处，无法确认原位置。"}
    if target and target["excerpt"] == saved and target["fingerprint"] == fingerprint:
        return {"status": "locatable", "label": "可定位", "message": "摘录与当前原文及上下文一致。", "source_index": source_index}
    if matches:
        return {"status": "changed", "label": "内容已变化", "message": "原文位置或前后内容已变化；本次不能据此核对。"}
    if context_before and context_after:
        removed = any(
            blocks[index]["excerpt"] == context_before and blocks[index + 1]["excerpt"] == context_after
            for index in range(len(blocks) - 1)
        )
    elif context_before:
        removed = bool(blocks and blocks[-1]["excerpt"] == context_before)
    elif context_after:
        removed = bool(blocks and blocks[0]["excerpt"] == context_after)
    else:
        removed = False
    if removed:
        return {"status": "missing", "label": "原文缺失", "message": "保存的摘录已从原位置删除。"}
    if target:
        return {"status": "changed", "label": "内容已变化", "message": "原文位置或前后内容已变化；本次不能据此核对。"}
    return {"status": "missing", "label": "原文缺失", "message": "保存的摘录已不在当前页面中。"}


class _SourceAnchorParser(HTMLParser):
    def __init__(self, blocks: list[dict]):
        super().__init__(convert_charrefs=False)
        self.by_tag: dict[str, list[int]] = {}
        for block in blocks:
            self.by_tag.setdefault(block["tag"], []).append(block["index"])
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self.get_starttag_text() or f"<{tag}>"
        indexes = self.by_tag.get(tag)
        if indexes:
            index = indexes.pop(0)
            start = re.sub(r"\s+(?:id|data-source-block)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", start, flags=re.I)
            start = start[:-1] + f' id="wiki-source-{index}" data-source-block="{index}">'
        self.parts.append(start)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


def _anchor_source_html(rendered: str, blocks: list[dict]) -> str:
    parser = _SourceAnchorParser(blocks)
    parser.feed(rendered)
    return "".join(parser.parts)


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
             "status": c["status"], "importance": c["importance"], "mastery": c["mastery"]}
            for c in concepts
        ],
        "links": [{"source": a, "target": b, "relation": "wikilink"} for a, b in sorted(links)],
    }


def render_page_html(path: str) -> tuple[dict, str]:
    """读取页面 → (meta, 正文 HTML)。wikilink 转成可点击样式（内部概念跳转）或不存在的目标转灰显。"""
    meta, body = read_page_markdown(path)

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
    rendered = _anchor_source_html(rendered, source_blocks(body))
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
