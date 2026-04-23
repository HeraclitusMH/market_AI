"""POST /api/v1/controls/* — all return { ok, bot }."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import BotState
from common.schema import BotStateOut

router = APIRouter(prefix="/controls", tags=["v1"])


class ControlResponse(BaseModel):
    ok: bool
    bot: BotStateOut


def _get_or_create(db: Session) -> BotState:
    state = db.query(BotState).first()
    if state is None:
        state = BotState(id=1)
        db.add(state)
        db.flush()
    return state


def _resp(state: BotState) -> ControlResponse:
    return ControlResponse(ok=True, bot=BotStateOut.model_validate(state))


@router.post("/pause", response_model=ControlResponse)
def pause(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.paused = True
    return _resp(s)


@router.post("/resume", response_model=ControlResponse)
def resume(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.paused = False
    return _resp(s)


@router.post("/kill/on", response_model=ControlResponse)
def kill_on(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.kill_switch = True
    return _resp(s)


@router.post("/kill/off", response_model=ControlResponse)
def kill_off(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.kill_switch = False
    return _resp(s)


@router.post("/options/enable", response_model=ControlResponse)
def options_enable(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.options_enabled = True
    return _resp(s)


@router.post("/options/disable", response_model=ControlResponse)
def options_disable(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.options_enabled = False
    return _resp(s)


@router.post("/approve_mode/on", response_model=ControlResponse)
def approve_on(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.approve_mode = True
    return _resp(s)


@router.post("/approve_mode/off", response_model=ControlResponse)
def approve_off(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.approve_mode = False
    return _resp(s)


@router.post("/close_all", response_model=ControlResponse)
def close_all(db: Session = Depends(get_db)):
    s = _get_or_create(db)
    s.kill_switch = True
    return _resp(s)
