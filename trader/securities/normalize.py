"""Company-name normalization for alias matching.

Rules (applied in order):
1. Lowercase + strip
2. Strip leading "the "
3. Replace "&" with space (not "and") to avoid "johnson and johnson" issues
4. Remove all punctuation (.,'"/-()\\)
5. Collapse "health care" → "healthcare"
6. Remove trailing corporate-suffix tokens one at a time
7. Collapse whitespace
"""
from __future__ import annotations

import re

# Tokens that appear as trailing words in legal company names.
# We strip them right-to-left so "Holding Company" → "holding" → "" (both gone).
_CORP_SUFFIXES: frozenset[str] = frozenset([
    "incorporated", "corporation", "company", "limited", "holdings",
    "holding", "group", "inc", "corp", "co", "ltd", "plc", "lp",
    "llc", "sa", "ag", "nv", "se", "bv", "associates", "international",
])

_PUNCT = re.compile(r"[.,'\"/()\\-]")
_WHITESPACE = re.compile(r"\s+")
_LEAD_THE = re.compile(r"^the\s+")
_HEALTHCARE = re.compile(r"\bhealth\s+care\b")


def normalize_company_name(name: str) -> str:
    """Return a normalized alias key for the given company name.

    Examples:
        "Molina Healthcare, Inc." → "molina healthcare"
        "Molina HealthCare"      → "molina healthcare"
        "The Coca-Cola Company"  → "coca cola"
        "Johnson & Johnson"      → "johnson johnson"
        "3M Company"             → "3m"
        "UnitedHealth Group Inc" → "unitedhealth"
        "AT&T Inc"               → "at t"
    """
    if not name:
        return ""

    s = name.strip().lower()
    s = _LEAD_THE.sub("", s)
    s = s.replace("&", " ")
    s = _PUNCT.sub(" ", s)
    s = _HEALTHCARE.sub("healthcare", s)

    words = _WHITESPACE.sub(" ", s).strip().split()

    # Strip trailing suffix tokens (one pass removes multiple stacked suffixes)
    while words and words[-1] in _CORP_SUFFIXES:
        words.pop()

    return " ".join(words)


def generate_aliases(symbol: str, name: str) -> list[tuple[str, str, int]]:
    """Return a list of (alias, alias_type, priority) tuples for a security.

    alias_type values:
        "normalized_name" — primary normalized alias            (priority 10)
        "symbol"          — lowercase ticker symbol             (priority 5)
        "short_name"      — first word only if ≥4 chars & unique-enough (priority 50)
    """
    aliases: list[tuple[str, str, int]] = []

    norm = normalize_company_name(name)
    if norm:
        aliases.append((norm, "normalized_name", 10))

    # Lowercase symbol so "aapl" matches "AAPL" mentions in text
    sym_lower = symbol.lower()
    if sym_lower and sym_lower != norm:
        aliases.append((sym_lower, "symbol", 5))

    # Short-name: first word of the normalized name, if it's ≥ 4 chars and
    # different from what we already have. Very broad — kept at low priority.
    if norm:
        first_word = norm.split()[0]
        if (
            len(first_word) >= 4
            and first_word != norm
            and first_word != sym_lower
        ):
            aliases.append((first_word, "short_name", 50))

    return aliases
