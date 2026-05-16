"""Tests for the long-format-aware `latest()` behavior.

For long-format datasets (INSURANCE_GENERAL, LIFE_INSURANCE, etc. — single
"value" measure with the semantic metric in the data_item dimension),
`latest()` must return all rows at the most recent period(s), not a single
record per measure. This fixture-driven suite covers the regression where
`row_count` was always 1 for long-format datasets.
"""
from __future__ import annotations

import pytest

from apra_mcp import server
from apra_mcp.parsing import drop_blank_rows, read_xlsx


@pytest.fixture(autouse=True)
def patch_fetch(
    monkeypatch,
    insurance_general_xlsx,
    insurance_general_historical_xlsx,
    life_insurance_xlsx,
    life_insurance_historical_xlsx,
    adi_key_stats_xlsx,
):
    fixtures = {
        "INSURANCE_GENERAL": insurance_general_xlsx,
        "INSURANCE_GENERAL_HISTORICAL": insurance_general_historical_xlsx,
        "LIFE_INSURANCE": life_insurance_xlsx,
        "LIFE_INSURANCE_HISTORICAL": life_insurance_historical_xlsx,
        "ADI_KEY_STATS": adi_key_stats_xlsx,
    }

    async def fake_fetch(cd, *, start_period=None, end_period=None):
        body = fixtures.get(cd.id)
        if body is None:
            raise RuntimeError(f"No fixture for {cd.id}")
        df = read_xlsx(
            body, sheet=cd.sheet,
            header_row=cd.header_row, data_start_row=cd.data_start_row,
            period_source_column=cd.period_column if cd.layout == "wide" else None,
            start_period=start_period,
            end_period=end_period,
        )
        dim_source_cols = [c.source_column for c in cd.columns.values() if c.role == "dimension"]
        if dim_source_cols:
            df = drop_blank_rows(df, dim_source_cols)
        return df, f"https://test/{cd.id}.xlsx", False, None

    monkeypatch.setattr(server, "_fetch_and_parse", fake_fetch)
    yield


@pytest.mark.asyncio
async def test_latest_life_insurance_returns_many_rows():
    """The regression: this used to return row_count=1."""
    r = await server.latest("LIFE_INSURANCE")
    assert r.row_count > 10, f"expected many rows at latest period, got {r.row_count}"


@pytest.mark.asyncio
async def test_latest_life_insurance_all_at_same_period():
    """All returned records should be from the most recent period."""
    r = await server.latest("LIFE_INSURANCE")
    periods = {rec.period for rec in r.records if rec.period}
    assert len(periods) == 1, f"expected 1 latest period, got {periods}"


@pytest.mark.asyncio
async def test_latest_insurance_general_returns_many_rows():
    r = await server.latest("INSURANCE_GENERAL")
    assert r.row_count > 10


@pytest.mark.asyncio
async def test_latest_insurance_general_historical_returns_many_rows():
    r = await server.latest("INSURANCE_GENERAL_HISTORICAL")
    assert r.row_count > 10


@pytest.mark.asyncio
async def test_latest_life_insurance_historical_returns_many_rows():
    r = await server.latest("LIFE_INSURANCE_HISTORICAL")
    assert r.row_count > 10


@pytest.mark.asyncio
async def test_latest_adi_key_stats_returns_per_measure():
    """Wide-format datasets keep the original per-measure-tail semantics —
    one record per measure × (entity) combo at the latest period."""
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    # CBA has 7 measures; latest=1 means 1 record per measure
    assert r.row_count == 7
    measures = {rec.measure for rec in r.records}
    assert "cet1_ratio" in measures
    assert "total_capital" in measures


@pytest.mark.asyncio
async def test_latest_with_data_item_filter():
    """Latest with a filter on data_item should narrow further."""
    r = await server.latest(
        "LIFE_INSURANCE",
        filters={"data_item": "Actual gross claims incurred"},
    )
    # Many product groups report this metric; all should be at the same latest period
    periods = {rec.period for rec in r.records if rec.period}
    assert len(periods) == 1
    assert r.row_count >= 1
