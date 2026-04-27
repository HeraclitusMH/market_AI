"""Diagnose yfinance fundamental scoring for one or more symbols.

Usage:
    python scripts/diagnose_fundamentals.py AMZN AAPL
"""
from __future__ import annotations

import sys
from typing import Iterable

from common.config import load_config
from trader.fundamental_scorer import FundamentalScorer


def diagnose_symbol(symbol: str) -> None:
    cfg = load_config()
    scorer = FundamentalScorer(cfg=cfg)

    print(f"\n=== {symbol.upper()} ===")
    try:
        info = scorer._fetch_yfinance_info(symbol.upper())
        ratios = scorer._parse_yfinance_info(info)
        print(f"yfinance info fields: {len(info)}")
        print(f"configured metrics parsed: {ratios}")

        scorer._cache.pop(symbol.upper(), None)
        result = scorer.get_score(symbol)
        print(f"source: {result.get('source')}")
        print(f"total_score: {result.get('total_score')}")
        print(f"missing_fields: {result.get('missing_fields')}")
        for pillar_name, pillar in result.get("pillars", {}).items():
            print(
                pillar_name,
                {
                    "score": pillar.get("score"),
                    "missing": pillar.get("missing"),
                    "metrics": pillar.get("metrics"),
                },
            )
    except Exception as exc:
        print(f"diagnostic error: {type(exc).__name__}: {exc}")


def main(argv: Iterable[str]) -> int:
    symbols = [arg.strip().upper() for arg in argv if arg.strip()]
    if not symbols:
        symbols = ["AMZN"]
    for symbol in symbols:
        diagnose_symbol(symbol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
