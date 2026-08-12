"""wiki_reader 单元测试"""
from app.services import wiki_reader


class TestParseFrontmatter:
    def test_full_frontmatter(self):
        text = '---\ntitle: Foo\nstatus: unread\ntags: [rag, retrieval]\n---\n\nbody'
        meta, body = wiki_reader.parse_frontmatter(text)
        assert meta["title"] == "Foo"
        assert meta["status"] == "unread"
        assert meta["tags"] == ["rag", "retrieval"]
        assert body == "body"

    def test_no_frontmatter(self):
        meta, body = wiki_reader.parse_frontmatter("# 只有正文")
        assert meta == {}
        assert "正文" in body

    def test_quoted_value(self):
        text = '---\ntitle: "带引号 标题"\n---\n\nx'
        meta, _ = wiki_reader.parse_frontmatter(text)
        assert meta["title"] == "带引号 标题"


class TestScanConcepts:
    def test_scan_excludes_dashboard_and_parses_meta(self, wiki):
        concepts = wiki_reader.scan_concepts()
        paths = [c["path"] for c in concepts]
        assert "dashboard.md" not in paths
        assert "AI/rag/query-rewriting.md" in paths
        assert "AI/rag/rag-from-scratch.md" in paths

        qr = next(c for c in concepts if c["path"] == "AI/rag/query-rewriting.md")
        assert qr["title"] == "Query Rewriting 查询改写"
        assert qr["section"] == "AI"
        assert qr["status"] == "unread"
        assert qr["tags"] == ["rag", "retrieval"]
        assert qr["line_count"] >= 8

        # 无 frontmatter 页面：标题从文件名推导
        rfs = next(c for c in concepts if c["path"] == "AI/rag/rag-from-scratch.md")
        assert rfs["title"] == "rag-from-scratch"
        assert rfs["status"] == "unread"

    def test_sections(self, wiki):
        concepts = wiki_reader.scan_concepts()
        sections = {c["section"] for c in concepts}
        assert sections == {"AI", "Financing"}
        finance_paths = {c["path"] for c in concepts if c["section"] == "Financing"}
        assert finance_paths == {
            "Financing/cashflow/budget-and-savings.md",
            "Financing/investing/investment-basics.md",
            "Financing/references/shared-note.md",
        }


class TestRenderPage:
    def test_render_html_with_wikilinks(self, wiki):
        meta, html = wiki_reader.render_page_html("AI/rag/query-rewriting.md")
        assert meta["title"] == "Query Rewriting 查询改写"
        assert "<h1" in html
        # 存在的目标 → wl-ok
        assert 'wl-ok" data-target="rag-from-scratch"' in html
        # 不存在的目标 → wl-missing
        assert 'wl-missing" data-target="不存在的页面"' in html

    def test_page_not_found(self, wiki):
        try:
            wiki_reader.render_page_html("AI/rag/nope.md")
            assert False, "应抛 FileNotFoundError"
        except FileNotFoundError:
            pass

    def test_sanitizes_unsafe_html(self, wiki):
        page = wiki / "pages" / "AI" / "rag" / "unsafe.md"
        page.write_text("# Safe\n\n<script>alert(1)</script><img src=x onerror=alert(1)>", encoding="utf-8")
        _, html = wiki_reader.render_page_html("AI/rag/unsafe.md")
        assert "<script" not in html
        assert "onerror" not in html

    def test_wikilinks_require_explicit_path_when_names_collide(self, wiki):
        page = wiki / "pages" / "AI" / "rag" / "links.md"
        page.write_text("[[shared-note]] and [[Financing/references/shared-note]]", encoding="utf-8")
        _, html = wiki_reader.render_page_html("AI/rag/links.md")
        assert 'wl-missing" data-target="shared-note"' in html
        assert 'wl-ok" data-target="Financing/references/shared-note" data-path="Financing/references/shared-note.md"' in html

    def test_graph_uses_explicit_path_when_names_collide(self, wiki):
        page = wiki / "pages" / "AI" / "rag" / "links.md"
        page.write_text("[[shared-note]] and [[Financing/references/shared-note]]", encoding="utf-8")
        graph = wiki_reader.build_graph()
        assert ("AI/rag/links.md", "Financing/references/shared-note.md") in {
            (link["source"], link["target"]) for link in graph["links"]
        }


class TestSlugify:
    def test_mixed(self):
        assert wiki_reader.slugify("Query Rewriting 查询改写") == "Query-Rewriting-查询改写"

    def test_empty(self):
        assert wiki_reader.slugify("!!!") == "concept"


class TestDisplaySize:
    def test_small(self):
        assert "1 分钟" in wiki_reader.display_size(10)

    def test_large(self):
        assert "5 分钟" in wiki_reader.display_size(300)
