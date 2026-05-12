"""Refresh src/apra_mcp/data/seed_urls.json from live APRA landing pages.

Run by .github/workflows/refresh-urls.yml on a daily schedule. Locally:

    uv run python scripts/refresh_seed.py

If any URL changed, the script rewrites seed_urls.json and prints a diff.
Exit codes: 0 = success (changed or unchanged), non-zero = scrape failure.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from apra_mcp import curated
from apra_mcp.client import APRAClient
from apra_mcp.discovery import DiscoveryError, DiscoverySpec, resolve_via_scrape


SEED_PATH = Path(__file__).resolve().parents[1] / "src" / "apra_mcp" / "data" / "seed_urls.json"


async def main() -> int:
    current = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    fresh_urls: dict[str, str] = {}
    failures: list[str] = []

    async with APRAClient() as client:
        for cd in curated.list_all():
            if cd.discovery is None:
                fresh_urls[cd.id] = current.get("urls", {}).get(cd.id, cd.download_url)
                continue
            spec = DiscoverySpec(
                landing_url=cd.discovery.landing_url,
                filename_pattern=cd.discovery.filename_pattern,
                prefer_database=cd.discovery.prefer_database,
                exclude_patterns=cd.discovery.exclude_patterns,
            )
            try:
                url = await resolve_via_scrape(client, spec)
                fresh_urls[cd.id] = url
            except DiscoveryError as e:
                failures.append(f"{cd.id}: {e}")
                fresh_urls[cd.id] = current.get("urls", {}).get(cd.id, cd.download_url)

    new_payload = {
        "$schema": current.get("$schema", ""),
        "generated_at": current.get("generated_at", date.today().isoformat()),
        "refreshed_at": date.today().isoformat(),
        "comment": current.get("comment", ""),
        "urls": fresh_urls,
    }

    # Print diff
    changed: list[str] = []
    for k, new_url in fresh_urls.items():
        old_url = current.get("urls", {}).get(k)
        if old_url != new_url:
            changed.append(f"  {k}\n    OLD: {old_url}\n    NEW: {new_url}")

    if changed:
        print("Changed URLs:")
        for c in changed:
            print(c)
        SEED_PATH.write_text(json.dumps(new_payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {SEED_PATH}")
    else:
        print("No URL changes; seed manifest is current.")
        # Still bump refreshed_at if you want, but keep file clean otherwise.

    if failures:
        print("\nFailures (URLs left at last-known-good):", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
