"""Multi-factor composite scoring for universe symbols.

Each factor function returns a dict with at minimum:
  value_0_1: float | None   (None = missing / unavailable)
  status: str               ("ok" | "missing" | "error" | "unknown")
  metrics: dict             raw numbers used to compute the score

Gate factors add:
  eligible: bool
  reasons: list[str]
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from ib_insync import Stock

from common.db import get_db
from common.time import utcnow
from trader.indicators import ema as _ema, sma as _sma, rsi as _rsi


# ─────────────────────── Sentiment internals ────────────────────────────────
# Moved here from trader/ranking.py so ranking can import composite scoring
# without circular deps. Re-exported from trader/ranking for backward compat.

def _age_hours(snap) -> Optional[float]:
    if snap is None:
        return None
    ts = snap.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def _apply_recency(score: float, age_h: Optional[float]) -> Tuple[float, str]:
    if age_h is None:
        return 0.0, "missing"
    if age_h > 72:
        return 0.0, "stale"
    if age_h > 24:
        return score * 0.5, "penalized"
    return score, "ok"


def _compute_score(
    market_snap, sector_snap, ticker_snap,
    w_market: float, w_sector: float, w_ticker: float,
) -> Tuple[float, Dict]:
    """Weighted sentiment score in [-1, 1] with recency penalty and weight redistribution."""
    mkt_age = _age_hours(market_snap)
    sec_age = _age_hours(sector_snap)
    tkr_age = _age_hours(ticker_snap)

    mkt_raw = market_snap.score if market_snap else 0.0
    sec_raw = sector_snap.score if sector_snap else 0.0
    tkr_raw = ticker_snap.score if ticker_snap else 0.0

    mkt_val, mkt_status = _apply_recency(mkt_raw, mkt_age)
    sec_val, sec_status = _apply_recency(sec_raw, sec_age)
    tkr_val, tkr_status = _apply_recency(tkr_raw, tkr_age)

    have_market = mkt_status in ("ok", "penalized")
    have_sector = sec_status in ("ok", "penalized")
    have_ticker = tkr_status in ("ok", "penalized")

    if not have_ticker and not have_sector:
        w_m, w_s, w_t = 1.0, 0.0, 0.0
    elif not have_ticker and have_sector:
        w_m, w_s, w_t = 0.35, 0.65, 0.0
    elif have_ticker and not have_sector:
        w_m, w_s, w_t = 0.30, 0.0, 0.70
    else:
        w_m, w_s, w_t = w_market, w_sector, w_ticker

    total = (
        w_m * (mkt_val if have_market else 0.0)
        + w_s * (sec_val if have_sector else 0.0)
        + w_t * (tkr_val if have_ticker else 0.0)
    )
    total = max(-1.0, min(1.0, round(total, 4)))

    components = {
        "market": {
            "raw": round(mkt_raw, 4) if market_snap else None,
            "age_hours": round(mkt_age, 1) if mkt_age is not None else None,
            "weight": round(w_m, 3),
            "contribution": round(w_m * (mkt_val if have_market else 0.0), 4),
            "status": mkt_status,
        },
        "sector": {
            "raw": round(sec_raw, 4) if sector_snap else None,
            "age_hours": round(sec_age, 1) if sec_age is not None else None,
            "weight": round(w_s, 3),
            "contribution": round(w_s * (sec_val if have_sector else 0.0), 4),
            "status": sec_status,
        },
        "ticker": {
            "raw": round(tkr_raw, 4) if ticker_snap else None,
            "age_hours": round(tkr_age, 1) if tkr_age is not None else None,
            "weight": round(w_t, 3),
            "contribution": round(w_t * (tkr_val if have_ticker else 0.0), 4),
            "status": tkr_status,
        },
    }
    return total, components


def compute_sentiment_factor(
    market_snap,
    sector_snap,
    ticker_snap,
    w_market: float = 0.20,
    w_sector: float = 0.30,
    w_ticker: float = 0.50,
) -> dict:
    """Sentiment factor: raw [-1,1] score normalized to [0,1]."""
    raw_score, components = _compute_score(
        market_snap, sector_snap, ticker_snap, w_market, w_sector, w_ticker
    )
    statuses = [components[k]["status"] for k in components]
    all_missing = all(s in ("stale", "missing") for s in statuses)
    status = "missing" if all_missing else "ok"
    value_0_1: Optional[float] = None if all_missing else round((raw_score + 1.0) / 2.0, 4)
    return {
        "value_0_1": value_0_1,
        "raw_score": round(raw_score, 4),
        "components": components,
        "status": status,
    }


# ─────────────────────── Liquidity ──────────────────────────────────────────

def compute_liquidity_factor(df: pd.DataFrame, cfg) -> dict:
    """Liquidity eligibility gate and diagnostics from daily bars.

    Gate passes when last_close >= min_price AND adv_20d >= min_dollar_volume.
    If bars are missing, marks eligible=True (cannot verify → don't block).
    """
    if df.empty or len(df) < 5:
        return {
            "eligible": True,
            "value_0_1": None,
            "metrics": {},
            "status": "missing",
            "reasons": ["insufficient_bars"],
        }

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    last_close = float(close.iloc[-1])
    n = min(20, len(df))
    adv_20d = float((close.tail(n) * volume.tail(n)).mean())

    min_price = cfg.universe.min_price
    min_adv = cfg.ranking.min_dollar_volume

    reasons: List[str] = []
    if last_close < min_price:
        reasons.append(f"price_too_low_{last_close:.2f}")
    if adv_20d < min_adv:
        reasons.append(f"low_adv_dollar_{adv_20d:.0f}")

    eligible = len(reasons) == 0

    # Log-scale ADV score: 10M→0 .. 10B→1
    _LOG_10M = math.log10(10_000_000)
    _LOG_10B = math.log10(10_000_000_000)
    log_adv = math.log10(max(adv_20d, 1.0))
    adv_score = max(0.0, min(1.0, (log_adv - _LOG_10M) / (_LOG_10B - _LOG_10M)))

    # Price score: min_price→0 .. 500→1
    price_score = max(0.0, min(1.0, (last_close - min_price) / (500.0 - min_price)))

    value = round(0.7 * adv_score + 0.3 * price_score, 4)
    return {
        "eligible": eligible,
        "value_0_1": value,
        "metrics": {
            "last_price": round(last_close, 4),
            "adv_dollar_20d": round(adv_20d, 0),
        },
        "status": "ok",
        "reasons": reasons,
    }


# ─────────────────────── Optionability ──────────────────────────────────────

def compute_optionability_factor(symbol: str, client=None) -> dict:
    """Options eligibility from SecurityMaster DB cache.

    Safe-by-default: returns eligible=False when record absent or client missing.
    This ensures the options bot never trades unknown symbols.
    """
    from common.models import SecurityMaster

    try:
        with get_db() as db:
            sm = db.query(SecurityMaster).filter(SecurityMaster.symbol == symbol).first()
    except Exception:
        sm = None

    if sm is not None:
        eligible = bool(sm.options_eligible)
        return {
            "eligible": eligible,
            "value_0_1": 1.0 if eligible else 0.0,
            "metrics": {"source": "security_master"},
            "status": "ok",
            "reasons": [] if eligible else ["not_options_eligible_in_master"],
        }

    return {
        "eligible": False,
        "value_0_1": 0.0,
        "metrics": {},
        "status": "unknown",
        "reasons": ["no_security_master_record"],
    }


# ─────────────────────── Momentum + Trend ───────────────────────────────────

def compute_momentum_trend_factor(df: pd.DataFrame) -> dict:
    """Momentum + trend score from daily bars.

    trend_subscore  = 0.5*(close>SMA200) + 0.5*(EMA20>EMA50)
    mom_subscore    = 0.5*scale(ret_63d) + 0.5*scale(ret_126d)
    total           = 0.6*trend + 0.4*momentum
    scale(r)        clamps r to [-0.30,+0.30] then maps to [0,1].
    """
    if df.empty or len(df) < 63:
        return {"value_0_1": None, "metrics": {}, "status": "missing"}

    close = df["close"].astype(float)
    n = len(close)

    ema20 = float(_ema(close, 20).iloc[-1])
    ema50 = float(_ema(close, 50).iloc[-1])
    last_close = float(close.iloc[-1])

    sma200_val: Optional[float] = None
    if n >= 200:
        v = float(_sma(close, 200).iloc[-1])
        if not math.isnan(v):
            sma200_val = v

    above_sma200 = sma200_val is not None and last_close > sma200_val
    ema_trend_up = ema20 > ema50
    trend_subscore = 0.5 * float(above_sma200) + 0.5 * float(ema_trend_up)

    def _scale(r: float) -> float:
        return (max(-0.30, min(0.30, r)) + 0.30) / 0.60

    ret_63d: Optional[float] = None
    ret_126d: Optional[float] = None
    s63: Optional[float] = None
    s126: Optional[float] = None

    if n >= 64:
        ret_63d = round(last_close / float(close.iloc[-64]) - 1.0, 4)
        s63 = _scale(ret_63d)
    if n >= 127:
        ret_126d = round(last_close / float(close.iloc[-127]) - 1.0, 4)
        s126 = _scale(ret_126d)

    if s63 is not None and s126 is not None:
        mom_subscore: Optional[float] = 0.5 * s63 + 0.5 * s126
    elif s63 is not None:
        mom_subscore = s63
    elif s126 is not None:
        mom_subscore = s126
    else:
        mom_subscore = None

    rsi14: Optional[float] = None
    if n >= 20:
        rsi14 = round(float(_rsi(close, 14).iloc[-1]), 2)

    if mom_subscore is not None:
        mt_score = round(0.6 * trend_subscore + 0.4 * mom_subscore, 4)
    else:
        mt_score = round(trend_subscore, 4)

    return {
        "value_0_1": mt_score,
        "metrics": {
            "sma200": round(sma200_val, 4) if sma200_val is not None else None,
            "ema20": round(ema20, 4),
            "ema50": round(ema50, 4),
            "above_sma200": above_sma200,
            "ema_trend_up": ema_trend_up,
            "ret_63d": ret_63d,
            "ret_126d": ret_126d,
            "rsi14": rsi14,
        },
        "status": "ok",
    }


# ─────────────────────── Risk ────────────────────────────────────────────────

def compute_risk_factor(df: pd.DataFrame) -> dict:
    """Risk score from annualised 20d realised vol + 252d max drawdown.

    Vol buckets  (lower is better):  <15%→1.0  <25%→0.75  <40%→0.50  <60%→0.25  else→0.10
    DD buckets   (lower is better):  <5%→1.0   <15%→0.75  <30%→0.50  <50%→0.25  else→0.10
    risk_score = 0.6*vol_score + 0.4*dd_score
    """
    if df.empty or len(df) < 20:
        return {"value_0_1": None, "metrics": {}, "status": "missing"}

    close = df["close"].astype(float)
    returns = close.pct_change().dropna()

    vol_20d_ann = float(returns.tail(20).std() * math.sqrt(252) * 100)

    window = close.tail(252)
    peak = window.cummax()
    max_dd_abs = float(abs((window / peak - 1.0).min()))

    if vol_20d_ann < 15:
        vol_score = 1.0
    elif vol_20d_ann < 25:
        vol_score = 0.75
    elif vol_20d_ann < 40:
        vol_score = 0.50
    elif vol_20d_ann < 60:
        vol_score = 0.25
    else:
        vol_score = 0.10

    if max_dd_abs < 0.05:
        dd_score = 1.0
    elif max_dd_abs < 0.15:
        dd_score = 0.75
    elif max_dd_abs < 0.30:
        dd_score = 0.50
    elif max_dd_abs < 0.50:
        dd_score = 0.25
    else:
        dd_score = 0.10

    return {
        "value_0_1": round(0.6 * vol_score + 0.4 * dd_score, 4),
        "metrics": {
            "vol_20d_ann": round(vol_20d_ann, 2),
            "max_dd_252d": round(max_dd_abs, 4),
        },
        "status": "ok",
    }


# ─────────────────────── Fundamentals (stub) ─────────────────────────────────

def _unused_fundamentals_stub(symbol: str, cfg=None) -> dict:
    """Legacy fundamentals stub. Returns missing."""
    return {"value_0_1": None, "metrics": {}, "status": "missing"}


_METRIC_ALIASES = {
    "pe_ratio": {
        "pe", "p/e", "p/e ratio", "pe ratio", "peexclxor", "apeexclxor",
        "apenorm", "priceearnings", "price to earnings",
    },
    "pb_ratio": {
        "pb", "p/b", "p/b ratio", "pb ratio", "price2bk", "price/book",
        "price to book", "price_to_book",
    },
    "roe": {
        "roe", "qroe", "ttmroe", "returnonequity", "return on equity",
        "return_on_equity",
    },
    "debt_to_equity": {
        "debt/equity", "debt to equity", "debt_to_equity", "qltdebt2eq",
        "qtotd2eq", "total debt/equity", "long term debt/equity",
    },
    "eps_ttm": {"eps", "eps ttm", "ttmepsxclx", "eps_excl_extra_items_ttm"},
    "market_cap": {"mktcap", "market cap", "market capitalization", "market_cap"},
}


def _norm_metric_name(name: str) -> str:
    return re.sub(r"[^a-z0-9/]+", "", name.strip().lower())


_ALIAS_LOOKUP = {
    _norm_metric_name(alias): metric
    for metric, aliases in _METRIC_ALIASES.items()
    for alias in aliases
}


def _parse_number(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--", "N/A", "NA", "nan"}:
        return None
    multiplier = 1.0
    if text.endswith("%"):
        text = text[:-1]
        multiplier = 0.01
    suffix = text[-1:].upper()
    if suffix in {"K", "M", "B", "T"}:
        text = text[:-1]
        multiplier *= {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[suffix]
    text = text.replace(",", "")
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def parse_fundamental_xml(xml_text: str) -> Dict[str, float]:
    """Extract common ratio fields from legacy fundamental XML."""
    if not xml_text or not xml_text.strip():
        return {}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    found: Dict[str, float] = {}
    for elem in root.iter():
        possible_names = [
            elem.attrib.get("FieldName"),
            elem.attrib.get("fieldName"),
            elem.attrib.get("Name"),
            elem.attrib.get("name"),
            elem.attrib.get("RatioID"),
            elem.attrib.get("ratioID"),
            elem.attrib.get("Type"),
            elem.attrib.get("type"),
            elem.tag,
        ]
        raw_value = (
            elem.attrib.get("Value")
            or elem.attrib.get("value")
            or elem.attrib.get("Amount")
            or elem.attrib.get("amount")
            or elem.text
        )
        value = _parse_number(raw_value)
        if value is None:
            continue
        for raw_name in possible_names:
            if not raw_name:
                continue
            metric = _ALIAS_LOOKUP.get(_norm_metric_name(raw_name))
            if metric and metric not in found:
                if metric == "roe" and abs(value) > 1.0:
                    value = value / 100.0
                if metric == "debt_to_equity" and abs(value) > 10.0:
                    value = value / 100.0
                found[metric] = round(value, 6)
                break

    return found


def _score_lower_better(value: Optional[float], good: float, bad: float) -> Optional[float]:
    if value is None or value <= 0:
        return None
    return max(0.0, min(1.0, (bad - value) / (bad - good)))


def _score_higher_better(value: Optional[float], bad: float, good: float) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, min(1.0, (value - bad) / (good - bad)))


def _score_fundamental_metrics(metrics: Dict[str, float]) -> Tuple[Optional[float], Dict[str, float]]:
    subscores = {
        "pe": _score_lower_better(metrics.get("pe_ratio"), good=10.0, bad=40.0),
        "pb": _score_lower_better(metrics.get("pb_ratio"), good=1.0, bad=8.0),
        "roe": _score_higher_better(metrics.get("roe"), bad=0.0, good=0.25),
        "debt": _score_lower_better(metrics.get("debt_to_equity"), good=0.0, bad=2.5),
    }
    present = {k: round(v, 4) for k, v in subscores.items() if v is not None}
    if not present:
        return None, present
    weights = {"pe": 0.30, "pb": 0.20, "roe": 0.30, "debt": 0.20}
    total_weight = sum(weights[k] for k in present)
    score = sum(present[k] * weights[k] / total_weight for k in present)
    return round(score, 4), present


def _fundamentals_enabled(cfg) -> bool:
    enabled = getattr(getattr(cfg, "fundamentals", None), "enabled", False)
    return enabled if isinstance(enabled, bool) else False


def _fundamentals_ttl(cfg) -> timedelta:
    days = int(getattr(getattr(cfg, "fundamentals", None), "ttl_days", 7) or 7)
    return timedelta(days=max(days, 1))


def _load_cached_fundamentals(symbol: str, cfg) -> Optional[dict]:
    from common.models import FundamentalSnapshot

    try:
        with get_db() as db:
            row = db.query(FundamentalSnapshot).filter(FundamentalSnapshot.symbol == symbol).first()
            if row is None:
                return None
            ts = row.ts.replace(tzinfo=timezone.utc) if row.ts.tzinfo is None else row.ts
            if datetime.now(timezone.utc) - ts > _fundamentals_ttl(cfg):
                return None
            metrics = json.loads(row.metrics_json or "{}")
            return {
                "metrics": metrics,
                "status": row.status,
                "reason": row.reason,
                "source": "cache",
                "as_of": row.ts.isoformat(),
            }
    except Exception:
        return None


def _save_cached_fundamentals(
    symbol: str,
    metrics: Dict[str, float],
    raw_xml: str,
    status: str = "ok",
    reason: Optional[str] = None,
) -> None:
    from common.models import FundamentalSnapshot

    try:
        with get_db() as db:
            row = db.query(FundamentalSnapshot).filter(FundamentalSnapshot.symbol == symbol).first()
            if row is None:
                row = FundamentalSnapshot(symbol=symbol)
                db.add(row)
            row.ts = utcnow().replace(tzinfo=None)
            row.report_type = "ReportSnapshot"
            row.metrics_json = json.dumps(metrics)
            row.raw_xml = raw_xml
            row.status = status
            row.reason = reason
    except Exception:
        return


def compute_fundamentals_factor(symbol: str, cfg=None, client=None) -> dict:
    """Fundamentals factor from yfinance.

    The composite scorer consumes the 0..1 ``value_0_1`` field. The full
    0..100 fundamental result and pillar breakdown are included under
    ``metrics`` for logging/debugging.
    """
    if cfg is None:
        from common.config import get_config
        cfg = get_config()

    if not _fundamentals_enabled(cfg):
        return {
            "value_0_1": None,
            "metrics": {},
            "status": "disabled",
            "reason": "fundamentals_disabled",
        }

    try:
        from trader.fundamental_scorer import FundamentalScorer

        result = FundamentalScorer(cfg=cfg, client=client).get_score(symbol)
    except Exception as exc:
        return {
            "value_0_1": None,
            "metrics": {},
            "status": "missing",
            "reason": str(exc),
        }

    raw_neutral = getattr(cfg.fundamentals, "neutral_score", 50)
    neutral = float(raw_neutral) if isinstance(raw_neutral, (int, float)) else 50.0
    no_metrics = not any(
        pillar.get("metrics") for pillar in result["pillars"].values()
    )
    status = "missing" if result["total_score"] == round(neutral, 1) and no_metrics else "ok"
    return {
        "value_0_1": None if status == "missing" else round(result["total_score"] / 100.0, 4),
        "metrics": result,
        "status": status,
        "fundamental_metrics": result.get("value_metrics", {}),
        **({"reason": "no_usable_fundamental_metrics"} if status == "missing" else {}),
    }


def _fundamentals_neutral_0_1(cfg) -> float:
    raw = getattr(getattr(cfg, "fundamentals", None), "neutral_score", 50)
    neutral = float(raw) if isinstance(raw, (int, float)) else 50.0
    return round(max(0.0, min(100.0, neutral)) / 100.0, 4)


# ─────────────────────── Composite ──────────────────────────────────────────

def compute_composite(
    factors: Dict[str, dict],
    nominal_weights: Dict[str, float],
) -> Tuple[float, Dict[str, float]]:
    """Combine factor scores in [0,1] with proportional weight redistribution.

    Missing factors (value_0_1 is None) have their weight redistributed among
    present factors. Returns (total_score_0_1, weights_actually_used).
    If ALL factors missing, returns (0.5, all-zeros) as a neutral placeholder.
    """
    present = {
        name: f["value_0_1"]
        for name, f in factors.items()
        if f.get("value_0_1") is not None
    }

    zero_weights = {name: 0.0 for name in nominal_weights}

    if not present:
        return 0.5, zero_weights

    total_nominal = sum(nominal_weights.get(name, 0.0) for name in present)
    if total_nominal <= 0.0:
        return 0.5, zero_weights

    weights_used: Dict[str, float] = {}
    for name in nominal_weights:
        if name in present:
            weights_used[name] = round(nominal_weights[name] / total_nominal, 4)
        else:
            weights_used[name] = 0.0

    total = sum(weights_used[name] * present[name] for name in present)
    return round(max(0.0, min(1.0, total)), 4), weights_used
