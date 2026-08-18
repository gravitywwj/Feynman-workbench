"""费曼学习工作台 — Wiki 受控写回层。

阅读状态只允许更新白名单 frontmatter 字段；学习内容只能在用户确认后追加到
一个应用管理的 ``学习增量`` 区块，或新建关联想法页。写入保持 UTF-8 + LF。
"""
import re
from datetime import datetime
from pathlib import Path

from app.config import get_wiki_path

ALLOWED_FIELDS = {"status", "importance"}
STATUS_VALUES = {"unread", "reading", "read"}
IMPORTANCE_VALUES = {"high", "medium", "low", ""}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
LEARNING_SECTION = "## 学习增量"


def _validate(path: str) -> Path:
    """路径必须位于 pages/ 下的 .md，防穿越。"""
    p = Path(path)
    if p.suffix != ".md" or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"非法页面路径: {path}")
    f = get_wiki_path() / "pages" / p
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


def append_learning_update(path: str, content: str, update_id: int) -> dict:
    """Append an approved draft under one app-owned Wiki section.

    The caller stores the returned before/after snapshot before exposing undo.
    Existing authored content is never rewritten or interpreted by the app.
    """
    entry = content.strip()
    if not entry:
        raise ValueError("知识库草案不能为空")
    file_path = _validate(path)
    before = _normalized(file_path.read_text(encoding="utf-8"))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    marker = f"<!-- feynman-workbench:update:{update_id} -->"
    block = f"\n### 学习记录 · {stamp}\n\n{entry}\n\n{marker}\n"
    if LEARNING_SECTION in before:
        after = before.rstrip() + "\n" + block
    else:
        after = before.rstrip() + f"\n\n{LEARNING_SECTION}\n" + block
    file_path.write_text(after, encoding="utf-8", newline="\n")
    return {"path": path, "before_content": before, "after_content": after, "created_page": False}


def create_linked_idea_page(source_path: str, title: str, content: str, update_id: int) -> dict:
    """Create one new, linked idea page after the learner approves the draft."""
    _validate(source_path)
    clean_title = re.sub(r"[\\/:*?\"<>|]+", "-", title).strip()[:80] or "学习想法"
    clean_title = re.sub(r"[\r\n]+", " ", clean_title).strip() or "学习想法"
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", clean_title).strip("-") or "learning-idea"
    ideas_dir = get_wiki_path() / "pages" / "学习想法"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = ideas_dir / f"{slug}-{suffix}.md"
    counter = 2
    while target.exists():
        target = ideas_dir / f"{slug}-{suffix}-{counter}.md"
        counter += 1
    source_label = Path(source_path).stem
    after = _normalized(
        f"---\n"
        f"title: {clean_title}\n"
        f"type: learning-idea\n"
        f"created: {datetime.now().date().isoformat()}\n"
        f"---\n\n"
        f"# {clean_title}\n\n"
        f"关联学习页：[[{source_label}]]\n\n"
        f"{LEARNING_SECTION}\n\n"
        f"{content.strip()}\n\n"
        f"<!-- feynman-workbench:update:{update_id} -->\n"
    )
    target.write_text(after, encoding="utf-8", newline="\n")
    relative_path = target.relative_to(get_wiki_path() / "pages").as_posix()
    return {"path": relative_path, "before_content": "", "after_content": after, "created_page": True}


def restore_revision(path: str, before_content: str, after_content: str, *, created_page: bool) -> None:
    """Undo only when the Wiki page still matches the recorded post-write state."""
    file_path = _validate(path)
    current = _normalized(file_path.read_text(encoding="utf-8"))
    if current != _normalized(after_content):
        raise ValueError("该 Wiki 页面在写入后又被修改，无法安全自动撤销。请先查看变更后再手动处理。")
    if created_page:
        file_path.unlink()
        return
    file_path.write_text(_normalized(before_content), encoding="utf-8", newline="\n")
