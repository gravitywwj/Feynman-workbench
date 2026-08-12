"""学习会话、笔记与间隔复习 API。"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import study_sessions

router = APIRouter(prefix="/api/study", tags=["study"])


class SessionCreate(BaseModel):
    page_path: str
    explanation: str = Field(min_length=1, max_length=10000)


class NoteSave(BaseModel):
    content: str = Field(max_length=10000)


class ReviewCreate(BaseModel):
    rating: str


class GapRevision(BaseModel):
    revision: str = Field(min_length=1, max_length=10000)


class PageRelink(BaseModel):
    old_path: str
    new_path: str


@router.post("/sessions")
def create_session(payload: SessionCreate) -> dict:
    try:
        return study_sessions.create_session(payload.page_path, payload.explanation)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions/{session_id}")
def get_session(session_id: int) -> dict:
    try:
        return study_sessions.session_detail(session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/notes")
def get_note(page_path: str = Query(...)) -> dict:
    try:
        return study_sessions.get_note(page_path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/notes")
def put_note(payload: NoteSave, page_path: str = Query(...)) -> dict:
    try:
        return study_sessions.save_note(page_path, payload.content)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/reviews/due")
def due_cards(limit: int = Query(20, ge=1, le=100)) -> dict:
    cards = study_sessions.list_due_cards(limit)
    return {"total": len(cards), "cards": cards}


@router.post("/reviews/{card_id}")
def submit_review(card_id: int, payload: ReviewCreate) -> dict:
    try:
        return study_sessions.review_card(card_id, payload.rating)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/history")
def history(limit: int = Query(20, ge=1, le=100), page_path: str | None = Query(None)) -> dict:
    return {"sessions": study_sessions.list_history(limit, page_path)}


@router.get("/gaps")
def gaps(limit: int = Query(50, ge=1, le=100), status: str | None = Query(None)) -> dict:
    if status and status not in {"open", "revised", "verified"}:
        raise HTTPException(status_code=400, detail="不支持的盲区状态")
    return {"gaps": study_sessions.list_gaps(limit, status)}


@router.post("/gaps/{gap_id}/revision")
def revise_gap(gap_id: int, payload: GapRevision) -> dict:
    try:
        return study_sessions.revise_gap(gap_id, payload.revision)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/export")
def export_learning_data() -> dict:
    return study_sessions.export_learning_data()


@router.get("/orphans")
def orphaned_records() -> dict:
    return {"orphans": study_sessions.list_orphaned_records()}


@router.post("/relink")
def relink_page(payload: PageRelink) -> dict:
    try:
        return study_sessions.relink_page(payload.old_path, payload.new_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (LookupError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
