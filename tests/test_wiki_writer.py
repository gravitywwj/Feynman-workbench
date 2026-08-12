"""wiki_writer（frontmatter 写回）单元测试"""

from app.services import wiki_reader, wiki_writer


class TestUpdateFrontmatter:
    def test_update_existing_status(self, wiki):
        wiki_writer.update_frontmatter("AI/rag/query-rewriting.md", {"status": "read"})
        text = (wiki / "pages" / "AI" / "rag" / "query-rewriting.md").read_text(encoding="utf-8")
        meta, _ = wiki_reader.parse_frontmatter(text)
        assert meta["status"] == "read"
        assert meta["title"] == "Query Rewriting 查询改写"   # 其他字段不动
        assert meta["tags"] == ["rag", "retrieval"]

    def test_add_new_field(self, wiki):
        wiki_writer.update_frontmatter("AI/rag/query-rewriting.md", {"importance": "high"})
        text = (wiki / "pages" / "AI" / "rag" / "query-rewriting.md").read_text(encoding="utf-8")
        meta, body = wiki_reader.parse_frontmatter(text)
        assert meta["importance"] == "high"
        assert "查询改写是" in body           # 正文未动
        assert "\r" not in text             # LF 保持

    def test_page_without_frontmatter_gets_one(self, wiki):
        wiki_writer.update_frontmatter("AI/rag/rag-from-scratch.md", {"status": "reading"})
        text = (wiki / "pages" / "AI" / "rag" / "rag-from-scratch.md").read_text(encoding="utf-8")
        assert text.startswith("---\nstatus: reading\n---")
        meta, body = wiki_reader.parse_frontmatter(text)
        assert meta["status"] == "reading"
        assert "无 frontmatter" in body

    def test_invalid_field_rejected(self, wiki):
        try:
            wiki_writer.update_frontmatter("AI/rag/query-rewriting.md", {"title": "hack"})
            assert False, "应拒绝未白名单字段"
        except ValueError:
            pass

    def test_invalid_value_rejected(self, wiki):
        try:
            wiki_writer.update_frontmatter("AI/rag/query-rewriting.md", {"status": "done"})
            assert False, "应拒绝非法 status"
        except ValueError:
            pass

    def test_path_traversal_rejected(self, wiki):
        for bad in ["../SCHEMA.md", "..\\SCHEMA.md", "C:/Windows/win.ini", "/etc/passwd"]:
            try:
                wiki_writer.update_frontmatter(bad, {"status": "read"})
                assert False, f"应拒绝路径: {bad}"
            except ValueError:
                pass

    def test_missing_page_404(self, wiki):
        try:
            wiki_writer.update_frontmatter("AI/rag/nope.md", {"status": "read"})
            assert False, "应 FileNotFoundError"
        except FileNotFoundError:
            pass

    def test_clear_importance_removes_field(self, wiki):
        wiki_writer.update_frontmatter("AI/rag/query-rewriting.md", {"importance": "high"})
        wiki_writer.update_frontmatter("AI/rag/query-rewriting.md", {"importance": ""})
        text = (wiki / "pages" / "AI" / "rag" / "query-rewriting.md").read_text(encoding="utf-8")
        meta, _ = wiki_reader.parse_frontmatter(text)
        assert "importance" not in meta


class TestExtractWikilinks:
    def test_plain_links(self):
        body = "见 [[page-a]] 与 [[page-b|别名]]。\n"
        assert wiki_reader.extract_wikilinks(body) == ["page-a", "page-b"]

    def test_skips_code_fence(self):
        body = '代码：\n```\n[[fake-link]] 向量示例\n```\n正文 [[real-link]]\n'
        assert wiki_reader.extract_wikilinks(body) == ["real-link"]


class TestBuildGraph:
    def test_nodes_and_links(self, wiki):
        g = wiki_reader.build_graph()
        ids = {n["id"] for n in g["nodes"]}
        assert "AI/rag/query-rewriting.md" in ids
        assert len(g["nodes"]) == 7
        # query-rewriting → rag-from-scratch（存在）；→ 不存在的页面被丢弃
        link_set = {(link["source"], link["target"]) for link in g["links"]}
        assert ("AI/rag/query-rewriting.md", "AI/rag/rag-from-scratch.md") in link_set
        assert not any("不存在的页面" in a or "不存在的页面" in b for a, b in link_set)
        # agent-memory-system → query-rewriting
        assert ("AI/agents/agent-memory-system.md", "AI/rag/query-rewriting.md") in link_set
