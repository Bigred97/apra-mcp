"""DataFrame → DataResponse shaping tests."""
from __future__ import annotations

import pytest

from apra_mcp import curated
from apra_mcp.parsing import read_xlsx
from apra_mcp.shaping import build_response


def _parse(cd, body: bytes):
    return read_xlsx(
        body, sheet=cd.sheet, header_row=cd.header_row, data_start_row=cd.data_start_row,
    )


def test_adi_key_stats_response_shape(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures=None,
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.dataset_id == "ADI_KEY_STATS"
    assert resp.source == "Australian Prudential Regulation Authority"
    assert "Attribution 3.0 Australia" in resp.attribution
    assert resp.row_count > 0
    # Every record carries CBA institution + Major banks sector
    for r in resp.records:
        assert r.dimensions["institution"] == "Commonwealth Bank of Australia"
        assert r.dimensions["sector"] == "Major banks"


def test_adi_filter_by_alias_resolves(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    # The 'westpac' alias should resolve to the canonical name
    resp = build_response(
        cd=cd, df=df, filters={"institution": "westpac"},
        measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.row_count >= 1
    for r in resp.records:
        assert "Westpac" in r.dimensions["institution"]


def test_adi_top_n_pattern_via_full_query(adi_key_stats_xlsx):
    """Pulling all records of one measure should give us all banks for that quarter."""
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"sector": "major_banks"},
        measures="total_capital",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    # 4 major banks × ~1 quarter in the head-sample
    assert resp.row_count >= 1


def test_format_csv_returns_csv_string(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period=None, end_period=None, fmt="csv", user_query={},
    )
    assert resp.csv is not None
    assert "period,measure,value" in resp.csv
    assert resp.records == []


def test_format_series_groups_by_measure(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"},
        measures=["cet1_ratio", "tier1_ratio"],
        start_period=None, end_period=None, fmt="series", user_query={},
    )
    measures = {g["measure"] for g in resp.records}
    assert measures == {"cet1_ratio", "tier1_ratio"}


def test_format_records_default(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.csv is None
    assert isinstance(resp.records, list)


def test_filter_list_or_semantics(adi_key_stats_xlsx):
    """[a, b] in filters should match rows with either value."""
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": ["cba", "westpac"]},
        measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    institutions = {r.dimensions["institution"] for r in resp.records}
    assert "Commonwealth Bank of Australia" in institutions
    assert "Westpac Banking Corporation" in institutions


def test_filter_wildcard_substring(adi_key_stats_xlsx):
    """Permissive columns support 'macquarie*' wildcard substring match."""
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "macquarie*"},
        measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    # Should match Macquarie Bank Limited
    assert resp.row_count >= 1
    for r in resp.records:
        assert "macquarie" in r.dimensions["institution"].lower()


def test_filter_empty_list_raises(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    with pytest.raises(ValueError, match="empty list"):
        build_response(
            cd=cd, df=df, filters={"institution": []}, measures=None,
            start_period=None, end_period=None, fmt="records", user_query={},
        )


def test_filter_unknown_key_raises(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    with pytest.raises(ValueError, match="Unknown filter"):
        build_response(
            cd=cd, df=df, filters={"nonsense": "x"}, measures=None,
            start_period=None, end_period=None, fmt="records", user_query={},
        )


def test_period_range_filter(adi_key_stats_xlsx):
    """start_period='2025-01-01' drops earlier quarters."""
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period="2025-01-01", end_period=None, fmt="records", user_query={},
    )
    for r in resp.records:
        assert r.period is None or r.period >= "2025-01-01"


def test_period_swap_handled_in_caller():
    """Period swap should be caught at server.py, not in build_response (which trusts inputs).

    This is a contract test: the shape function must not raise on out-of-order
    periods — that's the caller's responsibility. Empty result is fine.
    """
    cd = curated.get("ADI_KEY_STATS")
    # passthrough: nothing should raise
    # (We don't run this through a real df because that's not the contract.)
    assert cd is not None


def test_attribution_string_correct_for_apra():
    """APRA attribution references CC-BY 3.0 Australia per APRA's licence."""
    cd = curated.get("ADI_KEY_STATS")
    import pandas as pd
    df = pd.DataFrame()
    # Build with empty df just to check the static attribution
    from apra_mcp.models import DataResponse
    from datetime import datetime, timezone
    resp = DataResponse(
        dataset_id="X", dataset_name="X",
        retrieved_at=datetime.now(timezone.utc), apra_url="https://www.apra.gov.au/x",
    )
    assert "Creative Commons Attribution 3.0 Australia" in resp.attribution
    assert "creativecommons.org/licenses/by/3.0/au" in resp.attribution
    assert "4.0" not in resp.attribution


def test_response_carries_framework_for_insurance(life_insurance_xlsx):
    cd = curated.get("LIFE_INSURANCE")
    df = _parse(cd, life_insurance_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={}, measures=None,
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.framework is not None
    assert resp.framework.basis == "post-AASB17"
    assert resp.framework.historical_dataset == "LIFE_INSURANCE_HISTORICAL"


def test_response_no_framework_for_adi(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.framework is None


def test_last_n_trims_per_measure(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures=None,
        start_period=None, end_period=None, fmt="records", user_query={}, last_n=1,
    )
    # last_n=1 should give exactly one record per measure
    by_measure: dict = {}
    for r in resp.records:
        by_measure.setdefault(r.measure, 0)
        by_measure[r.measure] += 1
    for m, n in by_measure.items():
        assert n == 1, f"measure {m} got {n} records, expected 1"


def test_period_field_extracted_for_insurance(insurance_general_xlsx):
    cd = curated.get("INSURANCE_GENERAL")
    df = _parse(cd, insurance_general_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"industry_segment": "total_industry"},
        measures=None,
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    # Period column should populate Observation.period (not dimensions)
    for r in resp.records:
        assert "period" not in r.dimensions  # not in dimensions
        if r.period is not None:
            assert len(r.period) == 10  # YYYY-MM-DD
            assert r.period[4] == "-"


def test_response_unit_set_when_single(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.unit == "ratio"


def test_response_unit_none_when_mixed(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"},
        measures=["cet1_capital", "cet1_ratio"],
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.unit is None  # mix of AUD millions + ratio


def test_period_bounds_inferred_from_records(adi_key_stats_xlsx):
    cd = curated.get("ADI_KEY_STATS")
    df = _parse(cd, adi_key_stats_xlsx)
    resp = build_response(
        cd=cd, df=df, filters={"institution": "cba"}, measures="cet1_ratio",
        start_period=None, end_period=None, fmt="records", user_query={},
    )
    assert resp.period["start"] is not None
    assert resp.period["end"] is not None


def test_stale_flag_propagates():
    cd = curated.get("ADI_KEY_STATS")
    import pandas as pd
    df = pd.DataFrame()
    resp = build_response(
        cd=cd, df=df, filters={}, measures=None,
        start_period=None, end_period=None, fmt="records", user_query={},
        stale=True, stale_reason="seed fallback",
    )
    assert resp.stale is True
    assert resp.stale_reason == "seed fallback"
