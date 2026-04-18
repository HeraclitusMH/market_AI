# Market AI

## Project Overview

Automated swing trading bot for Interactive Brokers (IBKR) targeting US stocks/ETFs via **debit spread** options strategies only (defined risk). Paper-trading-first. Tech stack: Python 3.12, ib_insync, FastAPI, SQLAlchemy 2.0, SQLite, Jinja2+HTMX+Chart.js dashboard. User's base currency is EUR; IBKR account is a cash account (no margin, no debt).

## Architecture & Key Decisions

### Structure

Four top-level packages (`common/`, `trader/`, `api/`, `ui/`) — flat layout, no `src/` directory. Installed as editable package via `pyproject.toml`.

- **`common/`** — shared config, DB engine, ORM models, Pydantic schemas, logging, time utils
- **`trader/`** — trading engine: IBKR client, market data, indicators, sentiment, strategy, risk, execution, scheduler, Greeks (greeks/strike_selector/greeks_gate/greeks_logger)
- **`api/`** — FastAPI app with API routes + server-rendered UI pages
- **`ui/`** — Jinja2 templates + static CSS/JS
- **`scripts/`** — `init_db.py` (DB setup), `run_all.py` (starts API + trader as subprocesses)

### Key Design Decisions

- **Debit spreads only** — bull call spreads (long) and bear put spreads (bearish). No naked shorts, credit spreads, or undefined risk. Max loss = net debit paid.
- **Cash reservation** — before placing any order, cash equal to max loss is reserved; trade blocked if insufficient.
- **Approve mode defaults ON** — signals are generated and saved to DB but orders are not submitted until user disables approve mode from the dashboard.
- **Starlette 1.0 TemplateResponse API** — uses `TemplateResponse(request, name, context)` signature (not the old dict-with-request style).
- **Sentiment** — pluggable provider interface (`SentimentProvider` ABC). RSS provider does lexicon scoring with recency weighting. Mock provider for testing.
- **No paid APIs** — universe is seeded from an embedded list of 40 liquid US tickers/ETFs, filtered by IBKR historical bar data.

### Patterns & Conventions

- Config loaded from `config.yaml` (falls back to `config.example.yaml`), validated by Pydantic models in `common/config.py`. Cached after first load.
- DB access via `common/db.get_db()` context manager (yields SQLAlchemy session, auto-commits on success, rollbacks on exception).
- `intent_id` on orders prevents duplicate submissions on restart — format: `{symbol}_{direction}_{date}_{uuid8}`.
- Event logging to `events_log` table for audit trail of all trade decisions and order submissions.
- Risk checks in `trader/risk.py` return `(bool, str)` tuples — `(allowed, reason)`.

## Where to Find Things

- **Config schema & loader** → `common/config.py` (Pydantic models for all YAML sections)
- **DB models (10 tables)** → `common/models.py` (BotState, EquitySnapshot, Universe, SentimentSnapshot, SignalSnapshot, Order, Fill, Position, Trade, EventLog)
- **API response schemas** → `common/schema.py`
- **IBKR connection** → `trader/ibkr_client.py` (singleton via `get_ibkr_client()`)
- **Technical indicators** → `trader/indicators.py` (EMA, SMA, RSI, MACD, ATR + `compute_indicators()`)
- **Strategy & scoring** → `trader/strategy.py` (`check_regime()` for SPY-based regime, `score_symbol()` per ticker, `generate_signals()` main entry)
- **Risk engine** → `trader/risk.py` (`check_can_trade()`, `compute_max_risk_for_trade()`, `record_equity_snapshot()`)
- **Order construction** → `trader/execution.py` (debit spread spec building, IBKR BAG combo orders, `execute_signal()` main entry — runs GreeksService → StrikeSelector → GreeksGate)
- **Greeks fetching & IV Rank** → `trader/greeks.py` (`GreeksSnapshot`, `OptionChainGreeks`, `GreeksService`)
- **Delta-based strike selection** → `trader/strike_selector.py` (`StrikeSelector`, `StrikeSelectionCriteria`, `SpreadSelection`, IV-adjusted criteria, `calculate_limit_price()`)
- **Greeks trade gate** → `trader/greeks_gate.py` (`GreeksGate`, `GateResult` — 10 checks incl. IV rank, delta range, theta/delta ratio, vega, gamma-near-expiry, liquidity, pricing, buffer)
- **Greeks logging** → `trader/greeks_logger.py` (structured JSON for chain fetches, strike selections, gate results, entry snapshots)
- **Scheduler loop** → `trader/scheduler.py` (heartbeat 10s, sentiment refresh, signal eval, daily rebalance, IBKR sync)
- **Trader entry point** → `trader/main.py`
- **FastAPI app + UI routes** → `api/main.py` (both API routers and all 8 UI page handlers)
- **API route modules** → `api/routes/` (health, state, controls, signals, sentiment, trades)
- **Dashboard templates** → `ui/templates/` (layout.html + 8 page templates)
- **Static assets** → `ui/static/app.css` (dark theme), `ui/static/app.js` (Chart.js helpers, `postControl()`)
- **Tests** → `tests/` (test_indicators, test_risk, test_strategy — 18 tests total)

## Commands

```bash
pip install -e ".[dev]"          # Install with dev deps
python scripts/init_db.py        # Create/seed DB
python scripts/run_all.py        # Start API (port 8000) + trader worker
python -m pytest tests/ -v       # Run tests (18 tests)
uvicorn api.main:app --reload    # API only (no trader)
python trader/main.py            # Trader only (no API)
```

## Current State & Known Issues

### Working
- Full config system with YAML + Pydantic validation
- SQLite DB with 10 tables, init script, bot_state seeding
- FastAPI with 6 API route groups (14 endpoints) — all returning 200
- 8 dashboard pages with dark theme, Chart.js charts, manual control buttons
- Indicator calculations (EMA, SMA, RSI, MACD, ATR) — tested and deterministic
- Risk engine with drawdown stop, position limits, cash reservation, kill switch, approve mode — tested
- Sentiment system (RSS + mock providers) with DB persistence
- Strategy scoring with SPY regime filter and weighted multi-factor model
- Debit spread order construction (bull call / bear put) with IBKR BAG combos, delta-based strike selection, real bid/ask midpoint pricing
- Live Greeks fetching via IBKR `modelGreeks` with `lastGreeks`/`bidGreeks-askGreeks` fallbacks, IBKR -1.0 sentinel sanitation, 30s cache TTL
- IV Rank computed from `OPTION_IMPLIED_VOLATILITY` historical bars (252-day lookback) with IV-adjusted delta targets across 4 regimes (low/moderate/elevated/extreme)
- 10-check Greeks gate (IV rank, delta range, theta, theta/delta ratio, vega, gamma-near-expiry, liquidity, pricing ROC, buffer, composite risk score)
- Scheduler with configurable intervals
- 44 pytest tests all passing (18 existing + 26 Greeks)

### Not Yet Tested End-to-End
- Live IBKR connection (requires TWS/Gateway running)
- Actual option chain fetching and combo order placement
- Position sync from IBKR
- The `close_all` control currently just activates the kill switch; actual position closing via IBKR not yet wired

### Known Limitations
- Universe is static (embedded 40 tickers); no dynamic screener integration yet
- No EUR/USD FX conversion — all risk calculations in USD
- IV Rank requires the IBKR historical-volatility market data entitlement; falls back to "unknown" regime when unavailable (gate warns, does not block)

## Session Log

- [2026-04-18] Initial build: complete v1 of automated trading bot. Created all 4 packages (common, trader, api, ui), 10 DB tables, IBKR client, indicators, sentiment (RSS + mock), swing strategy with regime filter, risk engine, debit spread execution, scheduler, FastAPI with 14 API endpoints, 8-page dashboard with dark theme and Chart.js charts, run_all script, README, and 18 passing tests. Fixed Starlette 1.0 TemplateResponse API and setuptools flat-layout discovery.
- [2026-04-18] Greeks layer: added `trader/greeks.py` (fetching + IV Rank), `trader/strike_selector.py` (delta-based selection + IV-adjusted criteria + real-price `calculate_limit_price`), `trader/greeks_gate.py` (10-check gate), `trader/greeks_logger.py` (structured logs). Removed index-based strike heuristic and `spread_width * 0.4` placeholder from `execution.py`; now drives strike selection, pricing, and approval through live IBKR `modelGreeks`. All thresholds configurable via `GREEKS_*` env vars. 26 new tests (44 total, all passing).
