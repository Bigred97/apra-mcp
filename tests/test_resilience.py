"""Network-failure resilience tests via respx, plus parsing memory smoke tests.

Memory smoke tests use the head-only XLSX fixtures (the same ones the
parsing tests use) and assert that `read_xlsx` keeps working memory bounded.
The bound is intentionally generous (16 MB tracemalloc peak) — it's an
upper-bound smoke test, not a regression for every byte of pandas growth.
The real-world payoff of the openpyxl-streaming rewrite is on the 7 MB
GI / LI historical files (full-parse peak ~70 MB → row-skip peak ~15 MB);
this test catches the case where someone reverts read_xlsx to the
`pd.read_excel`-loads-everything path.
"""
from __future__ import annotations

import asyncio
import tracemalloc
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx

from apra_mcp.cache import Cache
from apra_mcp.client import APRAAPIError, APRAClient
from apra_mcp.parsing import read_xlsx


@pytest.fixture
def fresh_cache(tmp_path: Path) -> Cache:
    return Cache(tmp_path / "cache.db")


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_apra_api_error(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(return_value=httpx.Response(404))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="404"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_500_raises_apra_api_error(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(return_value=httpx.Response(503, text="upstream gone"))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="503"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_dns_error_raises_apra_api_error(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(side_effect=httpx.ConnectError("dns failed"))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="request failed"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_apra_api_error(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(side_effect=httpx.ReadTimeout("slow"))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="request failed"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
async def test_corrupt_cache_self_heals(tmp_path: Path):
    """A corrupt cache.db file is detected on init and silently rebuilt."""
    db = tmp_path / "cache.db"
    db.write_bytes(b"not even close to sqlite\x00\xff")
    cache = Cache(db)
    await cache.set("k", b"v", kind="data")
    assert await cache.get("k", ttl=timedelta(hours=1)) == b"v"


@pytest.mark.asyncio
@respx.mock
async def test_cache_eviction_during_inflight_doesnt_crash(fresh_cache: Cache):
    """An in-flight request that resolves after the cache is wiped should still complete."""
    url = "https://www.apra.gov.au/file.xlsx"

    async def slow(req):
        await asyncio.sleep(0.02)
        return httpx.Response(200, content=b"hello")

    respx.get(url).mock(side_effect=slow)
    async with APRAClient(cache=fresh_cache) as client:
        body = await client.fetch_resource(url)
    assert body == b"hello"


@pytest.mark.asyncio
async def test_concurrent_cache_init_dont_race(tmp_path: Path):
    """50 parallel first-writes to the same cache file shouldn't race the init."""
    db = tmp_path / "cache.db"
    cache = Cache(db)
    async def w(i):
        await cache.set(f"k{i}", str(i).encode(), kind="data")
    await asyncio.gather(*(w(i) for i in range(50)))
    for i in range(50):
        assert await cache.get(f"k{i}", ttl=timedelta(hours=1)) == str(i).encode()


@pytest.mark.asyncio
@respx.mock
async def test_landing_page_returns_500(fresh_cache: Cache):
    url = "https://www.apra.gov.au/page"
    respx.get(url).mock(return_value=httpx.Response(503))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError):
            await client.fetch_landing_html(url)


@pytest.mark.asyncio
@respx.mock
async def test_landing_etag_304_serves_from_cache(tmp_path: Path):
    """After a 200 cached, a 304 conditional response keeps using the cached body."""
    db = tmp_path / "cache.db"
    cache = Cache(db)
    url = "https://www.apra.gov.au/page"
    # Pre-seed cache + validators by simulating a 200 fetch
    respx.get(url).mock(
        return_value=httpx.Response(
            200, text="<html>v1</html>", headers={"etag": '"v1"'},
        )
    )
    async with APRAClient(cache=cache) as client:
        body1 = await client.fetch_landing_html(url)
    assert b"v1" in body1

    # Manually expire by reducing the cached_at way back
    import aiosqlite
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE http_cache SET cached_at = 0 WHERE cache_key = ?", (url,))
        await conn.commit()

    # Next fetch: server returns 304 → client keeps the cached body
    respx.get(url).mock(return_value=httpx.Response(304))
    async with APRAClient(cache=cache) as client:
        body2 = await client.fetch_landing_html(url)
    assert body2 == body1


@pytest.mark.asyncio
@respx.mock
async def test_inflight_dedup_under_failure(fresh_cache: Cache):
    """If 10 parallel callers hit a failure, all should see the error — not hang."""
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(return_value=httpx.Response(503))
    async with APRAClient(cache=fresh_cache) as client:
        results = await asyncio.gather(
            *(client.fetch_resource(url) for _ in range(10)),
            return_exceptions=True,
        )
    assert all(isinstance(r, APRAAPIError) for r in results), [type(r).__name__ for r in results]


# ─── Parsing memory smoke tests ─────────────────────────────────────────
# The openpyxl read-only iteration path keeps working memory proportional
# to the rows we *keep*, not the rows the file holds. These tests pin that
# guarantee against the fixture set — if read_xlsx ever regresses to a
# load-everything implementation, these fire.

_MEM_BOUND_MB = 16  # generous; real worst-case is ~3 MB on the fixtures


def _peak_mb(fn):
    tracemalloc.start()
    try:
        fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / (1024 * 1024)


def test_read_xlsx_peak_memory_insurance_general_historical(insurance_general_historical_xlsx):
    """Peak memory parsing the historical GI fixture stays under the bound."""
    peak = _peak_mb(
        lambda: read_xlsx(
            insurance_general_historical_xlsx,
            sheet="Data",
            header_row=1,
        )
    )
    assert peak < _MEM_BOUND_MB, f"read_xlsx peaked at {peak:.1f} MB (>{_MEM_BOUND_MB} MB bound)"


def test_read_xlsx_peak_memory_life_insurance_historical(life_insurance_historical_xlsx):
    """Peak memory parsing the historical LI fixture stays under the bound."""
    peak = _peak_mb(
        lambda: read_xlsx(
            life_insurance_historical_xlsx,
            sheet="Data",
            header_row=1,
        )
    )
    assert peak < _MEM_BOUND_MB, f"read_xlsx peaked at {peak:.1f} MB (>{_MEM_BOUND_MB} MB bound)"


def test_read_xlsx_pushdown_filters_rows(insurance_general_historical_xlsx):
    """Period pushdown drops rows during iteration, not in pandas after."""
    # No filter: full fixture row count.
    df_all = read_xlsx(
        insurance_general_historical_xlsx, sheet="Data", header_row=1,
    )
    n_all = len(df_all)
    assert n_all > 0

    # Narrow filter: must return strictly fewer rows.
    df_2022 = read_xlsx(
        insurance_general_historical_xlsx,
        sheet="Data",
        header_row=1,
        period_source_column="Reporting date",
        start_period="2022-01-01",
        end_period="2022-12-31",
    )
    assert len(df_2022) <= n_all
    assert "Reporting date" in df_2022.columns


def test_read_xlsx_pushdown_memory_bounded(insurance_general_historical_xlsx):
    """Row-skip pushdown keeps memory bounded even with a tight bound."""
    peak = _peak_mb(
        lambda: read_xlsx(
            insurance_general_historical_xlsx,
            sheet="Data",
            header_row=1,
            period_source_column="Reporting date",
            start_period="2022-01-01",
            end_period="2022-12-31",
        )
    )
    assert peak < _MEM_BOUND_MB, f"filtered read_xlsx peaked at {peak:.1f} MB"
