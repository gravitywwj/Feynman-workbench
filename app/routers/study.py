"""学习会话、笔记与间隔复习 API。"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import config
from app.services import study_sessions

router = APIRouter(prefix="/api/study", tags=["study"])


class SessionCreate(BaseModel):
    page_path: str
    explanation: str = Field(min_length=1, max_length=10000)
    elapsed_seconds: int = Field(default=0, ge=0, le=86400)


class SessionSimplify(BaseModel):
    explanation: str = Field(min_length=1, max_length=10000)
    elapsed_seconds: int = Field(default=0, ge=0, le=86400)


class NoteSave(BaseModel):
    content: str = Field(max_length=10000)


class ReflectionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    page_path: str | None = Field(default=None, max_length=2000)
    session_id: int | None = Field(default=None, ge=1)


class ReflectionUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class ReflectionSummaryCreate(BaseModel):
    reflection_ids: list[int] = Field(min_length=1, max_length=50)


class ReviewCreate(BaseModel):
    rating: str


class ReviewAttemptCreate(BaseModel):
    answer: str = Field(min_length=1, max_length=5000)
    agent: str = Field(pattern="^(feynman|strict)$")


class GapRevision(BaseModel):
    revision: str = Field(min_length=1, max_length=10000)


class DiagnosisFeedback(BaseModel):
    gap_id: int | None = Field(default=None, ge=1)
    verdict: str = Field(pattern="^(helpful|disputed)$")


class PageRelink(BaseModel):
    old_path: str
    new_path: str


class WorkspaceSettingsSave(BaseModel):
    mode: str = Field(pattern="^(local|demo)$")
    wiki_path: str | None = Field(default=None, max_length=2000)
    diagnostic_mode: str = Field(pattern="^(local|ai)$")
    daily_review_goal: int = Field(default=5, ge=1, le=50)
    learning_goal: str = Field(default="long_term", pattern="^(exam|presentation|long_term)$")


class LearningImport(BaseModel):
    payload: dict


@router.post("/sessions")
def create_session(payload: SessionCreate) -> dict:
    try:
        return study_sessions.create_session(payload.page_path, payload.explanation, payload.elapsed_seconds)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions/{session_id}")
def get_session(session_id: int) -> dict:
    try:
        return study_sessions.session_detail(session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/simplify")
def simplify_session(session_id: int, payload: SessionSimplify) -> dict:
    try:
        return study_sessions.complete_session(session_id, payload.explanation, payload.elapsed_seconds)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/diagnosis-feedback")
def diagnosis_feedback(session_id: int, payload: DiagnosisFeedback) -> dict:
    try:
        return study_sessions.record_diagnosis_feedback(session_id, payload.gap_id, payload.verdict)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/home")
def home_action() -> dict:
    return study_sessions.today_action()


@router.get("/today-summary")
def today_summary() -> dict:
    return study_sessions.today_study_summary()


@router.get("/workspace")
def workspace_settings() -> dict:
    return config.get_workspace_settings()


@router.post("/workspace/pick-folder")
def pick_workspace_folder() -> dict:
    """Open a local system folder picker only after the user requests it."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(parent=root, title="选择包含 pages 的 Wiki 文件夹", mustexist=True)
        root.destroy()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"无法打开系统文件夹选择器：{e}")
    if not selected:
        return {"cancelled": True}
    from pathlib import Path

    path = Path(selected)
    if not (path / "pages").is_dir():
        return {
            "cancelled": False, "valid": False, "path": str(path),
            "message": "已选择该文件夹，但其中没有 pages 子目录。不会保存或改写它。",
        }
    return {
        "cancelled": False, "valid": True, "path": str(path),
        "message": "已选择并验证资料文件夹。点击“预览扫描结果”确认内容后再保存。",
    }


@router.put("/workspace")
def put_workspace_settings(payload: WorkspaceSettingsSave) -> dict:
    try:
        return config.save_workspace_settings(**payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.get("/reflections")
def reflections(limit: int = Query(50, ge=1, le=100), page_path: str | None = Query(None)) -> dict:
    return {"reflections": study_sessions.list_reflections(limit, page_path)}


@router.post("/reflections")
def create_reflection(payload: ReflectionCreate) -> dict:
    try:
        return study_sessions.create_reflection(
            payload.content, page_path=payload.page_path, session_id=payload.session_id,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/reflections/{reflection_id}")
def update_reflection(reflection_id: int, payload: ReflectionUpdate) -> dict:
    try:
        return study_sessions.update_reflection(reflection_id, payload.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/reflections/summary")
def create_reflection_summary(payload: ReflectionSummaryCreate) -> dict:
    try:
        return study_sessions.create_reflection_summary(payload.reflection_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/reviews/due")
def due_cards(limit: int = Query(20, ge=1, le=100)) -> dict:
    cards = study_sessions.list_due_cards(limit)
    return {"total": len(cards), "cards": cards}


@router.get("/reviews/queue")
def review_queue(mode: str = Query("scheduled", pattern="^(scheduled|cram)$"), limit: int = Query(20, ge=1, le=100)) -> dict:
    cards = study_sessions.list_review_queue(mode, limit)
    return {"mode": mode, "total": len(cards), "cards": cards}


@router.get("/reviews/summary")
def review_summary() -> dict:
    return study_sessions.review_summary()


@router.post("/reviews/{card_id}/attempt")
def assess_review_attempt(card_id: int, payload: ReviewAttemptCreate) -> dict:
    try:
        return study_sessions.assess_review_attempt(card_id, payload.answer, payload.agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


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


@router.post("/import/preview")
def preview_learning_import(payload: LearningImport) -> dict:
    try:
        return study_sessions.import_learning_data(payload.payload, dry_run=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
def import_learning_data(payload: LearningImport) -> dict:
    try:
        return study_sessions.import_learning_data(payload.payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/weekly-report")
def weekly_report() -> dict:
    return study_sessions.weekly_report()


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
