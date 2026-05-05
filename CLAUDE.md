# Market AI

## Project Overview

Automated swing trading bot for Interactive Brokers (IBKR) targeting US stocks/ETFs. Two instruments: **debit spread options** (`OptionsSwingBot`) and **equity shares** (`EquitySwingBot`), runnable independently or together. Paper-trading-first. Tech stack: Python 3.12, ib_insync, FastAPI, SQLAlchemy 2.0, SQLite/Postgres, React 18 + Vite + Tailwind SPA, Click CLI. User base currency EUR; IBKR cash account (no margin).

## Architecture

### Package Structure

Flat layout (no `src/`), editable install via `pyproject.toml`.

| Package | Purpose |
|---------|--------|
| `common/` | Config, DB engine, ORM models (20 tables), Pydantic schemas, logging, time utils |
| `trader/` | Trading engine: IBKR client, market data, indicators, sentiment, strategy, risk, execution, scheduler, `greeks/`, `regime/`, `exits.py`, `composite_scorer/`, `securities/` |
| `bots/` | `BaseBot` ABC + `OptionsSwingBot` + `EquitySwingBot` |
| `execution/` | Order routing: `equity_execution.py` (STK) + `options_execution.py` (shim) |
| `api/` | FastAPI app + `api/v1/` versioned JSON endpoints (12 routers, all `/api/v1/`) |
| `frontend/` | React 18 + Vite + TS + Tailwind SPA (build -> `ui/static/dist/`) |
| `scripts/` | `init_db.py`, `run_all.py`, `routine_fetch_articles.py` |
| `data/` | `sp500.csv`, `us_listed_master.csv`, `manual_alias_overrides.csv`, sentiment contract files |
| `cli.py` | Unified Click CLI |

### Key Design Decisions

- **3-state regime model** -- `trader/regime/` evaluates 4 pillars (Trend 30%, Breadth 25%, Volatility 25%, Credit Stress 20%) -> 0-100 composite -> `RegimeStateMachine` with asymmetric hysteresis (2 to degrade, 3 to recover, no skip). States: `risk_on` (full sizing) -> `risk_reduced` (half sizing, no new options, +0.10 threshold) -> `risk_off` (no new entries). Falls back to legacy SPY check when `cfg.regime.enabled=False`. `RegimeState` supports `== "risk_on"` string comparison.
- **Debit spreads only (OptionsSwingBot)** -- bull call + bear put spreads. No naked shorts. Max loss = net debit.
- **EquitySwingBot long-only (v1)** -- ATR-based sizing: `stop = entry - (atr_stop_multiplier x stop_tightening_factor) x ATR(14)`; `shares = floor(nav x risk_pct / stop_distance) x sizing_factor`. Sizing/tightening factors from `regime_state`. Capped to cash and sector concentration.
- **6-factor composite scoring** -- `trader/composite_scorer/` computes Technical, Momentum, Sentiment, Quality, Growth, subtractive Risk Penalty. Regime-adaptive dual weight profiles selected per cycle based on regime level:
    - `aggressive_swing` (risk_on): Technical 0.30, Momentum 0.25, Sentiment 0.20, Quality 0.05, Growth 0.05, Risk Penalty 0.15
    - `defensive_swing` (risk_reduced/risk_off): Technical 0.25, Momentum 0.20, Sentiment 0.15, Quality 0.10, Growth 0.10, Risk Penalty 0.20
    - Formula: `(tech x W) + (mom x W) + (sent x W) + (qual x W) + (grow x W) - (risk x W)`, clamped [0,1]. Practical max ~0.85.
    - Profiles defined in `config.yaml` under `ranking.weight_profiles`. Weights validated to sum to 1.0.
    - `components_json.composite_6factor` is authoritative; includes `weight_profile` used.
- **Entry/bias thresholds (calibrated to 0.85 max)** -- equity entry: >= 0.45; bullish bias: >= 0.48; bearish bias: <= 0.35; score degradation exit: 0.32.
- **Portfolio isolation** -- `Order`, `Position`, `Trade` carry `portfolio_id` (`options_swing`/`equity_swing`/`unattributed`). `sync_positions()` reconciles via `TradeManagement` -> recent orders -> `unattributed`.
- **Bot plugin pattern** -- `BaseBot.run()`: regime -> resolve weight profile -> universe -> rank(profile) -> exit phase -> score -> select -> execute. `BotContext` carries `regime: str` + `regime_state: RegimeState`.
- **Eligibility gates** -- `equity_eligible` requires all factor scores present + liquidity + contract verified. `options_eligible` from `SecurityMaster`. Missing scores -> `eligible=False`, still listed.
- **Sentiment** -- pluggable `SentimentProvider` ABC. Providers: `rss_lexicon`, `claude_llm`, `claude_routine`, `mock`. `claude_routine` reads pre-computed `data/sentiment_output.json` (no API calls).
- **Exit management** -- `trader/exits.py` `ExitManager` with 8 equity + 8 options rules, priority-ordered. `TradeManagement` table tracks open positions. `BaseBot._run_exit_phase()` fires before new entries.
- **yfinance fundamentals** -- Three-tier cache: in-process (24h) -> DB `fundamental_snapshots` (7d) -> yfinance fetch. Weekly refresh via scheduler/API/CLI. Feeds Quality and Growth factors.
- **Cash reservation** -- max loss reserved before order; blocked if insufficient.
- **Approve mode ON by default** -- signals saved as `pending_approval`.
- **No paid APIs** -- yfinance + RSS + IBKR data only.

### Patterns & Conventions

- Config: `config.yaml` -> `config.example.yaml` -> defaults. Pydantic-validated. `WeightProfile` model validates sum to 1.0. Reset with `load_config(reload=True)`.
- DB: `common/db.get_db()` context manager (auto-commit/rollback). Tests must reset `common.db._engine = None`.
- `intent_id` prevents duplicate order submissions: `{symbol}_{direction}_{date}_{uuid8}`.
- Risk checks return `(bool, str)` -- `(allowed, reason)`.
- Greeks in `trader/greeks/` sub-package; old flat aliases (`trader.greeks_gate` etc.) no longer exist.
- IBKR delayed data: `reqMarketDataType(3)` by default; reads `delayedLast`/`delayedClose`.
- 1-year bar history (`_TF_MAP["1D"]` = `"1 Y"`) for momentum (needs 63+ bars).

## File Map

### Config & DB
- `common/config.py` -- all Pydantic models (incl. `RegimeConfig` with 12 sub-models, `WeightProfile`, `RankingConfig.weight_profiles`)
- `common/models.py` -- 21 ORM tables (incl. `RefreshLog` for manual-refresh audit trail)
- `common/schema.py` -- API response schemas

### Scoring Control (`api/v1/scoring.py`)
- `GET /scoring/weights` — both weight profiles + active one (driven by current regime snapshot)
- `GET /scoring/docs` — per-factor documentation cards with live weights
- `GET /scoring/refresh-history?limit=N[&action=...]` — last N `RefreshLog` entries, newest first
- `api/v1/_refresh_log.py` -- `log_refresh(action, status, duration_ms, message)` called by rankings, sentiment, fundamentals refresh endpoints; swallows errors

### Regime (`trader/regime/`)
- `models.py` -> `RegimeLevel`, `PillarScore`, `RegimeState`
- `indicators.py` -> 4 pillar compute functions
- `state_machine.py` -> `RegimeStateMachine`
- `engine.py` -> `RegimeEngine` (orchestrator, singleton in `trader/strategy.py`)
- API: `api/routes/regime.py` (`/api/v1/regime/current`, `/history?days=30`)

### Core Trading
- `trader/ibkr_client.py` -- singleton via `get_ibkr_client()`
- `trader/indicators.py` -- EMA, SMA, RSI, MACD, ATR
- `trader/market_data.py` -- `fetch_bars()`, in-memory cache
- `trader/strategy.py` -- `check_regime()` (delegates to `RegimeEngine`), `score_symbol()`
- `trader/composite_scorer/` -- 6-factor scorer (Technical, Momentum, Sentiment, Quality, Growth, Risk Penalty), regime-adaptive weight profile selection
- `trader/scoring.py` -- factor adapter functions for ranking
- `trader/ranking.py` -- `rank_symbols(universe, weight_profile)`, `select_candidates()`, `_compute_sector_medians()`
- `trader/fundamental_scorer.py` -- yfinance parser, three-tier cache, feeds Quality + Growth
- `trader/fundamentals_refresh.py` -- `refresh_fundamentals()` shared helper
- `trader/risk.py` -- drawdown, position limits, cash reservation, kill switch
- `trader/sync.py` -- `sync_positions()` with portfolio attribution
- `trader/execution.py` -- options debit spread orders
- `trader/options_planner.py` -- `plan_trade()` (planning only, writes TradePlan)
- `trader/universe.py` -- `seed_universe()`, `get_verified_universe()`
- `trader/exits.py` -- `ExitManager`, `TradeManagement` lifecycle
- `trader/scheduler.py` -- 10s heartbeat, watches local sentiment file

### Greeks (`trader/greeks/`)
- `service.py` -> `GreeksService`, IV Rank
- `strike_selector.py` -> delta-based selection
- `gate.py` -> `GreeksGate` (10 checks)
- `logger.py` -> `GreeksLogger`

### Bots & Execution
- `bots/base_bot.py` -- `BaseBot` ABC, `BotContext`, `TradeIntent`
- `bots/options_swing_bot.py` -- blocks when `allows_new_options_entries=False`
- `bots/equity_swing_bot.py` -- ATR sizing, applies `sizing_factor`/`score_threshold_adjustment`, entry threshold 0.45
- `execution/equity_execution.py` -- `place_equity_order()`
- `execution/options_execution.py` -- `TradeIntent` -> `SignalIntent` shim

### Sentiment (`trader/sentiment/`)
- `factory.py` -- `refresh_and_store()`
- `scoring.py` -- `get_latest_market/sector/ticker_score()`
- Providers: `rss_provider.py`, `claude_provider.py`, `routine_provider.py`, `mock_provider.py`
- `budget.py` -- hard EUR10/mo cap
- Contract files: `data/sentiment_output.json`, `data/seen_articles.json`
- Fetch script: `scripts/routine_fetch_articles.py` (Anthropic routine only)

### Security Master (`trader/securities/`)
- `normalize.py` -- `normalize_company_name()`, `generate_aliases()`
- `master.py` -- import, verify, options eligibility, liquidity refresh
- `matcher.py` -- `match_companies_to_symbols()`
- Seed: `data/us_listed_master.csv`, `data/manual_alias_overrides.csv`

### Frontend (`frontend/`)
- Vite 5, React 18, TS strict, Tailwind 3, TanStack Query 5, Zustand 4, Recharts 2
- Pages: Overview, Positions, Orders, Signals, Rankings, **Score Control**, Sentiment, Regime, Risk, Controls, Config
- `src/lib/api.ts` -- typed fetch wrappers
- `src/lib/cells.tsx` -- `symbolCell()` renders "Apple [AAPL]"
- `src/store/botStore.ts` -- Zustand state from control responses
- `src/styles/globals.css` -- CSS custom props design tokens
- `src/hooks/useTheme.ts` -- Matrix/Dream dual-theme toggle
- `src/theme-dream.css` -- dream overrides under `[data-theme="dream"]`

## Commands

```bash
pip install -e ".[dev]"          # Install with dev deps
python scripts/init_db.py        # Create/seed DB
alembic upgrade head             # Run migrations
python scripts/run_all.py        # Start API (8000) + trader
python -m pytest tests/ -v       # 379 tests
uvicorn api.main:app --reload    # API only
python trader/main.py            # Trader only (continuous)

# Frontend (from frontend/)
pnpm dev                         # localhost:5173, proxies /api to :8000
pnpm build                       # Build -> ui/static/dist/
pnpm test                        # 23 vitest tests

# CLI
python cli.py fundamentals refresh [--symbol AAPL]
python cli.py sentiment refresh [--source rss_lexicon|claude_llm|claude_routine|mock] [--dry-run]
python cli.py run options_swing --mode paper --dry-run
python cli.py run equity_swing  --mode paper --approve
python cli.py run all           --mode live  --once
python cli.py report last-run   --bot equity_swing [--json-out]
python cli.py securities import [--file data/us_listed_master.csv] [--verify-ibkr] [--load-overrides]
python cli.py securities verify  --symbol MOH --options-check
python cli.py securities verify  --all
python cli.py securities liquidity-refresh [--symbol AAPL]
python cli.py match-company --text "Today Molina Healthcare made 5 billion"
python cli.py universe import <csv> --source <name>
```

## Session Log

- [2026-05-05] Added Score Control Panel page (`/score-control`, sidebar under Model). Three tabs: Controls (refresh triggers for rankings/sentiment/fundamentals with status + history), Documentation (live per-factor cards reading weights from config), Rankings (full table, sortable, searchable, heatmap cells, CSV export). Backend: `RefreshLog` table + migration `0008`, `api/v1/scoring.py` (3 endpoints), `_refresh_log.py` audit helper wired into all 3 refresh endpoints. `api/v1/rankings.py` normalisation upgraded with `_ensure_composite_6factor` backfill for legacy rows.

## Known Limitations

- No EUR/USD FX conversion -- all risk calcs in USD
- EquitySwingBot long-only in v1
- IV Rank requires IBKR historical-vol entitlement; falls back to "unknown" (warns, doesn't block)
- yfinance may have no data for some symbols -> weight redistributed
- Live IBKR connection not end-to-end tested (requires TWS/Gateway)
- `close_all` only activates kill switch; actual IBKR position closing not wired
- VIX fetch uses `Stock(...)` which fails on IBKR; volatility pillar falls back to realized-vol only. Fix: add `Index("VIX", "CBOE")` support.
- Paper accounts default to delayed data (`ibkr.market_data_type: 3`); set to `1` only with live subscription.
- Exit logic implemented but live IBKR close orders not end-to-end tested.
