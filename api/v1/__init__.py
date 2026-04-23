"""API v1 — JSON endpoints for the React SPA."""
from fastapi import APIRouter

from api.v1 import (
    overview, positions, orders, signals, rankings,
    sentiment, risk, controls, config as config_routes,
)

router = APIRouter(prefix="/api/v1")
router.include_router(overview.router)
router.include_router(positions.router)
router.include_router(orders.router)
router.include_router(signals.router)
router.include_router(rankings.router)
router.include_router(sentiment.router)
router.include_router(risk.router)
router.include_router(controls.router)   # has its own /controls prefix
router.include_router(config_routes.router)
