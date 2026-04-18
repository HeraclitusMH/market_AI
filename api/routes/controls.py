"""Control endpoints for pause/resume/kill switch etc."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from common.models import BotState

router = APIRouter(prefix="/controls", tags=["controls"])


def _get_state(db: Session) -> BotState:
    state = db.query(BotState).first()
    if state is None:
        state = BotState(id=1)
        db.add(state)
        db.flush()
    return state


@router.post("/pause")
def pause(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.paused = True
    return {"paused": True}


@router.post("/resume")
def resume(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.paused = False
    return {"paused": False}


@router.post("/kill/on")
def kill_on(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.kill_switch = True
    return {"kill_switch": True}


@router.post("/kill/off")
def kill_off(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.kill_switch = False
    return {"kill_switch": False}


@router.post("/close_all")
def close_all(db: Session = Depends(get_db)):
    # Actual close-all logic delegates to trader; here we just set a flag.
    # The trader reads this and issues close orders.
    s = _get_state(db)
    s.kill_switch = True
    return {"message": "kill_switch activated — trader will close all positions"}


@router.post("/options/enable")
def options_enable(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.options_enabled = True
    return {"options_enabled": True}


@router.post("/options/disable")
def options_disable(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.options_enabled = False
    return {"options_enabled": False}


@router.post("/approve_mode/on")
def approve_on(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.approve_mode = True
    return {"approve_mode": True}


@router.post("/approve_mode/off")
def approve_off(db: Session = Depends(get_db)):
    s = _get_state(db)
    s.approve_mode = False
    return {"approve_mode": False}
