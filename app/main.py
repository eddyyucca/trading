from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.api.routes import router
from app.db.init_db import init_db, seed_defaults
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.services.jobs import ensure_history, start_jobs

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)
app.include_router(router)
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')


@app.on_event('startup')
def on_startup() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()
    if settings.auto_backfill_on_start:
        ensure_history()
    if settings.auto_start_jobs:
        start_jobs()


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse('dashboard.html', {'request': request, 'app_name': settings.app_name})
