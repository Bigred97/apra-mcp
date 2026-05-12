# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] — 2026-05-12

### Attribution correction

- **Attribution string switched from CC-BY 4.0 International to CC-BY 3.0
  Australia** to align with APRA's actual licence terms. Both the
  `attribution` field on every `DataResponse` and the README/llms.txt/docs
  now read "Creative Commons Attribution 3.0 Australia" with the
  https://creativecommons.org/licenses/by/3.0/au/ URL. No code-shape
  changes — only the licence text + URL.
- This brings apra-mcp in line with the sister packages (abs-mcp, ato-mcp,
  rba-mcp), which all carry CC-BY 3.0 AU attribution.
- Tests updated; 229 unit + 16 live remain green.

### Dataset scope — what shipped and what's deferred

The v0.1.0 spec listed six curated datasets including `ADI_PROPERTY_EXPOSURES`
and `SUPER_AGGREGATE`. After inspecting the actual APRA XLSX layouts, the
final v0.1.x cut substitutes:

- **Shipped** (7 datasets, all long-format / wide layout — cleaner to parse,
  easier to filter):
  - `ADI_KEY_STATS` — per-bank capital + key ratios (Table 1 from the ADI
    centralised publication; entity-level, the more valuable cut)
  - `ADI_RISK_WEIGHTED_ASSETS` — per-bank RWA breakdown (Table 2 from the
    same file; a free bonus that emerged from the inspection pass)
  - `SUPER_FUND_LEVEL` — fund-by-fund detail
  - `INSURANCE_GENERAL` + `INSURANCE_GENERAL_HISTORICAL`
  - `LIFE_INSURANCE` + `LIFE_INSURANCE_HISTORICAL`

- **Deferred to v0.2** (both are transposed multi-tab industry-aggregate
  files that need a transposed-layout parser before they can ship cleanly):
  - `ADI_PROPERTY_EXPOSURES` — industry-aggregate commercial property
    exposures + residential mortgage approvals from the ADI property file
  - `SUPER_AGGREGATE` — quarterly superannuation performance industry totals
    (the multi-tab presentational file, distinct from `SUPER_FUND_LEVEL`)

Net coverage is broader than the original spec (entity-level RWA is a clear
value-add for any agent that asks "which banks carry the most credit risk").

## [0.1.0] — 2026-05-12

### Initial release

apra-mcp v0.1.0 ships seven curated APRA datasets across banking,
superannuation, and insurance, exposed through a six-tool MCP surface that
mirrors abs-mcp / rba-mcp / ato-mcp.

### Tools (6)

- `search_datasets(query, limit=10)` — fuzzy search the curated catalog
- `describe_dataset(dataset_id)` — list dimensions, measures, framework info
- `get_data(dataset_id, filters, measures, start_period, end_period, format)`
- `latest(dataset_id, filters, measures)` — shortcut to last observation per measure
- `top_n(dataset_id, measure, n, filters, direction)` — server-side ranking
- `list_curated()` — enumerate curated IDs

### Curated datasets (7)

- **`ADI_KEY_STATS`** — per-bank CET1 / Tier 1 / total capital + RWA + ratios,
  every quarter since March 2013. Plain-English `institution: cba` aliases
  for the Big 4 + Macquarie + 70 other ADIs, sector enum, mutual flag.
- **`ADI_RISK_WEIGHTED_ASSETS`** — per-bank RWA broken down by credit /
  operational / market risk, plus IRRBB and traded-market-risk sub-components.
- **`SUPER_FUND_LEVEL`** — fund-by-fund member counts, benefits, median age,
  active/inactive splits. Plain-English aliases for AustralianSuper, Aware,
  HOSTPLUS, REST, UniSuper, HESTA, Cbus etc.
- **`INSURANCE_GENERAL`** — long-format quarterly general insurance database
  (post-AASB17). 14 dimensions × 1 value column × ~24k rows.
- **`INSURANCE_GENERAL_HISTORICAL`** — pre-AASB17 GI archive (Dec 2002 → Jun 2023).
- **`LIFE_INSURANCE`** — long-format quarterly life insurance database
  (post-AASB17). 9 dimensions × 1 value column × ~10.6k rows.
- **`LIFE_INSURANCE_HISTORICAL`** — pre-AASB17 LI archive (Jun 2008 → Jun 2023).

### Reliability engineering

- **3-tier URL discovery** — apra.gov.au publishes XLSX at date-versioned
  paths that change every quarter. The discovery layer scrapes the canonical
  landing page (with ETag-based conditional GET — 304s cost zero bytes), and
  falls back to a CI-refreshed seed manifest, and finally to the YAML default.
- **Schema-fingerprint warning surface** — `_apply_aliases` raises an
  actionable `ValueError` if any expected column disappears from the source
  XLSX, with the first 6 columns it actually saw embedded in the message.
- **Cache self-heal** — corrupt `~/.apra-mcp/cache.db` is detected on init
  and silently rebuilt.
- **In-flight request dedup** — 50 parallel callers asking for the same XLSX
  fan in to exactly one HTTP request.
- **Host pinning** — `fetch_resource` refuses any URL outside `apra.gov.au`,
  defense-in-depth against scraper or seed-manifest corruption.

### Trust contract

Every response includes:

- `source = "Australian Prudential Regulation Authority"`
- `source_url` — canonical APRA landing page
- `download_url` — the actual XLSX URL used (post-discovery)
- `attribution` — CC-BY 3.0 Australia string + license link
- `retrieved_at` — ISO UTC timestamp
- `server_version` — apra-mcp wheel version
- `stale` + `stale_reason` — true when the live scrape failed and we served
  from the bundled seed
- `framework` — basis (post-AASB17 / pre-AASB17), break date, cross-reference
  to the paired historical dataset (insurance datasets only)

### Permissive filters + wildcards

Dimensions flagged `permissive: true` accept any string value and support
substring matching: `{"institution": "macquarie*"}` substring-matches every
Macquarie entity. Useful for entity-name and data-item dimensions where
exhaustively enumerating ~100 long names in the YAML isn't realistic.

### Quality bar

- 229 unit tests, 16 live integration tests against apra.gov.au
- Zero-flake: full unit suite passes 10/10 sequential runs
- Schema fingerprint guards catch column renames
- Defensive validation guards on every MCP tool with "Try X" hints
