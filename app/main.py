"""费曼学习工作台 — FastAPI 入口"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import STATIC_DIR
from app.routers import concepts, study

app = FastAPI(title="费曼学习工作台", version="0.1.0")

app.include_router(concepts.router)
app.include_router(study.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
