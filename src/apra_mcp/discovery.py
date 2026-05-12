"""Auto-discovery of the latest XLSX URL for a curated APRA dataset.

APRA publishes quarterly XLSX files at date-versioned paths
(`/sites/default/files/YYYY-MM/...`), so the URL changes every quarter.
data.gov.au's APRA mirror is a dead-stub (catalogue entries point at
defunct SharePoint URLs), so the only reliable resolver is to scrape the
canonical APRA landing page HTML.

Three-tier resolution:

  Tier 1  Live scrape       → httpx GET landing page (conditional GET via
                                ETag → 304 between releases, near-zero cost).
                                Extract .xlsx hrefs, match against the YAML's
                                `filename_pattern` regex, pick the best match.
                                Result cached as "discovery" for 6h.

  Tier 2  Bundled seed      → src/apra_mcp/data/seed_urls.json shipped in
                                the wheel. Last-known-good URL per dataset.
                                Used when Tier 1 returns no match. Response
                                is flagged stale=True with a reason.

  Tier 3  Curated default   → YAML `download_url` (the URL hardcoded at
                                dataset-authoring time). Used only when seed
                                manifest is also missing.

The seed manifest is refreshed by `.github/workflows/refresh-urls.yml` so
even users on old pip installs stay current for weeks.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .client import APRAAPIError, APRAClient


@dataclass(frozen=True)
class DiscoverySpec:
    """How to scrape the landing page for a dataset's XLSX.

    Args:
        landing_url: canonical APRA landing page URL (e.g.
            "https://www.apra.gov.au/quarterly-..."). The scraper fetches
            this HTML.
        filename_pattern: regex applied to the *decoded* filename in each
            <a href> (after URL-decoding %20 etc.). The regex match wins;
            if multiple match, the one with the latest ISO/quarter date
            embedded in the filename wins.
        prefer_database: when True, prefer filenames containing 'database'
            (long-format APRA files) over presentation Excel. APRA's GI/LI
            publications ship both — the database file is what we want.
        exclude_patterns: optional list of regexes; any filename matching
            ANY exclude pattern is dropped. Used to filter out
            "specifications" or "(historical data)" files when the dataset
            wants the current series.
    """
    landing_url: str
    filename_pattern: str
    prefer_database: bool = False
    exclude_patterns: tuple[str, ...] = ()


class DiscoveryError(Exception):
    """Raised when no XLSX URL can be resolved.

    Callers should catch this and fall back to the seed manifest / YAML
    default. Discovery should upgrade staleness, never introduce failures.
    """


_HREF_XLSX_RE = re.compile(
    r'href=["\']([^"\']+\.xlsx)["\']',
    re.IGNORECASE,
)
_DATE_TOKEN_RE = re.compile(
    r"(\d{4})[-_/](\d{1,2})", re.IGNORECASE,
)
_MONTH_NAME_RE = re.compile(
    r"(?:^|[^A-Za-z])("
    r"january|february|march|april|may|june|"
    r"july|august|september|october|november|december|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
    r")[^A-Za-z]*?(\d{4})(?:[^0-9]|$)",
    re.IGNORECASE,
)
_MONTH_TO_NUM = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


async def resolve_via_scrape(client: APRAClient, spec: DiscoverySpec) -> str:
    """Tier 1: scrape the landing page, return the best-matching XLSX URL.

    Raises DiscoveryError on any failure path so callers can fall back.
    """
    try:
        html_bytes = await client.fetch_landing_html(spec.landing_url)
    except APRAAPIError as e:
        raise DiscoveryError(f"failed to fetch {spec.landing_url}: {e}") from e

    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        raise DiscoveryError(f"non-decodable HTML at {spec.landing_url}: {e}") from e

    raw_hrefs = _HREF_XLSX_RE.findall(html)
    if not raw_hrefs:
        raise DiscoveryError(
            f"no .xlsx links found on landing page {spec.landing_url} — "
            "page may have been redesigned"
        )

    from urllib.parse import unquote, urljoin

    candidates: list[tuple[int, str, str]] = []  # (sort_key, url, decoded_filename)
    pattern = re.compile(spec.filename_pattern, re.IGNORECASE)
    excludes = [re.compile(p, re.IGNORECASE) for p in spec.exclude_patterns]

    for href in raw_hrefs:
        url = urljoin(spec.landing_url, href)
        decoded = unquote(url.split("/")[-1])
        if any(ex.search(decoded) for ex in excludes):
            continue
        if not pattern.search(decoded):
            continue
        # Sort score: date in filename + database-preference bonus
        score = _filename_date_score(decoded)
        if spec.prefer_database and "database" in decoded.lower():
            score += 10_000_00  # decisive bonus
        candidates.append((score, url, decoded))

    if not candidates:
        raise DiscoveryError(
            f"no XLSX on {spec.landing_url} matched pattern "
            f"{spec.filename_pattern!r} (after applying excludes)"
        )

    candidates.sort(key=lambda t: -t[0])
    return candidates[0][1]


def _filename_date_score(filename: str) -> int:
    """Heuristic 'newer is better' score for a filename.

    Combines (in order of strength) any matched month-name + year, then
    YYYY-MM numeric tokens, then bare YYYY. Result is an integer where
    larger = newer. Used to break ties when multiple .xlsx files match
    the pattern.
    """
    best = 0
    # Month name + year — e.g. "December 2025"
    for m in _MONTH_NAME_RE.finditer(filename):
        month = _MONTH_TO_NUM.get(m.group(1).lower(), 0)
        try:
            year = int(m.group(2))
        except ValueError:
            continue
        score = year * 100 + month
        if score > best:
            best = score
    # Numeric YYYY-MM or YYYY_MM tokens
    for m in _DATE_TOKEN_RE.finditer(filename):
        try:
            year, month = int(m.group(1)), int(m.group(2))
        except ValueError:
            continue
        if 1 <= month <= 12 and 1990 <= year <= 2100:
            score = year * 100 + month
            if score > best:
                best = score
    # Bare 4-digit year
    if best == 0:
        for tok in re.findall(r"\b(\d{4})\b", filename):
            try:
                year = int(tok)
            except ValueError:
                continue
            if 1990 <= year <= 2100 and year * 100 > best:
                best = year * 100
    return best


def load_seed_manifest() -> dict[str, str]:
    """Load the bundled seed_urls.json manifest.

    Returns an empty dict if the manifest is missing or malformed — the
    caller will fall back to YAML defaults.
    """
    try:
        ref = resources.files("apra_mcp").joinpath("data/seed_urls.json")
        text = ref.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, AttributeError):
        here = Path(__file__).resolve().parent / "data" / "seed_urls.json"
        if not here.is_file():
            return {}
        text = here.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    urls = data.get("urls")
    if not isinstance(urls, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in urls.items():
        if isinstance(k, str) and isinstance(v, str) and v.startswith(("http://", "https://")):
            out[k] = v
    return out


def seed_manifest_metadata() -> dict[str, Any]:
    """Return the seed manifest's metadata block (when it was last refreshed).

    Used to populate stale_reason fields with an honest "last verified at" date.
    """
    try:
        ref = resources.files("apra_mcp").joinpath("data/seed_urls.json")
        text = ref.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, AttributeError):
        here = Path(__file__).resolve().parent / "data" / "seed_urls.json"
        if not here.is_file():
            return {}
        text = here.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k != "urls"}


@dataclass(frozen=True)
class ResolvedURL:
    """Result of a 3-tier resolution."""
    url: str
    tier: str                    # "scrape" | "seed" | "yaml-default"
    stale: bool = False
    reason: str | None = None


async def resolve_for_dataset(
    client: APRAClient,
    dataset_id: str,
    spec: DiscoverySpec | None,
    yaml_default: str,
) -> ResolvedURL:
    """Three-tier resolution.

    Returns ResolvedURL — never raises. The stale flag is True when the
    response did not come from a live successful scrape.
    """
    # Tier 1: live scrape
    if spec is not None:
        try:
            url = await resolve_via_scrape(client, spec)
            return ResolvedURL(url=url, tier="scrape", stale=False)
        except DiscoveryError as e:
            scrape_err = str(e)
        except Exception as e:  # pragma: no cover — be defensive
            scrape_err = f"unexpected discovery error: {type(e).__name__}: {e}"
    else:
        scrape_err = "no discovery spec on dataset"

    # Tier 2: bundled seed manifest
    seed = load_seed_manifest()
    seed_url = seed.get(dataset_id)
    if seed_url:
        meta = seed_manifest_metadata()
        as_of = meta.get("refreshed_at") or meta.get("generated_at") or "unknown date"
        return ResolvedURL(
            url=seed_url,
            tier="seed",
            stale=True,
            reason=(
                f"Live scrape failed ({scrape_err}); served from bundled seed "
                f"manifest (last verified {as_of})."
            ),
        )

    # Tier 3: YAML default
    return ResolvedURL(
        url=yaml_default,
        tier="yaml-default",
        stale=True,
        reason=(
            f"Live scrape failed ({scrape_err}); no seed manifest entry for "
            f"{dataset_id!r}; using YAML-default URL."
        ),
    )
