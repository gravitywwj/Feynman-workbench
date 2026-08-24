"""独立想法讨论 API。"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import idea_sessions

router = APIRouter(prefix="/api/ideas", tags=["ideas"])


class IdeaCreate(BaseModel):
    content: str = Field(min_length=4, max_length=10000)
    title: str = Field(default="", max_length=120)
    persona: str = Field(default="feynman", pattern="^(feynman|direct|exam|reflective)$")
    related_page_path: str | None = Field(default=None, max_length=2000)


class IdeaMessage(BaseModel):
    content: str = Field(min_length=2, max_length=10000)


class IdeaApply(BaseModel):
    mode: str = Field(pattern="^(create_idea|keep_local)$")
    title: str = Field(default="", max_length=120)
    content: str = Field(min_length=1, max_length=5000)


@router.get("")
def list_ideas(limit: int = Query(50, ge=1, le=100)) -> dict:
    return {"ideas": idea_sessions.list_ideas(limit)}


@router.post("")
def create_idea(payload: IdeaCreate) -> dict:
    try:
        return idea_sessions.create_idea(
            payload.content, title=payload.title, persona=payload.persona,
            related_page_path=payload.related_page_path,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{idea_id}")
def get_idea(idea_id: int) -> dict:
    try:
        return idea_sessions.get_idea(idea_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{idea_id}/messages")
def send_message(idea_id: int, payload: IdeaMessage) -> dict:
    try:
        return idea_sessions.send_message(idea_id, payload.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{idea_id}/draft")
def generate_draft(idea_id: int) -> dict:
    try:
        return idea_sessions.generate_draft(idea_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{idea_id}/apply")
def apply_idea(idea_id: int, payload: IdeaApply) -> dict:
    try:
        return idea_sessions.apply_idea(
            idea_id, mode=payload.mode, title=payload.title, content=payload.content,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{idea_id}/undo")
def undo_idea(idea_id: int) -> dict:
    try:
        return idea_sessions.undo_idea(idea_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
