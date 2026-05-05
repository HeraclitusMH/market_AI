# Market AI — End-to-End Pipeline Documentation

> Logical phases, inputs/outputs, and decision gates for the automated swing-trading bot.
> Two instrument types: **debit-spread options** (`OptionsSwingBot`) and **equity shares** (`EquitySwingBot`).

---

## 1) Executive Summary

- Two plug-in bots on a shared lifecycle: **regime → universe → rank → exit → score → select → execute**.
- **Market regime**: 3-state engine (`risk_on`/`risk_reduced`/`risk_off`) from 4 pillars with asymmetric hysteresis. Controls entry permissions, sizing, stop tightening, and score thresholds.
- **Universe**: persisted `universe` table from `data/sp500.csv`, dynamic CSVs, embedded ETFs, RSS-discovered tickers verified via IBKR.
- **Sentiment**: pluggable providers (`rss_lexicon`|`claude_llm`|`claude_routine`|`mock`). Claude LLM has hard EUR10/mo cap, no fallback to lexicon. Routine reads pre-computed `data/sentiment_output.json`.
- **Ranking**: 6-factor composite (Quality, Momentum, Growth, Sentiment, Technical, subtractive Risk Penalty) with regime-adaptive swing profiles. All required scores must be present for eligibility.
- **Exit management**: priority-ordered rule stacks (8 equity + 8 options rules) fire before new entries each cycle.
- **Options**: plan → Greeks → gate → BAG combo order. Debit spreads only.
- **Equity**: ATR-based sizing, sector concentration caps, long-only v1.
- **Risk gates**: kill switch → pause → drawdown → max positions → cash → per-trade risk → cash reservation → duplicate-intent guard.
- **Approve mode ON by default**: orders are `pending_approval` until human review.
- **Portfolio isolation**: `portfolio_id` on every Order/Position/Trade. Unattributed positions count conservatively.
- **Company→ticker matching**: deterministic via `security_master` + `security_alias`. Claude emits company names (not tickers); matcher resolves post-LLM.
- **Idempotency**: `intent_id`, planner cooldown, contract verification cache, LLM dedup, seen_articles.json.

---

## 2) System Map

```mermaid
flowchart TD
  CLI["cli.py run"] -->|mode, approve, dry_run| BOT["BaseBot.run()"]
  SCHED["Scheduler (10s heartbeat)"] -->|cadence| SYNC
  SCHED -->|refresh_minutes| SENT
  SCHED -->|rank cadence| RANK

  subgraph Ingestion
    SENT["Sentiment refresh"]
    SYNC["IBKR sync"]
    BARS["Market data (fetch_bars)"]
  end

  SENT -->|writes| DB_SENT["(sentiment_snapshots)"]
  SYNC -->|writes| DB_EQUITY["(equity_snapshots, positions, orders, fills)"]

  subgraph AnalysisPerCycle
    REGIME["check_regime() 3-state"]
    UNI["get_verified_universe()"]
    RANK["rank_symbols() 6-factor"]
  end

  BOT --> REGIME
  BOT --> UNI
  UNI -->|List[UniverseItem]| RANK
  DB_SENT --> RANK
  RANK -->|writes| DB_RANK["(symbol_rankings)"]
  RANK -->|List[RankedSymbol]| BOT

  subgraph SelectionAndGates
    EXIT["ExitManager (8 rules)"]
    BUILD["build_candidates()"]
    SELECT["select_trades()"]
    PLAN["options_planner.plan_trade()"]
    GREEKS["GreeksService → StrikeSelector → GreeksGate"]
    SIZE["_size_equity_trade() (ATR)"]
    RISK["risk.check_can_trade()"]
  end

  BOT --> EXIT
  EXIT --> BUILD --> SELECT
  SELECT -->|options| PLAN --> GREEKS
  SELECT -->|equity| SIZE

  subgraph Execution
    EXEC_OPT["execute_signal() BAG combo"]
    EXEC_EQ["place_equity_order() STK LIMIT"]
    IBKR["IBKR via ib_insync"]
  end

  GREEKS --> EXEC_OPT --> RISK
  SIZE --> EXEC_EQ --> RISK
  RISK -->|pending_approval OR submitted| DB_ORD["(orders)"]
  EXEC_OPT --> IBKR
  EXEC_EQ --> IBKR
```

---

## 3) Pipeline Phases

### Phase A — Startup & Orchestration

**Purpose**: Load config, create/upgrade DB, connect IBKR, start bot cycles.

- Config: `config.yaml` → `config.example.yaml` → defaults (Pydantic). Env overrides applied.
- CLI path: `cli.py::_setup` → `load_config` → `create_tables` → `_make_bot`.
- Scheduler path: `trader/main.py` → `Scheduler.run()` (10s heartbeat, per-task intervals).
- Bot skipped if `cfg.bots.<name>.enabled=False`. IBKR failure → offline-mode fallback.

**Key config**: `scheduling.{sentiment_refresh_minutes, signal_eval_minutes, rebalance_time_local}`, `features.approve_mode_default`, `bots.*.enabled`.

---

### Phase B — Sentiment Refresh

**Purpose**: Produce market/sector/ticker sentiment scores for ranking and scoring.

**Triggers**: Scheduler cadence (`sentiment.refresh_minutes`), CLI `sentiment refresh`, `POST /api/v1/rankings/refresh`, local routine file watcher (debounced).

**Provider logic**:
- **RSS lexicon**: fetches feeds, scans headlines with word-boundary alias matching (multi-word aliases + long manual overrides only).
- **Claude LLM**: dedup via `sentiment_llm_items`, budget cap via `sentiment_llm_usage`, emits `mentioned_companies` (not tickers), post-LLM matcher resolves to symbols. No fallback to lexicon on failure.
- **Claude Routine**: reads `data/sentiment_output.json` (local or GitHub URL), validates schema, checks staleness, clamps scores. Never calls APIs or writes `seen_articles.json`.
- **Routine fetch helper** (`scripts/routine_fetch_articles.py`): bot-external, runs on Anthropic cloud, fetches/dedups RSS into temp `data/_pending_analysis.json`.

**Outputs**: `SentimentSnapshot` rows (`scope in {market, sector, ticker}`).

**Decision gates**:
- Claude fails → `status=failed`, no fallback, empty snapshots written.
- Routine stale → no new snapshots, preserves last good DB rows.
- Budget exceeded → provider aborts.
- Ambiguous alias (2+ symbols) → skipped with audit row.
- Lock held → `status=skipped`.

---

### Phase C — Universe Build & Contract Verification

**Purpose**: Produce the tradeable ticker list with sector + verification.

- Core items from `Universe` table (`active=True`).
- RSS-discovered tickers verified via `IB.reqContractDetails` (rejects non-USD/OTC/Pink).
- Verification cached 24h in `contract_verification_cache`.
- **Output**: `List[UniverseItem]` with `symbol, sector, name, type, sources, verified, conid`.

---

### Phase D — Market Regime Check

**Purpose**: Produce global risk posture and downstream trading effects.

**Logic** (`trader/regime/engine.py`):
1. Compute 4 pillar scores (trend, breadth, volatility, credit stress).
2. Confidence-weighted composite (0-100) using `cfg.regime.weights`.
3. Map to raw state via `cfg.regime.thresholds`.
4. Resolve through `RegimeStateMachine` (2 to degrade, 3 to recover, no skip).
5. Attach effects from `cfg.regime.effects.<level>`.
6. Persist `RegimeSnapshot` for restart recovery.

**Fallback**: `cfg.regime.enabled=False` → legacy binary SPY check. Data quality `"fallback"` → use configured fallback state.

**Output**: `RegimeState` with level, score, pillars, transition, effects. Backward-compatible with `state == "risk_on"`.

---

### Phase E — Ranking (6-factor composite)

**Purpose**: Score each universe symbol [0,1], set eligibility, assign bias labels.

**Logic** (`trader/ranking.py` + `trader/composite_scorer/`):
1. Select one weight profile for the whole cycle from the current regime: `aggressive_swing` for `risk_on`, `defensive_swing` for `risk_reduced` and `risk_off`.
2. Per symbol: fetch bars, build adapter inputs (sentiment, momentum, risk, liquidity, optionability, fundamentals).
3. `CompositeScorer.score()` → Quality, Momentum, Growth, Sentiment, Technical, Risk Penalty.
4. Build eligibility: `equity_eligible = liquidity.eligible AND verified AND no missing scores`.
5. Bias: eligible + score >= 0.48 → bullish; <= 0.35 → bearish; missing scores → `bias=None`.
6. Persist to `symbol_rankings` with `components_json.composite_6factor` and `weight_profile_used` (authoritative).

**Key adapter details**:
- Composite formula: `technical*W_tech + momentum*W_mom + sentiment*W_sent + quality*W_qual + growth*W_grow - risk_penalty*W_risk`, clamped to `[0,1]`.
- `aggressive_swing`: technical 0.30, momentum 0.25, sentiment 0.20, quality 0.05, growth 0.05, risk_penalty 0.15.
- `defensive_swing`: technical 0.25, momentum 0.20, sentiment 0.15, quality 0.10, growth 0.10, risk_penalty 0.20.
- Sentiment: recency-weighted (>72h stale, 24-72h x0.5).
- Momentum: needs >=63 bars (SMA200/EMA20/EMA50 + 63d/126d returns).
- Risk: 20d vol + 252d max drawdown.
- Liquidity: ADV$ + price gate (eligibility only, not scored).
- Fundamentals: yfinance via `FundamentalScorer`; Quality/Growth consume profitability and growth pillars.

**Decision gates**:
- Any required score missing → `eligible=False`, `reasons` includes `missing_score_<factor>`.
- `options_eligible=False` → OptionsSwingBot skips.
- `equity_eligible=False` → EquitySwingBot skips.
- Max candidates capped at `cfg.ranking.max_candidates_total`.

---

### Phase F — Exit Management

**Purpose**: Evaluate open positions and generate close orders BEFORE new entries.

**Logic** (`trader/exits.py::ExitManager`):
1. Query `TradeManagement` rows for current `portfolio_id`.
2. Run instrument-specific rule stack in priority order.
3. First full-exit stops evaluation; partial-profit doesn't block subsequent rules.
4. Update trailing stops, watermarks, counters.

**Equity exit rules** (priority order):
| # | Rule | Urgency |
|---|---|---|
| 0 | Hard stop hit | immediate (MKT) |
| 1 | Max holding days | normal (LMT) |
| 2 | Profit target (full at `profit_target_r` R) | normal |
| 3 | Partial profit (at `partial_profit_r` R, once) | normal |
| 4 | Regime change exit | normal |
| 5 | Score degradation (N cycles below threshold) | end_of_day (MOC) |
| - | Trailing stop ratchet (management only) | - |
| - | Regime tighten stop (side-effect) | - |

**Options exit rules** (priority order):
| # | Rule | Urgency |
|---|---|---|
| 0 | Max loss stop | immediate |
| 1 | DTE threshold (gamma risk) | immediate |
| 2 | Profit target | normal |
| 3 | Regime change exit | normal |
| 4 | IV crush exit | normal |
| 5 | Delta drift exit | normal |
| 6 | Score degradation | end_of_day |
| 7 | Theta bleed | end_of_day |

**TradeManagement lifecycle**: Created on order placement → Updated every cycle → Deleted on full exit.

---

### Phase G — Candidate Selection

**Purpose**: Filter ranked symbols into `TradeIntent` objects.

- **OptionsSwingBot**: maps bias → direction, emits up to `max_positions` intents.
- **EquitySwingBot**: filters by `long_entry_threshold`, calls `_size_equity_trade` (ATR stop, shares, cash cap, sector cap), deducts running cash/sector allocation.

**Decision gates**:
- `risk_off_mode="cash"` + risk_off → no new equity trades.
- `risk_off_mode="defensive"` → only defensive sectors pass.
- Missing ATR/price → skipped. `shares < 1` → skipped.
- Sector concentration > max → skipped.

---

### Phase H — Options Trade Planning

**Purpose**: Turn a ranked symbol into a debit spread plan WITHOUT submitting orders.

**Logic** (`trader/options_planner.py::plan_trade`):
1. Cooldown + daily cap checks.
2. Fetch option chains, pick expiry nearest target DTE.
3. Greeks pipeline: `GreeksService` → `StrikeSelector` (IV-adjusted delta) → spread selection.
4. `calculate_limit_price` (bid/ask midpoint).
5. `GreeksGate.evaluate` (10 checks).
6. Sizing: `qty = floor(max_allowed / max_loss_per_contract)`.

**Output**: `TradePlan` row with `legs_json`, `pricing_json`, `rationale_json`, `status` (proposed/skipped).

**Skip reasons**: no_ibkr_client, no_option_chains, no_suitable_expiry, greeks_gate rejection, max_loss exceeds limit, cooldown, daily cap.

---

### Phase I — Greeks Gate (10 checks)

All must pass on a `SpreadSelection`:

1. IV rank <= max (debit: 60)
2. Delta range (short leg 0.10-0.35, net <= 0.30)
3. Theta >= min (0.01/day)
4. Theta/Delta ratio >= 0.02
5. Vega <= max (0.50)
6. Gamma near expiry (DTE<7: <=0.10)
7. Liquidity (spread/mid <= 30%)
8. Pricing (ROC >= 0.25)
9. Buffer >= 3%
10. Composite risk score (>70 = warning only)

`iv_rank is None` → warning only, not a block.

---

### Phase J — Risk Engine

**Purpose**: Account-level safety checks before any order is written.

**Checks** (`trader/risk.py::check_can_trade`):
1. Kill switch → block
2. Paused → block
3. Drawdown >= max → block
4. Open positions >= max → block
5. Cash <= 0 (if required) → block
6. Per-trade risk > NAV x max_pct → block
7. Cash reservation (sum pending max_loss) insufficient → block

Equity variant adds per-bot position cap filtered by `portfolio_id`.

**Output**: `(bool allowed, str reason)`.

---

### Phase K — Equity Order Placement

**Logic** (`execution/equity_execution.py::place_equity_order`):
1. Validate quantity/price.
2. Generate `intent_id`, check duplicate.
3. Risk check (Phase J).
4. Approve mode → `Order(status="pending_approval", portfolio_id="equity_swing")`.
5. Else → build `ib_insync.Stock + IBOrder`, place, persist as `submitted`.

---

### Phase L — Options Order Construction

**Logic** (`trader/execution.py::execute_signal`):
1. Intent_id + duplicate guard.
2. Risk check (Phase J).
3. `BotState.options_enabled` check.
4. Fetch chains, pick expiry, run Greeks pipeline (Phase I).
5. Build `SpreadSpec` → `Contract(BAG) + ComboLeg(BUY) + ComboLeg(SELL)`.
6. Approve mode → `pending_approval`. Else → `client.place_order` → `submitted`.

---

### Phase M — IBKR Sync

**Purpose**: Keep local DB in sync with IBKR account state.

- `sync_account`: NAV, cash, P&L, drawdown calculation.
- `sync_positions`: rebuilds from broker, reconciles `portfolio_id` via TradeManagement → orders → `unattributed`.
- `sync_orders`: updates status, inserts new `Fill` rows.

Cadence: Every 30s via scheduler.

---

### Phase N — Dashboard

- React SPA at `frontend/`, served by FastAPI from `ui/static/dist/`.
- JSON endpoints in `api/v1/` (12 routers).
- Dual theme: Matrix (default) / Dream (`<html data-theme="dream">`).
- Controls: kill switch, pause, approve mode, options enabled, approve/reject orders.
- **Score Control Panel** (`/score-control`):
  - *Controls tab*: trigger rankings / sentiment / fundamentals refresh independently; each shows last-refresh timestamp, status, error. All refresh calls append to `RefreshLog` (`api/v1/_refresh_log.log_refresh`).
  - *Documentation tab*: per-factor cards (Technical, Momentum, Sentiment, Quality, Growth, Risk Penalty) with live weights from `scoring_config.yaml` via `GET /api/v1/scoring/docs`.
  - *Rankings tab*: full symbol table with per-factor scores, heatmap colouring (inverted for Risk Penalty), sort/search/CSV export. Reuses the `['rankings']` TanStack Query key — no duplicate fetch.
  - Refresh history: `GET /api/v1/scoring/refresh-history` returns last N `RefreshLog` rows (action, status, duration_ms, message).

---

## 4) Key Data Contracts

| Artifact | Shape | Producer | Persistence |
|---|---|---|---|
| `UniverseItem` | `(symbol, sector, name, type, sources, verified, conid)` | `trader/universe.py` | in-memory |
| `SentimentSnapshot` | `(scope, key, score[-1,1], sources_json)` | `sentiment/factory.py` | DB |
| `RegimeState` | `(level, composite_score, pillars, effects, transition)` | `regime/engine.py` | DB as `RegimeSnapshot` |
| `RankedSymbol` | `(symbol, sector, score_total, components, equity_eligible, options_eligible, bias)` | `trader/ranking.py` | DB as `SymbolRanking` |
| `FundamentalResult` | `(symbol, total_score, pillars, missing_fields)` | `fundamental_scorer.py` | memory + DB |
| `TradeIntent` | `(symbol, direction, instrument_type, score, quantity, limit_price, stop_price, atr, max_risk_usd)` | `select_trades` | in-memory |
| `TradePlan` | `(symbol, bias, legs_json, pricing_json, rationale_json, status)` | `options_planner.py` | DB |
| `SpreadSelection` | `(legs, Greeks, pricing)` | `StrikeSelector` | in-memory |
| `GateResult` | `(approved, reason, checks_passed/failed, warnings)` | `GreeksGate` | in-memory |
| `Order` | `(intent_id, symbol, direction, instrument, portfolio_id, status, max_loss, payload_json)` | execution paths | DB |
| `TradeManagement` | `(symbol, portfolio_id, entry_price, stop_price, quantity, trailing fields)` | execution / exits | DB |
| `EquitySnapshot` | `(net_liquidation, cash, unrealized, realized, drawdown_pct)` | `risk.py` | DB |
| `BotState` | `(paused, kill_switch, options_enabled, approve_mode)` | startup / controls | DB (singleton) |

---

## 5) Run Loop & State

### BaseBot.run() sequence:
1. `check_regime(client)` → `RegimeState`
2. `get_verified_universe(client)` → `List[UniverseItem]`
3. `rank_symbols(universe)` → `List[RankedSymbol]` + DB write
4. `_run_exit_phase(context)` → evaluate + execute exits
5. `build_candidates(context)` → bot-specific filter
6. For each candidate: `score_candidate` (None → skipped)
7. Sort by score desc; `select_trades` → `List[TradeIntent]`
8. For each intent: `dry_run` → log only; else `execute_intent`
9. Return `BotRunResult`

### State across cycles:
- **DB is source of truth** — nothing important is purely in-memory.
- **Process-local caches** (lost on restart): bars cache, fundamental cache, config cache, refresh lock.
- **Scheduler fields** (not persisted): last-run timestamps, routine file signature. Restarts re-trigger cadence work.

### Idempotency mechanisms:
- `intent_id` unique index on `orders`
- `TradePlan` cooldown (`cooldown_hours`)
- `ContractVerificationCache` (24h TTL)
- `SentimentLlmItem` (dedup cache)
- `data/seen_articles.json` (routine dedup, bot never writes)

---

## 6) Troubleshooting

### "Trade didn't happen" — top 10 checks:

1. **Kill switch / paused** (`/controls`)
2. **Bot disabled**: `cfg.bots.<name>.enabled = false`
3. **Approve mode ON** (default): order is `pending_approval`, not submitted
4. **IBKR offline**: planner writes `skip_reason="no_ibkr_client"`
5. **Regime risk-off** + equity `risk_off_mode="cash"` → empty candidates
6. **Score below threshold**: check `symbol_rankings` + logs
7. **Greeks gate reject**: check `events_log` type `greeks_gate_reject`
8. **No delta-matched strikes**: thin chain or no 0.20-delta short leg
9. **Risk block**: drawdown/max positions/insufficient cash
10. **Cooldown / daily cap**: check `trade_plans.skip_reason`

### "Ticker sentiment not appearing":

1. **Security master empty** — run `cli.py securities import`
2. **No alias matches** — RSS only scans multi-word aliases + long manual overrides
3. **Claude returned empty `mentioned_companies`** — check `sentiment_llm_items`
4. **Ambiguous aliases** — check `rss_entity_matches WHERE reason='ambiguous'`
5. **Wrong DB file** — confirm `DATABASE_URL` matches across processes

### Observability gaps (known):
- No correlation ID across phases (would benefit from `cycle_id` per run)
- No fill latency / slippage tracking
- sync/regime/universe events missing from `events_log`

---

## 7) Unresolved Issues

1. **Kill switch blocks new orders but does not close existing ones.** `close_all` only flips the switch; needs explicit IBKR close routine.
2. **EUR/USD FX conversion missing** — budget in EUR, broker flow in USD, static rate (1.08).
3. **Two parallel scoring paths** — CLI bot runs + scheduler `generate_signals` can race; only `intent_id` randomness prevents duplicates.
