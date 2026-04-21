"""Unified CLI entry point for market-ai bots.

Usage:
  python cli.py sentiment refresh [--since 24h] [--dry-run]
  python cli.py run options_swing --mode paper --dry-run
  python cli.py run equity_swing  --mode live  --approve
  python cli.py run all           --mode paper --dry-run
  python cli.py report last-run   --bot equity_swing
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from typing import Optional

import click

from common.config import load_config, get_config
from common.db import create_tables
from common.logging import get_logger, setup_logging

log = get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _setup() -> None:
    setup_logging()
    load_config()
    create_tables()


def _connect_ibkr(mode: str):
    from common.config import get_config
    from trader.ibkr_client import get_ibkr_client
    cfg = get_config()
    client = get_ibkr_client()
    try:
        client.connect()
        log.info("IBKR connected (mode=%s host=%s port=%d)", mode, cfg.ibkr.host, cfg.ibkr.port)
        return client
    except Exception as e:
        log.warning("IBKR connection failed (%s) — offline mode.", e)
        return None


def _make_bot(bot_name: str):
    if bot_name == "options_swing":
        from bots.options_swing_bot import OptionsSwingBot
        return OptionsSwingBot()
    if bot_name == "equity_swing":
        from bots.equity_swing_bot import EquitySwingBot
        return EquitySwingBot()
    raise ValueError(f"Unknown bot: {bot_name}")


def _run_once(bot_names: list, mode: str, approve: bool, dry_run: bool, client) -> list:
    results = []
    for name in bot_names:
        bot = _make_bot(name)
        log.info("Running %s (mode=%s dry_run=%s approve=%s)", name, mode, dry_run, approve)
        try:
            result = bot.run(mode=mode, approve=approve, dry_run=dry_run, client=client)
            results.append(result)
        except Exception as e:
            log.error("Bot %s failed: %s", name, e)
    return results


def _print_result(result) -> None:
    click.echo(f"\n{'─'*60}")
    click.echo(f"Bot:      {result.bot_id}")
    click.echo(f"Regime:   {result.regime}")
    click.echo(f"Universe: {result.universe_size} symbols")
    click.echo(f"Scored:   {len(result.candidates)} candidates")
    click.echo(f"Intents:  {len(result.intents)}")
    click.echo(f"Executed: {result.executed}")
    click.echo(f"Skipped:  {result.skipped}")
    if result.errors:
        click.echo(f"Errors:   {len(result.errors)}")
        for err in result.errors[:5]:
            click.echo(f"  ! {err}")
    if result.intents:
        click.echo("\nTop trade intents:")
        for intent in result.intents[:10]:
            click.echo(
                f"  {intent['symbol']:8s} {intent['direction']:5s} "
                f"score={intent['score']:.3f} qty={intent.get('qty')} "
                f"lim={intent.get('limit_price')}"
            )
    click.echo(f"{'─'*60}\n")


# ── CLI groups ───────────────────────────────────────────────────────────────


@click.group()
def cli():
    """Market-AI trading bot CLI."""


# ── sentiment refresh ────────────────────────────────────────────────────────


@cli.group()
def sentiment():
    """Sentiment data commands."""


@sentiment.command("refresh")
@click.option("--since", default="24h", show_default=True, help="Max age of news items to process.")
@click.option("--source", default=None, help="Force specific provider (rss_lexicon | claude_llm).")
@click.option("--dry-run", is_flag=True, help="Fetch and score but do not persist.")
def sentiment_refresh(since: str, source: Optional[str], dry_run: bool) -> None:
    """Fetch news and refresh sentiment snapshots."""
    _setup()

    if source:
        import os
        os.environ["SENTIMENT_PROVIDER"] = source
        load_config(reload=True)

    from trader.sentiment.factory import refresh_and_store
    click.echo(f"Refreshing sentiment (source={source or get_config().sentiment.provider} since={since})…")

    if dry_run:
        click.echo("[DRY-RUN] Would refresh sentiment — skipping persistence.")
        return

    try:
        summary = refresh_and_store()
        click.echo(
            f"Done: provider={summary.get('provider')} "
            f"status={summary.get('status')} "
            f"snapshots_written={summary.get('snapshots_written', 0)}"
        )
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)


# ── run ─────────────────────────────────────────────────────────────────────


@cli.command("run")
@click.argument(
    "bot_name",
    metavar="BOT",
    type=click.Choice(["options_swing", "equity_swing", "all"]),
)
@click.option(
    "--mode",
    type=click.Choice(["paper", "live"]),
    default="paper",
    show_default=True,
    help="Trading mode.",
)
@click.option("--dry-run", is_flag=True, help="Plan trades but never submit orders.")
@click.option(
    "--approve/--no-approve",
    default=True,
    show_default=True,
    help="Require manual approval before submitting orders.",
)
@click.option(
    "--once",
    is_flag=True,
    help="Run a single cycle and exit (default: run continuously).",
)
@click.option(
    "--refresh-sentiment/--no-refresh-sentiment",
    default=True,
    show_default=True,
    help="Refresh sentiment before each bot cycle.",
)
def run_cmd(
    bot_name: str,
    mode: str,
    dry_run: bool,
    approve: bool,
    once: bool,
    refresh_sentiment: bool,
) -> None:
    """Run one or both bots in the specified mode."""
    _setup()

    cfg = get_config()
    if dry_run:
        click.echo("[DRY-RUN] No orders will be submitted.")
    if approve:
        click.echo("[APPROVE MODE] Orders will be queued for manual approval.")

    bot_names = (
        ["options_swing", "equity_swing"]
        if bot_name == "all"
        else [bot_name]
    )

    # Filter disabled bots
    enabled = []
    for name in bot_names:
        if name == "options_swing" and not cfg.bots.options_swing.enabled:
            click.echo(f"[SKIP] {name} is disabled in config.")
            continue
        if name == "equity_swing" and not cfg.bots.equity_swing.enabled:
            click.echo(f"[SKIP] {name} is disabled in config.")
            continue
        enabled.append(name)

    if not enabled:
        click.echo("No bots enabled — nothing to run.")
        return

    client = _connect_ibkr(mode)

    def _cycle() -> None:
        if refresh_sentiment:
            try:
                from trader.sentiment.factory import refresh_and_store
                refresh_and_store()
            except Exception as e:
                log.warning("Sentiment refresh failed: %s", e)

        results = _run_once(enabled, mode, approve, dry_run, client)
        for r in results:
            _print_result(r)

    if once:
        _cycle()
        return

    # Continuous mode — run scheduler loop
    stop_event = threading.Event()

    def _signal_handler(sig, frame):
        click.echo("\nShutting down…")
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    interval_s = cfg.scheduling.signal_eval_minutes * 60
    click.echo(
        f"Running {bot_names} continuously (interval={cfg.scheduling.signal_eval_minutes}min). "
        f"Press Ctrl-C to stop."
    )

    while not stop_event.is_set():
        try:
            _cycle()
        except Exception as e:
            log.error("Cycle error: %s", e)
        stop_event.wait(interval_s)

    if client:
        try:
            client.disconnect()
        except Exception:
            pass


# ── report ───────────────────────────────────────────────────────────────────


@cli.command("report")
@click.argument("subcommand", type=click.Choice(["last-run"]))
@click.option(
    "--bot",
    type=click.Choice(["options_swing", "equity_swing", "all"]),
    default="all",
    show_default=True,
)
@click.option("--json-out", is_flag=True, help="Output raw JSON.")
def report_cmd(subcommand: str, bot: str, json_out: bool) -> None:
    """Show run reports and summaries."""
    _setup()

    if subcommand == "last-run":
        _report_last_run(bot, json_out)


def _report_last_run(bot: str, json_out: bool) -> None:
    from common.db import get_db
    from common.models import SymbolRanking, Order, TradePlan

    with get_db() as db:
        # Latest rankings
        latest_ts = (
            db.query(SymbolRanking.ts)
            .order_by(SymbolRanking.ts.desc())
            .first()
        )
        if not latest_ts:
            click.echo("No ranking data found. Run a bot cycle first.")
            return

        ts = latest_ts[0]
        rankings = (
            db.query(SymbolRanking)
            .filter(SymbolRanking.ts == ts)
            .order_by(SymbolRanking.score_total.desc())
            .limit(20)
            .all()
        )

        # Recent orders filtered by portfolio_id
        portfolio_filter = []
        if bot in ("options_swing", "all"):
            portfolio_filter.append("options_swing")
        if bot in ("equity_swing", "all"):
            portfolio_filter.append("equity_swing")

        recent_orders = (
            db.query(Order)
            .filter(Order.portfolio_id.in_(portfolio_filter))
            .order_by(Order.timestamp.desc())
            .limit(20)
            .all()
        )

        report = {
            "last_ranking_ts": ts.isoformat(),
            "top_ranked": [
                {
                    "symbol": r.symbol,
                    "score": r.score_total,
                    "eligible": r.eligible,
                }
                for r in rankings
            ],
            "recent_orders": [
                {
                    "symbol": o.symbol,
                    "direction": o.direction,
                    "instrument": o.instrument,
                    "portfolio": o.portfolio_id,
                    "status": o.status,
                    "qty": o.quantity,
                    "lim": o.limit_price,
                    "ts": o.timestamp.isoformat(),
                }
                for o in recent_orders
            ],
        }

    if json_out:
        click.echo(json.dumps(report, indent=2))
        return

    click.echo(f"\nLast ranking: {report['last_ranking_ts']}")
    click.echo(f"\nTop 20 ranked symbols:")
    for r in report["top_ranked"]:
        elig = "✓" if r["eligible"] else "✗"
        click.echo(f"  {elig} {r['symbol']:8s} score={r['score']:+.4f}")

    click.echo(f"\nRecent orders (bot={bot}):")
    if not report["recent_orders"]:
        click.echo("  (none)")
    for o in report["recent_orders"]:
        click.echo(
            f"  {o['ts'][:16]} {o['portfolio']:14s} {o['symbol']:8s} "
            f"{o['direction']:6s} {o['instrument']:20s} "
            f"qty={o['qty']} status={o['status']}"
        )


# ── securities ───────────────────────────────────────────────────────────────


@cli.group()
def securities():
    """Security master management commands."""


@securities.command("import")
@click.option(
    "--file", "csv_file",
    default=None,
    help="Path to CSV file (default: config.securities.master_csv_path).",
)
@click.option("--verify-ibkr", is_flag=True, help="Verify each symbol via IBKR after import.")
@click.option("--refresh-aliases", is_flag=True, default=True, show_default=True,
              help="Regenerate aliases for every symbol.")
@click.option("--load-overrides", is_flag=True, default=True, show_default=True,
              help="Load manual_alias_overrides.csv after import.")
def securities_import(csv_file: Optional[str], verify_ibkr: bool, refresh_aliases: bool, load_overrides: bool) -> None:
    """Import/update the security master from a CSV file."""
    _setup()
    from trader.securities.master import import_csv, load_manual_overrides

    cfg = get_config().securities
    path = csv_file or cfg.master_csv_path
    client = _connect_ibkr("paper") if verify_ibkr else None

    click.echo(f"Importing security master from {path} …")
    try:
        summary = import_csv(path, verify_ibkr=verify_ibkr, refresh_aliases=refresh_aliases, client=client)
        click.echo(
            f"Done: added={summary['added']} updated={summary['updated']} "
            f"skipped={summary['skipped']} aliases={summary['aliases_written']}"
        )
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    if load_overrides:
        from trader.securities.master import load_manual_overrides
        ov = load_manual_overrides(cfg.alias_overrides_path)
        click.echo(f"Manual overrides loaded: {ov['loaded']}")


@securities.command("verify")
@click.option("--all", "verify_all", is_flag=True, help="Verify all active securities via IBKR.")
@click.option("--symbol", default=None, help="Verify a single symbol.")
@click.option("--options-check", is_flag=True, help="Also check options eligibility.")
def securities_verify(verify_all: bool, symbol: Optional[str], options_check: bool) -> None:
    """Verify securities via IBKR contract lookup."""
    _setup()
    from trader.securities.master import verify_security, check_options_eligibility
    from common.db import get_db
    from common.models import SecurityMaster

    client = _connect_ibkr("paper")
    if client is None:
        click.echo("ERROR: IBKR connection required for verification.", err=True)
        sys.exit(1)

    if symbol:
        symbols = [symbol.upper()]
    elif verify_all:
        with get_db() as db:
            rows = db.query(SecurityMaster.symbol).filter(SecurityMaster.active == True).all()
        symbols = [r.symbol for r in rows]
    else:
        click.echo("Specify --symbol SYMBOL or --all.", err=True)
        sys.exit(1)

    click.echo(f"Verifying {len(symbols)} symbol(s)…")
    for sym in symbols:
        result = verify_security(sym, client)
        status = "OK" if result["verified"] else "FAIL"
        line = f"  [{status}] {sym:10s} exchange={result.get('exchange','?'):8s} reason={result['reason']}"
        if options_check and result["verified"]:
            eligible = check_options_eligibility(sym, client)
            line += f" options={'YES' if eligible else 'NO'}"
        click.echo(line)


@securities.command("liquidity-refresh")
@click.option("--lookback", default=20, show_default=True, help="Trading days to average.")
@click.option("--symbol", default=None, help="Refresh a single symbol (default: all active).")
def securities_liquidity(lookback: int, symbol: Optional[str]) -> None:
    """Refresh avg_dollar_volume_20d for securities via IBKR market data."""
    _setup()
    from trader.securities.master import refresh_liquidity
    from common.db import get_db
    from common.models import SecurityMaster

    client = _connect_ibkr("paper")
    if client is None:
        click.echo("ERROR: IBKR connection required for liquidity refresh.", err=True)
        sys.exit(1)

    if symbol:
        symbols = [symbol.upper()]
    else:
        with get_db() as db:
            rows = db.query(SecurityMaster.symbol).filter(SecurityMaster.active == True).all()
        symbols = [r.symbol for r in rows]

    click.echo(f"Refreshing liquidity for {len(symbols)} symbol(s)…")
    updated = 0
    for sym in symbols:
        adv = refresh_liquidity(sym, client, lookback=lookback)
        if adv is not None:
            click.echo(f"  {sym:10s} avg_dollar_vol={adv:,.0f}")
            updated += 1
    click.echo(f"Updated {updated}/{len(symbols)} symbols.")


# ── match-company ─────────────────────────────────────────────────────────────


@cli.command("match-company")
@click.option("--text", default=None, help="Free-form text to extract companies from.")
@click.option("--companies", default=None,
              help="Comma-separated company names to match directly (skips LLM extraction).")
@click.option("--article-id", default="debug", show_default=True, help="Article ID for audit rows.")
@click.option("--no-audit", is_flag=True, help="Skip writing audit rows to rss_entity_matches.")
def match_company_cmd(
    text: Optional[str],
    companies: Optional[str],
    article_id: str,
    no_audit: bool,
) -> None:
    """Debug company-name → ticker matching.

    Provide --text to simulate LLM extraction, or --companies to test specific names.

    Examples:
      python cli.py match-company --text "Today Molina Healthcare made 5 billion in revenue"
      python cli.py match-company --companies "Molina Healthcare,UnitedHealth"
    """
    _setup()
    from trader.securities.matcher import match_companies_to_symbols
    from trader.securities.normalize import normalize_company_name

    if companies:
        company_list = [c.strip() for c in companies.split(",") if c.strip()]
    elif text:
        # Use the LLM to extract company names from free text
        try:
            company_list = _extract_companies_from_text(text)
            click.echo(f"LLM-extracted companies: {company_list}")
        except Exception as e:
            click.echo(f"LLM extraction failed ({e}). Tip: use --companies instead.", err=True)
            sys.exit(1)
    else:
        click.echo("Provide --text or --companies.", err=True)
        sys.exit(1)

    if not company_list:
        click.echo("No companies to match.")
        return

    click.echo(f"\nMatching {len(company_list)} company name(s):\n")
    results = match_companies_to_symbols(
        company_list,
        article_id=article_id,
        write_audit=not no_audit,
    )

    matched = skipped = 0
    for r in results:
        norm = normalize_company_name(r.company_input)
        if r.symbol:
            matched += 1
            click.echo(
                f"  {r.company_input!r:40s}  → {r.symbol:6s}  "
                f"[{r.match_type}  score={r.match_score:.2f}]"
            )
        else:
            skipped += 1
            click.echo(
                f"  {r.company_input!r:40s}  → (skip)  "
                f"[{r.match_type}  reason={r.reason}]"
            )
        click.echo(f"    normalized: '{norm}'")

    click.echo(f"\nMatched: {matched}  Skipped: {skipped}")
    if not no_audit:
        click.echo(f"Audit rows written to rss_entity_matches (article_id={article_id!r})")


def _extract_companies_from_text(text: str) -> list:
    """Call Claude to extract company names from free text (debug helper only)."""
    import os
    import anthropic as _anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = _anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-3-5-haiku-latest",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": (
                "Extract all publicly traded company names from this text. "
                "Return ONLY a JSON array of strings (company names, not tickers). "
                f"Text: {text!r}"
            ),
        }],
    )
    import json as _json
    raw = msg.content[0].text.strip()
    # Strip code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return _json.loads(raw.strip())


if __name__ == "__main__":
    cli()
