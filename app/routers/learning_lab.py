"""Local course reading and draft APIs for AI Engineering Learning Lab."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services import learning_lab

router = APIRouter(prefix="/api/learning-lab", tags=["learning-lab"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MissionDraft(StrictModel):
    prediction: str = Field(default="", max_length=3000)
    observation: str = Field(default="", max_length=5000)
    explanation: str = Field(default="", max_length=5000)
    code_text: str = Field(default="", max_length=learning_lab.MAX_CODE_CHARS)
    help_level: int = Field(default=0, ge=0, le=4)
    expected_updated_at: str | None = Field(default=None, max_length=40)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except learning_lab.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except learning_lab.ExecutionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LookupError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def get_overview() -> dict:
    return learning_lab.overview()


@router.get("/missions/{mission_key}")
def get_mission(mission_key: str) -> dict:
    return _call(learning_lab.mission_detail, mission_key)


@router.put("/missions/{mission_key}/draft")
def save_mission_draft(mission_key: str, payload: MissionDraft) -> dict:
    return _call(
        learning_lab.save_draft,
        mission_key,
        prediction=payload.prediction,
        observation=payload.observation,
        explanation=payload.explanation,
        code_text=payload.code_text,
        help_level=payload.help_level,
        expected_updated_at=payload.expected_updated_at,
    )


@router.post("/missions/{mission_key}/run")
def run_mission(mission_key: str) -> dict:
    _call(learning_lab.run_mission, mission_key)
    return {"enabled": False}
