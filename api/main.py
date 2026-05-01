"""FastAPI application."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from common.config import load_config
from common.db import create_tables, get_db
from common.models import BotState

from api.routes import health, state, controls, signals, sentiment, trades, rankings as rankings_route
from api.routes.regime import router as regime_router
from api.v1 import router as v1_router

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
SPA_INDEX = UI_DIR / "static" / "dist" / "index.html"

app = FastAPI(title="Market AI", version="0.1.0")

# --- API routes ---
app.include_router(health.router)
app.include_router(state.router)
app.include_router(controls.router)
app.include_router(signals.router)
app.include_router(sentiment.router)
app.include_router(trades.router)
app.include_router(rankings_route.router)
app.include_router(regime_router)
app.include_router(v1_router)

# --- React SPA assets ---
app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


@app.on_event("startup")
def _startup():
    load_config()
    create_tables()
    # Ensure bot_state row exists.
    with get_db() as db:
        if db.query(BotState).first() is None:
            cfg = load_config()
            db.add(BotState(
                id=1,
                options_enabled=cfg.options.enabled,
                approve_mode=cfg.features.approve_mode_default,
            ))


def _spa_index():
    if SPA_INDEX.exists():
        return FileResponse(str(SPA_INDEX))
    return PlainTextResponse(
        "React frontend build is missing. Run the frontend build first.",
        status_code=503,
    )


@app.get("/", include_in_schema=False)
def spa_root():
    return _spa_index()


@app.get("/{path:path}", include_in_schema=False)
def spa_shell(path: str):
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    return _spa_index()
