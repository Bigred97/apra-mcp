"""Tests for the user-period → ISO-date normaliser.

The period_column in every curated dataset carries quarter-end ISO dates
(`2025-12-31`). Users typically pass less-specific shapes — bare years,
year-month strings, or APRA-style quarter shorthand. The normaliser
expands them to the right inclusive bound before string comparison.
"""
from __future__ import annotations

import pytest

from apra_mcp.shaping import _expand_period_input


# ---- ISO date passthrough ----

def test_iso_date_passthrough_start():
    assert _expand_period_input("2025-12-31", bound="start") == "2025-12-31"


def test_iso_date_passthrough_end():
    assert _expand_period_input("2025-12-31", bound="end") == "2025-12-31"


# ---- Bare year ----

def test_bare_year_as_start():
    assert _expand_period_input("2024", bound="start") == "2024-01-01"


def test_bare_year_as_end():
    assert _expand_period_input("2024", bound="end") == "2024-12-31"


# ---- Quarter shorthand ----

def test_quarter_q1_start_end():
    assert _expand_period_input("2025-Q1", bound="start") == "2025-01-01"
    assert _expand_period_input("2025-Q1", bound="end") == "2025-03-31"


def test_quarter_q2_start_end():
    assert _expand_period_input("2025-Q2", bound="start") == "2025-04-01"
    assert _expand_period_input("2025-Q2", bound="end") == "2025-06-30"


def test_quarter_q3_start_end():
    assert _expand_period_input("2025-Q3", bound="start") == "2025-07-01"
    assert _expand_period_input("2025-Q3", bound="end") == "2025-09-30"


def test_quarter_q4_start_end():
    assert _expand_period_input("2025-Q4", bound="start") == "2025-10-01"
    assert _expand_period_input("2025-Q4", bound="end") == "2025-12-31"


def test_quarter_lowercase_q():
    """Validator accepts both Q and q; normaliser must too."""
    assert _expand_period_input("2025-q3", bound="start") == "2025-07-01"
    assert _expand_period_input("2025-q3", bound="end") == "2025-09-30"


def test_quarter_invalid_q5_passthrough():
    """Q5 is invalid — return the string as-is (best-effort)."""
    assert _expand_period_input("2025-Q5", bound="start") == "2025-Q5"


# ---- Year-Month ----

def test_year_month_start():
    assert _expand_period_input("2025-03", bound="start") == "2025-03-01"


def test_year_month_end_31_day_month():
    assert _expand_period_input("2025-03", bound="end") == "2025-03-31"


def test_year_month_end_30_day_month():
    assert _expand_period_input("2025-06", bound="end") == "2025-06-30"


def test_year_month_end_february_non_leap():
    # Generous 28-day Feb end; APRA quarter-ends never land on Feb 29.
    assert _expand_period_input("2025-02", bound="end") == "2025-02-28"


def test_year_month_invalid_month_passthrough():
    assert _expand_period_input("2025-13", bound="end") == "2025-13"


# ---- Edge cases ----

def test_empty_string_passthrough():
    assert _expand_period_input("", bound="start") == ""


def test_whitespace_trimmed():
    assert _expand_period_input("  2024  ", bound="start") == "2024-01-01"


def test_unrecognised_passthrough():
    """Unrecognised shapes fall through unchanged."""
    assert _expand_period_input("garbage", bound="start") == "garbage"


# ---- End-to-end: filter behaviour via build_response ----

def test_filter_quarter_string_matches_iso_date(adi_key_stats_xlsx):
    from apra_mcp import curated
    from apra_mcp.parsing import read_xlsx
    from apra_mcp.shaping import build_response
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx, sheet=cd.sheet,
        header_row=cd.header_row, data_start_row=cd.data_start_row,
    )
    # Q4 should include Dec 31 records
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"},
        measures="cet1_ratio",
        start_period="2025-Q4", end_period="2025-Q4",
        fmt="records", user_query={},
    )
    assert resp.row_count >= 1
    for r in resp.records:
        assert r.period == "2025-12-31"


def test_filter_bare_year_matches_year_range(adi_key_stats_xlsx):
    from apra_mcp import curated
    from apra_mcp.parsing import read_xlsx
    from apra_mcp.shaping import build_response
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx, sheet=cd.sheet,
        header_row=cd.header_row, data_start_row=cd.data_start_row,
    )
    # 2025 should include all 2025 quarters in the snapshot
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"},
        measures="cet1_ratio",
        start_period="2025", end_period="2025",
        fmt="records", user_query={},
    )
    assert resp.row_count >= 1
    for r in resp.records:
        assert r.period.startswith("2025-")
