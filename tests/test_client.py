"""HTTP client tests — error wrapping, host pinning, conditional GET."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from apra_mcp.cache import Cache
from apra_mcp.client import APRAAPIError, APRAClient, _is_apra_host


@pytest.fixture
def fresh_cache(tmp_path: Path) -> Cache:
    return Cache(tmp_path / "cache.db")


def test_is_apra_host_accepts_canonical():
    assert _is_apra_host("https://www.apra.gov.au/x") is True
    assert _is_apra_host("https://apra.gov.au/x") is True


def test_is_apra_host_rejects_other():
    assert _is_apra_host("https://evil.com/x") is False
    assert _is_apra_host("https://apra.gov.au.evil.com/x") is False
    assert _is_apra_host("https://subdomain.apra.gov.au/x") is False
    assert _is_apra_host("not-a-url") is False
    assert _is_apra_host("") is False


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_success(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"PKZIP-bytes"))
    async with APRAClient(cache=fresh_cache) as client:
        body = await client.fetch_resource(url)
    assert body == b"PKZIP-bytes"


@pytest.mark.asyncio
async def test_fetch_resource_rejects_non_http(fresh_cache: Cache):
    async with APRAClient(cache=fresh_cache) as client:
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/plain,hello",
            "ftp://example.org/file.xlsx",
            "",
        ):
            with pytest.raises(APRAAPIError, match="non-http"):
                await client.fetch_resource(url)


@pytest.mark.asyncio
async def test_fetch_resource_rejects_off_host(fresh_cache: Cache):
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="off-host"):
            await client.fetch_resource("https://evil.com/file.xlsx")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_404(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(return_value=httpx.Response(404))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="404"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_500(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(return_value=httpx.Response(503))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="503"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_timeout(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(side_effect=httpx.ConnectTimeout("timed out"))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="request failed"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_connection_error(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    respx.get(url).mock(side_effect=httpx.ConnectError("dns failed"))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="request failed"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_cache_hit_no_refetch(fresh_cache: Cache):
    url = "https://www.apra.gov.au/file.xlsx"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"hello"))
    async with APRAClient(cache=fresh_cache) as client:
        assert await client.fetch_resource(url) == b"hello"
        assert await client.fetch_resource(url) == b"hello"
        assert await client.fetch_resource(url) == b"hello"
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_resource_in_flight_dedup(fresh_cache: Cache):
    import asyncio
    url = "https://www.apra.gov.au/file.xlsx"

    async def slow(req):
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=b"hello")

    route = respx.get(url).mock(side_effect=slow)
    async with APRAClient(cache=fresh_cache) as client:
        results = await asyncio.gather(*(client.fetch_resource(url) for _ in range(10)))
    assert all(r == b"hello" for r in results)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_landing_html_first_call(fresh_cache: Cache):
    url = "https://www.apra.gov.au/page"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, text="<html>x</html>",
            headers={"etag": '"abc123"', "last-modified": "Wed, 01 Jan 2026 00:00:00 GMT"},
        )
    )
    async with APRAClient(cache=fresh_cache) as client:
        body = await client.fetch_landing_html(url)
    assert b"x" in body


@pytest.mark.asyncio
@respx.mock
async def test_fetch_landing_html_caches(fresh_cache: Cache):
    url = "https://www.apra.gov.au/page"
    route = respx.get(url).mock(
        return_value=httpx.Response(200, text="<html>x</html>",
                                    headers={"etag": '"abc123"'})
    )
    async with APRAClient(cache=fresh_cache) as client:
        body1 = await client.fetch_landing_html(url)
        body2 = await client.fetch_landing_html(url)
    assert body1 == body2
    assert route.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_fetch_landing_rejects_off_host(fresh_cache: Cache):
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="off-host"):
            await client.fetch_landing_html("https://evil.com/page")


@pytest.mark.asyncio
async def test_fetch_landing_rejects_non_http(fresh_cache: Cache):
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="non-http"):
            await client.fetch_landing_html("javascript:alert(1)")


@pytest.mark.asyncio
async def test_client_context_manager_closes(fresh_cache: Cache):
    async with APRAClient(cache=fresh_cache) as client:
        assert client._http is not None
    # After __aexit__, client._http should be closed
    assert client._http.is_closed


# ─── stale-fallback graceful degradation (CLAUDE.md quality dim #4) ──────


async def _prime_stale_cache(db_path: Path, url: str, payload: bytes, age_hours: float) -> None:
    """Put `payload` into the cache as if it was fetched `age_hours` ago.
    Used to test the stale-fallback path: a regular cache.get() with a normal
    TTL will miss this row (because cached_at is older than the TTL window),
    but cache.get_stale() will still return it.
    """
    import time
    import aiosqlite
    from apra_mcp.cache import Cache
    cache = Cache(db_path)
    await cache._ensure_init()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO http_cache (cache_key, payload, cached_at, kind) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
            "payload=excluded.payload, cached_at=excluded.cached_at",
            (url, payload, time.time() - age_hours * 3600, "data"),
        )
        await conn.commit()


@pytest.mark.asyncio
@respx.mock
async def test_stale_fallback_serves_cached_payload_on_5xx(tmp_path: Path):
    """When upstream apra.gov.au returns 5xx and we have a cached payload past
    its TTL, serve the cached payload and mark the response as stale. Agents
    continue reasoning rather than crashing."""
    from apra_mcp.client import get_stale_signal, reset_stale_signal

    url = "https://www.apra.gov.au/file.xlsx"
    db_path = tmp_path / "cache.db"

    # Prime an 8-day-old cache entry — past the 7-day "data" TTL, so cache.get()
    # misses but cache.get_stale() will still return it.
    await _prime_stale_cache(db_path, url, b"PKZIP-stale-bytes", age_hours=24 * 8)

    reset_stale_signal()
    respx.get(url).mock(return_value=httpx.Response(503, text="Service Unavailable"))
    cache = Cache(db_path)
    async with APRAClient(cache=cache) as client:
        body = await client.fetch_resource(url)
    assert body == b"PKZIP-stale-bytes", "fallback payload must be served"
    stale, reason = get_stale_signal()
    assert stale is True, "stale flag must be set after 5xx fallback"
    assert reason and "503" in reason, f"stale_reason should mention the 5xx: {reason}"
    assert "minute" in reason.lower(), f"stale_reason should report age: {reason}"


@pytest.mark.asyncio
@respx.mock
async def test_stale_fallback_serves_cached_on_request_error(tmp_path: Path):
    """Same as 5xx test but for httpx.RequestError (DNS / connection refused / etc.)."""
    from apra_mcp.client import get_stale_signal, reset_stale_signal

    url = "https://www.apra.gov.au/file.xlsx"
    db_path = tmp_path / "cache.db"
    await _prime_stale_cache(db_path, url, b"PKZIP-stale-bytes", age_hours=24 * 8)

    reset_stale_signal()
    respx.get(url).mock(side_effect=httpx.ConnectError("simulated DNS failure"))
    cache = Cache(db_path)
    async with APRAClient(cache=cache) as client:
        body = await client.fetch_resource(url)
    assert body == b"PKZIP-stale-bytes"
    stale, reason = get_stale_signal()
    assert stale is True
    assert reason and "ConnectError" in reason


@pytest.mark.asyncio
@respx.mock
async def test_raises_when_no_stale_cache_to_fall_back_to(fresh_cache: Cache):
    """Empty cache + upstream 5xx → still raises APRAAPIError (original behaviour
    when there's nothing to gracefully degrade to)."""
    from apra_mcp.client import reset_stale_signal

    url = "https://www.apra.gov.au/file.xlsx"
    reset_stale_signal()
    respx.get(url).mock(return_value=httpx.Response(503, text="Service Unavailable"))
    async with APRAClient(cache=fresh_cache) as client:
        with pytest.raises(APRAAPIError, match="503"):
            await client.fetch_resource(url)


@pytest.mark.asyncio
async def test_cache_get_stale_returns_payload_and_timestamp(tmp_path: Path):
    """Cache.get_stale() returns (payload, cached_at) regardless of TTL —
    the building block for the client's stale-fallback path."""
    from datetime import timedelta

    cache = Cache(tmp_path / "cache.db")
    await cache.set("https://example.org/x", b"hello", kind="data")
    # Normal `get` with a tiny TTL should miss
    fresh = await cache.get("https://example.org/x", ttl=timedelta(seconds=0))
    assert fresh is None
    # `get_stale` should return regardless of TTL
    stale = await cache.get_stale("https://example.org/x")
    assert stale is not None
    payload, cached_at = stale
    assert payload == b"hello"
    assert cached_at > 0
    # Non-existent key → None
    miss = await cache.get_stale("https://example.org/missing")
    assert miss is None
