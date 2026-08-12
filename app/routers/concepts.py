"""费曼学习工作台 — 概念库路由（读 wiki pages/ + 受控写回 frontmatter）"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import wiki_reader, wiki_writer

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


@router.get("")
def list_concepts(
    section: str | None = Query(None, description="分区过滤，如 AI / Financing"),
    q: str = Query("", description="标题模糊搜索"),
) -> dict:
    concepts = wiki_reader.scan_concepts()
    if section:
        concepts = [c for c in concepts if c["section"] == section]
    if q:
        concepts = [c for c in concepts if q.lower() in c["title"].lower() or q.lower() in c["path"].lower()]
    sections: dict[str, int] = {}
    for c in concepts:
        sections[c["section"]] = sections.get(c["section"], 0) + 1
    return {"total": len(concepts), "sections": sections, "concepts": concepts}


@router.get("/page")
def get_page(path: str = Query(..., description="pages/ 相对路径")) -> dict:
    try:
        meta, html = wiki_reader.render_page_html(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"页面不存在: {path}")
    meta.setdefault("path", path)
    meta["read_time"] = wiki_reader.display_size(meta.get("line_count", 0) or 0)
    return {"meta": meta, "html": html}


@router.get("/graph")
def get_graph() -> dict:
    """知识图谱：节点=概念页，边=页间 wikilink。"""
    return wiki_reader.build_graph()


class MetaUpdate(BaseModel):
    path: str
    status: str | None = None
    importance: str | None = None


@router.put("/meta")
def update_meta(payload: MetaUpdate) -> dict:
    """更新页面 frontmatter 白名单字段（status / importance），正文不动。"""
    updates = {k: v for k, v in payload.model_dump().items() if k != "path" and v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    try:
        return wiki_writer.update_frontmatter(payload.path, updates)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400 if isinstance(e, ValueError) else 404, detail=str(e))
