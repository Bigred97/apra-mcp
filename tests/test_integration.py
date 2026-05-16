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
    assert len(ids) == 13


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_response_has_server_version():
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    assert r.server_version
    assert r.server_version != "0.0.0+unknown"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_mysuper_products_range_check():
    """MYSUPER_PRODUCTS: transposed-then-wide; ~80 products × 11 years.

    Sanity range checks on documented measures in AUD '000s. A real MySuper
    product should have non-trivial total assets (>$10M = 10,000 in '000s)
    and the largest products are tens of billions.
    """
    r = await server.get_data(
        "MYSUPER_PRODUCTS", start_period="2023", end_period="2024",
    )
    assert r.row_count > 0, "Should return at least one row"
    assert r.stale is False, f"expected live scrape, got stale: {r.stale_reason}"

    # Schema check on first record: per-product wide layout exposes
    # product/fund identifiers as dimensions and financial measures separately.
    sample = r.records[0]
    assert sample.period is not None
    # June year-end snapshot — every period in 2023-2024 window should end with -06-30
    for rec in r.records:
        assert rec.period and rec.period.endswith("-06-30"), (
            f"MySuper is annual June-end; got period={rec.period!r}"
        )
    # Dimensions should include product + fund identifiers
    sample_dims = sample.dimensions
    expected_dim_keys = {"product_name", "fund_name", "fund_abn", "fund_type"}
    assert expected_dim_keys.issubset(sample_dims.keys()), (
        f"missing identifier dims; got {sorted(sample_dims.keys())}"
    )

    # Sanity range on total_assets_000: AUD '000s, real products are
    # 10k (=$10M) to ~250M (=$250B for the biggest balanced default).
    assets = [rec.value for rec in r.records if rec.measure == "total_assets_000"]
    assert len(assets) >= 1, "should report total_assets_000 for at least one product"
    assert min(assets) > 10_000, (
        f"smallest MySuper product total_assets_000 unrealistically low: {min(assets)}"
    )
    assert max(assets) < 500_000_000, (
        f"largest MySuper product total_assets_000 unrealistically high: {max(assets)}"
    )

    # Unit on every record should be AUD thousands
    for rec in r.records:
        if rec.measure == "total_assets_000":
            assert rec.unit == "AUD thousands"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_insurance_health_range_check():
    """INSURANCE_HEALTH: AASB 17 break flag is surfaced; HIB premium revenue sane."""
    r = await server.get_data(
        "INSURANCE_HEALTH",
        filters={"data_item": "HIB premium revenue"},
        start_period="2023",
        end_period="2024",
    )
    assert r.row_count > 0, "Should return at least one row"
    assert r.stale is False, f"expected live scrape, got stale: {r.stale_reason}"

    # AASB 17 framework flag must be surfaced on every INSURANCE_HEALTH response.
    assert r.framework is not None, "INSURANCE_HEALTH must expose AASB-17 framework"
    assert r.framework.basis == "post-AASB17"
    assert r.framework.break_date == "2023-09-30"

    # Schema check: long-format with data_item / subject / category dimensions
    # and a single "value" measure carrying the actual observation.
    for rec in r.records:
        assert rec.measure == "value"
        assert rec.unit == "AUD"
        assert rec.dimensions.get("data_item") == "HIB premium revenue"
        assert rec.dimensions.get("subject") == "Financial performance (supplementary)"

    # HIB premium revenue is the industry's total quarterly private health
    # insurance premium take. Currently ~$7B/quarter — assert in [$3B, $15B]
    # to give plenty of headroom for future quarters while still catching
    # unit-error parsing bugs (e.g. accidental ÷1000 or ×1000).
    values = [rec.value for rec in r.records]
    assert all(v > 3_000_000_000 for v in values), (
        f"HIB premium revenue suspiciously low: {min(values)}"
    )
    assert all(v < 15_000_000_000 for v in values), (
        f"HIB premium revenue suspiciously high: {max(values)}"
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_adi_performance_range_check():
    """ADI_PERFORMANCE: transposed with footnote markers on metric names.

    APRA's source XLSX uses footnote markers (trailing 'a') on some metric
    names; ADI_PERFORMANCE.yaml exposes stable plain-English aliases. Test
    both: (1) aliases route to the footnote-bearing source value, (2) the
    returned $ ranges are sensible for the ADI industry aggregate.
    """
    # Use the YAML's alias — exercises the footnote-marker indirection.
    r = await server.get_data(
        "ADI_PERFORMANCE",
        filters={"metric": "net_profit_after_tax"},
        start_period="2024-Q1",
        end_period="2024-Q4",
    )
    assert r.row_count >= 1, "Should return at least one quarter of NPAT"
    assert r.stale is False, f"expected live scrape, got stale: {r.stale_reason}"

    # The alias must resolve to the footnote-bearing source value.
    npat_metrics = {rec.dimensions.get("metric") for rec in r.records}
    assert "Net profit (loss) after taxa" in npat_metrics, (
        f"footnote-marker alias not preserved; got metrics: {npat_metrics}"
    )

    # NPAT is reported in AUD millions, consolidated group basis. Industry
    # aggregate quarterly NPAT runs ~$7B-$13B/quarter for the ADI sector —
    # assert in [$1B, $30B] to bracket real history without false flags.
    for rec in r.records:
        assert rec.unit == "AUD millions"
        assert rec.value is not None
        # Values are in millions, so $1B = 1000, $30B = 30000
        assert 1_000 < rec.value < 30_000, (
            f"ADI quarterly NPAT looks unrealistic: {rec.value} AUD millions "
            f"at period {rec.period}"
        )

    # Net interest income is much larger than NPAT — verify the NII alias
    # routes correctly and the value is materially bigger than NPAT.
    r_nii = await server.get_data(
        "ADI_PERFORMANCE",
        filters={"metric": "nii"},
        start_period="2024-Q1",
        end_period="2024-Q4",
    )
    assert r_nii.row_count >= 1
    nii_values = [rec.value for rec in r_nii.records]
    # NII quarterly aggregate is currently ~$24B; assert in [$10B, $50B]
    assert all(10_000 < v < 50_000 for v in nii_values), (
        f"ADI quarterly NII looks unrealistic: {nii_values}"
    )
