"""费曼学习工作台 — Wiki frontmatter 写入层（受控写回）

只允许更新白名单标量字段（status / importance），绝不改动正文与其它字段。
写入保持 UTF-8 + LF，防止破坏 raw sha256 与 git 行尾约定。
"""
import re
from pathlib import Path

from app.config import get_wiki_path

ALLOWED_FIELDS = {"status", "importance"}
STATUS_VALUES = {"unread", "reading", "read"}
IMPORTANCE_VALUES = {"high", "medium", "low"}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)


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
        # 无 frontmatter：在其顶部补一个最小 frontmatter（含更新字段）
        lines = ["---"]
        for field, value in updates.items():
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
                    head_lines[i] = f"{field}: {value}"
                    found = True
                    changed.add(field)
                    break
            if not found:
                # 追加在 title 之后（若有），否则追加在末尾
                insert_at = 1 if head_lines and head_lines[0].startswith("title:") else len(head_lines)
                head_lines.insert(insert_at, f"{field}: {value}")
                changed.add(field)
        new_text = "---\n" + "\n".join(head_lines) + "\n---\n\n" + body
    # 统一 LF 写回
    new_text = new_text.replace("\r\n", "\n").replace("\r", "\n")
    f.write_text(new_text, encoding="utf-8", newline="\n")
    return {"path": path, "updated": dict(updates)}
