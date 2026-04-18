# Market AI — Automated Swing Trading Bot

Paper-trading-first automated trading system for Interactive Brokers.
Trades US stocks/ETFs via **debit spread** options strategies (defined risk only).

## Features

- **Swing strategy** (2-20 day holds) using technical analysis + market/sector sentiment
- **Debit spreads only** — bull call spreads & bear put spreads (no naked shorts, no credit spreads)
- **No-debt guarantee** — cash reserved for max loss before every trade
- **Risk engine** — drawdown stop, position limits, per-trade risk caps
- **Approve mode** — signals generated but orders require manual approval (default ON)
- **Dashboard** — full local web UI with charts, controls, and manual overrides
- **Sentiment** — pluggable: RSS lexicon (free) or Claude LLM extractor (per-item sentiment + sector/ticker tagging + strict €10/month budget cap)

## Quick Start (Docker on Windows)

The fastest path — you only need Docker Desktop and IB Gateway installed on the host.

### 1. Start IB Gateway (paper) on the Windows host
- Log in with your **paper** credentials.
- Configure → API → Settings:
  - Enable ActiveX and Socket Clients
  - Socket port **7497** (TWS paper) or **4002** (Gateway paper)
  - Uncheck "Read-Only API"
  - Trusted IPs: add `127.0.0.1` and leave "Allow connections from localhost only" OFF so the container can reach it.
- Make sure Windows Defender Firewall allows the TWS/Gateway binary on the private network.

### 2. Configure env

```powershell
copy .env.example .env
# optional: edit POSTGRES_PASSWORD, IB_PORT, IB_CLIENT_ID, MODE
```

All runtime knobs (`DATABASE_URL`, `MODE`, `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `APPROVE_MODE_DEFAULT`) override `config.yaml` when present. You do **not** need a `config.yaml` file in the container — defaults from `config.example.yaml` plus env vars are enough.

### 3. Build & run

```bash
# First time (or after code changes to deps):
docker compose up -d postgres
docker compose up --build -d api trader

# Subsequent runs:
docker compose up -d
```

Open **http://localhost:8000**. API container auto-applies Alembic migrations and seeds `bot_state` on first boot.

### 4. Verify

```bash
# API health
curl http://localhost:8000/health           # -> {"status":"ok"}

# Trader heartbeat (updated every 10s by the scheduler)
docker compose exec postgres psql -U market_ai -d market_ai \
  -c "SELECT last_heartbeat FROM bot_state;"

# Container health states
docker compose ps
```

The dashboard overview page (`/`) also shows the heartbeat.

### 5. Optional — Adminer (DB inspector)

```bash
docker compose --profile debug up -d adminer
# Browse http://localhost:8081  — system: PostgreSQL, server: postgres, user/db: market_ai
```

### Troubleshooting

**Trader can't connect to IB Gateway**
- Confirm IB Gateway / TWS is actually running and logged in on the Windows host.
- Confirm API is enabled and listening on the port in `.env` (`IB_PORT`).
- From the trader container, sanity-check the route: `docker compose exec trader python -c "import socket; print(socket.create_connection(('host.docker.internal', 7497), timeout=3))"`.
- Windows Defender Firewall: allow inbound on the chosen port for the TWS/Gateway binary.
- The trader will **not** crash-loop if IB is unreachable — it logs the error and stays up in offline mode, polling for reconnect on the next tick.

**DB connection fails**
- `docker compose ps postgres` — make sure state is `healthy`, not just `running`.
- `docker compose logs postgres` for init errors.
- Confirm `DATABASE_URL` in `.env` uses host `postgres` (the service name inside the compose network), not `localhost`.
- If you changed `POSTGRES_PASSWORD` after the volume was created, either update `DATABASE_URL` to match or `docker compose down -v` to wipe and re-init (destroys data).

**Rebuild after code-only changes**
- With the bind mount `.:/app` in `docker-compose.yml`, Python reloads on file save in the API (uvicorn is run without `--reload` by default; restart the container or add `--reload` to the command if you want hot reload). For dependency changes, `docker compose build api` first.

---

## Bare-metal setup (no Docker)

### 1. Prerequisites

- Python 3.11+
- Docker (for PostgreSQL) **or** a running PostgreSQL 16 instance
- Interactive Brokers TWS or IB Gateway running

### 2. Install

```bash
pip install -e ".[dev]"
```

### 3. Start PostgreSQL

```bash
# Copy env template and (optionally) customise credentials
cp .env.example .env

# Start Postgres (and Adminer on :8081)
docker compose up -d postgres

# Verify it is healthy
docker compose ps
```

### 4. Configure

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your IBKR settings.
# Set DATABASE_URL in .env — the app reads it at startup.
```

The DB URL is resolved in this order (first wins):

1. `DATABASE_URL` environment variable
2. `db.url` in `config.yaml`
3. SQLite fallback via `db.path` in `config.yaml` (dev/test only)

### 5. Apply schema migrations

```bash
# With DATABASE_URL exported (or .env loaded):
export $(grep -v '^#' .env | xargs)   # Linux/macOS
alembic upgrade head
```

Or let `init_db.py` do it for you (see next step).

### 4. IBKR Paper Trading Setup

#### Using TWS (Trader Workstation)
1. Download TWS from [Interactive Brokers](https://www.interactivebrokers.com/en/trading/tws.php)
2. Log in with your **paper trading** credentials (username ends with a number, e.g. `DU12345`)
3. Go to **Edit > Global Configuration > API > Settings**:
   - Check "Enable ActiveX and Socket Clients"
   - Set Socket port to **7497** (paper) or **7496** (live)
   - Check "Allow connections from localhost only"
   - Uncheck "Read-Only API"
4. Click Apply/OK

#### Using IB Gateway (headless, recommended for servers)
1. Download IB Gateway from the same IBKR page
2. Log in with paper credentials
3. Default paper port is **4002** (live is 4001)
4. Update `config.yaml`:
   ```yaml
   ibkr:
     port: 4002
   ```

#### Setting Paper Mode
In `config.yaml`:
```yaml
mode: PAPER
ibkr:
  host: "127.0.0.1"
  port: 7497        # TWS paper
  client_id: 1
  account: ""       # auto-detect
```

### 6. Initialize & Run

```bash
# Runs `alembic upgrade head` then seeds bot_state
export $(grep -v '^#' .env | xargs)   # Linux/macOS — loads DATABASE_URL
python scripts/init_db.py

# Start everything (API + trader)
python scripts/run_all.py
```

Or start components separately:
```bash
# Terminal 1: API server (DATABASE_URL must be exported)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Trader worker
python trader/main.py
```

### 6. Open Dashboard

Navigate to **http://localhost:8000**

## Dashboard Pages

| Page | Description |
|------|-------------|
| `/` | Overview — equity, positions, bot status, events |
| `/positions` | Open positions with P&L |
| `/orders` | Order history and fills |
| `/signals` | Latest trading signals with scores |
| `/sentiment` | Market & sector sentiment timeline |
| `/risk` | Risk dashboard with drawdown chart |
| `/controls` | Manual overrides — pause, kill switch, approve mode |
| `/config` | Current configuration (read-only) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/state` | Bot state + equity + positions summary |
| POST | `/controls/pause` | Pause trading |
| POST | `/controls/resume` | Resume trading |
| POST | `/controls/kill/on` | Activate kill switch |
| POST | `/controls/kill/off` | Deactivate kill switch |
| POST | `/controls/close_all` | Close all positions |
| POST | `/controls/options/enable` | Enable options trading |
| POST | `/controls/options/disable` | Disable options trading |
| POST | `/controls/approve_mode/on` | Require manual approval |
| POST | `/controls/approve_mode/off` | Allow auto-trading |
| GET | `/signals/latest` | Latest signal snapshots |
| GET | `/sentiment/latest` | Latest sentiment data |
| GET | `/sentiment/llm-budget` | Claude sentiment spend + remaining caps |
| GET | `/orders` | Order history |
| GET | `/fills` | Fill history |
| GET | `/positions` | Current positions |

Full API docs at **http://localhost:8000/docs**

## Architecture

```
scripts/run_all.py
  |
  +-- uvicorn api.main:app     (FastAPI + dashboard)
  |
  +-- trader/main.py           (trading worker)
        |
        +-- Scheduler
              |-- heartbeat (10s)
              |-- sentiment refresh (30min)
              |-- signal evaluation (15min)
              |-- daily rebalance (09:45 local)
              +-- IBKR sync (30s)
```

## Safety Features

- **Approve mode** enabled by default — signals shown but no orders placed
- **Kill switch** — immediately stops all new orders
- **Drawdown stop** — halts trading at 50% drawdown (configurable)
- **Cash reservation** — checks available cash before every order
- **Duplicate prevention** — intent IDs prevent double-ordering on restart
- **Stale data check** — refuses to trade on old market data
- **Defined risk only** — only debit spreads allowed (max loss = debit paid)
- **No shorting** — bear exposure only via bear put spreads

## Sentiment: RSS lexicon vs Claude LLM

The bot ships with two sentiment providers, selected via `config.yaml`:

```yaml
sentiment:
  provider: "rss_lexicon"   # default — offline, free
  # provider: "claude_llm"  # headlines → Anthropic Claude → per-item sentiment
  refresh_minutes: 60
```

You can override at runtime with `SENTIMENT_PROVIDER=claude_llm`.

### Enabling the Claude LLM provider

1. **Create an Anthropic API key.**
   Go to [console.anthropic.com](https://console.anthropic.com/) → *Settings* → *API Keys* → *Create Key*.

   ⚠ Important: a Claude Pro / claude.ai chat subscription is **not** API access.
   The API is metered separately and requires a key from the Anthropic Console.

2. **Set the key in your environment.**
   The bot reads it from `ANTHROPIC_API_KEY` (configurable per-run via
   `sentiment.claude.api_key_env`). It is never read from `config.yaml`.

   ```bash
   # .env (bare-metal or docker-compose)
   ANTHROPIC_API_KEY=sk-ant-...
   ```

3. **Flip the provider in `config.yaml`:**
   ```yaml
   sentiment:
     provider: "claude_llm"
   ```

4. **Restart the trader.** The next scheduler tick (≤60 min) will run the LLM
   extractor. You can also force an immediate refresh with:
   ```bash
   python -c "from trader.sentiment.factory import refresh_and_store; print(refresh_and_store())"
   ```

### What the Claude provider does (per refresh)

1. Fetches headlines + snippets from the RSS feeds in
   `sentiment.rss.feeds` (no full-page scraping).
2. Deduplicates against `sentiment_llm_items` (default window: 14 days).
3. Consults the sentiment-only budget table (`sentiment_llm_usage`). If the
   month or day cap is exhausted, the refresh is **skipped** — no API call.
4. Otherwise sends up to `max_items_per_run` items to Claude (default: 40)
   with a strict JSON schema.
5. Validates output. Items missing the required market entity are dropped;
   if **all** items are invalid the run is treated as a failure and **no
   snapshots are overwritten** (prior data is kept).
6. Aggregates per-item entities into market / sector / ticker snapshots using
   `weight = confidence * recency_decay(half-life 72h)`.

On any API error the bot does **not** silently fall back to the RSS lexicon.
It logs the failure to `events_log` (`sentiment_refresh_failed`) and leaves
the previous snapshots in place.

### Budget cap (€10/month by default)

Costs are estimated from Anthropic-reported token counts when available, else
from a conservative character-based heuristic. Spend is recorded to
`sentiment_llm_usage` on every call (success or failure).

Defaults (in `sentiment.claude`):

| Knob | Default | Meaning |
|------|---------|---------|
| `monthly_budget_eur` | `10.0` | Hard ceiling for sentiment LLM calls per calendar month |
| `daily_budget_fraction` | `0.12` | Per-day cap = monthly × fraction |
| `hard_stop_on_budget` | `true` | If false, the provider only warns and keeps calling |
| `eur_usd_rate` | `1.08` | Used to convert cost estimates to EUR |

When a cap is hit, the dashboard's `/sentiment` page shows a red **STOPPED**
badge and `/state` exposes the spend totals. There is no UI reset button —
usage resets naturally at the month boundary.

> Set a matching **spend limit in the Anthropic Console** (Billing →
> Usage limits) for a true hard stop. This in-process cap is a best-effort
> safety net, not a substitute for provider-side enforcement.

### Sentiment-related tables

- `sentiment_snapshots` — final per-scope scores (used by the strategy).
- `sentiment_llm_items` — dedup ledger (hash id + processed_at).
- `sentiment_llm_usage` — per-call token + cost audit (USD + EUR estimates).

Apply the new migration with `alembic upgrade head` (or let `init_db.py` do
it on next startup). Existing `sentiment_snapshots` rows are untouched.

## Running Tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
market_AI/
  common/           # Shared: config, DB, models, schemas, logging
  trader/           # Trading engine
    sentiment/      # RSS + mock sentiment providers
    ibkr_client.py  # IBKR connection wrapper
    market_data.py  # Historical bar fetching
    indicators.py   # EMA, SMA, RSI, MACD, ATR
    universe.py     # Ticker universe management
    strategy.py     # Swing strategy + signal generation
    risk.py         # Risk engine + checks
    execution.py    # Debit spread order construction
    scheduler.py    # Main trading loop
    sync.py         # IBKR -> DB sync
  api/              # FastAPI backend
    routes/         # API endpoints
  ui/               # Dashboard templates + static assets
  scripts/          # Init DB, run all
  tests/            # pytest tests
```
