"""测试 fixture：模拟 wiki pages/ 目录结构（通过 FEYNMAN_WIKI_PATH 注入）"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PAGE_QR = """---
title: Query Rewriting 查询改写
created: 2026-08-01
updated: 2026-08-05
type: concept
status: unread
tags: [rag, retrieval]
sources: [raw/rag/xxx.md]
---

# Query Rewriting

查询改写是 **RAG** 的关键环节。

- 改写规则
- 映射表

相关：[[rag-from-scratch]]、[[不存在的页面]]
"""

PAGE_RFS = """# Rag From Scratch

无 frontmatter 的页面，标题应从文件名推导。
"""

PAGE_MEM = """---
title: Agent Memory System
created: 2026-08-02
updated: 2026-08-03
type: concept
status: read
tags: [agent]
---

# Agent Memory System

记忆系统与 [[query-rewriting]] 有关。
"""

PAGE_SAME_NAME_AI = """---
title: Shared Note AI
status: unread
---

# Shared Note AI

AI version.
"""

PAGE_SAME_NAME_FINANCE = """---
title: Shared Note Finance
status: unread
---

# Shared Note Finance

Finance version.
"""

DASHBOARD = """---
title: Wiki Dashboard
created: 2026-08-03
updated: 2026-08-03
type: query
status: read
tags: []
---

# Dashboard

管理页，不应进入概念库。
"""


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """构造临时 wiki 并注入 FEYNMAN_WIKI_PATH。"""
    pages = tmp_path / "pages"
    (pages / "AI" / "rag").mkdir(parents=True)
    (pages / "AI" / "agents").mkdir(parents=True)
    (pages / "Financing" / "cashflow").mkdir(parents=True)
    (pages / "Financing" / "investing").mkdir(parents=True)
    (pages / "AI" / "references").mkdir(parents=True)
    (pages / "Financing" / "references").mkdir(parents=True)
    (pages / "AI" / "rag" / "query-rewriting.md").write_text(PAGE_QR, encoding="utf-8")
    (pages / "AI" / "rag" / "rag-from-scratch.md").write_text(PAGE_RFS, encoding="utf-8")
    (pages / "AI" / "agents" / "agent-memory-system.md").write_text(PAGE_MEM, encoding="utf-8")
    (pages / "AI" / "references" / "shared-note.md").write_text(PAGE_SAME_NAME_AI, encoding="utf-8")
    (pages / "Financing" / "references" / "shared-note.md").write_text(PAGE_SAME_NAME_FINANCE, encoding="utf-8")
    (pages / "Financing" / "cashflow" / "budget-and-savings.md").write_text(
        "---\ntitle: 预算与储蓄\ncreated: 2026-08-01\nupdated: 2026-08-02\ntype: concept\nstatus: reading\ntags: [finance]\n---\n\n# 预算与储蓄\n\n理财基础。\n", encoding="utf-8"
    )
    (pages / "Financing" / "investing" / "investment-basics.md").write_text(
        "---\ntitle: 投资基础\ncreated: 2026-08-01\nupdated: 2026-08-02\ntype: concept\nstatus: unread\ntags: [finance]\n---\n\n# 投资基础\n\n长期投资基础。\n", encoding="utf-8"
    )
    (pages / "dashboard.md").write_text(DASHBOARD, encoding="utf-8")
    monkeypatch.setenv("FEYNMAN_WIKI_PATH", str(tmp_path))
    monkeypatch.setattr("app.config.DB_PATH", tmp_path / "feynman.db")
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "feynman.db")
    from app import db
    db.init_db()
    return tmp_path
