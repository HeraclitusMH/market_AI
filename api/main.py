"""FastAPI application."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.config import load_config
from common.db import create_tables, get_db
from common.models import BotState, EquitySnapshot, Position, SignalSnapshot, SentimentSnapshot, Order, Fill, EventLog

from api.routes import health, state, controls, signals, sentiment, trades

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

app = FastAPI(title="Market AI", version="0.1.0")

# --- API routes ---
app.include_router(health.router)
app.include_router(state.router)
app.include_router(controls.router)
app.include_router(signals.router)
app.include_router(sentiment.router)
app.include_router(trades.router)

# --- Static files & templates ---
app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(UI_DIR / "templates"))


@app.on_event("startup")
def _startup():
    load_config()
    create_tables()
    # ensure bot_state row exists
    with get_db() as db:
        if db.query(BotState).first() is None:
            cfg = load_config()
            db.add(BotState(
                id=1,
                options_enabled=cfg.options.enabled,
                approve_mode=cfg.features.approve_mode_default,
            ))


# ── UI pages ────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def page_overview(request: Request):
    with get_db() as db:
        bot = db.query(BotState).first()
        equity = db.query(EquitySnapshot).order_by(EquitySnapshot.id.desc()).first()
        equities = db.query(EquitySnapshot).order_by(EquitySnapshot.id.asc()).all()
        positions = db.query(Position).all()
        recent_events = db.query(EventLog).order_by(EventLog.id.desc()).limit(20).all()
    return templates.TemplateResponse(request, "overview.html", {
        "bot": bot, "equity": equity,
        "equities": equities, "positions": positions,
        "events": recent_events, "page": "overview",
    })


@app.get("/positions", include_in_schema=False)
def page_positions(request: Request):
    with get_db() as db:
        positions = db.query(Position).all()
    return templates.TemplateResponse(request, "positions.html", {
        "positions": positions, "page": "positions",
    })


@app.get("/orders", include_in_schema=False)
def page_orders(request: Request):
    with get_db() as db:
        orders = db.query(Order).order_by(Order.id.desc()).limit(100).all()
        fills = db.query(Fill).order_by(Fill.id.desc()).limit(100).all()
    return templates.TemplateResponse(request, "orders.html", {
        "orders": orders, "fills": fills, "page": "orders",
    })


@app.get("/signals", include_in_schema=False)
def page_signals(request: Request):
    with get_db() as db:
        sigs = db.query(SignalSnapshot).order_by(SignalSnapshot.id.desc()).limit(50).all()
    return templates.TemplateResponse(request, "signals.html", {
        "signals": sigs, "page": "signals",
    })


@app.get("/sentiment", include_in_schema=False)
def page_sentiment(request: Request):
    import json as _json
    from trader.sentiment import budget as _budget_mod
    cfg = load_config()
    with get_db() as db:
        rows = db.query(SentimentSnapshot).order_by(SentimentSnapshot.id.desc()).limit(200).all()
        status = _budget_mod.get_status(
            db,
            monthly_budget_eur=cfg.sentiment.claude.monthly_budget_eur,
            daily_budget_fraction=cfg.sentiment.claude.daily_budget_fraction,
            eur_usd_rate=cfg.sentiment.claude.eur_usd_rate,
            hard_stop_on_budget=cfg.sentiment.claude.hard_stop_on_budget,
        )

    # Latest row per (scope, key) — DB-order is id DESC so first-seen wins.
    latest: dict = {}
    for r in rows:
        latest.setdefault((r.scope, r.key), r)
    market_rows = [r for (s, _), r in latest.items() if s == "market"]
    sector_rows = sorted(
        [r for (s, _), r in latest.items() if s == "sector"],
        key=lambda r: abs(r.score), reverse=True,
    )
    ticker_rows = sorted(
        [r for (s, _), r in latest.items() if s == "ticker"],
        key=lambda r: abs(r.score), reverse=True,
    )[:20]

    # Parse breakdown JSON for display (defensive — legacy rows store plain strings).
    def _parse(r):
        try:
            return _json.loads(r.sources_json)[0] if r.sources_json else None
        except Exception:
            return None
    breakdowns = {r.id: _parse(r) for r in list(market_rows) + list(sector_rows) + list(ticker_rows)}

    return templates.TemplateResponse(request, "sentiment.html", {
        "sentiments": rows,
        "market_rows": market_rows,
        "sector_rows": sector_rows,
        "ticker_rows": ticker_rows,
        "breakdowns": breakdowns,
        "provider": cfg.sentiment.provider,
        "budget": status.as_dict(),
        "page": "sentiment",
    })


@app.get("/risk", include_in_schema=False)
def page_risk(request: Request):
    with get_db() as db:
        equities = db.query(EquitySnapshot).order_by(EquitySnapshot.id.asc()).all()
        bot = db.query(BotState).first()
    cfg = load_config()
    return templates.TemplateResponse(request, "risk.html", {
        "equities": equities, "bot": bot,
        "risk_cfg": cfg.risk, "page": "risk",
    })


@app.get("/controls", include_in_schema=False)
def page_controls(request: Request):
    with get_db() as db:
        bot = db.query(BotState).first()
    return templates.TemplateResponse(request, "controls.html", {
        "bot": bot, "page": "controls",
    })


@app.get("/config", include_in_schema=False)
def page_config(request: Request):
    cfg = load_config()
    return templates.TemplateResponse(request, "config.html", {
        "cfg": cfg, "page": "config",
    })
