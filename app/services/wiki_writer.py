"""费曼学习工作台的受控 Wiki 写回层。"""
import difflib
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from threading import Lock

from app.config import get_wiki_path

ALLOWED_FIELDS = {"status", "importance"}
STATUS_VALUES = {"unread", "reading", "read"}
IMPORTANCE_VALUES = {"high", "medium", "low", ""}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
LEARNING_SECTION = "## 学习增量"
PENDING_SECTION = "## 待验证问题"
_WRITE_LOCK = Lock()


def _validate(path: str) -> Path:
    """路径必须位于 pages/ 下的 .md，防穿越。"""
    wiki = get_wiki_path().resolve()
    if wiki.name == "personal-wiki" or ((wiki.parent / "SCHEMA.md").is_file() and wiki.name != "learning-wiki"):
        raise ValueError("工作台只能写入 learning-wiki，不能写入其他知识空间。")
    p = Path(path)
    if p.suffix != ".md" or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"非法页面路径: {path}")
    pages_root = (wiki / "pages").resolve()
    f = (pages_root / p).resolve()
    if not f.is_relative_to(pages_root):
        raise ValueError(f"非法页面路径: {path}")
    if not f.is_file():
        raise FileNotFoundError(path)
    return f


def update_frontmatter(path: str, updates: dict) -> dict:
    """更新页面 frontmatter 白名单字段。返回更新后的 (meta 子集, 修改列表)。"""
    unknown = set(updates) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"不允许修改字段: {sorted(unknown)}")
    for field, value in updates.items():
        if field == "status" and value not in STATUS_VALUES:
            raise ValueError(f"status 非法值: {value}")
        if field == "importance" and value not in IMPORTANCE_VALUES:
            raise ValueError(f"importance 非法值: {value}")

    f = _validate(path)
    text = f.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        # 清除一个原本不存在的标记时，不凭空创建 frontmatter。
        if all(value == "" for value in updates.values()):
            return {"path": path, "updated": dict(updates)}
        # 无 frontmatter：在其顶部补一个最小 frontmatter（含更新字段）
        lines = ["---"]
        for field, value in updates.items():
            if value == "":
                continue
            lines.append(f"{field}: {value}")
        lines.append("---")
        new_text = "\n".join(lines) + "\n\n" + text
    else:
        head = m.group(1)
        body = m.group(2)
        head_lines = head.splitlines()
        changed = set()
        for field, value in updates.items():
            found = False
            for i, line in enumerate(head_lines):
                if re.match(rf"^{re.escape(field)}\s*:", line):
                    if value == "":
                        head_lines.pop(i)
                    else:
                        head_lines[i] = f"{field}: {value}"
                    found = True
                    changed.add(field)
                    break
            if not found and value != "":
                # 追加在 title 之后（若有），否则追加在末尾
                insert_at = 1 if head_lines and head_lines[0].startswith("title:") else len(head_lines)
                head_lines.insert(insert_at, f"{field}: {value}")
                changed.add(field)
        new_text = "---\n" + "\n".join(head_lines) + "\n---\n\n" + body
    # 统一 LF 写回
    new_text = new_text.replace("\r\n", "\n").replace("\r", "\n")
    f.write_text(new_text, encoding="utf-8", newline="\n")
    return {"path": path, "updated": dict(updates)}


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _atomic_write(path: Path, content: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _wiki_files() -> tuple[Path, Path, Path]:
    wiki = get_wiki_path().resolve()
    if wiki.name == "personal-wiki":
        raise ValueError("工作台只能写入 learning-wiki，不能写入其他知识空间。")
    if (wiki.parent / "SCHEMA.md").is_file():
        if wiki.name != "learning-wiki":
            raise ValueError("工作台只能写入 learning-wiki，不能写入其他知识空间。")
        schema_root = wiki.parent
    else:
        schema_root = wiki
    index = wiki / "index.md"
    log = schema_root / "log.md"
    if not (schema_root / "SCHEMA.md").is_file() or not index.is_file() or not log.is_file():
        raise ValueError("Wiki 写回需要 SCHEMA.md、学习索引 index.md 和操作日志 log.md。")
    return wiki, index, log


def _insert_section(text: str, heading: str, block: str) -> str:
    marker = re.search(rf"(?m)^{re.escape(heading)}[ \t]*$", text)
    if not marker:
        return text.rstrip() + f"\n\n{heading}\n\n{block}\n"
    next_heading = re.search(r"(?m)^## ", text[marker.end():])
    end = marker.end() + next_heading.start() if next_heading else len(text)
    return text[:end].rstrip() + f"\n\n{block}\n\n" + text[end:].lstrip("\n")


def preview_reviewed_update(path: str, content: str, update_id: int, *, kind: str, citations: list[str]) -> dict:
    """Produce the exact one-page diff and stale-write fingerprint before approval."""
    if kind not in {"verified", "tentative"} or not content.strip():
        raise ValueError("写回类别或草稿无效")
    wiki, index_file, _ = _wiki_files()
    page = _validate(path)
    before = _normalized(page.read_text(encoding="utf-8"))
    index_before = _normalized(index_file.read_text(encoding="utf-8"))
    if not FRONTMATTER_RE.match(before) or not re.search(r"(?m)^updated:[ \t]*\d{4}-\d{2}-\d{2}[ \t]*$", before):
        raise ValueError("目标页面缺少可维护的 frontmatter 或 updated 日期")
    if f"[[{Path(path).stem}]]" not in index_before:
        raise ValueError("目标页面未登记在学习索引中")
    today = date.today().isoformat()
    after = re.sub(r"(?m)^updated:[ \t]*\d{4}-\d{2}-\d{2}[ \t]*$", f"updated: {today}", before, count=1)
    heading = LEARNING_SECTION if kind == "verified" else PENDING_SECTION
    source_line = "\n\n" + " ".join(f"^[{source}]" for source in citations) if kind == "verified" else "\n\n> 待验证；不能作为已核实事实引用。"
    block = f"{content.strip()}{source_line}\n\n<!-- feynman-workbench:{update_id}:{kind} -->"
    after = _insert_section(after, heading, block)
    match = re.search(r"(?m)^(> Last updated: )\d{4}-\d{2}-\d{2}( \| Total pages: \d+)[ \t]*$", index_before)
    if not match:
        raise ValueError("学习索引缺少规范的更新日期与页面总数")
    index_after = index_before[:match.start()] + match.group(1) + today + match.group(2) + index_before[match.end():]
    base_hash = hashlib.sha256((before + "\0" + index_before).encode("utf-8")).hexdigest()
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=path, tofile=path))
    return {"path": path, "wiki_root": str(wiki), "before_content": before, "after_content": after,
            "index_before": index_before, "index_after": index_after, "base_hash": base_hash, "diff": diff,
            "kind": kind, "citations": citations}


def _run_lint(wiki: Path) -> None:
    script = wiki / "scripts" / "lint_wiki.py"
    if not script.is_file():
        return
    result = subprocess.run([sys.executable, str(script)], cwd=wiki, capture_output=True, text=True, timeout=45)
    if result.returncode:
        errors = [line for line in result.stdout.splitlines() if line.startswith("ERROR")]
        raise ValueError("Wiki 校验失败：" + ("；".join(errors[:3]) or result.stderr.strip()[:400]))


def apply_reviewed_update(path: str, content: str, update_id: int, *, kind: str,
                          citations: list[str], expected_hash: str) -> dict:
    """Write one reviewed page, index date and append-only log as one recoverable operation."""
    with _WRITE_LOCK:
        preview = preview_reviewed_update(path, content, update_id, kind=kind, citations=citations)
        if preview["base_hash"] != expected_hash:
            raise ValueError("Wiki 页面或索引已变化，请重新审核草稿。")
        wiki, index_file, log_file = _wiki_files()
        page = _validate(path)
        if (_normalized(page.read_text(encoding="utf-8")) != preview["before_content"]
                or _normalized(index_file.read_text(encoding="utf-8")) != preview["index_before"]):
            raise ValueError("Wiki 页面或索引已变化，请重新审核草稿。")
        today = date.today().isoformat()
        evidence = ", ".join(citations) if citations else "待验证"
        log_entry = f"\n## [{today}] update | feynman-workbench: {path}\n- Update: {update_id}\n- Kind: {kind}\n- Evidence: {evidence}\n"
        try:
            _atomic_write(page, preview["after_content"])
            _atomic_write(index_file, preview["index_after"])
            _run_lint(wiki)
            with log_file.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(log_entry)
        except Exception:
            if page.read_text(encoding="utf-8") == preview["after_content"]:
                _atomic_write(page, preview["before_content"])
            if index_file.read_text(encoding="utf-8") == preview["index_after"]:
                _atomic_write(index_file, preview["index_before"])
            raise
        return {"path": path, "before_content": preview["before_content"],
                "after_content": preview["after_content"], "created_page": False,
                "manifest": {"wiki_root": str(wiki), "index_before": preview["index_before"],
                             "index_after": preview["index_after"], "kind": kind}}


def restore_revision(path: str, before_content: str, after_content: str, *, created_page: bool,
                     manifest: dict | None = None) -> None:
    """Undo only when the Wiki page still matches the recorded post-write state."""
    with _WRITE_LOCK:
        file_path = _validate(path)
        current = _normalized(file_path.read_text(encoding="utf-8"))
        if current != _normalized(after_content):
            raise ValueError("该 Wiki 页面在写入后又被修改，无法安全自动撤销。请先查看变更后再手动处理。")
        if manifest:
            wiki, index_file, log_file = _wiki_files()
            if str(wiki) != manifest.get("wiki_root") or _normalized(index_file.read_text(encoding="utf-8")) != manifest.get("index_after"):
                raise ValueError("Wiki 路径或索引已变化，不能安全自动撤销。")
            today = date.today().isoformat()
            log_entry = f"\n## [{today}] undo | feynman-workbench: {path}\n"
            try:
                _atomic_write(index_file, manifest["index_before"])
                _atomic_write(file_path, _normalized(before_content))
                _run_lint(wiki)
                with log_file.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(log_entry)
            except Exception:
                if file_path.read_text(encoding="utf-8") == _normalized(before_content):
                    _atomic_write(file_path, _normalized(after_content))
                if index_file.read_text(encoding="utf-8") == manifest["index_before"]:
                    _atomic_write(index_file, manifest["index_after"])
                raise
            return
        if created_page:
            file_path.unlink()
            return
        _atomic_write(file_path, _normalized(before_content))
