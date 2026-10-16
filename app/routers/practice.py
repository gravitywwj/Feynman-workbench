"""Offline application checks and user-reported external practice API."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import practice_sessions

router = APIRouter(prefix="/api/practice", tags=["practice"])


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class AttemptStart(StrictModel):
    page_path: str = Field(min_length=1, max_length=2000)
    task_key: str = Field(min_length=1, max_length=160)
    mode: str = Field(pattern="^(practice|assessment)$")
    familiarity: str = Field(pattern="^(unseen|seen|unknown)$")
    assistance: str = Field(pattern="^(none|allowed_docs|hint|solution|unknown)$")


class AttemptSubmit(StrictModel):
    answer: dict
    assistance: str | None = Field(default=None, pattern="^(none|allowed_docs|hint|solution|unknown)$")


class AttemptDraft(StrictModel):
    answer: dict
    assistance: str | None = Field(default=None, pattern="^(none|allowed_docs|hint|solution|unknown)$")


class ExternalAttempt(StrictModel):
    page_path: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=160)
    source_url: str = Field(default="", max_length=2000)
    reported_result: str = Field(default="", max_length=4000)
    artifact_text: str = Field(default="", max_length=10000)
    familiarity: str = Field(default="unknown", pattern="^(unseen|seen|unknown)$")
    assistance: str = Field(default="unknown", pattern="^(none|allowed_docs|hint|solution|unknown)$")


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except practice_sessions.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LookupError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks")
def list_tasks(page_path: str = Query(..., min_length=1, max_length=2000)) -> dict:
    return _call(practice_sessions.list_tasks, page_path)


@router.post("/attempts")
def start_attempt(payload: AttemptStart) -> dict:
    return _call(
        practice_sessions.start_attempt, payload.page_path, payload.task_key,
        payload.mode, payload.familiarity, payload.assistance,
    )


@router.get("/attempts/{uid}")
def get_attempt(uid: str) -> dict:
    return _call(practice_sessions.get_attempt, uid)


@router.put("/attempts/{uid}/draft")
def save_attempt_draft(uid: str, payload: AttemptDraft) -> dict:
    return _call(practice_sessions.save_attempt_draft, uid, payload.answer, payload.assistance)


@router.post("/attempts/{uid}/solution")
def view_solution(uid: str) -> dict:
    return _call(practice_sessions.view_solution, uid)


@router.post("/attempts/{uid}/submit")
def submit_attempt(uid: str, payload: AttemptSubmit) -> dict:
    return _call(practice_sessions.submit_attempt, uid, payload.answer, payload.assistance)


@router.post("/external-attempts")
def create_external_attempt(payload: ExternalAttempt) -> dict:
    return _call(
        practice_sessions.create_external_attempt, payload.page_path, payload.title,
        payload.source_url, payload.reported_result, payload.artifact_text,
        payload.familiarity, payload.assistance,
    )


@router.get("/history")
def history(
    page_path: str = Query(..., min_length=1, max_length=2000),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    return _call(practice_sessions.history, page_path, limit)
