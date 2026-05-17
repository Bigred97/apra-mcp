"""Edge-case data tests — blank rows, NaN, malformed cells, missing optional dims."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from apra_mcp import curated
from apra_mcp.parsing import drop_blank_rows, read_xlsx
from apra_mcp.shaping import build_response, _safe_str, _safe_value, _normalize_period_cell


def test_safe_value_handles_nan():
    assert _safe_value(float("nan")) is None


def test_safe_value_handles_none():
    assert _safe_value(None) is None


def test_safe_value_handles_int():
    assert _safe_value(42) == 42.0


def test_safe_value_handles_string_number():
    assert _safe_value("3.14") == 3.14


def test_safe_value_handles_non_numeric_string():
    assert _safe_value("not-a-number") is None


def test_safe_str_handles_nan():
    assert _safe_str(float("nan")) is None


def test_safe_str_handles_none():
    assert _safe_str(None) is None


def test_safe_str_handles_int():
    assert _safe_str(42) == "42"


def test_normalize_period_cell_timestamp():
    ts = pd.Timestamp("2025-12-31")
    assert _normalize_period_cell(ts) == "2025-12-31"


def test_normalize_period_cell_iso_string():
    assert _normalize_period_cell("2025-12-31 00:00:00") == "2025-12-31"


def test_normalize_period_cell_short_string():
    assert _normalize_period_cell("2025-12-31") == "2025-12-31"


def test_normalize_period_cell_none():
    assert _normalize_period_cell(None) is None


def test_normalize_period_cell_nan():
    assert _normalize_period_cell(float("nan")) is None


def test_normalize_period_cell_empty_string():
    assert _normalize_period_cell("   ") is None


def test_record_with_nan_value_dropped(adi_key_stats_xlsx):
    """If a measure value is NaN, the observation is dropped (not shipped)."""
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet,
        header_row=cd.header_row,
        data_start_row=cd.data_start_row,
    )
    # Inject a NaN into the first row's CET1 column
    df.loc[0, "Total Common Equity Tier 1 capital"] = float("nan")
    resp = build_response(
        cd=cd, df=df,
        filters={"institution": df.iloc[0]["Entity"]},
        measures="cet1_capital",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    # No NaN value should make it through
    for r in resp.records:
        assert r.value is not None
        assert not math.isnan(r.value)


def test_empty_dataframe_returns_empty_response(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet,
        header_row=cd.header_row,
        data_start_row=cd.data_start_row,
    )
    # Wildcard substring that matches no rows. We deliberately route through
    # the wildcard branch (skips alias validation) so we exercise the
    # empty-result path on the shaping pipeline — bare unknown values would
    # now raise via _validate_permissive_value (0.8.5+).
    resp = build_response(
        cd=cd, df=df,
        filters={"institution": "nonexistent_bank_xyz_wildcard*"},
        measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.row_count == 0
    assert resp.records == []


def test_response_unit_none_when_no_records(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet,
        header_row=cd.header_row,
        data_start_row=cd.data_start_row,
    )
    # Wildcard substring guaranteed to match nothing — exercises the
    # zero-records path without tripping the permissive-value validation.
    resp = build_response(
        cd=cd, df=df,
        filters={"institution": "nonexistent_xyz_wildcard*"},
        measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.unit is None


def test_drop_blank_rows_on_synthetic_blanks():
    df = pd.DataFrame({
        "A": ["x", None, "y", None],
        "B": [1.0, None, 3.0, None],
        "C": ["a", "b", "c", None],
    })
    out = drop_blank_rows(df, ["A", "B"])
    # Rows 1 and 3 have all NaN in A,B
    assert len(out) == 2
    assert list(out["A"]) == ["x", "y"]


def test_partial_blank_kept():
    """If only some key cols are NaN, the row stays."""
    df = pd.DataFrame({
        "A": ["x", None, "y"],
        "B": [1.0, 2.0, None],
    })
    out = drop_blank_rows(df, ["A", "B"])
    # All rows have at least one non-NaN in [A,B] → all kept
    assert len(out) == 3


def test_period_range_filter_excludes_old(adi_key_stats_xlsx):
    """start_period drops earlier observations."""
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet, header_row=cd.header_row, data_start_row=cd.data_start_row,
    )
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period="2099-01-01", end_period=None, fmt="records", user_query={},
    )
    # No quarter in 2099 → empty
    assert resp.row_count == 0


def test_filter_int_value_stringified(adi_key_stats_xlsx):
    """Filter values that aren't strings get stringified before matching.

    Since 0.8.5 the stringified value is also fed into the permissive-value
    validator, so an unknown int now raises a clean ValueError (with the
    int echoed) rather than silently producing zero rows.
    """
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet, header_row=cd.header_row, data_start_row=cd.data_start_row,
    )
    with pytest.raises(ValueError, match="12345"):
        build_response(
            cd=cd, df=df, filters={"institution": 12345},
            measures="cet1_ratio",
            start_period=None, end_period=None, fmt="records", user_query={},
        )


def test_wildcard_with_only_stars_raises(adi_key_stats_xlsx):
    """An '*' or '~' with no needle should be rejected."""
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet, header_row=cd.header_row, data_start_row=cd.data_start_row,
    )
    with pytest.raises(ValueError, match="reduced to empty"):
        build_response(
            cd=cd, df=df, filters={"institution": "*"},
            measures=None,
            start_period=None, end_period=None, fmt="records", user_query={},
        )


def test_csv_export_of_empty_records_is_empty_string(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = read_xlsx(
        adi_key_stats_xlsx,
        sheet=cd.sheet, header_row=cd.header_row, data_start_row=cd.data_start_row,
    )
    # Wildcard substring path — skips alias validation but matches no rows,
    # so we still exercise the empty-CSV serialisation branch.
    resp = build_response(
        cd=cd, df=df, filters={"institution": "nonexistent_xyz_wildcard*"},
        measures="cet1_ratio",
        start_period=None, end_period=None, fmt="csv", user_query={},
    )
    assert resp.csv == ""
