"""Async fetcher for APRA landing pages and XLSX files.

Two endpoints:
- `fetch_landing_html(url)` — pulls an APRA landing page. Cached as "landing".
  Uses conditional GET (If-None-Match / If-Modified-Since) when validators are
  cached so re-fetches between releases return HTTP 304 with zero body bytes.
- `fetch_resource(url)`     — pulls a static XLSX file by URL. Cached as "data".

apra.gov.au is a Drupal/Skpr-hosted site fronted by CloudFront. No auth, no
documented rate limit. We send a courteous User-Agent and dedupe concurrent
in-flight requests so a burst of `latest()` calls fans in to one HTTP request.
"""
from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any

import httpx

from .cache import TTL, Cache, CacheKind

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=15.0)  # GI historical is 7MB

_ALLOWED_HOSTS = ("www.apra.gov.au", "apra.gov.au")


# ─── stale signal (graceful-degradation reporting per CLAUDE.md dim #4) ─
# When apra.gov.au is unreachable, the byte-fetch path falls back to the
# cached payload regardless of TTL and records the staleness in this
# ContextVar. Server-side tool wrappers read it after the request chain
# and merge into DataResponse.stale / .stale_reason. ContextVar (not
# instance attr) so concurrent MCP tool calls each see their own state.
_stale_signal: ContextVar[tuple[bool, str | None]] = ContextVar(
    "apra_mcp_stale_signal", default=(False, None)
)


def reset_stale_signal() -> None:
    """Clear the stale state. Call once at the start of each tool call."""
    _stale_signal.set((False, None))


def get_stale_signal() -> tuple[bool, str | None]:
    """Return (stale, reason) for the most recent fetch chain in this context."""
    return _stale_signal.get()


def _mark_stale(reason: str) -> None:
    """Record that a stale-cache fallback was served this context.

    If multiple fetches in one chain are stale, we keep the FIRST reason
    (it's usually the most informative — the originating upstream failure).
    """
    cur_stale, _ = _stale_signal.get()
    if not cur_stale:
        _stale_signal.set((True, reason))


class APRAAPIError(Exception):
    """Raised when apra.gov.au returns non-2xx or the request fails."""


def _is_apra_host(url: str) -> bool:
    """True only for apra.gov.au and www.apra.gov.au.

    Defense-in-depth: even if the scraper or seed manifest gets corrupted with
    a URL pointing elsewhere, we refuse the fetch.
    """
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return False
    return host in _ALLOWED_HOSTS


class APRAClient:
    def __init__(
        self,
        cache: Cache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache = cache or Cache()
        self._http = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "User-Agent": "apra-mcp/0.1 (+https://github.com/Bigred97/apra-mcp)",
                "Accept": "*/*",
            },
            follow_redirects=True,
        )
        self._in_flight: dict[str, asyncio.Future[bytes]] = {}
        self._in_flight_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "APRAClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def fetch_resource(
        self, url: str, *, kind: CacheKind = "data"
    ) -> bytes:
        """Fetch a static XLSX file by URL. Cached. In-flight deduped.

        For "data" kind we don't use conditional GET — these files rarely
        change between fetches within a quarter, and the byte-cache TTL of
        7 days already catches everything. For "landing" kind, see
        `fetch_landing_html`.
        """
        if not url.startswith(("http://", "https://")):
            raise APRAAPIError(f"Refusing to fetch non-http(s) URL: {url!r}")
        if not _is_apra_host(url):
            raise APRAAPIError(
                f"Refusing to fetch off-host URL {url!r}. "
                "apra-mcp only fetches from apra.gov.au."
            )
        return await self._fetch_cached(url, kind=kind)

    async def fetch_landing_html(self, url: str) -> bytes:
        """Fetch an APRA landing page HTML with conditional-GET support.

        On cache hit within TTL → return cached HTML directly.
        On cache miss with stored ETag → emit If-None-Match; if server returns
        304 we keep using the stored body (and touch the freshness clock).
        Otherwise we store the fresh body + new validators.
        """
        if not url.startswith(("http://", "https://")):
            raise APRAAPIError(f"Refusing to fetch non-http(s) URL: {url!r}")
        if not _is_apra_host(url):
            raise APRAAPIError(
                f"Refusing to fetch off-host URL {url!r}. "
                "apra-mcp only fetches from apra.gov.au."
            )

        # Hot path: still within TTL → return cached body.
        cached_body, _, _ = await self.cache.get_with_validators(url, ttl=TTL["landing"])
        if cached_body is not None:
            return cached_body

        # Stale TTL but we may still have validators (etag / last-modified) from
        # a previous fetch. Send a conditional GET.
        etag, last_mod = await self.cache.get_validators_any_age(url)
        cond_headers: dict[str, str] = {}
        if etag:
            cond_headers["If-None-Match"] = etag
        if last_mod:
            cond_headers["If-Modified-Since"] = last_mod

        try:
            resp = await self._http.get(url, headers=cond_headers or None)
        except httpx.RequestError as e:
            raise APRAAPIError(f"apra.gov.au request failed: {e}") from e

        if resp.status_code == 304:
            # Stored body is still good; bump freshness clock.
            await self.cache.touch(url)
            stored = await self.cache.get(url, ttl=TTL["landing"] * 1000)  # any age
            if stored is not None:
                return stored
            # Race: cache eviction between get_validators and re-read. Re-fetch
            # unconditionally.
            cond_headers.clear()
            try:
                resp = await self._http.get(url)
            except httpx.RequestError as e:
                raise APRAAPIError(f"apra.gov.au request failed: {e}") from e

        if resp.status_code != 200:
            raise APRAAPIError(
                f"apra.gov.au returned {resp.status_code} for {url}"
            )

        body = resp.content
        await self.cache.set(
            url,
            body,
            kind="landing",
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
        )
        return body

    async def _fetch_cached(self, url: str, *, kind: CacheKind) -> bytes:
        cached = await self.cache.get(url, ttl=TTL[kind])
        if cached is not None:
            return cached

        async with self._in_flight_lock:
            existing = self._in_flight.get(url)
            if existing is None:
                future: asyncio.Future[bytes] = (
                    asyncio.get_running_loop().create_future()
                )
                self._in_flight[url] = future

        if existing is not None:
            return await existing

        try:
            try:
                resp = await self._http.get(url)
                resp.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                # Graceful degradation: when apra.gov.au is unreachable, fall
                # back to the most-recent cached payload (regardless of TTL)
                # rather than raising and breaking the agent's chain of
                # reasoning. Staleness is surfaced via the _stale_signal
                # ContextVar and ends up in DataResponse.stale / stale_reason.
                fallback = await self.cache.get_stale(url)
                if fallback is not None:
                    payload, cached_at = fallback
                    age_min = max(0, int((time.time() - cached_at) / 60))
                    if isinstance(e, httpx.HTTPStatusError):
                        upstream = f"APRA API returned {e.response.status_code}"
                    else:
                        upstream = f"APRA API unreachable ({type(e).__name__})"
                    _mark_stale(
                        f"{upstream} for {url}; serving cached payload from "
                        f"~{age_min} minute(s) ago"
                    )
                    future.set_result(payload)
                    return payload
                # Genuinely no cache to fall back to — preserve original
                # raise-with-APRAAPIError behaviour.
                if isinstance(e, httpx.HTTPStatusError):
                    raise APRAAPIError(
                        f"apra.gov.au returned {e.response.status_code} for {url}"
                    ) from e
                raise APRAAPIError(f"apra.gov.au request failed: {e}") from e
            await self.cache.set(
                url,
                resp.content,
                kind=kind,
                etag=resp.headers.get("etag"),
                last_modified=resp.headers.get("last-modified"),
            )
            future.set_result(resp.content)
            return resp.content
        except BaseException as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            async with self._in_flight_lock:
                self._in_flight.pop(url, None)
