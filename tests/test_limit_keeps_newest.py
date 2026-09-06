"""Regression: `limit` after ASCENDING sort must keep the NEWEST periods.

P0 (2026-09-06 worth-buying audit): ausdata-api `/v1/series/{id}/latest`
fetches sister `_get_data_impl(..., limit=1)` and takes `records[-1]`.
apra-mcp previously head-sliced (`records[:limit]`) after sorting oldest→
newest, so AU.BANK.TOTAL.LOANS / HOUSING.LOANS / INTEREST.INCOME published
`2004-09-01` while `/meta` correctly reported `observation_end=2026-03-31`
(and ADI_PERFORMANCE full history + `latest()` via `last_n=1` were fine).

Same defect class as ato `_truncate_records` / abs `truncate_records`.
"""
from __future__ import annotations

import pytest

from apra_mcp import server
from apra_mcp.models import Observation
from apra_mcp.shaping import _truncate_records

# Canonical Ausdata series → ADI_PERFORMANCE metric filters (series_catalog.yaml).
_BANK_SERIES_METRICS = (
    ("AU.BANK.TOTAL.LOANS", "Loans and advances"),
    ("AU.BANK.HOUSING.LOANS", "Housing loans"),
    ("AU.BANK.INTEREST.INCOME", "Interest income"),
)


def test_truncate_records_periodic_keeps_newest():
    rows = [
        Observation(period="2004-09-01", value=1.0, measure="v"),
        Observation(period="2010-06-30", value=2.0, measure="v"),
        Observation(period="2026-03-31", value=3.0, measure="v"),
    ]
    out = _truncate_records(rows, 1)
    assert len(out) == 1
    assert out[0].period == "2026-03-31"
    assert out[0].value == 3.0


def test_truncate_records_periodic_limit_n_still_ascending():
    rows = [
        Observation(period=f"2020-0{i}-01", value=float(i), measure="v")
        for i in range(1, 6)
    ]
    out = _truncate_records(rows, 2)
    assert [r.period for r in out] == ["2020-04-01", "2020-05-01"]


def test_truncate_records_period_less_still_head_slices():
    rows = [
        Observation(period=None, value=float(i), measure="v", dimensions={"id": str(i)})
        for i in range(5)
    ]
    out = _truncate_records(rows, 2)
    assert [r.dimensions["id"] for r in out] == ["0", "1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("series_id,metric", _BANK_SERIES_METRICS)
async def test_adi_performance_limit_1_matches_meta_end(series_id: str, metric: str):
    """Gateway contract: limit=1 row period == full-history period end.

    Mirrors the API canary `latest.date == meta.observation_end` for the
    three AU.BANK.* series that resolve to ADI_PERFORMANCE.
    """
    full = await server._get_data_impl(
        "ADI_PERFORMANCE", {"metric": metric}, None, None, None, "records",
    )
    assert full.row_count >= 2, f"{series_id}: expected multi-period history"
    meta_end = full.period.get("end")
    assert meta_end, f"{series_id}: full response missing period.end"
    assert full.records[-1].period == meta_end

    limited = await server._get_data_impl(
        "ADI_PERFORMANCE", {"metric": metric}, None, None, None, "records",
        limit=1,
    )
    assert limited.row_count == 1, f"{series_id}: limit=1 must return one row"
    assert limited.truncated_at == full.row_count
    latest_date = limited.records[0].period
    assert latest_date == meta_end, (
        f"{series_id}: limit=1 returned {latest_date!r}, "
        f"expected meta end {meta_end!r} (oldest-row head-slice regression)"
    )
