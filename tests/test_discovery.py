"""Discovery layer tests — 3-tier resolution with HTML scraping + seed manifest."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from apra_mcp.cache import Cache
from apra_mcp.client import APRAClient
from apra_mcp.discovery import (
    DiscoveryError,
    DiscoverySpec,
    _filename_date_score,
    load_seed_manifest,
    resolve_for_dataset,
    resolve_via_scrape,
    seed_manifest_metadata,
)


@pytest.fixture
def fresh_cache(tmp_path: Path) -> Cache:
    return Cache(tmp_path / "cache.db")


_SAMPLE_LANDING_HTML = """<!DOCTYPE html>
<html><body>
<p>Old release: <a href="/sites/default/files/2025-06/Quarterly%20life%20insurance%20performance%20statistics%20database%20%28historical%20data%29%20June%202023.xlsx">historical</a></p>
<p>Latest: <a href="/sites/default/files/2026-03/Quarterly%20life%20insurance%20performance%20statistics%20database%20December%202025.xlsx" class="document-link__label">current</a></p>
<p>Specs: <a href="/sites/default/files/2025-06/Quarterly%20life%20insurance%20performance%20statistics%20-%20specifications.xlsx">specs</a></p>
</body></html>"""


@pytest.mark.asyncio
@respx.mock
async def test_scrape_picks_latest_dated_database_file(fresh_cache: Cache):
    landing = "https://www.apra.gov.au/quarterly-life-insurance-performance-statistics"
    respx.get(landing).mock(return_value=httpx.Response(200, text=_SAMPLE_LANDING_HTML))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing,
            filename_pattern=r"(?i)life\s+insurance.*database",
            prefer_database=True,
            exclude_patterns=("(?i)historical", "(?i)specifications"),
        )
        url = await resolve_via_scrape(client, spec)
    assert "2026-03" in url
    assert "December%202025" in url


@pytest.mark.asyncio
@respx.mock
async def test_scrape_excludes_historical(fresh_cache: Cache):
    """exclude_patterns must skip files matching them."""
    landing = "https://www.apra.gov.au/quarterly-life-insurance-performance-statistics"
    respx.get(landing).mock(return_value=httpx.Response(200, text=_SAMPLE_LANDING_HTML))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing,
            filename_pattern=r"(?i)life\s+insurance.*database",
            exclude_patterns=("(?i)historical",),
        )
        url = await resolve_via_scrape(client, spec)
    assert "historical" not in url.lower()


@pytest.mark.asyncio
@respx.mock
async def test_scrape_no_match_raises(fresh_cache: Cache):
    """When pattern matches nothing, DiscoveryError is raised."""
    landing = "https://www.apra.gov.au/some-page"
    respx.get(landing).mock(return_value=httpx.Response(200, text="<html><body>no xlsx here</body></html>"))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing,
            filename_pattern=r"never_matches",
        )
        with pytest.raises(DiscoveryError, match="no .xlsx links"):
            await resolve_via_scrape(client, spec)


@pytest.mark.asyncio
@respx.mock
async def test_scrape_pattern_unmatched_raises(fresh_cache: Cache):
    """Pattern matched none of the available .xlsx links."""
    landing = "https://www.apra.gov.au/some-page"
    respx.get(landing).mock(
        return_value=httpx.Response(
            200,
            text='<a href="/sites/default/files/2026-03/Other.xlsx">x</a>',
        )
    )
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing,
            filename_pattern=r"(?i)never_matches",
        )
        with pytest.raises(DiscoveryError, match="matched pattern"):
            await resolve_via_scrape(client, spec)


@pytest.mark.asyncio
@respx.mock
async def test_scrape_404_raises_discovery_error(fresh_cache: Cache):
    landing = "https://www.apra.gov.au/dead-page"
    respx.get(landing).mock(return_value=httpx.Response(404))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing, filename_pattern=r".*",
        )
        with pytest.raises(DiscoveryError, match="failed to fetch"):
            await resolve_via_scrape(client, spec)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_for_dataset_tier1_success(fresh_cache: Cache):
    """Successful scrape → ResolvedURL with stale=False."""
    landing = "https://www.apra.gov.au/quarterly-life-insurance-performance-statistics"
    respx.get(landing).mock(return_value=httpx.Response(200, text=_SAMPLE_LANDING_HTML))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing,
            filename_pattern=r"(?i)life\s+insurance.*database",
            prefer_database=True,
            exclude_patterns=("(?i)historical", "(?i)specifications"),
        )
        result = await resolve_for_dataset(client, "LIFE_INSURANCE", spec, yaml_default="https://www.apra.gov.au/fallback.xlsx")
    assert result.tier == "scrape"
    assert result.stale is False
    assert "2026-03" in result.url


@pytest.mark.asyncio
@respx.mock
async def test_resolve_for_dataset_falls_back_to_seed(fresh_cache: Cache):
    """When scrape fails, fall through to the bundled seed_urls.json."""
    landing = "https://www.apra.gov.au/dead-page"
    respx.get(landing).mock(return_value=httpx.Response(503))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(landing_url=landing, filename_pattern=r".*")
        # Use a real dataset_id that's in the seed manifest
        result = await resolve_for_dataset(
            client, "ADI_KEY_STATS", spec, yaml_default="https://www.apra.gov.au/yaml-default.xlsx",
        )
    assert result.tier == "seed"
    assert result.stale is True
    assert result.reason is not None
    assert "centralised" in result.url.lower()


@pytest.mark.asyncio
@respx.mock
async def test_resolve_for_dataset_falls_back_to_yaml(fresh_cache: Cache):
    """When scrape fails AND no seed entry, return the YAML default."""
    landing = "https://www.apra.gov.au/dead-page"
    respx.get(landing).mock(return_value=httpx.Response(503))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(landing_url=landing, filename_pattern=r".*")
        result = await resolve_for_dataset(
            client, "NO_SUCH_DATASET", spec, yaml_default="https://www.apra.gov.au/yaml-default.xlsx",
        )
    assert result.tier == "yaml-default"
    assert result.stale is True
    assert result.url == "https://www.apra.gov.au/yaml-default.xlsx"


@pytest.mark.asyncio
async def test_resolve_for_dataset_no_spec(fresh_cache: Cache):
    """Dataset without discovery spec → fall straight to seed/yaml."""
    async with APRAClient(cache=fresh_cache) as client:
        result = await resolve_for_dataset(
            client, "ADI_KEY_STATS", None, yaml_default="https://www.apra.gov.au/yaml.xlsx",
        )
    assert result.tier in ("seed", "yaml-default")
    assert result.stale is True


def test_seed_manifest_loads():
    urls = load_seed_manifest()
    assert isinstance(urls, dict)
    assert "ADI_KEY_STATS" in urls
    assert urls["ADI_KEY_STATS"].startswith("https://")


def test_seed_manifest_metadata():
    meta = seed_manifest_metadata()
    assert "generated_at" in meta or "refreshed_at" in meta


def test_filename_date_score_prefers_newer():
    older = _filename_date_score("file%20-%20June%202023.xlsx")
    newer = _filename_date_score("file%20-%20December%202025.xlsx")
    assert newer > older


def test_filename_date_score_handles_numeric():
    s = _filename_date_score("/files/2026-03/file.xlsx")
    assert s > 0


def test_filename_date_score_returns_zero_for_no_date():
    assert _filename_date_score("no_dates_here.xlsx") == 0


def test_filename_date_score_handles_month_name_only():
    s = _filename_date_score("data-March-2026.xlsx")
    assert s == 2026 * 100 + 3


def test_filename_date_score_handles_sept_abbreviation():
    s = _filename_date_score("data-Sept-2025.xlsx")
    assert s == 2025 * 100 + 9


@pytest.mark.asyncio
@respx.mock
async def test_scrape_handles_non_utf8_gracefully(fresh_cache: Cache):
    """Non-UTF8 HTML should be decoded with errors='replace', not crash."""
    landing = "https://www.apra.gov.au/x"
    bad_bytes = b'<a href="/sites/default/files/2026-03/Foo.xlsx">\xff\xfe</a>'
    respx.get(landing).mock(return_value=httpx.Response(200, content=bad_bytes))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(
            landing_url=landing, filename_pattern=r"Foo",
        )
        url = await resolve_via_scrape(client, spec)
    assert "Foo.xlsx" in url


@pytest.mark.asyncio
@respx.mock
async def test_resolve_url_pins_apra_host(fresh_cache: Cache):
    """If the landing page somehow advertised an off-host XLSX, refuse it.

    Defense-in-depth: the URL we pick is resolved via urljoin against the
    landing_url base, so an off-host href becomes the off-host URL — but the
    fetch_resource boundary check refuses non-apra hosts.
    """
    landing = "https://www.apra.gov.au/x"
    # The scraper's regex will pick this up — let's verify the host pinning
    # happens later (at fetch time, not at resolve time).
    html = '<a href="https://evil.com/Foo.xlsx">x</a>'
    respx.get(landing).mock(return_value=httpx.Response(200, text=html))
    async with APRAClient(cache=fresh_cache) as client:
        spec = DiscoverySpec(landing_url=landing, filename_pattern=r"Foo")
        url = await resolve_via_scrape(client, spec)
        # The URL would resolve to evil.com — but fetching is blocked.
        with pytest.raises(Exception, match="off-host"):
            await client.fetch_resource(url)
