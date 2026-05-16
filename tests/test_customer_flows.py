"""End-to-end multi-call customer-flow tests against fixtures.

These simulate the journey an AI agent would take: search → describe → query.
They run offline using fixtures (the live equivalents live in test_integration.py).
"""
from __future__ import annotations

import pytest

from apra_mcp import server
from apra_mcp.parsing import drop_blank_rows, read_xlsx


@pytest.fixture(autouse=True)
def patch_fetch(
    monkeypatch,
    adi_key_stats_xlsx,
    adi_rwa_xlsx,
    super_fund_level_xlsx,
    insurance_general_xlsx,
    life_insurance_xlsx,
    life_insurance_historical_xlsx,
    insurance_general_historical_xlsx,
):
    fixtures = {
        "ADI_KEY_STATS": adi_key_stats_xlsx,
        "ADI_RISK_WEIGHTED_ASSETS": adi_rwa_xlsx,
        "SUPER_FUND_LEVEL": super_fund_level_xlsx,
        "INSURANCE_GENERAL": insurance_general_xlsx,
        "INSURANCE_GENERAL_HISTORICAL": insurance_general_historical_xlsx,
        "LIFE_INSURANCE": life_insurance_xlsx,
        "LIFE_INSURANCE_HISTORICAL": life_insurance_historical_xlsx,
    }

    async def fake_fetch(cd):
        body = fixtures.get(cd.id)
        if body is None:
            raise RuntimeError(f"No fixture for {cd.id}")
        df = read_xlsx(
            body, sheet=cd.sheet,
            header_row=cd.header_row, data_start_row=cd.data_start_row,
        )
        dim_source_cols = [c.source_column for c in cd.columns.values() if c.role == "dimension"]
        if dim_source_cols:
            df = drop_blank_rows(df, dim_source_cols)
        return df, f"https://test/{cd.id}.xlsx", False, None

    monkeypatch.setattr(server, "_fetch_and_parse", fake_fetch)
    yield


@pytest.mark.asyncio
async def test_flow_search_describe_query_adi():
    # 1. Search to discover the dataset
    results = await server.search_datasets("bank capital ratio")
    assert any(s.id == "ADI_KEY_STATS" for s in results)

    # 2. Describe to learn the schema
    d = await server.describe_dataset("ADI_KEY_STATS")
    assert "cet1_ratio" in {m.key for m in d.measures}

    # 3. Query CBA
    r = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    assert r.row_count > 0
    cet1 = [rec for rec in r.records if rec.measure == "cet1_ratio"]
    assert len(cet1) == 1
    assert 0.0 < cet1[0].value < 1.0


@pytest.mark.asyncio
async def test_flow_top_n_then_get_data():
    # Identify the largest bank, then pull its detailed profile
    top = await server.top_n("ADI_KEY_STATS", "total_capital", n=1)
    biggest = top.records[0].dimensions["institution"]

    detail = await server.get_data(
        "ADI_KEY_STATS",
        filters={"institution": biggest},
    )
    institutions = {rec.dimensions["institution"] for rec in detail.records}
    assert biggest in institutions


@pytest.mark.asyncio
async def test_flow_compare_two_banks():
    """Two queries for two different banks, then assert on the spreads."""
    cba = await server.latest("ADI_KEY_STATS", filters={"institution": "cba"})
    westpac = await server.latest("ADI_KEY_STATS", filters={"institution": "westpac"})
    assert cba.row_count > 0
    assert westpac.row_count > 0
    cba_cet1 = next(r.value for r in cba.records if r.measure == "cet1_ratio")
    wpc_cet1 = next(r.value for r in westpac.records if r.measure == "cet1_ratio")
    # Both should be in the regulated range
    assert 0.05 < cba_cet1 < 0.30
    assert 0.05 < wpc_cet1 < 0.30


@pytest.mark.asyncio
async def test_flow_super_fund_drill_down():
    """Look up the biggest super fund, then get its full schema."""
    top = await server.top_n("SUPER_FUND_LEVEL", "total_member_accounts", n=1)
    biggest_fund = top.records[0].dimensions["fund_name"]
    detail = await server.get_data(
        "SUPER_FUND_LEVEL",
        filters={"fund_name": biggest_fund},
    )
    assert detail.row_count > 0


@pytest.mark.asyncio
async def test_flow_insurance_describe_shows_framework_warning():
    """describe_dataset on insurance should surface framework break info."""
    d = await server.describe_dataset("LIFE_INSURANCE")
    assert d.framework is not None
    assert d.framework.historical_dataset == "LIFE_INSURANCE_HISTORICAL"


@pytest.mark.asyncio
async def test_flow_insurance_current_then_historical():
    """An agent compares post-AASB17 and pre-AASB17 datasets — both load."""
    current = await server.describe_dataset("LIFE_INSURANCE")
    historical = await server.describe_dataset("LIFE_INSURANCE_HISTORICAL")
    assert current.framework.basis == "post-AASB17"
    assert historical.framework.basis == "pre-AASB17"


@pytest.mark.asyncio
async def test_flow_response_envelope_consistent_across_datasets():
    """Every dataset returns the same response envelope shape."""
    for did in (
        "ADI_KEY_STATS", "ADI_RISK_WEIGHTED_ASSETS", "SUPER_FUND_LEVEL",
        "INSURANCE_GENERAL", "INSURANCE_GENERAL_HISTORICAL",
        "LIFE_INSURANCE", "LIFE_INSURANCE_HISTORICAL",
    ):
        r = await server.get_data(did, measures=None)
        # Every response carries the same top-level fields
        assert r.dataset_id == did
        assert r.source == "Australian Prudential Regulation Authority"
        assert "Attribution 3.0 Australia" in r.attribution
        assert r.apra_url.startswith("https://www.apra.gov.au/")
        assert r.download_url is not None


@pytest.mark.asyncio
async def test_flow_csv_format_round_trip():
    r = await server.get_data(
        "ADI_KEY_STATS",
        filters={"institution": "cba"}, measures="cet1_ratio",
        format="csv",
    )
    lines = r.csv.splitlines()
    assert lines[0].startswith("period,measure,value")
    assert len(lines) >= 2


@pytest.mark.asyncio
async def test_flow_list_curated_is_complete():
    ids = server.list_curated()
    expected = {
        "ADI_KEY_STATS", "ADI_RISK_WEIGHTED_ASSETS", "SUPER_FUND_LEVEL",
        "INSURANCE_GENERAL", "INSURANCE_GENERAL_HISTORICAL",
        "LIFE_INSURANCE", "LIFE_INSURANCE_HISTORICAL",
        "QUARTERLY_SUPER_PERFORMANCE", "ADI_PROPERTY_EXPOSURES",
        "MONTHLY_BANKING_STATS", "ADI_PERFORMANCE",
        "INSURANCE_HEALTH", "MYSUPER_PRODUCTS",
    }
    assert set(ids) == expected


@pytest.mark.asyncio
async def test_flow_search_ranks_topical_match_first():
    """A query about 'insurance' should rank insurance datasets at the top."""
    out = await server.search_datasets("life insurance premium")
    assert out[0].id in ("LIFE_INSURANCE", "LIFE_INSURANCE_HISTORICAL")
