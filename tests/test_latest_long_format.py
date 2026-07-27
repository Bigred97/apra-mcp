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
async def test_latest_adi_key_stats_unfiltered_returns_all_institutions():
    """Regression: latest("ADI_KEY_STATS") with NO filters must return every
    institution at the latest period, not one arbitrary bank's numbers
    presented as the whole sector.

    Grouping wide-format tail-N only by `measure` (without also slicing by
    period) pools every institution's observations into a single list per
    measure, so obs[-last_n:] silently keeps just one institution — this is
    the exact bug corroborated by the CHANGELOG 0.8.15 entry recording
    latest(ADI_KEY_STATS) -> 7 rows (one per measure, one bank total).
    """
    r = await server.latest("ADI_KEY_STATS")
    institutions = {
        rec.dimensions.get("institution") for rec in r.records if rec.dimensions.get("institution")
    }
    assert len(institutions) > 1, (
        f"expected more than one distinct institution, got {institutions!r} "
        f"(row_count={r.row_count})"
    )


@pytest.mark.asyncio
async def test_latest_adi_key_stats_truncation_is_entity_complete():
    """Regression: truncating latest()'s wide-format response must yield
    COMPLETE entities, not `limit` rows of a single measure.

    The selection logic above (all institutions at the latest period) is
    correct on its own, but it builds `records` measure-major, and because
    every survivor shares the same latest period the old plain (period,)
    sort was a stable no-op that left that measure-major order intact. A
    `limit` head-slice then returned e.g. row_count=50, truncated_at=532,
    every single row the SAME measure ("cet1_capital") — "latest" went from
    "one arbitrary institution" (the original bug) to "one arbitrary
    measure" (this one). The fix sorts (period, entity, measure) so a
    head-slice returns whole institutions with all of their measures.
    """
    # Ground truth: the untruncated response (fixture is well under 10,000
    # rows), used to know each institution's REAL measure set — some
    # institutions may legitimately be missing a measure or two in the
    # source data, so we compare against ground truth rather than assuming
    # every institution has the full dataset-wide measure catalog.
    full = await server.latest("ADI_KEY_STATS", limit=10000)
    assert full.truncated_at is None, "fixture must fit under limit=10000 for this test to be valid"
    pre_truncation_count = full.row_count

    full_by_institution: dict[str, set[str | None]] = {}
    for rec in full.records:
        inst = rec.dimensions.get("institution")
        if inst:
            full_by_institution.setdefault(inst, set()).add(rec.measure)

    # limit=21 is smaller than the full result (~500+ rows) and is an exact
    # multiple of ADI_KEY_STATS's 7 measures, so a correct entity-major sort
    # returns exactly 3 complete institutions.
    r = await server.latest("ADI_KEY_STATS", limit=21)

    measures = {rec.measure for rec in r.records}
    institutions = {
        rec.dimensions.get("institution") for rec in r.records if rec.dimensions.get("institution")
    }
    assert len(measures) > 1, (
        f"expected more than one distinct measure in the truncated slice, got {measures!r}"
    )
    assert len(institutions) > 1, (
        f"expected more than one distinct institution in the truncated slice, got {institutions!r}"
    )

    by_institution: dict[str, set[str | None]] = {}
    for rec in r.records:
        inst = rec.dimensions.get("institution")
        if inst:
            by_institution.setdefault(inst, set()).add(rec.measure)
    for inst, ms in by_institution.items():
        assert ms == full_by_institution[inst], (
            f"{inst} has an incomplete measure set in the truncated slice: "
            f"got {ms}, expected the full set {full_by_institution[inst]}"
        )

    assert r.truncated_at == pre_truncation_count, (
        f"truncated_at must equal the pre-truncation row count "
        f"({pre_truncation_count}), got {r.truncated_at}"
    )
    assert r.row_count == 21


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
