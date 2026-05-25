from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from content_collector.api import router as api_router
from content_collector.database import init_db
from content_collector.web import router as web_router

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Content Collector")
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(web_router)
    return app


app = create_app()