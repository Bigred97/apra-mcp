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
