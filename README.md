# Market AI — Automated Swing Trading Bot

Automated swing trading system for Interactive Brokers — supports both paper and live trading modes.  
Runs two independent bots powered by **LLM-driven sentiment analysis** (Claude AI), sharing market intelligence while trading different instruments:

| Bot | Trades | Strategy |
|-----|--------|----------|
| **OptionsSwingBot** | Debit spread options (bull call / bear put) | Greek-gated, delta-based strike selection |
| **EquitySwingBot** | Stock shares (long-only v1) | ATR-based stop sizing, sector concentration cap |

Both bots can run together or independently. Each has its own position namespace — they never share portfolio state.

---

## Features

- **7-factor composite scoring** — [0,1] score per symbol from Quality, Value, Momentum, Growth, Sentiment, Technical Structure, and subtractive Risk Penalty. Weights adapt by market regime from `trader/composite_scorer/config/scoring_config.yaml`; liquidity is an eligibility gate only.
- **yfinance fundamental score** — optional scorer normalizes valuation, profitability, growth, and financial-health ratios to a 0-100 breakdown, then feeds Quality/Value/Growth adapters when richer statement data is unavailable. Persisted to `fundamental_snapshots` (default 7-day TTL) and refreshed weekly by the scheduler. Manual refresh via dashboard button (all symbols or per-row) or `python cli.py fundamentals refresh`.
- **Eligibility gates** — `equity_eligible` (liquidity + IBKR-verified contract) and `options_eligible` (from `SecurityMaster`; safe-by-default=False). Each bot hard-blocks symbols that fail its gate.
- **Swing strategy** (2–20 day holds) — technical analysis (EMA, SMA, RSI, MACD, ATR) + market/sector/ticker sentiment
- **Defined risk only** — options bot trades debit spreads exclusively (max loss = net debit paid)
- **ATR-based equity sizing** — `shares = floor(nav × risk% / (atr × multiplier))`; automatically caps to available cash and sector concentration limit
- **Portfolio isolation** — `portfolio_id` on all orders/positions/trades; each bot enforces its own limits
- **Approve mode** — signals and trade plans saved to DB but no orders submitted until toggled OFF (default ON)
- **Risk engine** — drawdown stop, per-bot position limits, cash reservation, kill switch
- **Greeks gate** — 10-check filter before any options order (IV rank, delta range, theta/delta ratio, vega, gamma-near-expiry, liquidity, pricing ROC, composite score)
- **LLM-powered sentiment** — Claude AI scores each news item with per-item sentiment, sector/ticker tagging, and a strict €10/month budget cap; RSS lexicon available as a lightweight fallback
- **React SPA dashboard** — dark-mode single-page app (React 18 + Vite + Tailwind) served on root-level browser routes, with TanStack Query live polling, Recharts equity/drawdown charts, per-symbol factor breakdowns, and a Tweaks panel for accent colour + layout density
- **Unified CLI** — `python cli.py run [options_swing|equity_swing|all]`

---

## Quick Start (Docker on Windows)

The fastest path — you only need Docker Desktop and IB Gateway installed on the host.

### 1. Start IB Gateway (paper) on the Windows host

- Log in with your **paper** credentials.
- Configure → API → Settings:
  - Enable ActiveX and Socket Clients
  - Socket port **7497** (TWS paper) or **4002** (Gateway paper)
  - Uncheck "Read-Only API"
  - Trusted IPs: add `127.0.0.1`; leave "Allow connections from localhost only" OFF so the container can reach it
- Allow the TWS/Gateway binary through Windows Defender Firewall (private network).

### 2. Configure env

```powershell
copy .env.example .env
# Optional: edit POSTGRES_PASSWORD, IB_PORT, IB_CLIENT_ID, MODE
```

All runtime knobs (`DATABASE_URL`, `MODE`, `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `APPROVE_MODE_DEFAULT`, `SENTIMENT_PROVIDER`) override `config.yaml` when set. A `config.yaml` file is optional — defaults from `config.example.yaml` plus env vars are sufficient.

### 3. Build & run

```bash
# First time (or after dependency changes):
docker compose up -d postgres
docker compose up --build -d api trader

# Subsequent runs:
docker compose up -d
```

Open **http://localhost:8000**. The API container auto-applies Alembic migrations and seeds `bot_state` on first boot.

> ⚠️ **Rebuild after adding/upgrading dependencies.** The repo is bind-mounted into the containers (see `docker-compose.yml`), so plain code changes are picked up live — but Python packages live inside the image. After any `pyproject.toml` change (e.g. the `yfinance` dependency added for fundamental scoring), run `docker compose up --build -d api trader` once to refresh the image. The fundamentals refresh endpoint lazily imports `yfinance`, so a missing package returns `503` from `/api/v1/fundamentals/refresh` instead of crashing API startup.

### 4. Verify

```bash
curl http://localhost:8000/health          # -> {"status":"ok"}

docker compose exec postgres psql -U market_ai -d market_ai \
  -c "SELECT last_heartbeat FROM bot_state;"

docker compose ps
```

### 5. Optional — Adminer (DB inspector)

```bash
docker compose --profile debug up -d adminer
# http://localhost:8081 — system: PostgreSQL, server: postgres, user/db: market_ai
```

### Troubleshooting

**Trader can't connect to IB Gateway**
- Confirm IB Gateway is running and logged in.
- From the container: `docker compose exec trader python -c "import socket; print(socket.create_connection(('host.docker.internal', 7497), timeout=3))"`.
- The trader does **not** crash if IB is unreachable — it runs in offline mode (sentiment + ranking still run; order submission is skipped).

**DB connection fails**
- `docker compose ps postgres` — state must be `healthy`.
- Confirm `DATABASE_URL` uses host `postgres` (the compose service name), not `localhost`.
- If you changed `POSTGRES_PASSWORD` after the volume was created: `docker compose down -v` (destroys data) then re-up.

---

## Bare-metal Setup (no Docker)

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 16+ **or** SQLite (dev/test only)
- Interactive Brokers TWS or IB Gateway running

### 2. Install

```bash
pip install -e ".[dev]"
```

### 3. Configure

```bash
cp config.example.yaml config.yaml
# Edit IBKR host/port, DB path/URL, enable/disable bots
cp .env.example .env
# Set DATABASE_URL (or leave blank for SQLite fallback)
```

DB URL resolution order (first wins):
1. `DATABASE_URL` environment variable
2. `db.url` in `config.yaml`
3. SQLite via `db.path` in `config.yaml` (dev/test only, default `app.db`)

### 4. IBKR Paper Trading Setup

**TWS (Trader Workstation):**
1. Log in with paper credentials (username like `DU12345`)
2. Edit → Global Configuration → API → Settings:
   - Enable ActiveX and Socket Clients
   - Socket port: **7497** (paper) or **7496** (live)
   - Uncheck "Read-Only API"

**IB Gateway (headless, recommended):**
- Default paper port: **4002** (live: 4001)
- Update `config.yaml`: `ibkr.port: 4002`

### 5. Apply schema & run

```bash
export $(grep -v '^#' .env | xargs)   # load DATABASE_URL (Linux/macOS)

python scripts/init_db.py              # runs alembic upgrade head + seeds bot_state

# Start everything
python scripts/run_all.py              # API on :8000 + trader worker

# Or separately:
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload   # Terminal 1
python trader/main.py                                         # Terminal 2
```

---

## CLI Reference

The unified CLI (`cli.py`) is the recommended entry point for bot operations:

```bash
# Refresh fundamentals (yfinance)
python cli.py fundamentals refresh                         # every symbol in the verified universe
python cli.py fundamentals refresh --symbol AAPL           # one symbol only

# Refresh sentiment data
python cli.py sentiment refresh
python cli.py sentiment refresh --source claude_llm --dry-run

# Run a single bot (continuous, Ctrl-C to stop)
python cli.py run options_swing --mode paper --approve
python cli.py run equity_swing  --mode paper --dry-run

# Run both bots together
python cli.py run all --mode paper --dry-run

# Single cycle then exit (useful for cron / scripted runs)
python cli.py run all --mode paper --once

# Live mode (real orders — use with caution)
python cli.py run options_swing --mode live --no-approve

# Reports
python cli.py report last-run --bot equity_swing
python cli.py report last-run --bot all --json-out

# Security master (company → ticker matching)
python cli.py securities import                              # import CSV + generate aliases
python cli.py securities import --verify-ibkr               # also verify each symbol with IBKR
python cli.py securities import --load-overrides            # load manual_alias_overrides.csv
python cli.py securities verify --symbol MOH --options-check
python cli.py securities verify --all                       # verify all active securities
python cli.py securities liquidity-refresh --symbol AAPL    # refresh avg_dollar_volume_20d

# Debug: resolve a company name or text to a ticker
python cli.py match-company --text "Molina Healthcare beat earnings"
python cli.py match-company --companies "Molina Healthcare,UnitedHealth"
```

Key flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--mode paper\|live` | `paper` | IBKR trading mode |
| `--dry-run` | off | Plan trades, log actions, never submit orders |
| `--approve / --no-approve` | `--approve` | Queue orders for manual approval vs auto-submit |
| `--once` | off | Run one cycle and exit (vs continuous loop) |
| `--refresh-sentiment / --no-refresh-sentiment` | on | Refresh sentiment before each cycle |

The continuous mode runs at the interval set by `scheduling.signal_eval_minutes` (default: 15 min).

---

## Configuration

All settings live in `config.yaml` (or `config.example.yaml` as the reference). Key sections:

```yaml
mode: PAPER          # PAPER or LIVE

bots:
  options_swing:
    enabled: true

  equity_swing:
    enabled: true
    long_only: true              # v1: no shorting
    long_entry_threshold: 0.55   # enter long when score >= this
    exit_threshold: 0.45         # exit when score drops below this
    max_positions: 5             # equity_swing portfolio cap
    risk_per_trade_pct: 1.0      # % of NAV risked per position
    atr_stop_multiplier: 2.0     # stop = entry - k * ATR(14)
    max_sector_concentration: 0.30
    risk_off_mode: "cash"        # "cash" or "defensive"

ranking:
  # Score threshold: >= enter_threshold → bullish; <= (1-enter_threshold) → bearish
  enter_threshold: 0.55
  min_dollar_volume: 20_000_000  # liquidity gate for equity_eligible

scoring:
  enabled: true
  config_path: "trader/composite_scorer/config/scoring_config.yaml"
  use_cache: false

fundamentals:
  enabled: true                  # set false to disable yfinance fundamental scoring
  ttl_days: 7                    # DB cache TTL (rows older than this are recomputed)
  cache_ttl_hours: 24            # in-process cache TTL (intra-run dedup)
  refresh_days: 7                # scheduler bulk-refresh cadence (every universe symbol)
  provider: "yfinance"
  request_timeout_seconds: 15
  neutral_score: 50              # internal no-data placeholder; not counted in composite
  pillars:
    valuation:
      weight: 0.25
      metrics: [PEEXCLXOR, PRICE2BK, EVCUR2EBITDA, PRICE2SALESTTM]
    profitability:
      weight: 0.30
      metrics: [TTMROEPCT, TTMROAPCT, TTMGROSMGN, TTMNPMGN]
    growth:
      weight: 0.25
      metrics: [REVCHNGYR, EPSCHNGYR, REVTRENDGR]
    financial_health:
      weight: 0.20
      metrics: [QCURRATIO, QQUICKRATI, QTOTD2EQ]

risk:
  max_drawdown_pct: 50
  max_risk_per_trade_pct: 5      # used by options bot
  max_positions: 5               # options bot position cap
  require_positive_cash: true

sentiment:
  provider: "rss_lexicon"        # or "claude_llm"
  refresh_minutes: 60

dry_run: false                   # true = never submit orders (global override)
```

Full reference: `config.example.yaml`.

---

## Architecture

```
cli.py  ──────────────────────────────────────────────────────────┐
                                                                   │
scripts/run_all.py (legacy launcher)                               │
  ├── uvicorn api.main:app   (FastAPI + dashboard, port 8000)     │
  └── trader/main.py         (scheduler, continuous)              │
                                                                   ▼
                     ┌──────────────────────────────────────────────┐
                     │              Shared Core (trader/)           │
                     │  check_regime() · get_verified_universe()   │
                     │  rank_symbols()  · score_symbol()           │
                     │  sentiment pipeline · risk engine           │
                     └────────────┬─────────────────┬─────────────┘
                                  │                 │
                     ┌────────────▼───┐   ┌─────────▼──────────┐
                     │ OptionsSwingBot│   │  EquitySwingBot     │
                     │ bots/options_  │   │  bots/equity_swing_ │
                     │ swing_bot.py   │   │  bot.py             │
                     └────────┬───────┘   └──────────┬──────────┘
                              │                      │
                     ┌────────▼───────┐   ┌──────────▼──────────┐
                     │options_execution│  │ equity_execution.py  │
                     │ plan_trade()   │   │  place_equity_order()│
                     │ execute_signal()│  │  portfolio_id=       │
                     │ (BAG combo)    │   │  "equity_swing"      │
                     └────────────────┘   └─────────────────────┘
```

### Package Layout

```
market_AI/
  cli.py                  # Unified Click CLI entry point
  config.example.yaml     # Full configuration reference
  data/
    sp500.csv                    # ~180 S&P 500 symbols with sectors (reference only)
    us_listed_master.csv         # ~220 major US stocks — seed for security_master table
    manual_alias_overrides.csv   # Priority-1 manual company→ticker aliases (~80 rows)
  common/                 # Shared: config, DB engine, ORM models, schemas, logging
  trader/
    greeks/               # Greek fetching, IV rank, strike selection, gate, logger
    sentiment/            # RSS lexicon + Claude LLM + mock providers
    securities/           # Company→ticker matching: normalize, master import, alias matcher
    composite_scorer/     # 7-factor scorer: factors, regime detector, normalizer, YAML weights
    ibkr_client.py        # IBKR connection (singleton)
    market_data.py        # Historical bars with in-memory cache
    indicators.py         # EMA, SMA, RSI, MACD, ATR
    universe.py           # Ticker universe: seed, verify, get_verified_universe()
    scoring.py            # Legacy scoring adapters reused by 7-factor ranking and fallback
    fundamental_scorer.py # yfinance parser/scorer; 3-tier cache (memory → fundamental_snapshots DB → yfinance)
    fundamentals_refresh.py # Shared refresh helper used by scheduler + API + CLI
    ranking.py            # rank_symbols() - runs 7-factor pipeline per symbol, sets equity_eligible/options_eligible
    strategy.py           # Regime check, score_symbol(), generate_signals()
    risk.py               # Risk engine: drawdown, position limits, cash reservation
    execution.py          # Debit spread order construction + submit
    options_planner.py    # Options trade planning (no submission)
    scheduler.py          # Main trading loop (heartbeat 10s)
    sync.py               # IBKR → DB account/position/order sync
  bots/
    base_bot.py           # BaseBot ABC + Candidate/ScoreBreakdown/TradeIntent dataclasses
    options_swing_bot.py  # OptionsSwingBot plugin
    equity_swing_bot.py   # EquitySwingBot plugin (ATR sizing, sector cap)
  execution/
    equity_execution.py   # Stock order placement (portfolio_id="equity_swing")
    options_execution.py  # Shim: TradeIntent → SignalIntent → execute_signal()
  api/
    main.py               # FastAPI app; serves React SPA browser routes
    routes/               # Legacy routes: health, state, controls, signals, sentiment, trades, rankings
    v1/                   # JSON API v1: overview, positions, orders, fills, signals, rankings,
                          #   trade-plans, sentiment, risk, controls, config, fundamentals
  ui/
    static/
      dist/               # Built React SPA output (index.html + assets/)
  frontend/               # React SPA source
    package.json          # pnpm workspace (React 18, Vite 5, Tailwind 3, TanStack Query 5, Zustand 4)
    vite.config.ts        # outDir → ../ui/static/dist; dev proxy /api → :8000
    src/
      main.tsx / App.tsx  # Entry + React Router
      types/api.ts        # TypeScript interfaces matching Python Pydantic models
      lib/api.ts          # Typed fetch wrappers
      lib/formatters.ts   # fmtMoney, fmtCompact, fmtPct, fmtSign, fmtTs
      store/botStore.ts   # Zustand bot state store
      styles/globals.css  # Design tokens (CSS custom props) + component classes
      components/         # AppShell, Sidebar, Topbar, Card, KPI, Badge, Sparkline,
                          #   LineChart, Donut, ScoreBar, DataTable, Toggle, Button,
                          #   SegmentedControl, TweaksPanel
      pages/              # 9 pages (Overview … Config)
      test/               # vitest setup + 11 smoke tests (one per page)
  scripts/
    init_db.py            # Run alembic + seed bot_state
    run_all.py            # Launch API + trader as subprocesses
  tests/                  # 205 pytest tests
  alembic/versions/       # DB migrations (0001–0006)
```

---

## Dashboard

The dashboard is a React 18 SPA (`frontend/`) served by FastAPI once built.

| SPA route | Description |
|-----------|-------------|
| `/overview` | Net liq KPIs, equity/drawdown chart (7D/30D/90D), bot status, top positions, recent events |
| `/positions` | Open positions — filterable (all/equity/options/winners/losers), per-row sparkline |
| `/orders` | Orders + Fills tabs, status badges, mono timestamps |
| `/signals` | Signals with filter tabs, inline ScoreBar distribution |
| `/rankings` | Top Bullish / Bearish panels, full ranking table with 7-factor breakdowns, trade plans |
| `/sentiment` | 60h market trend chart, sectors & tickers tables, headlines list, budget gauge, Refresh button |
| `/risk` | Drawdown + position-slots donuts, 90d chart, limits card |
| `/controls` | Trading / Kill switch / Options / Approve mode cards; two-step Close All confirmation |
| `/config` | Read-only 3-column config grid (8 sections) |

### Building and running the frontend

```bash
# Development (hot reload; proxies /api to localhost:8000)
cd frontend
pnpm dev          # → http://localhost:5173/

# Production build (outputs to ui/static/dist/)
pnpm build

# Then start FastAPI as usual
uvicorn api.main:app --reload
# Navigate to http://localhost:8000/overview
```

---

## API Endpoints

### v1 JSON API (used by the React SPA)

All endpoints are prefixed `/api/v1/`. Controls return `{ ok: bool, bot: BotState }`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/overview` | Bot state, equity history, positions, events, LLM budget |
| GET | `/api/v1/positions` | Open positions |
| GET | `/api/v1/orders` | Order history |
| GET | `/api/v1/fills` | Fill history |
| GET | `/api/v1/signals` | Signal snapshots |
| GET | `/api/v1/rankings` | Latest symbol rankings with factor components |
| GET | `/api/v1/trade-plans` | Recent trade plans |
| GET | `/api/v1/sentiment` | Market/sector/ticker scores, history, headlines, budget |
| GET | `/api/v1/risk` | Drawdown history + risk config |
| GET | `/api/v1/config` | Active config (8 sections) |
| POST | `/api/v1/controls/pause` | Pause trading |
| POST | `/api/v1/controls/resume` | Resume trading |
| POST | `/api/v1/controls/kill/on` | Activate kill switch |
| POST | `/api/v1/controls/kill/off` | Deactivate kill switch |
| POST | `/api/v1/controls/close_all` | Close all positions (sets kill switch) |
| POST | `/api/v1/controls/options/enable` | Enable options trading |
| POST | `/api/v1/controls/options/disable` | Disable options trading |
| POST | `/api/v1/controls/approve_mode/on` | Require manual approval |
| POST | `/api/v1/controls/approve_mode/off` | Allow auto-trading |
| POST | `/api/v1/sentiment/refresh` | Trigger sentiment refresh |
| POST | `/api/v1/fundamentals/refresh` | Force-refresh yfinance fundamentals (`?symbol=AAPL` for one, no params = all) |

### Legacy API (backward compat, unchanged)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/state` | Bot state + equity + positions summary |
| GET | `/signals/latest` | Latest signal snapshots |
| GET | `/sentiment/latest` | Latest sentiment data |
| GET | `/sentiment/llm-budget` | Claude sentiment spend + caps |
| GET | `/orders` | Order history |
| GET | `/fills` | Fill history |
| GET | `/positions` | Current positions |
| GET | `/api/rankings/latest` | Latest symbol rankings |
| POST | `/controls/*` | All legacy control endpoints |

Full interactive docs: **http://localhost:8000/docs**

---

## Safety Features

- **Approve mode ON by default** — trade plans saved but no orders submitted until toggled OFF
- **Kill switch** — immediately stops all new order submissions
- **Drawdown stop** — halts trading when account drawdown exceeds limit (default 50%)
- **Cash reservation** — verifies available cash before every order; reserves max-loss amount
- **Per-bot position caps** — `OptionsSwingBot` and `EquitySwingBot` each enforce their own `max_positions` independently
- **Sector concentration cap** — equity bot blocks trades that would push any GICS sector above 30% of NAV
- **Duplicate prevention** — `intent_id` format prevents double-ordering on restart
- **Stale data guard** — refuses to trade on market data older than `safety.data_stale_minutes`
- **Defined risk only** — options bot only places debit spreads (max loss = debit paid, never more)
- **Verified tickers only** — discovered tickers require IBKR contract lookup before trading (cached 24h)

---

## Sentiment: RSS Lexicon vs Claude LLM

```yaml
sentiment:
  provider: "rss_lexicon"   # default — no API key needed, free
  # provider: "claude_llm"  # headlines → Claude → per-item sentiment + sector/ticker tagging
  refresh_minutes: 60
```

Override at runtime: `SENTIMENT_PROVIDER=claude_llm`.

### Enabling the Claude LLM provider

1. Get an Anthropic API key from [console.anthropic.com](https://console.anthropic.com/) → Settings → API Keys.  
   ⚠ A Claude Pro subscription is **not** API access — the API is billed separately.

2. Set the key: `ANTHROPIC_API_KEY=sk-ant-...` in `.env` (never in `config.yaml`).

3. Set `sentiment.provider: "claude_llm"` in `config.yaml` and restart.

4. Force an immediate refresh:
   ```bash
   python cli.py sentiment refresh --source claude_llm
   ```

### What the Claude provider does (per refresh)

1. Fetches headlines + snippets from the configured RSS feeds (no full-page scraping).
2. Deduplicates against `sentiment_llm_items` (14-day window by default).
3. Checks the budget table — if the monthly or daily cap is exhausted, the refresh is **skipped** with no API call.
4. Sends up to `max_items_per_run` items (default 40) to Claude with a strict JSON output schema.
5. Validates output — items with missing required fields are dropped; if **all** items fail, no snapshots are overwritten (previous data kept intact).
6. Aggregates per-item results into market / sector / ticker snapshots with `weight = confidence × recency_decay(half-life 72h)`.
7. After aggregation, resolves each `mentioned_companies` name to a ticker via the security master alias table (deterministic — no LLM guessing), producing additional `scope="ticker"` snapshots.

On any API error the bot does **not** fall back to the RSS lexicon. It logs the failure to `events_log` and leaves existing snapshots untouched.

### What the RSS lexicon provider does (per refresh)

1. Fetches up to 50 entries from configured RSS feeds.
2. Scores each headline with a positive/negative keyword lexicon, weighted by recency.
3. Produces `scope=market` and `scope=sector` snapshots via keyword-based sector detection.
4. Scans the same headlines for known company aliases from the `security_alias` table
   (multi-word aliases + long single-word manual overrides), producing `scope=ticker` snapshots.

### Budget cap (€10/month default)

| Config key | Default | Meaning |
|------------|---------|---------|
| `monthly_budget_eur` | `10.0` | Hard ceiling for sentiment LLM calls per calendar month |
| `daily_budget_fraction` | `0.12` | Per-day cap = monthly × fraction (≈€1.20/day) |
| `hard_stop_on_budget` | `true` | `false` = warn only, keep calling |
| `eur_usd_rate` | `1.08` | Used to convert Anthropic USD costs to EUR |

The dashboard `/sentiment` page shows a red **STOPPED** badge when a cap is hit. Usage resets naturally at month boundary.

> Also set a matching spend limit in the **Anthropic Console** (Billing → Usage limits) for a true hard stop — the in-process cap is best-effort.

---

## Company → Ticker Matching

Both sentiment providers produce `scope="ticker"` snapshots using a deterministic company-name resolver — no LLM ticker guessing.

### How it works

1. `data/us_listed_master.csv` (~220 major US stocks) is imported into `security_master` on first startup.
2. For each security, aliases are generated automatically: normalised company name (suffixes stripped), symbol lowercase, short name. Manual overrides can be added in `data/manual_alias_overrides.csv`.
3. **Claude LLM path**: the prompt asks Claude to emit `mentioned_companies` (company names as written in the text). After the LLM call, each name is normalised and looked up in `security_alias`. Exact matches resolve to a ticker; ambiguous matches (two different symbols) are skipped.
4. **RSS lexicon path**: headlines are scanned with `\b{alias}\b` word-boundary regex against multi-word aliases and long single-word manual overrides. This avoids false positives from short words like "apple" or "meta" appearing in non-financial context.

### Setting up the security master

```bash
# Import CSV + generate aliases (runs automatically on first init_db.py)
python cli.py securities import

# Load manual alias overrides (e.g. "molina" → MOH, "nvidia" → NVDA)
python cli.py securities import --load-overrides

# Verify IBKR contracts for all securities (requires IB Gateway running)
python cli.py securities verify --all

# Debug: check how a company name resolves
python cli.py match-company --companies "Molina Healthcare,UnitedHealth Group"
```

The `security_master` table is auto-seeded from `data/us_listed_master.csv` whenever `create_tables()` finds the table empty (e.g., on fresh DB creation). You can re-import at any time without duplicates.

---

## Running Tests

```bash
# Python backend
python -m pytest tests/ -v

# Focused fundamentals + composite scoring tests
python -m pytest tests\test_fundamental_scorer.py tests\test_scoring.py tests\test_composite_scorer.py -q

# Frontend smoke tests (11 tests)
cd frontend && pnpm test
```

---

## Known Limitations

- **EquitySwingBot long-only in v1** — set `bots.equity_swing.long_only: false` to enable shorting (not yet validated end-to-end).
- **No automated exits** — the exit threshold (`exit_threshold`) and `max_holding_days` are tracked in config but position exit orders are not yet submitted automatically. Manual close via dashboard or IBKR.
- **No EUR/USD FX conversion** — all risk calculations are in USD.
- **IV Rank** requires the IBKR historical-volatility market data entitlement; falls back to "unknown" regime when unavailable (gate warns, does not block).
- **Fundamental score** uses yfinance. If yfinance returns no usable metrics for a symbol, the 7-factor Quality/Value/Growth adapters fall back to neutral/missing confidence as appropriate. Successful results are persisted to `fundamental_snapshots` for `fundamentals.ttl_days` (default 7); the scheduler force-refreshes every symbol weekly. Manual refresh: dashboard button on `/rankings` or `python cli.py fundamentals refresh [--symbol]`.
- **IBKR market data subscription** — paper accounts without live US data subscriptions default to delayed quotes (`ibkr.market_data_type: 3`). If you see Error 10089/354, that knob isn't being applied; if you see Error 200 ("no security definition") storms, the underlying-price fetch returned NaN — both are now handled, but a real subscription is still needed for live trading.
- **`data/sp500.csv`** exists as a reference file (~180 stocks with sectors) but is not yet auto-ingested — the live universe is still seeded from the embedded `SEED_TICKERS` list in `trader/universe.py`.
- **`close_all` control** currently activates the kill switch only; actual IBKR position-closing orders are not yet wired.
- **Company→ticker matching coverage** — only securities in `security_master` (~220 major US stocks) are resolved. Smaller-cap names mentioned in news will not produce ticker snapshots unless manually added to the CSV and re-imported.

## QUICK CMD:

- ** uvicorn api.main:app --reload ** To observe the port
