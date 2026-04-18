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
- **Sentiment** — RSS-based headline scoring with sector tagging

## Quick Start

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
