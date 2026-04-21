"""Tests for security master — normalization, alias matching, and eligibility."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from trader.securities.normalize import normalize_company_name, generate_aliases


# ── Normalization ─────────────────────────────────────────────────────────────

class TestNormalizeCompanyName:
    def test_removes_inc(self):
        assert normalize_company_name("Molina Healthcare, Inc.") == "molina healthcare"

    def test_removes_inc_no_punctuation(self):
        assert normalize_company_name("Molina Healthcare Inc") == "molina healthcare"

    def test_capital_variant(self):
        assert normalize_company_name("Molina HealthCare") == "molina healthcare"

    def test_coca_cola(self):
        assert normalize_company_name("The Coca-Cola Company") == "coca cola"

    def test_removes_the(self):
        assert normalize_company_name("The Walt Disney Company") == "walt disney"

    def test_ampersand_becomes_space(self):
        assert normalize_company_name("Johnson & Johnson") == "johnson johnson"

    def test_removes_corp(self):
        assert normalize_company_name("Microsoft Corporation") == "microsoft"

    def test_removes_group(self):
        assert normalize_company_name("UnitedHealth Group Inc") == "unitedhealth"

    def test_removes_holdings(self):
        assert normalize_company_name("Berkshire Hathaway Holdings Inc") == "berkshire hathaway"

    def test_3m(self):
        assert normalize_company_name("3M Company") == "3m"

    def test_at_t(self):
        # "&" → space; "inc" removed
        assert normalize_company_name("AT&T Inc") == "at t"

    def test_healthcare_space_variant(self):
        assert normalize_company_name("Molina Health Care Inc") == "molina healthcare"

    def test_empty(self):
        assert normalize_company_name("") == ""

    def test_whitespace_only(self):
        assert normalize_company_name("   ") == ""

    def test_stacked_suffixes(self):
        # "Group" then "Inc" — both should be removed
        assert normalize_company_name("Acme Holdings Group Inc") == "acme"

    def test_goldman_sachs(self):
        result = normalize_company_name("Goldman Sachs Group Inc")
        assert result == "goldman sachs"

    def test_jpmorgan(self):
        result = normalize_company_name("JPMorgan Chase & Co")
        assert result == "jpmorgan chase"

    def test_procter_gamble(self):
        result = normalize_company_name("Procter & Gamble Co")
        assert result == "procter gamble"

    def test_mcdonalds_apostrophe(self):
        result = normalize_company_name("McDonald's Corporation")
        assert result == "mcdonald s"  # apostrophe → space, then corporation removed


class TestGenerateAliases:
    def test_normalized_name_alias(self):
        aliases = generate_aliases("MOH", "Molina Healthcare Inc")
        alias_strs = [a for a, _, _ in aliases]
        assert "molina healthcare" in alias_strs

    def test_symbol_alias(self):
        aliases = generate_aliases("MOH", "Molina Healthcare Inc")
        alias_strs = [a for a, _, _ in aliases]
        assert "moh" in alias_strs

    def test_short_name_alias(self):
        # First word "molina" is ≥4 chars and different from full name
        aliases = generate_aliases("MOH", "Molina Healthcare Inc")
        alias_strs = [a for a, _, _ in aliases]
        assert "molina" in alias_strs

    def test_priorities(self):
        aliases = generate_aliases("MOH", "Molina Healthcare Inc")
        by_alias = {a: p for a, _, p in aliases}
        # symbol alias should have lower priority number (higher importance) than short_name
        assert by_alias.get("moh", 999) < by_alias.get("molina", 999)
        # normalized name should have highest priority
        assert by_alias.get("molina healthcare", 999) < by_alias.get("molina", 999)

    def test_no_duplicate_aliases(self):
        aliases = generate_aliases("V", "Visa Inc")
        alias_strs = [a for a, _, _ in aliases]
        assert len(alias_strs) == len(set(alias_strs)), "duplicate aliases generated"

    def test_single_letter_ticker_not_short_name(self):
        # Symbol "V" is only 1 char — short_name rule requires ≥4 chars for first word
        aliases = generate_aliases("V", "Visa Inc")
        alias_types = [t for _, t, _ in aliases]
        # "v" should be a symbol alias, but "visa" could be short_name (4 chars)
        alias_strs = [a for a, _, _ in aliases]
        assert "v" in alias_strs  # symbol alias


# ── Matcher ───────────────────────────────────────────────────────────────────

class TestMatcher:
    """Tests for match_companies_to_symbols using a mocked DB."""

    def _make_alias_row(self, symbol: str, priority: int):
        row = MagicMock()
        row.symbol = symbol
        row.priority = priority
        return row

    def _setup_db_mock(self, mock_db_ctx, alias_rows):
        mock_db = MagicMock()
        mock_db_ctx.return_value.__enter__.return_value = mock_db

        query_chain = mock_db.query.return_value
        query_chain.join.return_value = query_chain
        query_chain.filter.return_value = query_chain
        query_chain.order_by.return_value = query_chain
        query_chain.limit.return_value = query_chain
        query_chain.all.return_value = alias_rows

        return mock_db

    def test_exact_match(self):
        from trader.securities.matcher import match_companies_to_symbols

        alias_row = self._make_alias_row("MOH", 10)

        with patch("trader.securities.matcher.get_db") as mock_db_ctx, \
             patch("trader.securities.matcher.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE", "NASDAQ", "AMEX"]
            self._setup_db_mock(mock_db_ctx, [alias_row])

            results = match_companies_to_symbols(
                ["Molina Healthcare"],
                article_id="test123",
                write_audit=False,
            )

        assert len(results) == 1
        r = results[0]
        assert r.symbol == "MOH"
        assert r.match_type == "exact_alias"
        assert r.match_score == 1.0

    def test_no_match(self):
        from trader.securities.matcher import match_companies_to_symbols

        with patch("trader.securities.matcher.get_db") as mock_db_ctx, \
             patch("trader.securities.matcher.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE", "NASDAQ", "AMEX"]
            self._setup_db_mock(mock_db_ctx, [])  # no aliases found

            results = match_companies_to_symbols(
                ["Unknown Corp XYZ"],
                article_id="test123",
                write_audit=False,
            )

        assert len(results) == 1
        r = results[0]
        assert r.symbol is None
        assert r.match_type == "unmatched"

    def test_ambiguous_match(self):
        from trader.securities.matcher import match_companies_to_symbols

        row1 = self._make_alias_row("MOH", 10)
        row2 = self._make_alias_row("UNH", 20)

        with patch("trader.securities.matcher.get_db") as mock_db_ctx, \
             patch("trader.securities.matcher.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE", "NASDAQ", "AMEX"]
            self._setup_db_mock(mock_db_ctx, [row1, row2])

            results = match_companies_to_symbols(
                ["Health Company"],
                article_id="test123",
                write_audit=False,
            )

        assert len(results) == 1
        r = results[0]
        assert r.symbol is None
        assert r.match_type == "ambiguous"
        assert "MOH" in (r.reason or "")
        assert "UNH" in (r.reason or "")

    def test_empty_input(self):
        from trader.securities.matcher import match_companies_to_symbols

        with patch("trader.securities.matcher.get_db"):
            results = match_companies_to_symbols([], article_id="test", write_audit=False)

        assert results == []

    def test_empty_after_normalization(self):
        from trader.securities.matcher import match_companies_to_symbols

        with patch("trader.securities.matcher.get_db") as mock_db_ctx, \
             patch("trader.securities.matcher.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE"]
            # DB should not be queried for empty normalized name
            mock_db = MagicMock()
            mock_db_ctx.return_value.__enter__.return_value = mock_db

            results = match_companies_to_symbols(
                ["Inc."],  # normalizes to empty string
                article_id="test",
                write_audit=False,
            )

        r = results[0]
        assert r.symbol is None
        assert r.reason == "empty_after_normalization"

    def test_same_symbol_two_aliases_not_ambiguous(self):
        from trader.securities.matcher import match_companies_to_symbols

        # Two rows but SAME symbol → not ambiguous
        row1 = self._make_alias_row("MOH", 10)
        row2 = self._make_alias_row("MOH", 20)

        with patch("trader.securities.matcher.get_db") as mock_db_ctx, \
             patch("trader.securities.matcher.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE", "NASDAQ", "AMEX"]
            self._setup_db_mock(mock_db_ctx, [row1, row2])

            results = match_companies_to_symbols(
                ["Molina Healthcare"],
                article_id="test",
                write_audit=False,
            )

        r = results[0]
        assert r.symbol == "MOH"
        assert r.match_type == "exact_alias"


# ── Master import ─────────────────────────────────────────────────────────────

class TestMasterImport:
    def test_import_csv_adds_rows(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol,name,exchange,security_type\nMOH,Molina Healthcare Inc,NYSE,STK\n")

        from trader.securities.master import import_csv

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("trader.securities.master.get_db") as mock_db_ctx, \
             patch("trader.securities.master.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE", "NASDAQ", "AMEX"]
            mock_db_ctx.return_value.__enter__.return_value = mock_db

            summary = import_csv(str(csv_file), verify_ibkr=False, refresh_aliases=True)

        assert summary["added"] == 1
        assert summary["skipped"] == 0
        assert mock_db.add.called

    def test_import_csv_skips_disallowed_exchange(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol,name,exchange\nXYZ,Some OTC Corp,OTC\n")

        from trader.securities.master import import_csv

        with patch("trader.securities.master.get_db") as mock_db_ctx, \
             patch("trader.securities.master.get_config") as mock_cfg:
            mock_cfg.return_value.securities.allowed_exchanges = ["NYSE", "NASDAQ", "AMEX"]
            mock_db = MagicMock()
            mock_db_ctx.return_value.__enter__.return_value = mock_db

            summary = import_csv(str(csv_file), verify_ibkr=False, refresh_aliases=False)

        assert summary["skipped"] == 1
        assert summary["added"] == 0

    def test_import_csv_file_not_found(self):
        from trader.securities.master import import_csv

        with pytest.raises(FileNotFoundError):
            import_csv("/nonexistent/path.csv")

    def test_load_manual_overrides(self, tmp_path):
        csv_file = tmp_path / "overrides.csv"
        csv_file.write_text("alias,symbol\ngoogle,GOOGL\nalphabet,GOOGL\n")

        from trader.securities.master import load_manual_overrides

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("trader.securities.master.get_db") as mock_db_ctx:
            mock_db_ctx.return_value.__enter__.return_value = mock_db
            result = load_manual_overrides(str(csv_file))

        assert result["loaded"] == 2
        assert mock_db.add.call_count == 2

    def test_load_manual_overrides_missing_file(self, tmp_path):
        from trader.securities.master import load_manual_overrides

        result = load_manual_overrides(str(tmp_path / "nonexistent.csv"))
        assert result["loaded"] == 0


# ── Integration-style: end-to-end normalization → alias → match ───────────────

class TestEndToEnd:
    """Verify that the normalization logic is consistent with how aliases are stored."""

    def test_molina_healthcare_normalizes_correctly(self):
        norm = normalize_company_name("Molina Healthcare")
        assert norm == "molina healthcare"

    def test_molina_healthcare_inc_normalizes_to_same(self):
        norm1 = normalize_company_name("Molina Healthcare")
        norm2 = normalize_company_name("Molina Healthcare Inc")
        norm3 = normalize_company_name("Molina Healthcare, Inc.")
        assert norm1 == norm2 == norm3

    def test_unitedhealth_variants_consistent(self):
        variants = [
            "UnitedHealth",
            "UnitedHealth Group",
            "UnitedHealth Group Inc",
        ]
        norms = [normalize_company_name(v) for v in variants]
        assert len(set(norms)) == 1, f"Expected one unique normalization, got: {set(norms)}"

    def test_generate_aliases_includes_molina_healthcare(self):
        aliases_tuples = generate_aliases("MOH", "Molina Healthcare Inc")
        alias_keys = [a for a, _, _ in aliases_tuples]
        assert "molina healthcare" in alias_keys

    def test_generate_aliases_includes_moh_lowercase(self):
        aliases_tuples = generate_aliases("MOH", "Molina Healthcare Inc")
        alias_keys = [a for a, _, _ in aliases_tuples]
        assert "moh" in alias_keys
