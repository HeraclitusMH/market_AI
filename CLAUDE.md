# Market AI

## Project Overview

Automated swing trading bot for Interactive Brokers (IBKR) targeting US stocks/ETFs. Supports two instrument types: **debit spread options** (`OptionsSwingBot`) and **equity shares** (`EquitySwingBot`), runnable independently or together. Paper-trading-first. Tech stack: Python 3.12, ib_insync, FastAPI, SQLAlchemy 2.0, SQLite/Postgres, React 18 + Vite + Tailwind SPA (replaces Jinja2/HTMX), Click CLI. User's base currency is EUR; IBKR account is a cash account (no margin, no debt).

## Architecture & Key Decisions

### Structure

Seven top-level packages plus data, scripts, and frontend — flat layout, no `src/` directory. Installed as editable package via `pyproject.toml`.

- **`common/`** — shared config, DB engine, ORM models, Pydantic schemas, logging, time utils
- **`trader/`** — trading engine: IBKR client, market data, indicators, sentiment, strategy, risk, execution, scheduler, Greeks sub-package, regime sub-package, exits
- **`bots/`** — bot plugin layer: `BaseBot` ABC + `OptionsSwingBot` + `EquitySwingBot`
- **`execution/`** — order routing: `equity_execution.py` (STK orders) + `options_execution.py` (shim to existing pipeline)
- **`api/`** — FastAPI app with JSON APIs and React SPA browser-route fallback
- **`api/v1/`** — versioned JSON endpoints (overview, positions, orders, fills, signals, rankings, trade-plans, sentiment, risk, controls, config, regime). Controls return `{ ok, bot: BotState }`. All prefixed `/api/v1/`.
- **`ui/`** — `static/dist/` built React SPA output
- **`frontend/`** — React 18 + Vite + TypeScript + Tailwind SPA. Build output goes to `ui/static/dist/`. Dev server on port 5173 proxies `/api` to FastAPI.
- **`scripts/`** — `init_db.py` (DB setup), `run_all.py` (starts API + trader as subprocesses)
- **`data/`** — `sp500.csv` (~180 S&P 500 stocks with symbol/name/sector); `us_listed_master.csv` (~220 major US stocks seed for security master); `manual_alias_overrides.csv` (manual priority-1 aliases); `seen_articles.json` + `sentiment_output.json` (Claude Routine sentiment contract files). `data/_pending_analysis.json` is a temporary Claude Routine handoff file and is gitignored.
- **`cli.py`** — unified Click CLI entry point

### Key Design Decisions

- **3-state market regime model** — `trader/regime/` package replaces the old binary `check_regime`. `RegimeEngine` evaluates 4 pillars (Trend 30%, Breadth 25%, Volatility 25%, Credit Stress 20%) to produce a 0–100 composite score, then runs it through `RegimeStateMachine` with asymmetric hysteresis: 2 confirmations to degrade, 3 to recover, no state skipping. States: `risk_on` → full sizing; `risk_reduced` → half sizing, no new options entries, +0.10 score threshold; `risk_off` → no new entries. Falls back to legacy binary SPY check when `cfg.regime.enabled=False`. `RegimeState` supports `== "risk_on"` string comparison for backward compatibility.
- **Debit spreads only (OptionsSwingBot)** — bull call spreads + bear put spreads. No naked shorts. Max loss = net debit paid.
- **EquitySwingBot long-only (v1)** — buys stock shares; no shorting unless `long_only: false` in config.
- **ATR-based equity sizing** — `stop = entry - (atr_stop_multiplier × stop_tightening_factor) × ATR(14)`; `shares = floor(nav × risk_per_trade_pct% / stop_distance) × sizing_factor`. Both `stop_tightening_factor` and `sizing_factor` come from `context.regime_state` (1.0 in `risk_on`, reduced in `risk_reduced`/`risk_off`). Capped to available cash and sector concentration limit.
- **Portfolio isolation via `portfolio_id`** — `Order`, `Position`, `Trade` rows carry `portfolio_id` (`options_swing`, `equity_swing`, or `unattributed`). `sync_positions()` reconciles IBKR's flat broker positions from `TradeManagement` first, then recent filled/submitted/pending approval orders. Unmatched/manual positions become `unattributed` and count conservatively against the matching bot's instrument cap. Migration: `alembic/versions/0004_portfolio_id.py`.
- **Bot plugin pattern** — `BaseBot` ABC defines `build_candidates / score_candidate / select_trades / execute_intent / execute_exit_intent`. `run()` orchestrates the full cycle (regime → universe → rank → **exit phase** → score → select → execute). Both bots share universe, regime check, and composite ranking score. `BotContext` carries both `regime: str` (backward compat) and `regime_state: RegimeState` (full object).
- **7-factor composite scoring** — `rank_symbols()` always uses `trader/composite_scorer/CompositeScorer`. It computes Quality, Value, Momentum, Growth, Sentiment, Technical Structure, and subtractive Risk Penalty with regime-adaptive weights from `trader/composite_scorer/config/scoring_config.yaml`. `components_json.composite_7factor` is authoritative for API/UI display; the API and Rankings page no longer recompute or display the old 4-factor formula.
- **yfinance fundamental scoring** — `trader/fundamental_scorer.py` fetches yfinance quote data, normalizes configured metrics to 0-100, rolls them into valuation/profitability/growth/financial-health pillars, and exposes the result through `compute_fundamentals_factor()` as `value_0_1` for the composite scorer. Three-tier cache: in-process (`cache_ttl_hours`, default 24h) → DB `fundamental_snapshots` (`ttl_days`, default 7) → yfinance fetch. `get_score(symbol, force_refresh=True)` bypasses both caches. `FundamentalResult` includes a `value_metrics` dict (raw yfinance fields: `enterprise_to_ebitda`, `free_cash_flow_ttm`, `market_cap`, `forward_pe`, `eps_growth_next_year`, `price_to_book`, `price_to_sales`, `sector`) extracted by `_parse_value_metrics()` for use by `ValueFactor`.
- **ValueFactor uses relative-to-peer scoring** — `trader/composite_scorer/factors/fundamental.py::ValueFactor` computes FCF yield (`free_cash_flow_ttm / market_cap`), PEG ratio (`forward_pe / eps_growth_next_year`), and sector-relative EV/EBITDA / P/B discounts vs. peer medians. `eps_growth_next_year` is only populated when positive to prevent misleading PEG from negative growth. Falls back to absolute valuation pillar score only when `fundamental_metrics` is fully absent.
- **Two-phase ranking with sector medians** — `rank_symbols()` first pre-fetches fundamentals for all symbols, computes per-sector median multiples via `_compute_sector_medians()` (`sector_median_ev_ebitda`, `sector_median_price_to_book`, `sector_median_price_to_sales`), then runs the main scoring loop with those medians attached to each symbol's `fundamental_metrics`. This means expensive but profitable tech stocks (e.g. AVGO) are scored relative to sector peers rather than against absolute value-stock thresholds.
- **Weekly fundamentals refresh** — `trader/fundamentals_refresh.py::refresh_fundamentals(symbols=None, force=True)` is the single entry point shared by the scheduler, API endpoint, and CLI. When called with no symbols, it pulls the verified universe and force-refreshes every ticker. Scheduler tick runs every `fundamentals.refresh_days` (default 7). The API route imports this helper lazily so missing optional dependency `yfinance` returns a `503` from `/api/v1/fundamentals/refresh` instead of crashing FastAPI at startup.
- **IBKR delayed market data** — `IBKRClient.connect()` calls `reqMarketDataType(cfg.ibkr.market_data_type)` (default `3` = delayed) so paper accounts without live US subscriptions get delayed quotes instead of Error 10089/354 storms. `_fetch_underlying_price` reads `delayedLast`/`delayedClose` in addition to `last`/`close`. When the underlying is still unavailable, `_select_strikes_in_range` bails with `[]` instead of returning the full chain (which previously triggered Error 200 floods on strikes that don't exist for the requested expiry).
- **1-year bar history for momentum** — `trader/market_data.py::_TF_MAP["1D"]` fetches `"1 Y"` (~252 trading bars) from IBKR. The minimum for `compute_momentum_trend_factor()` is 63 bars (ret_63d); the prior `"60 D"` (~42 bars) caused the factor to always return `status: "missing"`. Results are cached in-process; the extra fetch cost is one-time per process lifetime.
- **Company names in API responses** — all `/api/v1/` endpoints that return symbol rows (`positions`, `orders`, `fills`, `signals`, `rankings`, `trade-plans`) now look up `SecurityMaster.name` and include it as `name` (nullable). `RankedSymbol` carries `name` from `UniverseItem.name`. The SPA renders "Apple [AAPL]" format via a shared `symbolCell()` helper (`frontend/src/lib/cells.tsx`); falls back to ticker-only when name is absent.
- **Dashboard dual-theme architecture** — Matrix remains the default with no `data-theme` attribute and existing `frontend/src/styles/globals.css` behavior unchanged. Dream mode sets `<html data-theme="dream">`, loads `frontend/src/theme-dream.css` after the base stylesheet, persists selection in `localStorage` via `frontend/src/hooks/useTheme.ts`, and injects/removes `.dream-particles` only while dream mode is active. Theme switching must not remount routes or alter layout density.
- **Eligibility gates** — `equity_eligible` (liquidity + contract verified) and `options_eligible` (from `SecurityMaster.options_eligible`, safe-by-default=False). OptionsSwingBot hard-blocks `options_eligible=False`; EquitySwingBot hard-blocks `equity_eligible=False`.
- **DTE config unification** — canonical planner DTE lives in `cfg.options.planner_dte_{min,max,target,fallback_min}`. `cfg.ranking.dte_*` kept for backward compat with deprecation comment.
- **Cash reservation** — before placing any order, cash equal to max loss is reserved; trade blocked if insufficient.
- **Approve mode defaults ON** — signals saved to DB as `pending_approval`; orders not submitted until disabled.
- **Sentiment** — pluggable `SentimentProvider` ABC. Providers: `rss_lexicon`, `claude_llm`, `claude_routine`, `mock`. Shared by both bots. Sentiment is one input to the composite score (not the only input). `claude_routine` is a pure reader for pre-computed scores in `data/sentiment_output.json` (local path or GitHub raw URL); it does no NLP/API work and never writes `data/seen_articles.json`.
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
- **Config schema & loader** → `common/config.py` (all Pydantic models incl. `BotsConfig`, `EquityBotConfig`, `OptionsBotConfig`, `RegimeConfig` and 11 sub-models)
- **DB models (20 tables)** → `common/models.py` (BotState, EquitySnapshot, Universe, SentimentSnapshot, SignalSnapshot, Order, Fill, Position, Trade, EventLog, ContractVerificationCache, SymbolRanking, FundamentalSnapshot, TradePlan, SentimentLlmItem, SentimentLlmUsage, SecurityMaster, SecurityAlias, RssEntityMatch, **RegimeSnapshot**, TradeManagement)
- **API response schemas** → `common/schema.py` (`PositionOut` includes `portfolio_id` for dashboard attribution visibility)

### Regime Detection (all in `trader/regime/` sub-package)
- **Models** → `trader/regime/models.py` (`RegimeLevel` enum, `PillarScore` dataclass, `RegimeState` dataclass with backward-compat `__eq__`/`__str__`/`.regime` property)
- **Pillar indicators** → `trader/regime/indicators.py` (`compute_trend_score`, `compute_breadth_score`, `compute_volatility_score`, `compute_credit_stress_score`)
- **State machine** → `trader/regime/state_machine.py` (`RegimeStateMachine` — hysteresis, no state skipping, asymmetric confirmation counts)
- **Engine** → `trader/regime/engine.py` (`RegimeEngine` — orchestrates pillars, confidence-weighted composite, state machine, DB persistence; module-level singleton in `trader/strategy.py::_regime_engine`)
- **Re-exports** → `trader/regime/__init__.py`
- **DB table** → `common/models.py::RegimeSnapshot` (persists every evaluation; used for restart state recovery)
- **API** → `api/routes/regime.py` (`GET /api/v1/regime/current`, `GET /api/v1/regime/history?days=30`)
- **Config** → `common/config.py::RegimeConfig` (+ `RegimeWeights`, `RegimeTrendConfig`, `RegimeBreadthConfig`, `RegimeVolatilityConfig`, `RegimeCreditStressConfig`, `RegimeThresholds`, `RegimeHysteresis`, `RegimeEffectsConfig`, `RegimeFallbackConfig`)
- **Frontend** → `frontend/src/pages/Regime.tsx` (current score/effects, pillars, 30-day history chart/table) and `frontend/src/components/RegimeSummaryCard.tsx` (shared current-regime card used on Overview/Risk)

### Exit Management
- **Exit manager** → `trader/exits.py` (`ExitManager`, 8 equity rules + 8 options rules, priority-ordered)
- **TradeManagement table** → `common/models.py::TradeManagement` (one row per open position; created on order placement, deleted on full close)
- **BaseBot hook** → `bots/base_bot.py::BaseBot._run_exit_phase()` — called before `build_candidates` each cycle
- **Config** → `common/config.py::ExitConfig` / `EquityExitConfig` / `OptionsExitConfig`; `cfg.exits.*`

### Core Trading
- **IBKR connection** → `trader/ibkr_client.py` (singleton via `get_ibkr_client()`)
- **Technical indicators** → `trader/indicators.py` (EMA, SMA, RSI, MACD, ATR + `compute_indicators()`)
- **Market data** → `trader/market_data.py` (`fetch_bars()`, `get_latest_bars()`, in-memory cache; `_TF_MAP["1D"]` = `"1 Y"` duration to supply enough bars for momentum/SMA200)
- **Strategy & scoring** → `trader/strategy.py` (`check_regime()` — delegates to `RegimeEngine` when `cfg.regime.enabled=True`, returns `RegimeState`; `score_symbol()` per ticker, `generate_signals()` legacy entry)
- **7-factor composite scoring** → `trader/composite_scorer/` (`CompositeScorer`, factor modules, regime detector, normalizer, YAML weights). `trader/ranking.py::_score_7factor()` adapts existing bar/sentiment/fundamental/risk outputs into this scorer.
- **Scoring adapter inputs** → `trader/scoring.py` (`compute_sentiment_factor()`, `compute_liquidity_factor()`, `compute_momentum_trend_factor()`, `compute_risk_factor()`, `compute_optionability_factor()`, `compute_fundamentals_factor()`). Used by ranking to build reusable inputs for the 7-factor scorer; `rank_symbols()` does not use `compute_composite()`. `compute_fundamentals_factor()` returns `"fundamental_metrics"` at top level (raw value_metrics from `FundamentalResult`) so `ValueFactor` can read it from `stock_data`.
- **Fundamental scoring** → `trader/fundamental_scorer.py` (`FundamentalScorer`, `FundamentalResult`; yfinance, three-tier cache: memory → `fundamental_snapshots` DB → yfinance; `force_refresh` param; missing-factor redistribution; `_parse_value_metrics()` extracts raw fields into `value_metrics` dict stored in `FundamentalResult`)
- **Fundamentals refresh helper** → `trader/fundamentals_refresh.py` (`refresh_fundamentals(symbols=None, force=True)` — shared by scheduler/API/CLI)
- **Risk engine** → `trader/risk.py` (`check_can_trade()`, `compute_max_risk_for_trade()`, `record_equity_snapshot()`, `log_event()`)
- **IBKR position sync/reconciliation** → `trader/sync.py::sync_positions()` and helpers `_build_attribution_map()`, `_reconcile_portfolio_id()`, `_classify_instrument()`. Emits `sync_positions_reconciled` every sync and `sync_unattributed_positions` when manual/unmatched positions are found.
- **Options order construction** → `trader/execution.py` (debit spread spec, IBKR BAG combo orders, `execute_signal()`)
- **Options trade planner** → `trader/options_planner.py` (`plan_trade()` — pure planning, no submission, writes TradePlan rows)
- **Universe** → `trader/universe.py` (`seed_universe()`, `get_verified_universe()`, `verify_contract()`, `SEED_TICKERS`)
- **Symbol ranking** → `trader/ranking.py` (`rank_symbols()`, `select_candidates()`, `RankedSymbol`, `_compute_sector_medians()` — re-exports sentiment internals from `trader/scoring.py`). `rank_symbols()` does a two-phase pass: pre-fetch all fund_factors → compute sector medians → score loop with peer-relative `fundamental_metrics`.
- **Scheduler** → `trader/scheduler.py` (10s heartbeat, sentiment + ranking + signal eval + rebalance + IBKR sync)
- **Trader entry point** → `trader/main.py`

### Greeks (all in `trader/greeks/` sub-package)
- **Greeks fetching & IV Rank** → `trader/greeks/service.py` (`GreeksService`, `OptionChainGreeks`, `GreeksSnapshot`)
- **Delta-based strike selection** → `trader/greeks/strike_selector.py` (`StrikeSelector`, `StrikeSelectionCriteria`, `SpreadSelection`, `calculate_limit_price()`)
- **Greeks trade gate** → `trader/greeks/gate.py` (`GreeksGate`, `GateResult` — 10 checks)
- **Greeks logging** → `trader/greeks/logger.py` (`GreeksLogger`)
- **Re-exports** → `trader/greeks/__init__.py` (import everything from here)

### Bot Layer
- **Bot plugin interface** → `bots/base_bot.py` (`BaseBot` ABC, `Candidate`, `ScoreBreakdown`, `TradeIntent`, `BotContext`, `BotRunResult`). `BotContext` carries `regime: str` (backward compat) + `regime_state: RegimeState` (full object).
- **Options bot** → `bots/options_swing_bot.py` (wraps `plan_trade()` + `execute_signal()` pipeline; `_count_options_positions()` counts distinct option/combo symbols and unattributed option/combo positions; blocks all new entries when `regime_state.allows_new_options_entries=False`)
- **Equity bot** → `bots/equity_swing_bot.py` (ATR sizing, sector cap, `_size_equity_trade()`, `_count_equity_positions()`; counts unattributed stock positions; applies `regime_state.sizing_factor` and `score_threshold_adjustment`; blocks entries when `allows_new_equity_entries=False`)
- **Equity execution** → `execution/equity_execution.py` (`place_equity_order()`, portfolio_id="equity_swing")
- **Options execution shim** → `execution/options_execution.py` (`execute_options_intent()` — `TradeIntent` → `SignalIntent`)

### Sentiment
- **Factory + refresh** → `trader/sentiment/factory.py` (`refresh_and_store()`, serialised by lock)
- **Scoring getters** → `trader/sentiment/scoring.py` (`get_latest_market_score()`, `get_latest_sector_score()`, `get_latest_ticker_score()`)
- **Providers** → `trader/sentiment/rss_provider.py`, `claude_provider.py`, `routine_provider.py`, `mock_provider.py`
- **Claude Routine files** → `data/sentiment_output.json` (bot reads scores), `data/seen_articles.json` (external routine owns dedup), `config/routine_rss_feeds.txt` (routine feed list)
- **Claude Routine fetch script** → `scripts/routine_fetch_articles.py` (runs on Anthropic cloud after repo clone; fetches RSS, prunes/dedups via `data/seen_articles.json`, writes temporary `data/_pending_analysis.json`). Routine-only deps live in `scripts/requirements_routine.txt`.
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
- **JSON API v1** → `api/v1/` — 11 modules, master router at `api/v1/__init__.py`, all mounted under `/api/v1/`. Controls POST endpoints return `{ ok: bool, bot: BotState }`. Includes `fundamentals.py` (`POST /api/v1/fundamentals/refresh[?symbol=]`).
- **Regime API** → `api/routes/regime.py` (`GET /api/v1/regime/current`, `GET /api/v1/regime/history?days=30`; registered in `api/main.py`). `/current` includes current level, composite score, pillars, data quality, hysteresis flag, transition, and configured per-state effects (`allows_new_*`, sizing, stop tightening, threshold adjustment).
- **SPA workspace** → `frontend/` — Vite 5, React 18, TypeScript strict, Tailwind 3, TanStack Query 5, Zustand 4, Recharts 2, lucide-react, @fontsource/inter + jetbrains-mono
- **SPA entry** → `frontend/src/main.tsx` → `App.tsx` → React Router
- **SPA pages** → `frontend/src/pages/` (Overview, Positions, Orders, Signals, Rankings, Sentiment, Regime, Risk, Controls, Config)
- **SPA components** → `frontend/src/components/` (AppShell, Sidebar, Topbar, ThemeSwitch, Card, KPI, Badge, Sparkline, LineChart, Donut, ScoreBar, DataTable, Toggle, Button, SegmentedControl, TweaksPanel, RegimeSummaryCard)
- **API client** → `frontend/src/lib/api.ts` (typed fetch wrappers keyed by `api.*` functions)
- **Symbol cell helper** → `frontend/src/lib/cells.tsx` (`symbolCell(r)` — renders "Apple [AAPL]" with name bold and ticker dimmed; used in all table "Company" columns)
- **Bot state store** → `frontend/src/store/botStore.ts` (Zustand; updated from every control POST response and overview poll)
- **Design tokens** → `frontend/src/styles/globals.css` (CSS custom props: `--bg-0..4`, `--ink-1..5`, `--accent-h`, `--pos/neg/warn`, `--density`)
- **Dashboard theme state** → `frontend/src/hooks/useTheme.ts` (`market-ai-theme` localStorage key, toggles `<html data-theme="dream">`, manages `.dream-particles`)
- **Dream theme stylesheet** → `frontend/src/theme-dream.css` (all dream-mode visual overrides scoped under `[data-theme="dream"]`; loaded after `globals.css` from `frontend/index.html`)
- **Tweaks panel** → persists `{ accentHue, density }` to `localStorage` under `mai_tweaks`; density maps `dense=0.75 / balanced=1 / airy=1.25` into `--density`
- **Built SPA** → `ui/static/dist/index.html` + `assets/` (Vite output, gitignored in practice)

### Entry Points & Data
- **Unified CLI** → `cli.py` (Click; commands below)
- **SP500 reference** → `data/sp500.csv` (~180 stocks, `symbol,name,sector`)
- **Alembic migrations** → `alembic/versions/` (0001–0006; 0006 = `fundamental_snapshots`; `regime_snapshots` + `trade_management` tables auto-created via `create_tables()`)
- **Python tests** → `tests/` (pytest suite; 331 passing — incl. `tests/test_regime.py` with 37 regime-specific tests)
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
python cli.py fundamentals refresh [--symbol AAPL]   # weekly bulk; --symbol for one
python cli.py sentiment refresh [--source rss_lexicon|claude_llm|claude_routine|mock] [--dry-run]
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

# Claude Routine infrastructure (not used by trading bot)
pip install -r scripts/requirements_routine.txt
python scripts/routine_fetch_articles.py
python -m json.tool data/_pending_analysis.json
```

## Current State & Known Issues

### Working
- Full config system with YAML + Pydantic validation (`BotsConfig` + per-bot configs, `FundamentalsConfig`)
- SQLite DB with 20 tables; Postgres-ready via alembic
- FastAPI with legacy API routes (14 endpoints) + new `/api/v1/` JSON layer (10 endpoints)
- **React SPA** (`frontend/`) — 10 pages, Matrix/Dream dual-theme design system, TanStack Query polling, Zustand bot store, Recharts charts, Tweaks panel; built to `ui/static/dist/`, served on root-level browser routes. Regime is surfaced on `/regime`, Overview, Risk, Rankings, and Signals.
- Indicator calculations (EMA, SMA, RSI, MACD, ATR) — deterministic, tested
- Risk engine: drawdown stop, position limits, cash reservation, kill switch, approve mode
- Position sync portfolio attribution: `sync_positions()` rebuilds broker positions with reconciled `portfolio_id`, tags unmatched rows as `unattributed`, logs attribution events, and the Positions page displays attribution/warning badges.
- Sentiment: RSS lexicon + Claude LLM + Claude Routine + mock providers, DB persistence, recency weighting. `claude_routine` reads pre-computed routine output, clamps invalid scores, warns near staleness, returns `status="stale"` without writing new snapshots when output is too old, and degrades gracefully on missing/unparseable files.
- Claude Routine fetch infrastructure: `scripts/routine_fetch_articles.py` is repo-owned but bot-external. It hardcodes routine RSS feeds, filters to articles from the last 12 hours, hashes URLs with SHA-256 first 8 chars, writes `data/_pending_analysis.json`, and updates `data/seen_articles.json`; exit codes: 0=new articles, 1=partial/error, 2=no new articles.
- **3-state regime model** (`trader/regime/`): `RegimeEngine` with 4 pillars (Trend/Breadth/Volatility/Credit Stress), confidence-weighted composite score, asymmetric hysteresis state machine, DB persistence to `regime_snapshots`, restart recovery; `check_regime()` returns `RegimeState` backward-compatible with string comparisons
- Strategy: SPY regime filter, legacy 4-factor `score_symbol()` (still used by equity bot `score_candidate`)
- **7-factor composite scoring** (`trader/composite_scorer/` + `trader/ranking.py`): [0,1] score per symbol from Quality, Value, Momentum, Growth, Sentiment, Technical Structure, and subtractive Risk Penalty; regime weights adapt via `RegimeDetector`; liquidity remains an eligibility gate only
- **FundamentalScorer** (`trader/fundamental_scorer.py`): yfinance parser/scorer with configured metric bounds/pillars, missing-factor redistribution, three-tier cache (memory → `fundamental_snapshots` DB → yfinance), `force_refresh` param
- **Fundamentals manual refresh**: `POST /api/v1/fundamentals/refresh` + Rankings page "Refresh Fundamentals" button (all) and per-row Refresh button (one); CLI `python cli.py fundamentals refresh [--symbol]`
- `equity_eligible` and `options_eligible` eligibility gates
- DTE config unified: canonical `cfg.options.planner_dte_*`; `cfg.ranking.dte_*` kept deprecated
- OptionsSwingBot: full debit spread pipeline (Greeks → gate → pricing → approve/submit)
- EquitySwingBot: ATR-based sizing, sector concentration cap, risk-off cash/defensive modes, portfolio isolation
- Unified CLI with continuous and single-cycle modes
- **Security master** (`trader/securities/`): company-name→ticker deterministic matching
- **Exit management** (`trader/exits.py`): `ExitManager` with 8 equity + 8 options rules; `TradeManagement` table tracks open-position lifecycle; `BaseBot._run_exit_phase()` fires before new entries each cycle
- 359 pytest tests passing (37 regime tests plus routine sentiment coverage)

### Not Yet Tested End-to-End
- Live IBKR connection (requires TWS/Gateway running)
- EquitySwingBot live order placement and fill tracking
- `close_all` control only activates kill switch; actual IBKR position closing not wired
- `data/sp500.csv` exists but is not yet auto-ingested — universe still seeded from `SEED_TICKERS`

### Known Limitations
- No EUR/USD FX conversion — all risk calculations in USD
- EquitySwingBot long-only in v1 (`long_only: true` in config)
- IV Rank requires IBKR historical-volatility entitlement; falls back to "unknown" (gate warns, does not block)
- Fundamental scoring uses yfinance. If yfinance has no usable fields for a symbol, fundamentals are missing and composite weight is redistributed. Once a symbol has data, results are persisted to `fundamental_snapshots` for `fundamentals.ttl_days` (default 7) so restarts don't re-hammer yfinance.
- IBKR market-data subscription required for live data — paper accounts default to delayed (`ibkr.market_data_type: 3`). Set to `1` only when a live US data subscription is active.
- VIX is a CBOE index; `fetch_bars("VIX", ...)` uses `Stock(...)` contract which silently fails on IBKR. The volatility pillar falls back to realized-vol only (reduced confidence). Fix: add `Index("VIX", "CBOE")` contract support to `trader/market_data.py`.
- Position exit logic is fully implemented (`trader/exits.py`); wiring to live IBKR close orders not yet end-to-end tested.
- Config-page sentiment provider switching calls `POST /api/v1/config/sentiment/provider`. If the UI shows 405 Method Not Allowed, confirm the API process has the route loaded (`docker compose exec -T api python -c "from api.main import app; print([r.path for r in app.routes])"`) and restart/rebuild the API container if it is serving an older code version.

## Session Log

- [2026-04-18] Initial build: complete v1 of automated trading bot. Created all 4 packages (common, trader, api, ui), 10 DB tables, IBKR client, indicators, sentiment (RSS + mock), swing strategy with regime filter, risk engine, debit spread execution, scheduler, FastAPI with 14 API endpoints, 8-page dashboard with dark theme and Chart.js charts, run_all script, README, and 18 passing tests. Fixed Starlette 1.0 TemplateResponse API and setuptools flat-layout discovery.
- [2026-04-18] Greeks layer: added `trader/greeks/` sub-package (service, gate, strike_selector, logger). Removed stale flat-module aliases. Delta-based strike selection, IV-adjusted criteria, real bid/ask pricing, 10-check GreeksGate. 26 new tests (44 total).
- [2026-04-19] Multi-bot refactor: `bots/` plugin package (`BaseBot`, `OptionsSwingBot`, `EquitySwingBot`); `execution/` package (`equity_execution.py` with ATR sizing + portfolio isolation, `options_execution.py` shim); unified `cli.py` (Click: sentiment refresh, run, report); `data/sp500.csv`; alembic migration 0004 (`portfolio_id` on orders/positions/trades); `BotsConfig`/`EquityBotConfig`/`OptionsBotConfig` in config; fixed broken greeks flat-module imports in `trader/execution.py`. 94 new tests (138 total, all passing).
- [2026-04-21] Security master + deterministic company→ticker matching: `trader/securities/` package (normalize, master, matcher); 3 new DB tables (`security_master`, `security_alias`, `rss_entity_matches`) + alembic 0005; `SecuritiesConfig`; Claude LLM prompt updated to emit `mentioned_companies` instead of ticker entities; `_build_ticker_results_from_companies()` in claude_provider maps them to verified symbols post-LLM; `securities import/verify/liquidity-refresh` CLI commands; `match-company` debug command; ~220-row `data/us_listed_master.csv` seed + `data/manual_alias_overrides.csv`; 41 new tests (179 total, all passing).
- [2026-04-22] Multi-factor composite scoring: new `trader/scoring.py` with 5 factor functions (sentiment, momentum/trend, risk, liquidity, optionability) + `compute_composite()` with proportional weight redistribution; `rank_symbols()` rewritten to run full factor pipeline per symbol; `RankedSymbol` gains `equity_eligible`/`options_eligible` flags; both bots hard-gate on their eligibility flag; DTE config unified under `cfg.options.planner_dte_*`; `/rankings` dashboard shows expandable `<details>` factor breakdowns; `FundamentalsConfig` added to config; `enter_threshold` default changed 0.25→0.55; 26 new tests in `tests/test_scoring.py` (205 total, all passing).
- [2026-04-23] React SPA + JSON API v1: replaced Jinja2/HTMX/Chart.js dashboard with a full React 18 + Vite + TypeScript + Tailwind SPA (`frontend/`). Added `api/v1/` package (10 FastAPI routers, all under `/api/v1/`; controls return `{ok, bot}`). FastAPI now also serves the SPA at `/app/{path:path}`. SPA features: dark design system with CSS custom property tokens, TanStack Query polling, Zustand bot state, Recharts charts, Tweaks panel (accent hue + density, persisted to localStorage). Legacy Jinja2 routes untouched. 11 vitest smoke tests added; all 205 Python tests still pass.
- [2026-04-27] Added yfinance fundamental scoring: `trader/fundamental_scorer.py`, config-driven metric bounds/pillars in `common/config.py` and `config.example.yaml`, and `compute_fundamentals_factor()` adapter returning composite `value_0_1` plus full 0-100 breakdown. Added/updated tests for yfinance mapping, normalization, pillar fallback, cache expiry, and composite adapter.
- [2026-04-28] Fundamentals persistence + weekly refresh + IBKR delayed-data fix.
  - `FundamentalScorer` now reads/writes `fundamental_snapshots` (TTL = `fundamentals.ttl_days`, default 7) and supports `get_score(symbol, force_refresh=True)`. Diagnosed root cause of "Fundamentals: missing" in the rankings UI: scorer only used in-memory cache, dead DB helpers in `trader/scoring.py:509-554`, and migration `0006_fundamental_snapshots.py` had never been applied.
  - New `trader/fundamentals_refresh.py::refresh_fundamentals()` shared helper. `Scheduler` calls it once per week (`fundamentals.refresh_days`, default 7). New `POST /api/v1/fundamentals/refresh[?symbol=]` (in `api/v1/fundamentals.py`) + new `python cli.py fundamentals refresh [--symbol]`. Rankings page got a "Refresh Fundamentals" button (all) and per-row Refresh button via `useMutation` + `invalidateQueries(['rankings'])`. The API route lazily imports the helper so `ModuleNotFoundError: yfinance` cannot take down `uvicorn`; rebuild the Docker image after dependency changes.
  - IBKR noise fix: added `ibkr.market_data_type: int = 3` config; `IBKRClient.connect()` now calls `reqMarketDataType(...)` to enable delayed quotes; `_fetch_underlying_price` reads `delayedLast`/`delayedClose`; `_select_strikes_in_range` returns `[]` (with warning) when underlying is unavailable instead of returning the entire chain (which had been triggering Error 200 floods on non-existent strikes).
  - Test isolation: new autouse fixture `_isolate_fundamental_caches` in `tests/conftest.py` clears `_shared_cache` + `fundamental_snapshots` between tests. 5 new fundamental tests + 2 new refresh-helper tests. 235 Python tests + 14 vitest smokes all green.
- [2026-04-30] Momentum fix + company names in UI.
  - **Momentum fix**: `trader/market_data.py` `_TF_MAP["1D"]` changed from `"60 D"` to `"1 Y"` — prior 60-day window (~42 trading bars) was below the 63-bar minimum required by `compute_momentum_trend_factor()`, causing the factor to always return `status: "missing"` and have its weight redistributed away.
  - **Company names**: all six `/api/v1/` symbol-row endpoints now look up `SecurityMaster.name` and include it as `name` in responses (`PositionOut`, `OrderOut`, `FillOut`, `SignalOut`, `RankingRow`, `PlanRow` updated). `RankedSymbol` gains `name` field populated from `UniverseItem.name`. New `frontend/src/lib/cells.tsx::symbolCell()` renders "Apple [AAPL]" across all five pages (Overview, Positions, Orders, Signals, Rankings); column headers renamed "Symbol" → "Company".
- [2026-04-30] 7-factor composite scoring and dashboard display.
  - Added `trader/composite_scorer/` with Quality, Value, Momentum, Growth, Sentiment, Technical, subtractive Risk, normalization, regime smoothing, `CompositeScorer`, `CachedFactor`, result dataclasses, and `config/scoring_config.yaml`.
  - Added `common.config.CompositeScoringConfig` and wired `trader/ranking.py` to persist authoritative `components_json.composite_7factor` while reusing existing factor helpers as adapter inputs.
  - Fixed `/api/v1/rankings` and legacy `/api/rankings/latest` to preserve 7-factor rows; Rankings UI now renders the seven factors and subtractive risk formula. Verified with 242 pytest tests, Rankings vitest, frontend build, Docker rebuild/restart, and live `/api/v1/rankings` response.
- [2026-04-30] Removed legacy scoring fallback.
  - `trader/ranking.py` now always scores with `CompositeScorer`; `scoring.enabled` was removed from config.
  - Ranking API normalization and the Rankings full-list breakdown no longer recompute or render the old score formula. Rows without `components_json.composite_7factor` keep their stored score and show the composite payload as missing.
- [2026-04-30] Fixed ValueFactor always returning 0 for expensive-but-quality stocks (e.g. AVGO).
  - Root cause: `ValueFactor.calculate()` read `data["fundamental_metrics"]` but `stock_data` only had `"fundamentals_factor"` → all sub-scores were None → fell back to absolute pillar score → high-multiple tech always clamped to 0.
  - `FundamentalScorer._parse_value_metrics(info)` added to extract raw yfinance fields (`enterprise_to_ebitda`, `free_cash_flow_ttm`, `market_cap`, `forward_pe`, `eps_growth_next_year` in %, `price_to_book`, `price_to_sales`, `sector`). `_fetch_ratios()` now returns `(ratios, value_metrics)` tuple; `FundamentalResult` gains `value_metrics: dict` field stored through all cache paths.
  - `compute_fundamentals_factor()` exposes `"fundamental_metrics"` at top level. `rank_symbols()` pre-fetches all fund_factors, computes per-sector median multiples via `_compute_sector_medians()`, attaches them to each symbol's `fundamental_metrics`, and passes them through `_score_7factor()` into `stock_data["fundamental_metrics"]`. ValueFactor now scores FCF yield, PEG, and sector-relative discounts. 240 tests passing.
- [2026-05-01] Enhanced market regime model: replaced binary `check_regime` with a full 3-state engine.
  - New `trader/regime/` package: `models.py` (`RegimeLevel` enum, `PillarScore`, `RegimeState` with backward-compat `__eq__`/`__str__`), `indicators.py` (4 pillar functions), `state_machine.py` (asymmetric hysteresis — 2 confirmations to degrade, 3 to recover, no state skipping), `engine.py` (`RegimeEngine` singleton, confidence-weighted composite score, DB persistence).
  - New `RegimeSnapshot` ORM table (20th table); state persisted every cycle for restart recovery.
  - `common/config.py`: renamed old `RegimeConfig` → `RegimeStrategyConfig`; added 12 new Pydantic models under new top-level `RegimeConfig` (weights, per-pillar params, thresholds, hysteresis, per-state effects, fallback). `AppConfig` gains `regime: RegimeConfig`.
  - `check_regime()` now returns `RegimeState`; downstream bots receive both `context.regime: str` (backward compat) and `context.regime_state: RegimeState`. `EquitySwingBot` applies `sizing_factor` + `score_threshold_adjustment`; `OptionsSwingBot` blocks entries on `allows_new_options_entries=False`.
  - New `api/routes/regime.py`: `GET /api/v1/regime/current` + `/history?days=30`.
  - `tests/test_regime.py`: 37 tests across 6 classes covering all pillars, state machine, backward compat, composite scoring. Total: 331 passing.
- [2026-05-01] Frontend regime integration: added typed `api.getRegimeCurrent/getRegimeHistory`, `/regime` SPA page, sidebar nav, shared `RegimeSummaryCard`, and current-regime indicators on Overview, Risk, Rankings, and Signals. `/api/v1/regime/current` now returns configured per-state downstream effects for UI display.
- [2026-05-01] Position sync portfolio reconciliation: `trader/sync.py::sync_positions()` now attributes IBKR positions via `TradeManagement` then recent orders, falls back to `unattributed`, skips zero quantity rows, and logs `sync_positions_reconciled` / `sync_unattributed_positions`. Risk caps and sector checks count unattributed positions conservatively; options count distinct option/combo symbols. `/api/v1/positions` and the Positions page now expose/show `portfolio_id`. Added `tests/test_sync.py`.
- [2026-05-01] Dashboard dual-theme toggle: added topbar `ThemeSwitch`, `useTheme()` persistence, and scoped `frontend/src/theme-dream.css`. Matrix remains the no-attribute default; Dream mode uses `<html data-theme="dream">`, per-route mantras, fixed aura/mandala backgrounds, and a JS `.dream-particles` layer that is removed on switch-back. Dream readability was tuned upward after review. Verified with frontend build and 14 Vitest tests.
- [2026-05-04] Claude Routine sentiment provider: added `trader/sentiment/routine_provider.py`, `SentimentRoutineConfig`, factory stale handling, routine contract files (`data/sentiment_output.json`, `data/seen_articles.json`, `config/routine_rss_feeds.txt`), and tests. Config page now has a runtime sentiment modality switch (RSS / Claude LLM / Routine / Mock). Verified with 359 pytest tests, Config vitest, and TypeScript build.
- [2026-05-04] Claude Routine fetch infrastructure: added `scripts/routine_fetch_articles.py` plus `scripts/requirements_routine.txt`; `_pending_analysis.json` is gitignored. The script is for Anthropic's routine environment only and writes pending article JSON for Claude analysis before the routine commits `sentiment_output.json`.
