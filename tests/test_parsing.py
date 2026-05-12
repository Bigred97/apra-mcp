"""XLSX parser unit tests using the head-only fixtures."""
from __future__ import annotations

import pytest

from apra_mcp.parsing import ParseError, _normalize_header, drop_blank_rows, read_xlsx


def test_read_adi_key_stats(adi_key_stats_xlsx):
    df = read_xlsx(adi_key_stats_xlsx, sheet="Table 1", header_row=3)
    # Column count + presence of key columns
    assert "Entity" in df.columns
    assert "Sector" in df.columns
    assert "Total Common Equity Tier 1 capital" in df.columns
    assert len(df) > 0


def test_read_adi_rwa(adi_rwa_xlsx):
    df = read_xlsx(adi_rwa_xlsx, sheet="Table 2", header_row=3)
    assert "Credit risk" in df.columns
    assert "Operational risk" in df.columns
    assert "Total risk-weighted assets" in df.columns


def test_read_super_fund_level(super_fund_level_xlsx):
    df = read_xlsx(super_fund_level_xlsx, sheet="Table 1", header_row=4, data_start_row=7)
    assert "Fund name" in df.columns
    assert "ABN" in df.columns
    assert len(df) > 0
    # First data row should be a real fund
    first_fund = df.iloc[0]["Fund name"]
    assert isinstance(first_fund, str)
    assert len(first_fund) > 0


def test_read_insurance_general(insurance_general_xlsx):
    df = read_xlsx(insurance_general_xlsx, sheet="Database", header_row=1)
    assert "Reporting Period" in df.columns
    assert "Data item" in df.columns
    assert "Value" in df.columns


def test_read_insurance_general_historical(insurance_general_historical_xlsx):
    df = read_xlsx(insurance_general_historical_xlsx, sheet="Data", header_row=1)
    assert "Reporting date" in df.columns
    assert "Data item" in df.columns
    assert "Value" in df.columns


def test_read_life_insurance(life_insurance_xlsx):
    df = read_xlsx(life_insurance_xlsx, sheet="Database", header_row=1)
    assert "Reporting Date" in df.columns
    assert "Data item" in df.columns
    assert "Value ($)" in df.columns


def test_read_life_insurance_historical(life_insurance_historical_xlsx):
    df = read_xlsx(life_insurance_historical_xlsx, sheet="Data", header_row=1)
    assert "Reporting date" in df.columns
    assert "Value $" in df.columns


def test_empty_body_raises():
    with pytest.raises(ParseError, match="empty"):
        read_xlsx(b"", sheet="Table 1", header_row=1)


def test_invalid_header_row_raises():
    with pytest.raises(ParseError, match="header_row"):
        read_xlsx(b"x", sheet="X", header_row=0)


def test_unknown_sheet_raises(adi_key_stats_xlsx):
    with pytest.raises(ParseError, match="not found"):
        read_xlsx(adi_key_stats_xlsx, sheet="Nope", header_row=1)


def test_corrupt_body_raises():
    with pytest.raises(ParseError, match="corrupt"):
        read_xlsx(b"not a zip file at all", sheet="X", header_row=1)


def test_data_start_row_less_than_header_raises():
    with pytest.raises(ParseError, match="data_start_row"):
        read_xlsx(b"x", sheet="X", header_row=3, data_start_row=2)


def test_max_rows_caps_dataframe(adi_key_stats_xlsx):
    df = read_xlsx(adi_key_stats_xlsx, sheet="Table 1", header_row=3, max_rows=5)
    assert len(df) == 5


def test_normalize_header_collapses_newlines():
    assert _normalize_header("Of which: \nIRRBB - Internal model approach") == "Of which: IRRBB - Internal model approach"


def test_normalize_header_strips_trailing_space():
    assert _normalize_header("Credit risk ") == "Credit risk"


def test_normalize_header_collapses_internal_runs():
    assert _normalize_header("Total  capital   base") == "Total capital base"


def test_normalize_header_handles_non_string():
    assert _normalize_header(42) == 42
    assert _normalize_header(None) is None


def test_normalize_header_handles_crlf():
    assert _normalize_header("Foo\r\nBar") == "Foo Bar"


def test_drop_blank_rows_strips_trailing_nan(adi_key_stats_xlsx):
    df = read_xlsx(adi_key_stats_xlsx, sheet="Table 1", header_row=3)
    n_before = len(df)
    cleaned = drop_blank_rows(df, ["Entity", "Sector"])
    assert len(cleaned) <= n_before


def test_drop_blank_rows_with_no_key_cols_is_noop(adi_key_stats_xlsx):
    df = read_xlsx(adi_key_stats_xlsx, sheet="Table 1", header_row=3)
    cleaned = drop_blank_rows(df, [])
    assert len(cleaned) == len(df)


def test_drop_blank_rows_with_missing_key_cols_is_noop(adi_key_stats_xlsx):
    df = read_xlsx(adi_key_stats_xlsx, sheet="Table 1", header_row=3)
    cleaned = drop_blank_rows(df, ["NotAColumn"])
    assert len(cleaned) == len(df)
