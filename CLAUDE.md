# Market AI

## Project Overview

Automated swing trading bot for Interactive Brokers (IBKR) targeting US stocks/ETFs. Supports two instrument types: **debit spread options** (`OptionsSwingBot`) and **equity shares** (`EquitySwingBot`), runnable independently or together. Paper-trading-first. Tech stack: Python 3.12, ib_insync, FastAPI, SQLAlchemy 2.0, SQLite/Postgres, React 18 + Vite + Tailwind SPA (replaces Jinja2/HTMX), Click CLI. User's base currency is EUR; IBKR account is a cash account (no margin, no debt).

## Architecture & Key Decisions

### Structure

Seven top-level packages plus data, scripts, and frontend — flat layout, no `src/` directory. Installed as editable package via `pyproject.toml`.

- **`common/`** — shared config, DB engine, ORM models, Pydantic schemas, logging, time utils
- **`trader/`** — trading engine: IBKR client, market data, indicators, sentiment, strategy, risk, execution, scheduler, Greeks sub-package
- **`bots/`** — bot plugin layer: `BaseBot` ABC + `OptionsSwingBot` + `EquitySwingBot`
- **`execution/`** — order routing: `equity_execution.py` (STK orders) + `options_execution.py` (shim to existing pipeline)
- **`api/`** — FastAPI app with JSON APIs and React SPA browser-route fallback
- **`api/v1/`** — versioned JSON endpoints (overview, positions, orders, fills, signals, rankings, trade-plans, sentiment, risk, controls, config). Controls return `{ ok, bot: BotState }`. All prefixed `/api/v1/`.
- **`ui/`** — `static/dist/` built React SPA output
- **`frontend/`** — React 18 + Vite + TypeScript + Tailwind SPA. Build output goes to `ui/static/dist/`. Dev server on port 5173 proxies `/api` to FastAPI.
- **`scripts/`** — `init_db.py` (DB setup), `run_all.py` (starts API + trader as subprocesses)
- **`data/`** — `sp500.csv` (~180 S&P 500 stocks with symbol/name/sector); `us_listed_master.csv` (~220 major US stocks seed for security master); `manual_alias_overrides.csv` (manual priority-1 aliases)
- **`cli.py`** — unified Click CLI entry point

### Key Design Decisions

- **Debit spreads only (OptionsSwingBot)** — bull call spreads + bear put spreads. No naked shorts. Max loss = net debit paid.
- **EquitySwingBot long-only (v1)** — buys stock shares; no shorting unless `long_only: false` in config.
- **ATR-based equity sizing** — `stop = entry - atr_stop_multiplier × ATR(14)`; `shares = floor(nav × risk_per_trade_pct% / stop_distance)`. Capped to available cash and sector concentration limit.
- **Portfolio isolation via `portfolio_id`** — `Order`, `Position`, `Trade` rows carry `portfolio_id` ("options_swing" or "equity_swing"). Each bot's risk/position checks filter by its own portfolio_id. Migration: `alembic/versions/0004_portfolio_id.py`.
- **Bot plugin pattern** — `BaseBot` ABC defines `build_candidates / score_candidate / select_trades / execute_intent`. `run()` orchestrates the full cycle (regime → universe → rank → score → select → execute). Both bots share universe, regime check, and composite ranking score.
- **Multi-factor composite scoring** — `rank_symbols()` computes a unified [0,1] score per symbol from sentiment, momentum/trend, risk, and fundamentals. Missing factors redistribute weight; liquidity is an eligibility gate only. Score drives bias (≥0.55 → bullish, ≤0.45 → bearish) and bot selection thresholds.
- **IBKR fundamental scoring** — `trader/fundamental_scorer.py` fetches IBKR `ReportRatios` XML, parses numeric `<Ratio FieldName=...>` values, normalizes configured metrics to 0-100, rolls them into valuation/profitability/growth/financial-health pillars, and exposes a neutral-safe result through `compute_fundamentals_factor()` as `value_0_1` for the existing composite scorer.
- **Eligibility gates** — `equity_eligible` (liquidity + contract verified) and `options_eligible` (from `SecurityMaster.options_eligible`, safe-by-default=False). OptionsSwingBot hard-blocks `options_eligible=False`; EquitySwingBot hard-blocks `equity_eligible=False`.
- **DTE config unification** — canonical planner DTE lives in `cfg.options.planner_dte_{min,max,target,fallback_min}`. `cfg.ranking.dte_*` kept for backward compat with deprecation comment.
- **Cash reservation** — before placing any order, cash equal to max loss is reserved; trade blocked if insufficient.
- **Approve mode defaults ON** — signals saved to DB as `pending_approval`; orders not submitted until disabled.
- **Sentiment** — pluggable `SentimentProvider` ABC. Providers: `rss_lexicon`, `claude_llm`, `mock`. Shared by both bots. Sentiment is one input to the composite score (not the only input).
- **No paid APIs** — core universe seeded from embedded SEED_TICKERS (~50 tickers) + RSS-discovered tickers (verified via IBKR). `data/sp500.csv` exists as reference but is not yet auto-ingested into the universe builder.

### Patterns & Conventions

- Config loaded from `config.yaml` → `config.example.yaml` → all-defaults. Pydantic-validated. Cached as `common.config._cached`. Reset with `load_config(reload=True)`.
- DB access via `common/db.get_db()` context manager (auto-commit on success, rollback on exception). Engine cached globally — tests must reset `common.db._engine = None` via `monkeypatch`.
- `intent_id` on orders prevents duplicate submissions on restart — format: `{symbol}_{direction}_{date}_{uuid8}`.
- Event logging to `events_log` table for audit trail.
- Risk checks return `(bool, str)` tuples — `(allowed, reason)`.
- Greeks modules live in `trader/greeks/` sub-package; all re-exported from `trader/greeks/__init__.py`. Do NOT use the old flat aliases (`trader.greeks_gate`, `trader.greeks_logger`, `trader.strike_selector`) — they no longer exist as files.

## Where to Find Things

### Config & DB
- **Config schema & loader** → `common/config.py` (all Pydantic models incl. `BotsConfig`, `EquityBotConfig`, `OptionsBotConfig`)
- **DB models (18 tables)** → `common/models.py` (BotState, EquitySnapshot, Universe, SentimentSnapshot, SignalSnapshot, Order, Fill, Position, Trade, EventLog, ContractVerificationCache, SymbolRanking, TradePlan, SentimentLlmItem, SentimentLlmUsage, SecurityMaster, SecurityAlias, RssEntityMatch)
- **API response schemas** → `common/schema.py`

### Core Trading
- **IBKR connection** → `trader/ibkr_client.py` (singleton via `get_ibkr_client()`)
- **Technical indicators** → `trader/indicators.py` (EMA, SMA, RSI, MACD, ATR + `compute_indicators()`)
- **Market data** → `trader/market_data.py` (`fetch_bars()`, `get_latest_bars()`, in-memory cache)
- **Strategy & scoring** → `trader/strategy.py` (`check_regime()` SPY-based regime, `score_symbol()` per ticker, `generate_signals()` legacy entry)
- **Multi-factor scoring** → `trader/scoring.py` (`compute_composite()`, `compute_sentiment_factor()`, `compute_liquidity_factor()`, `compute_momentum_trend_factor()`, `compute_risk_factor()`, `compute_optionability_factor()`, `compute_fundamentals_factor()`)
- **Fundamental scoring** → `trader/fundamental_scorer.py` (`FundamentalScorer`, `FundamentalResult`; IBKR `ReportRatios`, in-memory 24h default cache, neutral fallback)
- **Risk engine** → `trader/risk.py` (`check_can_trade()`, `compute_max_risk_for_trade()`, `record_equity_snapshot()`, `log_event()`)
- **Options order construction** → `trader/execution.py` (debit spread spec, IBKR BAG combo orders, `execute_signal()`)
- **Options trade planner** → `trader/options_planner.py` (`plan_trade()` — pure planning, no submission, writes TradePlan rows)
- **Universe** → `trader/universe.py` (`seed_universe()`, `get_verified_universe()`, `verify_contract()`, `SEED_TICKERS`)
- **Symbol ranking** → `trader/ranking.py` (`rank_symbols()`, `select_candidates()`, `RankedSymbol` — re-exports sentiment internals from `trader/scoring.py`)
- **Scheduler** → `trader/scheduler.py` (10s heartbeat, sentiment + ranking + signal eval + rebalance + IBKR sync)
- **Trader entry point** → `trader/main.py`

### Greeks (all in `trader/greeks/` sub-package)
- **Greeks fetching & IV Rank** → `trader/greeks/service.py` (`GreeksService`, `OptionChainGreeks`, `GreeksSnapshot`)
- **Delta-based strike selection** → `trader/greeks/strike_selector.py` (`StrikeSelector`, `StrikeSelectionCriteria`, `SpreadSelection`, `calculate_limit_price()`)
- **Greeks trade gate** → `trader/greeks/gate.py` (`GreeksGate`, `GateResult` — 10 checks)
- **Greeks logging** → `trader/greeks/logger.py` (`GreeksLogger`)
- **Re-exports** → `trader/greeks/__init__.py` (import everything from here)

### Bot Layer
- **Bot plugin interface** → `bots/base_bot.py` (`BaseBot` ABC, `Candidate`, `ScoreBreakdown`, `TradeIntent`, `BotContext`, `BotRunResult`)
- **Options bot** → `bots/options_swing_bot.py` (wraps `plan_trade()` + `execute_signal()` pipeline)
- **Equity bot** → `bots/equity_swing_bot.py` (ATR sizing, sector cap, `_size_equity_trade()`, `_count_equity_positions()`)
- **Equity execution** → `execution/equity_execution.py` (`place_equity_order()`, portfolio_id="equity_swing")
- **Options execution shim** → `execution/options_execution.py` (`execute_options_intent()` — `TradeIntent` → `SignalIntent`)

### Sentiment
- **Factory + refresh** → `trader/sentiment/factory.py` (`refresh_and_store()`, serialised by lock)
- **Scoring getters** → `trader/sentiment/scoring.py` (`get_latest_market_score()`, `get_latest_sector_score()`, `get_latest_ticker_score()`)
- **Providers** → `trader/sentiment/rss_provider.py`, `claude_provider.py`, `mock_provider.py`
- **Budget cap** → `trader/sentiment/budget.py` (hard €10/mo cap; failures must NOT fall back to lexicon)

### Security Master (company-name → ticker)
- **Normalization** → `trader/securities/normalize.py` (`normalize_company_name()`, `generate_aliases()`)
- **Import pipeline + IBKR verification** → `trader/securities/master.py` (`import_csv()`, `verify_security()`, `check_options_eligibility()`, `refresh_liquidity()`, `load_manual_overrides()`)
- **Matcher** → `trader/securities/matcher.py` (`match_companies_to_symbols()`, `MatchResult`)
- **DB tables** → `common/models.py`: `SecurityMaster`, `SecurityAlias`, `RssEntityMatch`
- **Config** → `common/config.py`: `SecuritiesConfig` (allowed_exchanges, min_price, etc.)
- **Seed data** → `data/us_listed_master.csv` (~220 major US-listed stocks), `data/manual_alias_overrides.csv`
- **Alembic migration** → `alembic/versions/0005_security_master.py`
- **Claude prompt** → `trader/sentiment/claude_provider.py` now requests `mentioned_companies` (company names) not ticker entities; `_build_ticker_results_from_companies()` runs the matcher post-LLM

### API & UI
- **FastAPI app** → `api/main.py` — includes all routers and serves the React SPA for browser routes (returns `ui/static/dist/index.html` if built)
- **Legacy API routes** → `api/routes/` (health, state, controls, signals, sentiment, trades, rankings) — unchanged, kept for backward compat
- **JSON API v1** → `api/v1/` — 10 modules, master router at `api/v1/__init__.py`, all mounted under `/api/v1/`. Controls POST endpoints return `{ ok: bool, bot: BotState }`.
- **SPA workspace** → `frontend/` — Vite 5, React 18, TypeScript strict, Tailwind 3, TanStack Query 5, Zustand 4, Recharts 2, lucide-react, @fontsource/inter + jetbrains-mono
- **SPA entry** → `frontend/src/main.tsx` → `App.tsx` → React Router
- **SPA pages** → `frontend/src/pages/` (Overview, Positions, Orders, Signals, Rankings, Sentiment, Risk, Controls, Config)
- **SPA components** → `frontend/src/components/` (AppShell, Sidebar, Topbar, Card, KPI, Badge, Sparkline, LineChart, Donut, ScoreBar, DataTable, Toggle, Button, SegmentedControl, TweaksPanel)
- **API client** → `frontend/src/lib/api.ts` (typed fetch wrappers keyed by `api.*` functions)
- **Bot state store** → `frontend/src/store/botStore.ts` (Zustand; updated from every control POST response and overview poll)
- **Design tokens** → `frontend/src/styles/globals.css` (CSS custom props: `--bg-0..4`, `--ink-1..5`, `--accent-h`, `--pos/neg/warn`, `--density`)
- **Tweaks panel** → persists `{ accentHue, density }` to `localStorage` under `mai_tweaks`; density maps `dense=0.75 / balanced=1 / airy=1.25` into `--density`
- **Built SPA** → `ui/static/dist/index.html` + `assets/` (Vite output, gitignored in practice)

### Entry Points & Data
- **Unified CLI** → `cli.py` (Click; commands below)
- **SP500 reference** → `data/sp500.csv` (~180 stocks, `symbol,name,sector`)
- **Alembic migrations** → `alembic/versions/` (0001–0005)
- **Python tests** → `tests/` (pytest suite; focused fundamental/scoring run: 37 passing)
- **Frontend tests** → `frontend/src/test/pages/` (11 vitest smoke tests, one per page + helpers)

## Commands

```bash
pip install -e ".[dev]"          # Install with dev deps (includes click)
python scripts/init_db.py        # Create/seed DB
alembic upgrade head             # Run all migrations (Postgres / fresh SQLite)
python scripts/run_all.py        # Start API (port 8000) + trader worker (legacy)
python -m pytest tests/ -v       # Run Python tests
uvicorn api.main:app --reload    # API only (no trader)
python trader/main.py            # Trader only (no API, continuous)

# Frontend (from frontend/)
pnpm dev                         # Dev server → localhost:5173, proxies /api to :8000
pnpm build                       # Build SPA → ui/static/dist/
pnpm test                        # Run vitest smoke tests (11 tests)

# Unified CLI (new)
python cli.py sentiment refresh [--source rss_lexicon|claude_llm] [--dry-run]
python cli.py run options_swing --mode paper --dry-run
python cli.py run equity_swing  --mode paper --approve
python cli.py run all           --mode live  --once        # single cycle + exit
python cli.py report last-run   --bot equity_swing [--json-out]

# Security master
python cli.py securities import [--file data/us_listed_master.csv] [--verify-ibkr] [--load-overrides]
python cli.py securities verify  --symbol MOH --options-check
python cli.py securities verify  --all
python cli.py securities liquidity-refresh [--symbol AAPL] [--lookback 20]
python cli.py match-company --text "Today Molina Healthcare made 5 billion in revenue"
python cli.py match-company --companies "Molina Healthcare,UnitedHealth"
```

## Current State & Known Issues

### Working
- Full config system with YAML + Pydantic validation (`BotsConfig` + per-bot configs, `FundamentalsConfig`)
- SQLite DB with 18 tables; Postgres-ready via alembic
- FastAPI with legacy API routes (14 endpoints) + new `/api/v1/` JSON layer (10 endpoints)
- **React SPA** (`frontend/`) — 9 pages, dark design system, TanStack Query polling, Zustand bot store, Recharts charts, Tweaks panel; built to `ui/static/dist/`, served on root-level browser routes
- Indicator calculations (EMA, SMA, RSI, MACD, ATR) — deterministic, tested
- Risk engine: drawdown stop, position limits, cash reservation, kill switch, approve mode
- Sentiment: RSS lexicon + Claude LLM + mock providers, DB persistence, recency weighting
- Strategy: SPY regime filter, legacy 4-factor `score_symbol()` (still used by equity bot `score_candidate`)
- **Multi-factor composite scoring** (`trader/scoring.py`): [0,1] score per symbol from sentiment, momentum/trend, risk, and fundamentals; liquidity is an eligibility gate only
- **FundamentalScorer** (`trader/fundamental_scorer.py`): IBKR `ReportRatios` parser/scorer with configured metric bounds/pillars, neutral 50 fallback for unavailable data, and shared in-memory TTL cache
- `equity_eligible` and `options_eligible` eligibility gates
- DTE config unified: canonical `cfg.options.planner_dte_*`; `cfg.ranking.dte_*` kept deprecated
- OptionsSwingBot: full debit spread pipeline (Greeks → gate → pricing → approve/submit)
- EquitySwingBot: ATR-based sizing, sector concentration cap, risk-off cash/defensive modes, portfolio isolation
- Unified CLI with continuous and single-cycle modes
- **Security master** (`trader/securities/`): company-name→ticker deterministic matching
- 205 pytest tests + 11 vitest smoke tests all passing

### Not Yet Tested End-to-End
- Live IBKR connection (requires TWS/Gateway running)
- EquitySwingBot live order placement and fill tracking
- `close_all` control only activates kill switch; actual IBKR position closing not wired
- `data/sp500.csv` exists but is not yet auto-ingested — universe still seeded from `SEED_TICKERS`

### Known Limitations
- No EUR/USD FX conversion — all risk calculations in USD
- EquitySwingBot long-only in v1 (`long_only: true` in config)
- IV Rank requires IBKR historical-volatility entitlement; falls back to "unknown" (gate warns, does not block)
- Fundamental scoring requires IBKR Reuters/Refinitiv fundamental data entitlement; empty/error responses return neutral score 50 and log warnings
- Position exit logic (trailing stop, max_holding_days) is config-specified but not yet automated — manual via dashboard

## Session Log

- [2026-04-18] Initial build: complete v1 of automated trading bot. Created all 4 packages (common, trader, api, ui), 10 DB tables, IBKR client, indicators, sentiment (RSS + mock), swing strategy with regime filter, risk engine, debit spread execution, scheduler, FastAPI with 14 API endpoints, 8-page dashboard with dark theme and Chart.js charts, run_all script, README, and 18 passing tests. Fixed Starlette 1.0 TemplateResponse API and setuptools flat-layout discovery.
- [2026-04-18] Greeks layer: added `trader/greeks/` sub-package (service, gate, strike_selector, logger). Removed stale flat-module aliases. Delta-based strike selection, IV-adjusted criteria, real bid/ask pricing, 10-check GreeksGate. 26 new tests (44 total).
- [2026-04-19] Multi-bot refactor: `bots/` plugin package (`BaseBot`, `OptionsSwingBot`, `EquitySwingBot`); `execution/` package (`equity_execution.py` with ATR sizing + portfolio isolation, `options_execution.py` shim); unified `cli.py` (Click: sentiment refresh, run, report); `data/sp500.csv`; alembic migration 0004 (`portfolio_id` on orders/positions/trades); `BotsConfig`/`EquityBotConfig`/`OptionsBotConfig` in config; fixed broken greeks flat-module imports in `trader/execution.py`. 94 new tests (138 total, all passing).
- [2026-04-21] Security master + deterministic company→ticker matching: `trader/securities/` package (normalize, master, matcher); 3 new DB tables (`security_master`, `security_alias`, `rss_entity_matches`) + alembic 0005; `SecuritiesConfig`; Claude LLM prompt updated to emit `mentioned_companies` instead of ticker entities; `_build_ticker_results_from_companies()` in claude_provider maps them to verified symbols post-LLM; `securities import/verify/liquidity-refresh` CLI commands; `match-company` debug command; ~220-row `data/us_listed_master.csv` seed + `data/manual_alias_overrides.csv`; 41 new tests (179 total, all passing).
- [2026-04-22] Multi-factor composite scoring: new `trader/scoring.py` with 5 factor functions (sentiment, momentum/trend, risk, liquidity, optionability) + `compute_composite()` with proportional weight redistribution; `rank_symbols()` rewritten to run full factor pipeline per symbol; `RankedSymbol` gains `equity_eligible`/`options_eligible` flags; both bots hard-gate on their eligibility flag; DTE config unified under `cfg.options.planner_dte_*`; `/rankings` dashboard shows expandable `<details>` factor breakdowns; `FundamentalsConfig` added to config; `enter_threshold` default changed 0.25→0.55; 26 new tests in `tests/test_scoring.py` (205 total, all passing).
- [2026-04-23] React SPA + JSON API v1: replaced Jinja2/HTMX/Chart.js dashboard with a full React 18 + Vite + TypeScript + Tailwind SPA (`frontend/`). Added `api/v1/` package (10 FastAPI routers, all under `/api/v1/`; controls return `{ok, bot}`). FastAPI now also serves the SPA at `/app/{path:path}`. SPA features: dark design system with CSS custom property tokens, TanStack Query polling, Zustand bot state, Recharts charts, Tweaks panel (accent hue + density, persisted to localStorage). Legacy Jinja2 routes untouched. 11 vitest smoke tests added; all 205 Python tests still pass.
- [2026-04-27] Added IBKR `ReportRatios` fundamental scoring: new `trader/fundamental_scorer.py`, config-driven metric bounds/pillars in `common/config.py` and `config.example.yaml`, and `compute_fundamentals_factor()` adapter returning composite `value_0_1` plus full 0-100 breakdown. Added/updated tests for XML parsing, normalization, pillar fallback, cache expiry, and composite adapter; focused run `python -m pytest tests\test_fundamental_scorer.py tests\test_scoring.py -q` passed (37 tests).
