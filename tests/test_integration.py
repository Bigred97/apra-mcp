"""Live integration tests — marked @pytest.mark.live.

Run with: pytest -m live

These hit the real apra.gov.au — they verify the discovery layer, HTTP
client, parsing, and shaping against current production data. Slow (~10s)
because of XLSX downloads, but high-confidence.
"""
from __future__ import annotations

import pytest

from apra_mcp import server


@pytest.fixture(autouse=True)
async def reset_clients():
    """Each live test starts with a fresh in-process client."""
    await server.reset_client_for_tests()
    server.reset_df_cache_for_tests()
    yield
    await server.reset_client_for_tests()
    server.reset_df_cache_for_tests()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_search_returns_curated():
    out = await server.search_datasets("bank capital")
    ids = {s.id for s in out}
    assert "ADI_KEY_STATS" in ids


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_describe_dataset_adi():
    d = await server.describe_dataset("ADI_KEY_STATS")
    assert d.id == "ADI_KEY_STATS"
    assert d.source_url.startswith("https://www.apra.gov.au/")
    measure_keys = {m.key for m in d.measures}
    assert "cet1_ratio" in measure_keys


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_describe_life_insurance_carries_framework():
    d = await server.describe_dataset("LIFE_INSURANCE")
    assert d.framework is not None
    assert d.framework.basis == "post-AASB17"
    assert d.framework.historical_dataset == "LIFE_INSURANCE_HISTORICAL"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_latest_cba_capital_ratios():
    """CBA's CET1 ratio should be sensible (5–25% range)."""
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    assert r.row_count >= 1
    assert r.stale is False, f"expected live scrape, got stale: {r.stale_reason}"
    cet1 = [rec for rec in r.records if rec.measure == "cet1_ratio"]
    assert len(cet1) == 1
    assert 0.05 < cet1[0].value < 0.25, f"CBA CET1 looks unrealistic: {cet1[0].value}"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_top_5_banks_by_total_capital_are_major():
    """The top 5 banks by total capital should include the Big 4."""
    r = await server.top_n("ADI_KEY_STATS", "total_capital", n=5)
    assert r.row_count >= 4
    institutions = {rec.dimensions.get("institution", "") for rec in r.records}
    # At least 3 of the Big 4 + Macquarie should appear in the top 5
    big_5 = {
        "Commonwealth Bank of Australia",
        "Australia and New Zealand Banking Group Limited",
        "Westpac Banking Corporation",
        "National Australia Bank Limited",
        "Macquarie Bank Limited",
    }
    overlap = institutions & big_5
    assert len(overlap) >= 3, f"only {len(overlap)} of Big 5 in top 5: {institutions}"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_super_fund_top_5_includes_australiansuper():
    """AustralianSuper is the biggest fund by member accounts; should be in top 5."""
    r = await server.top_n(
        "SUPER_FUND_LEVEL", "total_member_accounts", n=5,
    )
    fund_names = {rec.dimensions.get("fund_name", "") for rec in r.records}
    assert "AustralianSuper" in fund_names


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_life_insurance_gross_premiums():
    """Life insurance latest gross premium values are non-trivial."""
    r = await server.latest(
        "LIFE_INSURANCE",
        filters={
            "data_item": "Actual gross premiums accrued",
            "reporting_structure": "Total statutory funds",
        },
    )
    assert r.framework is not None
    assert r.framework.basis == "post-AASB17"
    assert r.row_count >= 1
    # Every record should have a positive value
    for rec in r.records:
        assert rec.value > 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_response_carries_download_url():
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    assert r.download_url is not None
    assert r.download_url.startswith("https://www.apra.gov.au/")
    assert r.download_url.lower().endswith(".xlsx")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_response_attribution_is_ccby3au():
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    assert "Creative Commons Attribution 3.0 Australia" in r.attribution
    assert "creativecommons.org/licenses/by/3.0/au" in r.attribution


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_get_data_format_csv():
    """Format=csv returns a non-empty CSV string."""
    r = await server.get_data(
        "ADI_KEY_STATS",
        filters={"institution": "cba"},
        measures="cet1_ratio",
        format="csv",
    )
    assert r.csv is not None
    assert "period,measure,value" in r.csv
    # Should have at least the header + one row
    lines = r.csv.splitlines()
    assert len(lines) >= 2


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_general_insurance_query():
    """Use a data_item that is reported at the Total-industry level."""
    r = await server.get_data(
        "INSURANCE_GENERAL",
        filters={"data_item": "Additional Tier 1 capital", "industry_segment": "Total industry"},
    )
    assert r.framework is not None
    assert r.framework.basis == "post-AASB17"
    assert r.row_count >= 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_historical_general_insurance():
    """Historical GI should also load and surface its pre-AASB17 framework."""
    r = await server.latest("INSURANCE_GENERAL_HISTORICAL")
    assert r.framework is not None
    assert r.framework.basis == "pre-AASB17"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_top_n_bottom_direction():
    """Bottom 3 banks by CET1 ratio should be non-empty."""
    r = await server.top_n(
        "ADI_KEY_STATS", "cet1_ratio", n=3, direction="bottom",
    )
    assert r.row_count >= 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_period_range_filter():
    r = await server.get_data(
        "ADI_KEY_STATS",
        filters={"institution": "cba"},
        measures="cet1_ratio",
        start_period="2024-01-01",
    )
    # Every record's period should be >= 2024-01-01
    for rec in r.records:
        if rec.period:
            assert rec.period >= "2024-01-01"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_list_curated_count():
    ids = server.list_curated()
    assert len(ids) == 11


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_response_has_server_version():
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    assert r.server_version
    assert r.server_version != "0.0.0+unknown"
