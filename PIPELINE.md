# Market AI — End-to-End Pipeline Documentation

> Human-readable process overview of the automated swing-trading bot. Focuses on
> logical phases, inputs/outputs, and decision gates — **not** a per-function reference.
>
> Scope: paper-trading-first IBKR swing bot supporting two instrument types —
> **debit-spread options** (`OptionsSwingBot`) and **equity shares** (`EquitySwingBot`) —
> runnable independently or together.

---

## 1) One-page executive summary

- The bot runs **two plug-in bots** (`OptionsSwingBot`, `EquitySwingBot`) on a shared
  lifecycle: **regime → universe → rank → score → select → execute**. Both live in
  `bots/` and derive from `bots/base_bot.py::BaseBot`.
- There are **two orchestrators**: the CLI (`cli.py`) triggers single or continuous
  bot runs; the legacy `trader/scheduler.py::Scheduler` provides a daemon that combines
  sync, sentiment refresh, ranking, planning, and rebalance on cadence.
- **Market regime** is gated by SPY: a single daily check produces `risk_on` or
  `risk_off`. `risk_off` closes the equity bot (cash mode) or restricts it to
  defensive sectors, and suppresses new long entries.
- **Universe** = embedded `SEED_TICKERS` (~50 large-cap US stocks + sector ETFs) +
  RSS/LLM-discovered tickers, each verified against IBKR `reqContractDetails` with
  24h caching. `data/sp500.csv` exists but is **not yet auto-ingested**.
- **Sentiment** is pluggable (`rss_lexicon` | `claude_llm` | `mock`). Claude has a
  hard €10/month budget cap and **must not fall back to lexicon** on failure. Output
  is three scopes — market / sector / ticker — persisted as `SentimentSnapshot`.
- **Ranking** runs a 5-factor composite scoring pipeline per symbol — sentiment (30%),
  momentum/trend (25%), risk (20%), liquidity (15%), fundamentals (10%) — with
  proportional weight redistribution for missing factors. Each symbol also gets
  `equity_eligible` (liquidity gate + IBKR-verified) and `options_eligible` (from
  `SecurityMaster`; safe-by-default=False) flags. Score drives bias labels
  (`≥0.55 → bullish`; `≤0.45 → bearish`). Rows persisted as `SymbolRanking`.
- **Options** score + plan produces a `TradePlan` (status `proposed` / `skipped`) via
  `trader/options_planner.py::plan_trade`. Execution picks delta-matched legs through
  `GreeksService → StrikeSelector → GreeksGate`, then builds an IBKR BAG combo order.
- **Equity** sizing uses ATR(14): `stop = entry - atr_stop_multiplier × ATR`;
  `shares = floor(nav × risk_per_trade_pct% / stop_distance)`, capped by cash and
  sector concentration.
- **Risk gates** stack up: kill switch → pause → drawdown cap → max positions →
  positive-cash constraint → per-trade risk cap → cash reservation (sum of pending
  `max_loss`). Orders also get a **duplicate-intent** guard.
- **Approve mode** (default ON) never submits to IBKR — orders are written with
  `status="pending_approval"` for human review through the dashboard.
- **Portfolio isolation**: every `Order`/`Position`/`Trade` row carries
  `portfolio_id` (`options_swing` | `equity_swing`). Each bot's risk checks filter
  by its own portfolio.
- **Company → ticker matching** is deterministic: `security_master` + `security_alias`
  tables map normalised company names to verified symbols. The Claude LLM provider emits
  `mentioned_companies` (raw names, not guessed tickers); the RSS provider scans
  headlines with word-boundary alias matching. Both paths produce `scope="ticker"`
  `SentimentSnapshot` rows identical to the sector/market rows.
- **Persistence** is SQLite/Postgres via SQLAlchemy; 18 tables. Key time-series:
  `equity_snapshots`, `sentiment_snapshots`, `symbol_rankings`, `signal_snapshots`,
  `orders`, `fills`, `positions`, `trades`, `events_log`, `trade_plans`.
- **Idempotency**: `intent_id = {symbol}_{direction}_{yyyymmdd}_{uuid8}` guards
  against duplicate submission across restarts. Planner cooldown (`rc.cooldown_hours`)
  prevents re-proposing the same symbol.
- **Broker**: IBKR via `ib_insync`; singleton client in `trader/ibkr_client.py`.
  Live connection is optional — most phases tolerate offline mode with cached data.
- **Observability**: structured `events_log` table, Python logging, and an
  FastAPI dashboard (`/overview`, `/rankings`, `/sentiment`, `/risk`, `/controls`, …).

---

## 2) System map

```mermaid
flowchart TD
  CLI["cli.py run"] -->|mode, approve, dry_run| BOT["BaseBot.run()"]
  SCHED["Scheduler (trader/scheduler.py)"] -->|heartbeat 10s| SYNC
  SCHED -->|refresh_minutes| SENT
  SCHED -->|rank cadence| RANK
  SCHED -->|rebalance_time_local| LEGACY["generate_signals()"]

  subgraph Ingestion
    SENT["Sentiment refresh\n(factory.refresh_and_store)"]
    SYNC["IBKR sync\n(sync.full_sync)"]
    BARS["Market data\n(market_data.fetch_bars)"]
  end

  SENT -->|writes| DB_SENT["(sentiment_snapshots)"]
  SYNC -->|writes| DB_EQUITY["(equity_snapshots, positions, orders, fills)"]

  subgraph AnalysisPerCycle
    REGIME["check_regime() — SPY daily"]
    UNI["get_verified_universe()"]
    RANK["rank_symbols()"]
    SCORE["score_symbol() — 4-factor"]
  end

  BOT --> REGIME
  BOT --> UNI
  UNI -->|List[UniverseItem]| RANK
  DB_SENT --> RANK
  RANK -->|writes| DB_RANK["(symbol_rankings)"]
  RANK -->|List[RankedSymbol]| BOT
  REGIME --> SCORE
  BARS --> SCORE
  DB_SENT --> SCORE
  SCORE -->|ScoreBreakdown| BOT

  subgraph SelectionAndGates
    BUILD["build_candidates()"]
    SELECT["select_trades()"]
    PLAN["options_planner.plan_trade()"]
    GREEKS["GreeksService → StrikeSelector → GreeksGate"]
    SIZE["_size_equity_trade() (ATR)"]
    RISK["risk.check_can_trade()"]
  end

  BOT --> BUILD --> SELECT
  SELECT -->|options path| PLAN -->|writes TradePlan| DB_PLAN["(trade_plans)"]
  PLAN --> GREEKS
  SELECT -->|equity path| SIZE

  subgraph Execution
    EXEC_OPT["trader.execution.execute_signal()"]
    EXEC_EQ["execution.equity_execution.place_equity_order()"]
    IBKR["IBKR via ib_insync\n(BAG combo | STK LIMIT)"]
  end

  GREEKS -->|approved spread| EXEC_OPT
  SIZE -->|TradeIntent| EXEC_EQ
  EXEC_OPT --> RISK
  EXEC_EQ --> RISK
  RISK -->|pending_approval OR submitted| DB_ORD["(orders)"]
  EXEC_OPT --> IBKR
  EXEC_EQ --> IBKR
  IBKR -->|fills| SYNC

  DB_ORD --> UI["FastAPI + Jinja UI (api/main.py)"]
  DB_RANK --> UI
  DB_EQUITY --> UI
  DB_SENT --> UI
```

---

## 3) Pipeline — phase-by-phase

### Phase A — Startup & Orchestration

- **Purpose.** Load config, create/upgrade DB, connect to IBKR, and kick off either
  a single cycle (`cli.py run --once`) or a continuous loop (CLI / Scheduler).
- **Trigger / cadence.** User invocation for the CLI; `Scheduler.run()` spins at a
  10-second heartbeat and delegates by per-task intervals.
- **Upstream inputs.**
  - `config.yaml` (→ `config.example.yaml` → defaults), parsed in `common/config.py`.
  - Environment overrides (`MODE`, `DATABASE_URL`, `IB_HOST`, `APPROVE_MODE_DEFAULT`,
    `SENTIMENT_PROVIDER`, …) applied in `_apply_env_overrides`.
- **Core logic.**
  - CLI path (`cli.py::_setup`) sets up logging, calls `load_config`, `create_tables`,
    then resolves one or more bots via `_make_bot`.
  - Scheduler path (`trader/main.py`) seeds universe once, connects IBKR, then calls
    `Scheduler.run()` which loops `Scheduler.run_once()` with `Event.wait(10)`.
- **Key parameters/config.**
  - `scheduling.sentiment_refresh_minutes` (legacy), `sentiment.refresh_minutes` (preferred),
    `scheduling.signal_eval_minutes`, `scheduling.rebalance_time_local` (HH:MM).
  - `features.approve_mode_default` and `bots.*.enabled` gate the downstream flow.
- **Outputs/artifacts.** `AppConfig` (Pydantic) cached in `common.config._cached`;
  live `IBKRClient` singleton from `trader/ibkr_client.get_ibkr_client()`.
- **Downstream consumers.** Every phase below.
- **Decision gates.** Bot is **skipped entirely** if `cfg.bots.<name>.enabled is False`.
  IBKR connection failure → offline-mode fallback (most phases still run; anything
  needing IBKR returns `None` / writes a `skipped` row).
- **Logging/metrics.** `log_event("INFO","startup", …)` in `trader/main.py`.
- **Failure modes.**
  - Missing `config.yaml` and no example → Pydantic default config, safe to run.
  - IBKR unreachable → `IBKRClient.connect()` raises; CLI logs a warning and
    continues with `client=None`.

---

### Phase B — Sentiment Refresh

- **Purpose.** Produce market / sector / ticker sentiment scores used by ranking
  and by the 4-factor score of each symbol.
- **Trigger / cadence.**
  - CLI: before each bot cycle if `--refresh-sentiment` (default on) or standalone
    via `cli.py sentiment refresh`.
  - Scheduler: every `sentiment.refresh_minutes` minutes (default 60).
  - Serialised by `trader/sentiment/factory.py::_REFRESH_LOCK` (threading lock).
- **Upstream inputs.**
  - Provider name from `cfg.sentiment.provider` (`rss_lexicon` | `claude_llm`).
  - RSS feeds list from `cfg.sentiment.rss.feeds`.
  - Claude config from `cfg.sentiment.claude` (`model`, `monthly_budget_eur`,
    `daily_budget_fraction`, `max_items_per_run`, …).
  - Dedup cache from `sentiment_llm_items` (Claude only).
  - Monthly / daily spend state from `sentiment_llm_usage` (Claude only).
- **Core logic.**
  - `build_provider()` lazy-constructs the provider; Claude import is deferred so
    a missing `ANTHROPIC_API_KEY` never breaks the RSS path.
  - **Claude path**: provider has `.run()` → handles dedup, budget cap, retry, token
    accounting; returns a structured run object persisted via `_persist_snapshots`.
    The LLM prompt requests `mentioned_companies` (company names as written in text,
    never ticker guesses). After the DB session commits, `_build_ticker_results_from_companies()`
    resolves each name via `trader/securities/matcher.py::match_companies_to_symbols()`
    (alias-table exact lookup, ambiguity-rejected), and emits `scope="ticker"` snapshots.
    Audit rows written to `rss_entity_matches`.
  - **RSS lexicon path**: `fetch_market_sentiment()` + `fetch_sector_sentiment()` for
    market/sector scopes. If `fetch_ticker_sentiment()` is present on the provider
    (always true for `RSSProvider`), `factory.py` calls it too. `RSSProvider` loads
    multi-word aliases (≥2 tokens) plus single-word manual overrides (priority≤1, len≥5)
    from `security_alias` and scans headlines with `\b{alias}\b` word-boundary regex,
    producing `scope="ticker"` `SentimentResult` items per matched symbol.
  - Either way, snapshots are persisted to `sentiment_snapshots` with
    `scope ∈ {market, sector, ticker}`.
- **Key parameters/config.** `cfg.sentiment.*` (see above); `trader/sentiment/budget.py`
  pricing table `MODEL_PRICING_USD_PER_MTOK` must be updated when Anthropic pricing changes.
- **Outputs/artifacts.**
  - `SentimentSnapshot` rows (`(scope, key, score, sources_json)`).
  - `SentimentLlmItem` dedup rows and `SentimentLlmUsage` cost rows (Claude only).
  - Run summary dict: `{provider, status, snapshots_written, usage_cost_eur, …}`.
- **Downstream consumers.**
  - `trader/ranking.py` reads latest snapshots (market + sector + ticker).
  - `trader/strategy.py::score_symbol` reads `get_latest_market_score()` and
    `get_latest_sector_score(sector)`.
  - `trader/universe.py::get_verified_universe` uses `get_recent_ticker_scores()`
    to inject RSS-discovered tickers.
  - `/sentiment` dashboard page.
- **Decision gates.**
  - Claude fails → status `failed`; **no fallback to lexicon** (saved memory rule).
    Empty snapshots are written so downstream can apply recency penalties.
  - Lock already held → `{"status": "skipped", "reason": "already_running"}`.
  - Budget cap exceeded → provider aborts; `status != "success"`.
  - `security_alias` table empty → ticker matching skipped with a warning log; market
    and sector snapshots are still written.
  - Company name matches 2+ different symbols (ambiguous alias) → skipped; audit row
    written with `reason="ambiguous"`.
  - RSS ticker detection: alias must be ≥2 words **or** a manual priority≤1 override
    ≥5 chars to be included in scanning (avoids false positives on short common words).
- **Logging/metrics.**
  - `events_log` entries: `sentiment_refresh_success`.
  - Per-call `SentimentLlmUsage` rows with `prompt_tokens`, `completion_tokens`,
    `cost_usd_est`, `cost_eur_est`.
- **Failure modes.**
  - Feed timeout → individual feed skipped; others continue.
  - Claude API error / 429 → retried with exponential backoff; on exhaustion the
    run returns `status="failed"` (no write).

---

### Phase C — Universe Build & Contract Verification

- **Purpose.** Produce the tradeable ticker list for this cycle with sector + verification.
- **Trigger / cadence.** Called at the start of every bot cycle (via `BaseBot.run`)
  and within the scheduler's ranking cadence.
- **Upstream inputs.**
  - Seed set: `trader/universe.py::SEED_TICKERS` (embedded).
  - `universe` table (seeded once by `seed_universe()` in `trader/main.py`).
  - `get_recent_ticker_scores(hours=72, limit=200)` — RSS-discovered symbols.
  - `ContractVerificationCache` rows (24h TTL via `ranking.contract_cache_hours`).
- **Core logic.**
  - Build core items from `Universe` rows with `active=True`.
  - Always-tradable ETFs ensured (SPY/QQQ/IWM/DIA).
  - For each RSS-discovered ticker not in core, call `verify_contract(symbol, client)`
    which uses `IB.reqContractDetails` and rejects non-USD / OTC / Pink.
  - Verification result cached in `contract_verification_cache`.
- **Key parameters/config.** `cfg.universe.min_price`, `cfg.universe.min_dollar_volume`,
  `cfg.universe.exclude_leveraged_etfs`, `cfg.ranking.contract_cache_hours`.
- **Outputs/artifacts.** `List[UniverseItem]` — `(symbol, sector, name, type,
  sources, verified, conid)`.
- **Downstream consumers.** `BaseBot.build_candidates`, `rank_symbols`,
  `score_symbol` (via regime+sector), `plan_trade` (for bias→strategy mapping).
- **Decision gates.**
  - Discovered ticker with `verified=False` → excluded (logged at DEBUG).
  - `client is None` → cannot verify new tickers; only core list is returned.
  - `refresh_universe()` (separate function) may flip `Universe.active=False` when
    dollar-volume / price thresholds fail.
- **Logging/metrics.** `events_log` does **not** track verification outcomes —
  only the contract cache row holds the reason.
- **Failure modes.**
  - IBKR error → symbol cached as `verified=False` with `reason="error: …"` for TTL.
  - `SPY` or benchmark ETFs missing from DB → fallback insertion to guarantee
    regime check still works.

---

### Phase D — Market Regime Check

- **Purpose.** Single global switch; controls whether the equity bot takes entries
  and whether `score_symbol` labels a name `long` vs `bearish`.
- **Trigger / cadence.** Per bot cycle (once, via `BaseBot.run`).
- **Upstream inputs.** Daily SPY bars (`trader/market_data.fetch_bars("SPY", "1D")`)
  with in-memory cache honouring `cfg.safety.data_stale_minutes`.
- **Core logic** (`trader/strategy.py::check_regime`):
  1. Fetch ≥ 50 daily bars; if insufficient, return `risk_off`.
  2. Compute indicators (EMA20, EMA50, SMA200, ATR14, realised vol).
  3. **risk_on** iff `close > SMA200` **AND** `EMA20 > EMA50` **AND** 20-day
     realised vol < `cfg.strategy.regime.vol_threshold` (default 25%).
- **Key parameters/config.** `cfg.strategy.regime.vol_threshold`,
  `cfg.strategy.weights` (not used by regime but consumed by `score_symbol`).
- **Outputs/artifacts.** `regime: "risk_on" | "risk_off"` (Python string).
- **Downstream consumers.** `BaseBot.build_candidates`, `score_symbol`,
  `EquitySwingBot` risk-off branching (cash vs defensive sectors).
- **Decision gates.**
  - Insufficient SPY data → `risk_off` (fail-safe).
  - Indicators invalid (`valid=False`) → `risk_off`.
- **Failure modes.** IBKR unavailable + cache empty → `fetch_bars` returns empty
  DataFrame → `risk_off`.

---

### Phase E — Ranking (multi-factor composite)

- **Purpose.** Compute a 5-factor composite [0,1] score per universe symbol, set
  eligibility flags, and label each symbol bullish / bearish / neutral.
- **Trigger / cadence.** Per bot cycle (via `BaseBot.run`). Scheduler also invokes
  it at the sentiment-refresh cadence.
- **Upstream inputs.**
  - `List[UniverseItem]` from Phase C.
  - Daily OHLCV bars (`market_data.get_latest_bars` or `fetch_bars` per symbol).
  - Latest `SentimentSnapshot` rows (market + sector + ticker).
  - `SecurityMaster.options_eligible` via `compute_optionability_factor`.
- **Core logic** (`trader/ranking.py::rank_symbols` + `trader/scoring.py`):
  1. For each symbol, fetch bars (try/except — missing bars → factor "missing").
  2. Call `compute_sentiment_factor(market_snap, sector_snap, ticker_snap)` → `[0,1]`.
     Internally applies recency weighting (`>72h → stale`; `24-72h → ×0.5`).
  3. Call `compute_momentum_trend_factor(df)` — SMA200/EMA20/EMA50 trend subscore
     + scaled 63d/126d returns. Requires ≥63 bars or returns `missing`.
  4. Call `compute_risk_factor(df)` — 20d annualised vol + 252d max drawdown,
     bucketed. Requires ≥20 bars or returns `missing`.
  5. Call `compute_liquidity_factor(df, cfg)` — log-scale ADV$ + price score.
     Sets `eligible=False` when price < min_price or ADV < min_dollar_volume.
  6. Call `compute_optionability_factor(symbol)` — reads `SecurityMaster`.
     `eligible=False` when no record (safe-by-default).
  7. `compute_composite(factors, nominal_weights)` — proportional weight
     redistribution for any `value_0_1=None` factor; returns `(score, weights_used)`.
  8. `equity_eligible = liquidity.eligible AND symbol verified in IBKR`.
  9. `options_eligible = optionability.eligible`.
  10. Bias: `score ≥ enter_threshold → bullish`; `score ≤ 1-enter_threshold → bearish`.
  11. Persist all rows to `symbol_rankings` with full `components_json`.
- **Key parameters/config.**
  - `cfg.ranking.{w_sentiment, w_momentum_trend, w_risk, w_liquidity, w_fundamentals}` — nominal weights.
  - `cfg.ranking.{w_market, w_sector, w_ticker}` — sentiment sub-weights.
  - `cfg.ranking.enter_threshold` (default 0.55), `cfg.ranking.min_dollar_volume`.
  - `cfg.universe.min_price`.
- **Outputs/artifacts.** `List[RankedSymbol]` — `(symbol, sector, score_total,
  components{sentiment,momentum_trend,risk,liquidity,optionability,fundamentals},
  equity_eligible, options_eligible, eligible, reasons, sources, bias)`.
- **Downstream consumers.** `OptionsSwingBot.select_trades` (hard-gates on
  `options_eligible`), `EquitySwingBot.select_trades` (hard-gates on
  `equity_eligible`), `/rankings` dashboard (expandable factor cards via `<details>`),
  `cli.py report last-run`.
- **Decision gates.**
  - `eligible=False` → never traded; row still persisted for audit.
  - `options_eligible=False` → `OptionsSwingBot` skips the symbol.
  - `equity_eligible=False` → `EquitySwingBot` skips the symbol.
  - `select_candidates` caps at `cfg.ranking.max_candidates_total` (prefer 2 bull + 1 bear).
  - `cfg.ranking.fallback_trade_broad_etf` (default `False`) — ETF fallback when no candidates.
- **Logging/metrics.** `log.info("Ranked N symbols (E eligible, B with bias)")`.
- **Failure modes.** Missing bars → factors return `missing` → weight redistributed;
  all factors missing → composite = 0.5 (neutral placeholder, unlikely to exceed threshold).

---

### Phase F — Per-Symbol Scoring (`score_symbol`) — Legacy / Secondary Path

- **Purpose.** Produce a weighted 4-factor score (trend / momentum / volatility /
  sentiment) for a single symbol. Now used primarily by `EquitySwingBot.score_candidate`
  and the legacy `generate_signals()` path. The composite ranking score from Phase E
  is the primary driver of bot selection — Phase F refines the equity bot's per-candidate
  `ScoreBreakdown` (adds ATR14, last_price for sizing).
- **Trigger / cadence.** Called by `BaseBot.score_candidate` for each candidate
  in a cycle; also by the legacy `generate_signals()` path.
- **Upstream inputs.** Daily bars (cached), `get_latest_market_score()`,
  `get_latest_sector_score(sector)`, regime from Phase D.
- **Core logic** (`trader/strategy.py::score_symbol`):
  - Trend (0–1): `+0.5` if EMA20>EMA50, `+0.5` if close>SMA200.
  - Momentum (0–1): RSI bucketed (`<40 → 0.6`, `40–60 → 0.3`, `>60 → 0.1`),
    plus `+0.4` if MACD histogram bullish.
  - Volatility (0–1): bucketed on 20-day realised vol (`<15% → 1.0`, `<25 → 0.7`,
    `<40 → 0.4`, else `0.1`).
  - Sentiment (0–1): normalise average of market+sector scores from `-1..1` to `0..1`.
  - `total = Σ weights[i] × factor[i]`.
  - Direction label: `regime="risk_on" AND total>0.5 → long`;
    `regime="risk_off" AND total<0.35 → bearish`; else `None` (hold/skip).
- **Key parameters/config.** `cfg.strategy.weights.{trend,momentum,volatility,sentiment}`.
- **Outputs/artifacts.** `Optional[SignalIntent]` wrapped by the bot into a
  `ScoreBreakdown` (adds ATR14 + last_price for equity sizing).
- **Downstream consumers.** `build_candidates` / `select_trades` of each bot.
- **Decision gates.** `len(bars) < 50` or `compute_indicators` invalid → `None`.
  Intent returned only when the `(regime, total)` thresholds match.

---

### Phase G — Candidate Selection per Bot

- **Purpose.** Filter ranked symbols into the final set of `TradeIntent` objects
  this cycle.
- **Trigger / cadence.** After ranking + scoring, once per cycle per bot.
- **Upstream inputs.** `List[(Candidate, ScoreBreakdown)]` sorted by score.
- **Core logic.**
  - **OptionsSwingBot.select_trades** (`bots/options_swing_bot.py`): takes the
    `select_candidates(context.ranked)` result, maps bias → direction
    (`bullish→long`, `bearish→short`), and emits up to `cfg.risk.max_positions`
    intents. Score / components come from `ranked`, sizing deferred to planner.
  - **EquitySwingBot.select_trades** (`bots/equity_swing_bot.py`):
    - Compute remaining `slots_available = equity_cfg.max_positions - current`.
    - For each candidate: filter by `breakdown.final_score ≥ long_entry_threshold`
      and `direction=="long"`.
    - Call `_size_equity_trade` — ATR stop, share count, cash cap, sector concentration cap.
    - Running deduction against `available_cash` and `sector_values` within the cycle
      to avoid over-allocation.
- **Key parameters/config.**
  - Options: `cfg.risk.max_positions`, `cfg.ranking.{max_candidates_total,enter_threshold}`.
  - Equity: `cfg.bots.equity_swing.{max_positions, long_entry_threshold,
    risk_per_trade_pct, atr_stop_multiplier, atr_period, max_sector_concentration,
    risk_off_mode, defensive_sectors}`.
- **Outputs/artifacts.** `List[TradeIntent]` (`bots/base_bot.py`).
- **Downstream consumers.** `execute_intent` → planner / equity order placement.
- **Decision gates.**
  - `equity_cfg.risk_off_mode == "cash"` and regime `risk_off` → no new equity trades
    (handled in `build_candidates`).
  - `risk_off_mode == "defensive"` → only symbols whose sector is in
    `defensive_sectors` pass `build_candidates`.
  - Missing ATR or last_price → symbol skipped with a DEBUG log.
  - `shares < 1` (after risk / cash caps) → skipped.
  - Sector concentration > `max_sector_concentration × NAV` → skipped.

---

### Phase H — Options Trade Planning (`options_planner`)

- **Purpose.** Turn a ranked bullish/bearish symbol into a fully-specified debit
  spread plan **without** submitting any orders.
- **Trigger / cadence.** `OptionsSwingBot.execute_intent` per selected intent;
  also called directly by `Scheduler.run_once`.
- **Upstream inputs.** `RankedSymbol`, IBKR client (for chains + greeks),
  `cfg.ranking.{dte_min/max/target/fallback_min, cooldown_hours, max_trades_per_day}`,
  latest `EquitySnapshot` for NAV.
- **Core logic** (`trader/options_planner.py::plan_trade`):
  1. Cooldown: reject if a `proposed|approved|submitted` plan exists for this
     symbol within `cooldown_hours`.
  2. Daily cap: reject if ≥ `max_trades_per_day` plans already `approved|submitted` today.
  3. IBKR: fetch `option_chains(symbol)`; pick expiry nearest `dte_target`
     inside `[dte_min, dte_max]` else fallback to `[dte_fallback_min, dte_min)`.
  4. Greeks: `GreeksService.fetch_chain_greeks(symbol, expiry)`.
  5. `StrikeSelector.adjust_delta_for_iv(criteria, iv_rank)` then
     `select_debit_spread_strikes(chain_greeks, direction, criteria)`.
  6. `calculate_limit_price(spread)` — bid/ask midpoint.
  7. `GreeksGate.evaluate(spread, chain_greeks, "debit")` — 10-check gate.
  8. Sizing: reject if `max_loss_per_contract > NAV × max_risk_per_trade_pct%`;
     else `qty = floor(max_allowed / max_loss_per_contract)`.
- **Key parameters/config.** `cfg.ranking.*` (DTE + cadence) and all `GREEKS_*`
  env vars consumed by `StrikeSelectionCriteria` and `GreeksGate`.
- **Outputs/artifacts.** `TradePlan` row in `trade_plans` with:
  - `legs_json`: `{long_strike, short_strike, right, expiry, long_delta, short_delta, iv_rank}`.
  - `pricing_json`: `{debit_per_contract, max_loss_per_contract, max_loss_total,
    max_profit_total, spread_width, quantity}`.
  - `rationale_json`: `{score_total, components, sources, iv_environment, gate_warnings}`.
  - `status: "proposed" | "skipped"`, `skip_reason`.
- **Downstream consumers.** `trader.execution.execute_signal` (via
  `_plan_to_signal`) when `approve=False`; human approval UI otherwise; scheduler
  `_execute_plan` helper.
- **Decision gates.** `no_ibkr_client`, `no_option_chains`, `no_suitable_expiry_*`,
  `greeks_fetch_error`, `no_greeks_data`, `no_delta_matched_strikes`,
  `invalid_limit_price`, `greeks_gate: <reason>`, `max_loss_*_exceeds_limit_*`,
  cooldown, daily cap.
- **Logging/metrics.** `GreeksLogger.log_chain_fetch`, `log_strike_selection`,
  `log_gate_result`; planner writes one DB row per symbol per cycle regardless of
  outcome for full auditability.

---

### Phase I — Greeks Gate (options)

- **Purpose.** 10 independent checks on a `SpreadSelection`; all must pass.
- **Trigger / cadence.** Once per planned options trade.
- **Upstream inputs.** `SpreadSelection` (legs, Greeks, bid/ask, debit/credit),
  `OptionChainGreeks` (iv_rank), `strategy_type ∈ {"debit","credit"}`.
- **Core logic** (`trader/greeks/gate.py::GreeksGate.evaluate`):
  1. IV rank — debit: `iv_rank ≤ max_iv_rank_debit_spreads` (default 60).
  2. Delta range — short-leg |delta| inside `[min_short_leg_delta, max_short_leg_delta]`
     (0.10–0.35) and position `|net_delta| ≤ max_position_delta` (0.30).
  3. Theta — `|net_theta| ≥ min_theta_per_day` (0.01).
  4. Theta/|Delta| ratio — `≥ min_theta_to_delta_ratio` (0.02).
  5. Vega — `|net_vega| ≤ max_vega_exposure` (0.50).
  6. Gamma near expiry — if DTE < 7, `|net_gamma| ≤ 0.10`.
  7. Liquidity — each leg `(ask-bid)/mid ≤ 30%`.
  8. Pricing — `return_on_capital ≥ min_roc` (0.25); credits also require
     `estimated_credit ≥ min_credit_received`.
  9. Buffer — `buffer_pct ≥ min_buffer_pct` (0.03).
  10. Composite risk score (0-100); `>70` → warning but not blocking.
- **Key parameters/config.** All `GREEKS_*` env vars in `_default_config()`.
- **Outputs/artifacts.** `GateResult` (`approved`, `reason`, lists of passed /
  failed / warnings, `greeks_summary`).
- **Downstream consumers.** Planner and execution branches abort on `approved=False`.
- **Decision gates.** ANY failed check → rejection. `iv_rank is None` → warning
  only (not a block — spec memory).

---

### Phase J — Risk Engine (`trader/risk.py`)

- **Purpose.** Account-level safety checks applied right before any order is written.
- **Trigger / cadence.** Called by `trader.execution.execute_signal` (options) and
  `execution.equity_execution._check_equity_risk` (equity).
- **Upstream inputs.** `BotState`, latest `EquitySnapshot`, open `Order` rows
  (for cash reservation), candidate `SignalIntent` / `TradeIntent`.
- **Core logic** (`trader/risk.py::check_can_trade`):
  1. `BotState.kill_switch` → block.
  2. `BotState.paused` → block.
  3. `drawdown_pct ≥ cfg.risk.max_drawdown_pct` → block.
  4. `open_positions ≥ cfg.risk.max_positions` → block (global count for options path).
  5. `cfg.risk.require_positive_cash` and `cash ≤ 0` → block.
  6. `intent.max_risk_usd > NAV × max_risk_per_trade_pct%` → block.
  7. Cash reservation: sum `max_loss` over `pending|submitted` orders; block if
     `intent.max_risk_usd > cash - reserved`.
- **Equity variant** (`_check_equity_risk`): same kill-switch / pause / drawdown /
  cash constraints **plus** per-bot position cap filtering by `portfolio_id="equity_swing"`.
- **Key parameters/config.** `cfg.risk.{max_drawdown_pct, max_positions,
  max_risk_per_trade_pct, require_positive_cash}`.
- **Outputs/artifacts.** `(bool allowed, str reason)` tuple.
- **Downstream consumers.** Execution paths; failures are logged via `log_event`
  with type `risk_block` / `equity_risk_block`.

---

### Phase K — Equity Order Placement

- **Purpose.** Persist and optionally submit a stock limit order.
- **Trigger / cadence.** `EquitySwingBot.execute_intent(intent)` per intent.
- **Upstream inputs.** `TradeIntent` (with `quantity`, `limit_price`, `stop_price`,
  `atr`, `max_risk_usd`), optional IBKR client, `approve` flag.
- **Core logic** (`execution/equity_execution.py::place_equity_order`):
  1. Validate `quantity ≥ 1` and `limit_price > 0`.
  2. `intent_id = f"{symbol}_{direction}_{yyyymmdd}_{uuid8}"`.
  3. `_is_duplicate(intent_id)` → skip.
  4. `_check_equity_risk` (Phase J).
  5. Approve mode → insert `Order(status="pending_approval", portfolio_id="equity_swing")`.
  6. Else: build `ib_insync.Stock + IBOrder` (`action=BUY/SELL, type=LIMIT, tif=DAY`),
     `client.place_order(…)`, persist `Order(status="submitted", ibkr_order_id=…)`.
- **Key parameters/config.** `cfg.bots.equity_swing.entry_order_type` (LIMIT / MARKET).
- **Outputs/artifacts.** `Order` row; log_events `equity_signal` or
  `equity_order_submitted`.
- **Decision gates.** Any Phase J failure → `None`; IBKR exception → `None`
  + `equity_order_failed` event.

---

### Phase L — Options Order Construction & Submission

- **Purpose.** Build an IBKR BAG combo order for a debit spread and submit/queue it.
- **Trigger / cadence.** `trader.execution.execute_signal(intent)` from the bot
  or scheduler after the planner approves a spread.
- **Core logic** (`trader/execution.py::execute_signal`):
  1. Generate intent_id; duplicate guard.
  2. `compute_max_risk_for_trade` from NAV.
  3. `check_can_trade` (Phase J).
  4. `BotState.options_enabled` must be `True`.
  5. Fetch option chains, pick expiry in `[cfg.options.dte_min, cfg.options.dte_max]`.
  6. Run Greeks pipeline (Phase I). Abort on any rejection.
  7. Compute `limit_price = calculate_limit_price(spread)` and
     `per_contract_max_loss = limit_price × 100`.
  8. Build `SpreadSpec`, then `build_combo_order` → `Contract(BAG) + ComboLeg(BUY long) +
     ComboLeg(SELL short)` with `order.orderType = cfg.execution.order_type` and
     `order.tif = cfg.execution.tif`.
  9. Approve mode → `Order(status="pending_approval", instrument="bull_call_spread"/"bear_put_spread")`.
     Else → `client.place_order(combo, ib_order)` and `Order(status="submitted",
     ibkr_order_id=…)`.
- **Key parameters/config.** `cfg.options.*`, `cfg.execution.{order_type, tif,
  fill_timeout_seconds, requote_attempts}`, `cfg.risk.*`.
- **Outputs/artifacts.** `Order` row with `payload_json` containing full spec
  (spread_type, strikes, greeks, warnings). Separate `log_event("order_submitted", …)`.
- **Decision gates.** `options_enabled=False`, `no_option_chains`,
  `no_suitable_expiry`, Greeks gate fail, `per_contract_max_loss > intent.max_risk_usd`
  → skip.

---

### Phase M — IBKR Sync (reconcile broker state)

- **Purpose.** Keep `equity_snapshots`, `positions`, `orders`, `fills` in sync
  with IBKR account state.
- **Trigger / cadence.** `Scheduler.run_once` calls `full_sync` every 30 seconds
  (when `_should_sync()` is true).
- **Upstream inputs.** Live IBKR connection.
- **Core logic** (`trader/sync.py`):
  - `sync_account`: reads `NetLiquidation / TotalCashValue / Unrealized / Realized`,
    calls `record_equity_snapshot` which also computes drawdown against the
    peak NAV.
  - `sync_positions`: deletes local `positions` and repopulates from
    `client.positions()` (instruments classified stock/option/combo).
  - `sync_orders`: updates `orders.status` by IBKR orderId and inserts new `Fill`
    rows from `trade.fills`.
- **Outputs/artifacts.** Updated `equity_snapshots.drawdown_pct` — drives the
  drawdown gate in Phase J.
- **Decision gates.** Any IBKR call failure → logged, no DB write.
- **Known limitation.** Position rows are not tagged with `portfolio_id` during
  sync (IBKR doesn't know about the bot partition), so the per-bot position
  caps use only orders the bot itself wrote. See open questions.

---

### Phase N — Approval UI / Dashboard

- **Purpose.** Human approval of `pending_approval` orders; visibility into
  rankings, sentiment, risk, events.
- **Trigger / cadence.** FastAPI request-driven (`api/main.py`).
- **Core logic.** Server-rendered Jinja templates under `ui/templates/` powered
  by API routers in `api/routes/` (health, state, controls, signals, sentiment,
  trades, rankings).
- **Outputs.** Control-plane mutations write to `BotState` (kill switch, pause,
  approve mode, options enabled) and to `orders` (approve / reject / close).

---

## 4) Data contracts & artifacts catalog

| Artifact / variable | Type / shape | Producer | Consumer | Persistence | Notes |
|---|---|---|---|---|---|
| `AppConfig` | Pydantic model | `common/config.py::load_config` | Everyone | in-memory (cached) | Reset with `load_config(reload=True)` in tests |
| `UniverseItem` | dataclass `(symbol, sector, name, type, sources, verified, conid)` | `trader/universe.py::get_verified_universe` | `BaseBot`, `rank_symbols` | in-memory list | Core tickers always `verified=True`, RSS-discovered pass through IBKR check |
| `SentimentSnapshot` | ORM row `(scope, key, score∈[-1,1], summary, sources_json)` | `trader/sentiment/factory.py::_persist_snapshots` | ranking, strategy, UI | DB `sentiment_snapshots` | `scope ∈ {market, sector, ticker}` |
| `SentimentLlmItem` | ORM `(id=sha256 prefix, processed_at, url)` | Claude provider | dedup inside provider | DB `sentiment_llm_items` | TTL = `dedup_cache_days` (default 14) |
| `SentimentLlmUsage` | ORM `(prompt_tokens, completion_tokens, cost_usd_est, cost_eur_est, status)` | Claude provider | `trader/sentiment/budget.py` | DB `sentiment_llm_usage` | Source of truth for budget cap |
| `RankedSymbol` | dataclass `(symbol, sector, score_total, components{sentiment,momentum_trend,risk,liquidity,optionability,fundamentals}, equity_eligible, options_eligible, eligible, reasons, sources, bias)` | `trader/ranking.py::rank_symbols` | `OptionsSwingBot.select_trades` (gates on `options_eligible`), `EquitySwingBot.select_trades` (gates on `equity_eligible`), UI, planner | in-memory + `symbol_rankings` row per cycle | `bias ∈ {bullish, bearish, None}`; `equity_eligible` = liquidity gate + IBKR-verified; `options_eligible` = SecurityMaster safe-by-default |
| `SignalIntent` | dataclass `(symbol, direction, instrument, score, max_risk_usd, explanation, components, regime)` | `score_symbol` / `_plan_to_signal` | `execute_signal`, `generate_signals` | `signal_snapshots` (for generate_signals only) | `direction ∈ {long, bearish}` |
| `Candidate` | dataclass `(symbol, sector, source, verified)` | `BaseBot.build_candidates` | `score_candidate` | in-memory | |
| `ScoreBreakdown` | dataclass `(trend, momentum, volatility, sentiment, final_score, direction, explanations, components, atr14?, last_price?)` | `BaseBot.score_candidate` | `select_trades` | in-memory | Equity bot fills `atr14` + `last_price` |
| `TradeIntent` | dataclass `(symbol, direction, instrument_type, score, explanation, components, regime, bot_id, max_risk_usd, quantity, limit_price, stop_price, atr)` | `select_trades` | `execute_intent` | in-memory | `instrument_type ∈ {equity, options}` |
| `TradePlan` | ORM row `(symbol, bias, strategy, expiry, dte, legs_json, pricing_json, rationale_json, status, skip_reason)` | `trader/options_planner.py::plan_trade` | `execute_signal`, `/rankings` page, human approval | DB `trade_plans` | `status ∈ {proposed, approved, submitted, skipped}` |
| `SpreadSelection` | dataclass with legs, Greeks, pricing | `StrikeSelector.select_debit_spread_strikes` | `GreeksGate.evaluate`, `calculate_limit_price`, `SpreadSpec` | in-memory | |
| `GateResult` | dataclass `(approved, reason, checks_passed, checks_failed, warnings, greeks_summary)` | `GreeksGate.evaluate` | Planner + `execute_signal` | in-memory; summary embedded in `orders.payload_json` | |
| `Order` | ORM row `(intent_id, symbol, direction, instrument, portfolio_id, quantity, order_type, limit_price, status, ibkr_order_id, max_loss, payload_json)` | `execute_signal` / `place_equity_order` | risk checks, UI, `sync_orders` | DB `orders` | `status` lifecycle: `pending_approval → submitted → filled/cancelled/rejected` |
| `Fill` | ORM row `(order_id, timestamp, qty, price, commission)` | `sync_orders` | UI, P&L | DB `fills` | Source: IBKR `trade.fills` |
| `Position` | ORM row `(symbol, quantity, avg_cost, market_price, market_value, unrealized_pnl, instrument, portfolio_id)` | `sync_positions` | risk, `_get_sector_values`, UI | DB `positions` | Rebuilt each sync; `portfolio_id` not auto-populated from IBKR |
| `EquitySnapshot` | ORM row `(net_liquidation, cash, unrealized, realized, drawdown_pct)` | `trader/risk.py::record_equity_snapshot` | risk checks, equity bot cash math, UI | DB `equity_snapshots` | Peak NAV across all rows drives drawdown |
| `BotState` | ORM row (singleton id=1) `(paused, kill_switch, options_enabled, approve_mode, last_heartbeat)` | startup / controls endpoint | risk gates, scheduler heartbeat | DB `bot_state` | Default `approve_mode=True` |
| `EventLog` | ORM row `(level, type, message, payload_json)` | `log_event`, `_log_event` | UI `/overview`, debugging | DB `events_log` | Types include `startup, signal, order_submitted, risk_block, greeks_gate_reject, sentiment_refresh_success` |
| `ContractVerificationCache` | ORM row `(symbol, verified, checked_at, conid, primary_exchange, reason)` | `verify_contract` | `get_verified_universe` | DB `contract_verification_cache` | TTL = `cfg.ranking.contract_cache_hours` (24h) |
| `SymbolRanking` | ORM row `(ts, symbol, score_total, components_json, eligible, reasons_json)` | `_persist_rankings` | UI `/rankings`, `report last-run` | DB `symbol_rankings` | One batch per `ts` |
| `SecurityMaster` | ORM row `(symbol PK, name, exchange, security_type, currency, active, market_cap, avg_dollar_volume_20d, options_eligible, ibkr_conid, updated_at)` | `trader/securities/master.py::import_csv` | `SecurityAlias` JOIN queries in matcher + RSS loader | DB `security_master` | Auto-seeded from `data/us_listed_master.csv` on first `create_tables()` |
| `SecurityAlias` | ORM row `(alias PK, symbol, alias_type, priority, created_at)` | `import_csv` / `load_manual_overrides` | `matcher.match_companies_to_symbols`, `rss_provider._load_ticker_aliases` | DB `security_alias` | `alias_type ∈ {normalized_name, symbol, short_name, manual}`; lower priority = higher precedence |
| `RssEntityMatch` | ORM row `(id, article_id, company_input, normalized_input, symbol, match_type, match_score, reason, created_at)` | `matcher.match_companies_to_symbols` | Debugging / audit UI | DB `rss_entity_matches` | One row per company name per article; `symbol=NULL` when no match or ambiguous |
| In-memory bars cache | `Dict[(symbol, timeframe), (ts, DataFrame)]` | `trader/market_data.fetch_bars` | indicators, regime, scoring, ATR | in-memory (process-local) | TTL = `cfg.safety.data_stale_minutes` (default 15) |

---

## 5) Control flow & state

### Main entrypoints

- **CLI** (`cli.py`) — preferred:
  - `python cli.py run <bot>|all --mode paper|live [--once] [--approve/--no-approve] [--dry-run] [--refresh-sentiment/--no-refresh-sentiment]`
  - `python cli.py sentiment refresh [--source rss_lexicon|claude_llm] [--dry-run]`
  - `python cli.py report last-run [--bot ...] [--json-out]`
- **Legacy trader daemon** (`trader/main.py`) — calls `Scheduler.run()` with a 10 s heartbeat.
- **API server** (`uvicorn api.main:app`) — separate process, no trading, only DB reads + control-plane writes.
- **`scripts/run_all.py`** — spawns API + trader as subprocesses (legacy).

### Run loop (per `BaseBot.run`)

1. `check_regime(client)` → `"risk_on"|"risk_off"` (errors demoted to `risk_off`).
2. `get_verified_universe(client)` → `List[UniverseItem]`.
3. `rank_symbols(universe)` → `List[RankedSymbol]` + DB write.
4. `build_candidates(context)` → bot-specific filter.
5. For each candidate: `score_candidate` (returns `None` → skipped with reason).
6. Sort scored by `final_score` desc; `select_trades` → `List[TradeIntent]`.
7. For each intent:
   - `dry_run=True` → log only, count as executed.
   - Else → `execute_intent(intent, context)` (per-bot: plan+execute or place_equity_order).
8. Produce `BotRunResult` with counts and errors.

### State carried across cycles

- **Database** is the source of truth — nothing important is kept purely in-memory:
  - `sentiment_snapshots`: consumed via "latest row per `(scope, key)`".
  - `symbol_rankings`: persisted per cycle, read back by UI.
  - `orders`: duplicate/`intent_id` guard; pending orders reserve cash via `max_loss`.
  - `trade_plans`: cooldown + daily cap.
  - `equity_snapshots`: peak NAV used for drawdown.
  - `bot_state` (singleton): kill switch / pause / approve mode / last heartbeat.
- **Process-local caches** (lost on restart):
  - `trader/market_data._cache` — IBKR bars, TTL `cfg.safety.data_stale_minutes`.
  - `common.config._cached` — parsed `AppConfig`.
  - `trader/sentiment/factory._REFRESH_LOCK` — prevents overlapping refresh.
- **Scheduler fields** (`Scheduler.__init__`):
  `_last_sentiment`, `_last_signal`, `_last_ranking`, `_last_rebalance` (date string),
  `_last_sync` — none are persisted, so restarts immediately re-trigger everything.

### Idempotency

- `intent_id = {symbol}_{direction}_{yyyymmdd}_{uuid8}` with unique index on
  `orders.intent_id`. `check_duplicate_intent` / `_is_duplicate` guard both paths.
- `TradePlan` cooldown (`cooldown_hours`) prevents re-proposing the same symbol
  even across restarts.
- `ContractVerificationCache` prevents repeated IBKR lookups within 24h.
- `SentimentLlmItem` prevents re-sending the same RSS items to Claude.
- `Universe` `active` flag and `SEED_TICKERS` guarantee the seed list survives restarts.

---

## 6) Decision logic index — "Where to change what"

| # | Concern | File(s) | Phase |
|---|---|---|---|
| 1 | Which tickers are tradable (core seed list) | `trader/universe.py::SEED_TICKERS` | C |
| 2 | How RSS-discovered tickers are added to the universe | `trader/universe.py::get_verified_universe`; `get_recent_ticker_scores` in `trader/sentiment/scoring.py` | C |
| 3 | Liquidity / price thresholds for universe activation | `cfg.universe.min_price`, `cfg.universe.min_dollar_volume`; `trader/universe.py::refresh_universe` | C |
| 4 | IBKR contract verification (OTC/PINK rejection, currency) | `trader/universe.py::verify_contract` | C |
| 5 | Market regime definition | `trader/strategy.py::check_regime` (+ `cfg.strategy.regime.vol_threshold`) | D |
| 6 | 4-factor scoring (weights, RSI buckets, vol buckets) | `trader/strategy.py::score_symbol` (+ `cfg.strategy.weights`) | F |
| 7 | Sentiment weight & recency penalty | `trader/scoring.py::_compute_score`, `_apply_recency` (+ `cfg.ranking.w_market/w_sector/w_ticker`); re-exported from `trader/ranking.py` | E |
| 8 | Bullish/bearish bias threshold | `cfg.ranking.enter_threshold` (default 0.55); `≥threshold → bullish`, `≤1-threshold → bearish` in `rank_symbols`; `select_candidates` sizes the split | E |
| 9 | Options DTE window + expiry target | Canonical: `cfg.options.{planner_dte_min, planner_dte_max, planner_dte_target, planner_dte_fallback_min}` (consumed by `options_planner._select_expiry`). `cfg.ranking.dte_*` kept for backward compat (deprecated). `cfg.options.dte_min/max` still used by legacy `trader.execution` path. | H / L |
| 10 | Delta targets for strikes | `StrikeSelectionCriteria` defaults + `GREEKS_*` env vars in `trader/greeks/strike_selector.py` | H |
| 11 | IV-adjusted criteria (low/high IV branch) | `StrikeSelector.adjust_delta_for_iv` | H |
| 12 | Greeks gate thresholds (delta, theta, vega, gamma, liquidity, ROC, buffer) | `trader/greeks/gate.py::_default_config` and `GREEKS_*` env vars | I |
| 13 | Per-trade max risk, drawdown, positions, positive-cash | `cfg.risk.*`; checks in `trader/risk.py::check_can_trade` | J |
| 14 | Equity position sizing (ATR, cash cap, sector cap) | `bots/equity_swing_bot.py::_size_equity_trade` (+ `cfg.bots.equity_swing.*`) | G |
| 15 | Equity entry threshold / long-only | `cfg.bots.equity_swing.long_entry_threshold`, `long_only` | G |
| 16 | Equity risk-off behaviour (cash vs defensive) | `cfg.bots.equity_swing.risk_off_mode`, `defensive_sectors` | G |
| 17 | Order routing (type, TIF, limit pricing) | `cfg.execution.*` (options); `cfg.bots.equity_swing.entry_order_type`; `build_combo_order` (options) | K / L |
| 18 | Planner cadence (cooldown, daily cap) | `cfg.ranking.cooldown_hours`, `cfg.ranking.max_trades_per_day`; `_check_cooldown`, `_check_max_trades_today` | H |
| 19 | Scheduler cadence (sentiment / signal / rebalance / sync) | `cfg.scheduling.*` + `cfg.sentiment.refresh_minutes`; `trader/scheduler.py::Scheduler._should_*` | A |
| 20 | Sentiment provider selection | `cfg.sentiment.provider` ∈ `{rss_lexicon, claude_llm}`; env `SENTIMENT_PROVIDER`; `trader/sentiment/factory.py::build_provider` | B |
| 21 | Claude budget cap (€10/mo) + pricing table | `cfg.sentiment.claude.{monthly_budget_eur, daily_budget_fraction, eur_usd_rate, hard_stop_on_budget}`; `trader/sentiment/budget.py::MODEL_PRICING_USD_PER_MTOK` | B |
| 22 | RSS feeds | `cfg.sentiment.rss.feeds` (YAML) | B |
| 23 | Approve mode default | `cfg.features.approve_mode_default`; env `APPROVE_MODE_DEFAULT`; `BotState.approve_mode` | A / K / L |
| 24 | Kill switch / pause toggles | `/controls` UI → mutates `BotState`; enforced in `trader/risk.py::check_can_trade` and `execution/equity_execution.py::_check_equity_risk` | J |
| 25 | Portfolio isolation tag | `execution/equity_execution.py::_PORTFOLIO_ID`; `common/models.py::Order.portfolio_id`, `Position.portfolio_id`, `Trade.portfolio_id` | K |
| 26 | `data/sp500.csv` universe ingestion | **Not found** — file exists but no loader reads it. Should live in `trader/universe.py` (e.g., new `load_sp500_csv()` invoked from `seed_universe`). | — |
| 27 | Position exit logic (trailing stop, max_holding_days) | **Not found** — `cfg.strategy.max_holding_days` and `cfg.bots.equity_swing.max_holding_days` are declared but not wired. Should live in `trader/scheduler.py` or a dedicated `trader/exits.py` module invoked from `run_once`. | — |
| 28 | `close_all` control | **Partial** — only flips the kill switch via `/api/controls`; actual IBKR position close is not implemented. Should call `client.place_order` with closing legs in `api/routes/controls.py`. | — |
| 29 | Security master seed / CSV import | `data/us_listed_master.csv` (source CSV); `trader/securities/master.py::import_csv` (loader + alias gen); `common/db.py::_auto_seed_security_master` (auto-called from `create_tables`); `data/manual_alias_overrides.csv` (priority-1 manual aliases) | B |
| 30 | Company name normalisation (suffix stripping, punctuation, "health care"→"healthcare") | `trader/securities/normalize.py::normalize_company_name` | B |
| 31 | Alias generation rules (which aliases get written per security) | `trader/securities/normalize.py::generate_aliases`; alias types: `normalized_name` (priority 10), `symbol` (5), `short_name` (50), `manual` (1) | B |
| 32 | Company→ticker matching logic (exact, ambiguity, audit) | `trader/securities/matcher.py::match_companies_to_symbols`; ambiguous = 2+ different symbols → skipped | B |
| 33 | RSS ticker detection filter (which aliases are scanned in headlines) | `trader/sentiment/rss_provider.py::_load_ticker_aliases` — ≥2-word aliases or manual priority≤1 with len≥5; avoids short single-word false positives | B |
| 34 | Allowed exchanges + liquidity thresholds for security master | `cfg.securities.{allowed_exchanges, min_price, min_avg_dollar_volume_20d}`; filters applied in `_load_ticker_aliases` JOIN | B |
| 35 | Composite factor weights (5 factors, proportional redistribution) | `cfg.ranking.{w_sentiment, w_momentum_trend, w_risk, w_liquidity, w_fundamentals}`; `trader/scoring.py::compute_composite` | E |
| 36 | Momentum/trend factor (SMA200, EMA trend, 63d/126d returns) | `trader/scoring.py::compute_momentum_trend_factor`; requires ≥63 bars | E |
| 37 | Risk factor (vol buckets, drawdown buckets) | `trader/scoring.py::compute_risk_factor`; vol weight 0.6, drawdown weight 0.4 | E |
| 38 | Liquidity factor + equity_eligible gate | `trader/scoring.py::compute_liquidity_factor`; log-scale ADV$ score; gate: price ≥ min_price AND ADV ≥ min_dollar_volume | E |
| 39 | Options eligibility gate (safe-by-default) | `trader/scoring.py::compute_optionability_factor`; reads `SecurityMaster.options_eligible`; returns `eligible=False` when no DB record | E |
| 40 | Fundamentals factor (stub) | `trader/scoring.py::compute_fundamentals_factor`; always returns `missing` until IBKR entitlement wired; `cfg.fundamentals.enabled` | E |

---

## 7) Observability & troubleshooting

### What logs exist

- **Python logging** — `common/logging.setup_logging()`; module-scope loggers
  (e.g., `trader.strategy`, `trader.execution`). Stream to stdout by default.
- **`events_log` table** — structured audit trail with `type ∈
  {startup, signal, order_submitted, order_failed, risk_block, greeks_fetch_failed,
  greeks_empty, no_strikes, greeks_gate_reject, equity_signal, equity_risk_block,
  equity_order_submitted, equity_order_failed, sentiment_refresh_success}`.
- **Greeks logger** — `trader/greeks/logger.py::GreeksLogger` writes chain-fetch,
  strike-selection, gate results, and entry-time Greeks. Writes are intertwined
  with `events_log` and the trade's `payload_json`.
- **Dashboard** — `/overview` (events + NAV), `/rankings` (latest ranking + recent
  plans), `/sentiment` (latest per scope, budget status), `/risk` (drawdown chart),
  `/orders`, `/positions`, `/signals`.

### Gaps to close for easier debugging

- **No correlation ID across phases.** The planner logs with `intent_id` only at
  the very end; ranking / universe / score don't have one at all. Threading a
  `cycle_id` (UUID per bot run) into every `events_log` row would make end-to-end
  traces trivial.
- **Structured logs.** Python logging is formatted as strings; moving to JSON
  (e.g., via `structlog`) would let you query by symbol / phase / regime.
- **Sync/regime/universe events are missing from `events_log`.** Good candidates
  to add: `regime_change`, `universe_refresh`, `ranking_cycle`, `sync_error`.
- **No fill latency / slippage tracking.** A derived view over `orders` vs
  `fills` keyed by `intent_id` would catch execution quality regressions.

### "Trade didn't happen" — top 10 things to check

1. `BotState.kill_switch == True` or `BotState.paused == True` (see `/controls`).
2. Bot disabled: `cfg.bots.<name>.enabled = false` → skipped before any phase.
3. `approve_mode = True` (default): the order is `pending_approval`, not submitted.
   Check `/orders` and approve manually.
4. IBKR offline: CLI printed "IBKR connection failed — offline mode"; options
   planner writes `status="skipped", skip_reason="no_ibkr_client"`.
5. Regime risk-off + equity bot in `risk_off_mode="cash"` → `build_candidates`
   returned empty; see log: `Risk-off + cash mode — no new candidates.`
6. Score below threshold: `final_score < cfg.bots.equity_swing.long_entry_threshold`
   or `score_symbol` returned `None` (direction gate). Check `symbol_rankings` + logs.
7. Greeks gate reject — look for `greeks_gate_reject` in `events_log` and inspect
   `checks_failed`.
8. No delta-matched strikes — `no_strikes` / `no_delta_matched_strikes` in events
   log; often an expiry with thin chain or a ticker with no 0.20-delta short leg.
9. Risk check blocked — `risk_block` / `equity_risk_block` event with reason
   (drawdown cap, max positions, insufficient cash after reservation).
10. Cooldown / daily cap — `trade_plans.skip_reason` contains `cooldown:` or
    `max_trades_per_day:`. Flush `trade_plans` or wait `cfg.ranking.cooldown_hours`.

Bonus checks:
- `signal_snapshots` may be empty while `symbol_rankings` is populated — those
  are different paths (`generate_signals` vs `rank_symbols`).
- Sentiment stale (`age > 72h`) zeros out the sentiment factor; check
  `SentimentSnapshot.timestamp` on `/sentiment`.

### "Ticker snapshots not appearing" — additional checks

If the `/sentiment` dashboard shows market/sector rows but no ticker rows:

1. **Security master empty** — run `python cli.py securities import` (or check
   `SELECT COUNT(*) FROM security_master`). The table auto-seeds on first `create_tables()`
   from `data/us_listed_master.csv`; if that CSV was missing, the seed silently skipped.
2. **API container not restarted** — code changes to `rss_provider.py` or `factory.py`
   require a container restart; the DB may contain ticker rows while the UI still shows
   stale pre-change data from a previous deploy.
3. **RSS provider with no alias matches** — the RSS path only scans multi-word aliases
   and long single-word manual overrides. If the alias table has only short or ambiguous
   entries, no tickers fire. Check `SELECT * FROM security_alias LIMIT 20`.
4. **Claude provider: `mentioned_companies` empty** — the LLM may return empty
   `mentioned_companies` lists for generic macro headlines. Check `sentiment_llm_items`
   and inspect `response_json`. The matcher also logs a warning when the alias table
   is empty.
5. **Ambiguous aliases** — if the same alias resolves to 2+ different symbols, it is
   silently dropped. Check `SELECT * FROM rss_entity_matches WHERE reason='ambiguous'`.
6. **Wrong DB file** — if running bare-metal alongside Docker, confirm both
   processes use the same `DATABASE_URL` / `app.db` path.

---

## 8) Open questions / ambiguities

1. **`data/sp500.csv` is not read anywhere.** The README and CLAUDE.md state it
   exists for reference; `trader/universe.py::SEED_TICKERS` is the only source of
   seeded symbols.
   - *Why it matters:* the ~130 extra names won't be ranked or traded.
   - *How to verify:* `grep -R "sp500.csv" .` — only the README/CLAUDE.md
     mention it; no loader or tests reference it.

2. **Two parallel scoring paths.** `BaseBot` uses `score_symbol` directly, while
   `Scheduler.run_once()` also runs `generate_signals()` and the legacy
   `execute_signal` on a daily rebalance.
   - *Why it matters:* a bot run via CLI can produce pending orders, and the
     scheduler running in parallel can produce more at rebalance time; ordering
     and duplicate protection rely only on `intent_id` randomness.
   - *How to verify:* inspect `trader/scheduler.py::run_once` for both
     `generate_signals` and `plan_trade` branches and check whether CLI-run
     orders fight with scheduler-run orders in `orders.portfolio_id`.

3. **`Position.portfolio_id` is not populated by `sync_positions`.**
   - *Why it matters:* per-bot position caps rely on `Position.portfolio_id`,
     but the sync wipes and re-inserts positions without a tag. Caps may be
     under-counted after a restart or after a manual trade.
   - *How to verify:* `SELECT symbol, portfolio_id FROM positions` after a sync.
     Fix candidate: reconcile using `orders.symbol → portfolio_id` at sync time.

4. **Kill-switch blocks new orders but does not close existing ones.**
   - *How to verify:* `api/routes/controls.py::close_all` flips the kill switch
     only. Needs an explicit IBKR close routine before "close_all" lives up to
     its name.

5. **Equity `exit_threshold` (0.45) and `max_holding_days` are declared but not
   wired.** No exit / trailing-stop loop exists.
   - *How to verify:* `grep -R "exit_threshold\|max_holding_days" trader/ bots/ execution/`
     — only config reads; no caller acts on the value.

6. **EUR/USD FX conversion is missing** across risk / budget math (budget is
   computed in EUR while broker flow is USD).
   - *How to verify:* `cfg.sentiment.claude.eur_usd_rate` is a static constant
     (1.08); all position/risk math is in USD.

7. ~~**Legacy vs new options DTE config.**~~ **RESOLVED** — canonical DTE now lives
   in `cfg.options.{planner_dte_min, planner_dte_max, planner_dte_target,
   planner_dte_fallback_min}`; `options_planner._select_expiry` uses these via the
   `_dte_param(obj, attr, fallback)` helper with `isinstance(v, int)` guard.
   `cfg.ranking.dte_*` are kept with a deprecation comment; `cfg.options.dte_min/max`
   still used by the legacy `trader.execution` executor path only.

8. ~~**`BaseBot.build_candidates` vs `ranked` decoupling.**~~ **RESOLVED** —
   `OptionsSwingBot.select_trades` now hard-gates on `rs.options_eligible=False`
   (skips the symbol) and uses the 5-factor composite `score_total` from `ranked`
   for all selection logic. `EquitySwingBot.select_trades` similarly gates on
   `rs.equity_eligible=False`. The composite score from Phase E is now the primary
   selection driver for both bots; `score_candidate` (Phase F) provides the per-candidate
   `ScoreBreakdown` with ATR14/last_price used for equity sizing, not filtering.
