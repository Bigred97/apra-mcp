"""top_n ranking-tool tests against offline fixtures via monkeypatched fetch."""
from __future__ import annotations


import pytest

from apra_mcp import server
from apra_mcp.parsing import drop_blank_rows, read_xlsx


@pytest.fixture(autouse=True)
def patch_fetch_with_fixture(monkeypatch, adi_key_stats_xlsx, super_fund_level_xlsx, life_insurance_xlsx):
    """Replace _fetch_and_parse with a fixture-backed version so top_n tests
    run without network."""
    fixtures = {
        "ADI_KEY_STATS": adi_key_stats_xlsx,
        "SUPER_FUND_LEVEL": super_fund_level_xlsx,
        "LIFE_INSURANCE": life_insurance_xlsx,
    }

    async def fake_fetch(cd, *, start_period=None, end_period=None):
        body = fixtures.get(cd.id)
        if body is None:
            raise RuntimeError(f"No fixture for dataset {cd.id}")
        df = read_xlsx(
            body,
            sheet=cd.sheet,
            header_row=cd.header_row,
            data_start_row=cd.data_start_row,
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
async def test_top_n_returns_n_rows():
    r = await server.top_n("ADI_KEY_STATS", "total_capital", n=3)
    assert r.row_count == 3


@pytest.mark.asyncio
async def test_top_n_sorted_descending():
    r = await server.top_n("ADI_KEY_STATS", "total_capital", n=5)
    values = [rec.value for rec in r.records]
    assert values == sorted(values, reverse=True)


@pytest.mark.asyncio
async def test_top_n_bottom_sorted_ascending():
    r = await server.top_n("ADI_KEY_STATS", "total_capital", n=5, direction="bottom")
    values = [rec.value for rec in r.records]
    assert values == sorted(values)


@pytest.mark.asyncio
async def test_top_n_with_sector_filter():
    r = await server.top_n(
        "ADI_KEY_STATS", "total_capital", n=10, filters={"sector": "major_banks"},
    )
    for rec in r.records:
        assert rec.dimensions.get("sector") == "Major banks"


@pytest.mark.asyncio
async def test_top_n_returns_fewer_when_pool_is_small():
    r = await server.top_n(
        "ADI_KEY_STATS", "total_capital", n=100, filters={"sector": "major_banks"},
    )
    # Only ~4 major banks → row_count <= 5 (Macquarie might be in there too)
    assert r.row_count <= 10


@pytest.mark.asyncio
async def test_top_n_preserves_envelope():
    """top_n should keep dataset_id, name, attribution, framework etc."""
    r = await server.top_n("ADI_KEY_STATS", "cet1_ratio", n=3)
    assert r.dataset_id == "ADI_KEY_STATS"
    assert "Creative Commons Attribution 3.0 Australia" in r.attribution
    assert r.row_count == 3


@pytest.mark.asyncio
async def test_top_n_super_funds():
    r = await server.top_n("SUPER_FUND_LEVEL", "total_member_accounts", n=3)
    assert r.row_count == 3
    # Should have fund_name in dimensions
    for rec in r.records:
        assert "fund_name" in rec.dimensions


@pytest.mark.asyncio
async def test_top_n_life_insurance_value():
    """Long-format LI dataset — measure key is 'value', not a domain name."""
    r = await server.top_n("LIFE_INSURANCE", "value", n=5)
    assert r.row_count == 5
    values = [rec.value for rec in r.records]
    assert values == sorted(values, reverse=True)


@pytest.mark.asyncio
async def test_top_n_framework_propagates_for_insurance():
    r = await server.top_n("LIFE_INSURANCE", "value", n=3)
    assert r.framework is not None
    assert r.framework.basis == "post-AASB17"


@pytest.mark.asyncio
async def test_top_n_n_equal_to_one():
    r = await server.top_n("ADI_KEY_STATS", "cet1_ratio", n=1)
    assert r.row_count == 1


@pytest.mark.asyncio
async def test_top_n_with_period_filter():
    r = await server.top_n(
        "ADI_KEY_STATS", "cet1_ratio", n=5, filters={"period": "2025-12-31"},
    )
    # All rows should have the requested period
    for rec in r.records:
        assert rec.period == "2025-12-31"


@pytest.mark.asyncio
async def test_top_n_handles_no_results():
    """Filter that matches nothing → empty result, not error.

    Uses a wildcard substring to exercise the empty-result path on the
    permissive `institution` dim — bare unknown values now raise via
    `_validate_permissive_value` (0.8.5+).
    """
    r = await server.top_n(
        "ADI_KEY_STATS", "cet1_ratio", n=5,
        filters={"institution": "atlantis_bank_xyz_wildcard*"},
    )
    assert r.row_count == 0
    assert r.records == []


@pytest.mark.asyncio
async def test_top_n_skips_none_values():
    """Records with None measure values get filtered out before ranking."""
    r = await server.top_n("ADI_KEY_STATS", "cet1_ratio", n=10)
    for rec in r.records:
        assert rec.value is not None
