"""中等规模知识图谱性能回归测试。"""
from time import monotonic

from app.services import wiki_reader


def test_build_graph_handles_hundreds_of_linked_pages(wiki):
    pages = wiki / "pages" / "Performance"
    pages.mkdir()
    total = 260
    for index in range(total):
        link = f"[[Performance/topic-{index + 1}]]" if index + 1 < total else ""
        (pages / f"topic-{index}.md").write_text(f"# Topic {index}\n\n{link}", encoding="utf-8")

    started = monotonic()
    graph = wiki_reader.build_graph()
    elapsed = monotonic() - started

    assert len(graph["nodes"]) == total + 7
    assert len(graph["links"]) >= total - 1
    assert elapsed < 4.0
