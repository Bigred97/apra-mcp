# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-05-15

### Added

- **DataResponse.source_url**: canonical click-through URL field, populated
  alongside the legacy `apra_url` alias. Cross-sister consumers can now read
  `.source_url` uniformly across the portfolio. `apra_url` remains populated
  with the same value for backward compatibility.

## [0.3.0] — 2026-05-15

### Added — Wave 1 portfolio interoperability fixes

Cross-sister consistency pass on input handling + error messages. Three
additive, non-breaking changes that bring `apra-mcp` up to the abs/aihw/ato
standard identified in the portfolio interoperability audit.

- **Int-year coercion in period validation.** `start_period=2024` (a bare
  JSON int) now coerces to `"2024"` instead of raising a TypeError-style
  message. LLM clients routinely send JSON ints; this removes a confusing
  failure mode that surfaced as `must be a string, got int`. Out-of-range
  ints (e.g. `12345`, `1800`, `2200`) still raise — with a hint pointing
  at the canonical `'YYYY'` / `'YYYY-Qx'` / `'YYYY-MM-DD'` forms.
- **Strengthened `ValueError` messages.** Every rejection now follows the
  canonical shape `<rejection>. Did you mean X?. Valid options: <list>. Try
  <tool>(<args>) for more.`. New rapidfuzz-driven "Did you mean ...?"
  hints on: unknown `format` values (`'record'` → `'records'`), unknown
  filter keys (`'institutio'` → `'institution'`). Period-format
  reminders added to both invalid-format and end-before-start errors,
  with worked examples.
- **Type signature broadened** on `get_data`'s `start_period` / `end_period`
  to `str | int | None` so the tool's published schema reflects the new
  coercion behaviour.

8 new unit tests in `tests/test_server_validation.py` cover the coercion
boundary, the `bool`-subclass-of-int guard, the suggestion hints, and the
strengthened period/format error shapes.

### Backward compatibility

No breaking changes. Inputs that previously raised a type error on bare
int years now succeed; every existing rejection path still raises, just
with a more actionable message.

## [0.2.0] — 2026-05-15

### Added — aus-identity integration

The cross-source compatibility moat for the AU public-data MCP stack.
The `state_territory` filter on INSURANCE_GENERAL (and any future
state-aware APRA dataset) now accepts the full canonical menu:

- Canonical short codes (`NSW`, `VIC`, `QLD`, `SA`, `WA`, `TAS`, `NT`, `ACT`)
- Case-insensitive variants (`nsw`, `Nsw`)
- Full names — APRA's canonical form (`New South Wales`, `Victoria`)
- ISO 3166-2 (`AU-NSW`, `AU-VIC`)
- Common aliases (`Tassie`)
- 4-digit postcodes (`2000` → `New South Wales`, `2600` → `Australian Capital Territory`)

All inputs normalise to APRA's canonical long-form so the filter applies
correctly regardless of input shape. Powered by
[`aus-identity`](https://pypi.org/project/aus-identity/).

- **`aus-identity>=0.1.0`** added as a new top-level dependency.
- **`curated.translate_filter_value`** runs state-shaped dim values through
  `aus_identity.normalize_state` + `state_full_name` (or
  `postcode_to_state` + `state_full_name` for postcode input) before
  falling back to the existing permissive pass-through. Values that
  don't match any state form pass through unchanged — backward-compatible
  for legacy buckets like "Total Australia".
- **7 new unit tests** in `tests/test_curated.py` covering short code,
  lowercase, full name, ISO 3166-2, postcode, ACT-postcode boundary, and
  non-state pass-through.

### Backward compatibility

No breaking changes — every input that worked in 0.1.4 still works.

## [0.1.4] — 2026-05-15

### Error-message sweep — rejection messages now suggest the correction

Quality dimension #5 (Deterministic Error Handling) audit. Every `ValueError`
across the public tool surface and the YAML loader was reviewed and rewritten
to carry a "Try X" / "Did you mean X?" / "Valid options: ..." hint instead of
just describing the rejection. ~15 weak sites rewritten across `server.py`,
`curated.py`, and `shaping.py`.

Highlights:

- **`Dataset {id} is not a curated apra-mcp dataset`** (the highest-volume
  agent-facing rejection) now embeds a rapidfuzz-driven `Did you mean 'X'?`
  match plus the first 10 valid IDs and pointers to `list_curated()` /
  `search_datasets()`. A typo like `ADI_KEYSTATS` (missing underscore) now
  surfaces `Did you mean 'ADI_KEY_STATS'?` directly in the error.
- **`Could not fetch dataset X from apra.gov.au`** now includes the upstream
  URL it tried, the landing page to sanity-check, and a "try again — the
  client retries with cached fallback on next warm call" hint, so the agent
  knows whether to retry, escalate, or move on.
- **measures-list type errors** now show example syntax
  (`['cet1_ratio', 'tier1_ratio']`) and point at `describe_dataset('<id>')`
  for valid measure keys.
- **YAML loader errors** (developer-facing, when authoring new curated
  YAMLs) now show example correct syntax inline, e.g. the `framework`
  block, `discovery` block, and individual `column` mappings each carry a
  full one-line example of the correct shape.
- **`Duplicate curated id`** now spells out the fix: rename one of the
  colliding files or delete the duplicate.

### Tests

- 270 unit tests (up from 267 — 3 new regression tests covering the
  dataset-id "Did you mean?" path, the valid-ID enumeration on far typos,
  and the example-syntax check on measures-list type errors)
- Zero-flake across 10 sequential runs

## [0.1.3] — 2026-05-15

### Reliability — stale-cache fallback on upstream failure

- **Graceful degradation when apra.gov.au is unreachable.** Previously, an
  `httpx.HTTPStatusError` (5xx) or `httpx.RequestError` (DNS / connection
  refused / timeout) from apra.gov.au surfaced as an `APRAAPIError` and
  broke the agent's chain of reasoning mid-conversation. The byte-fetch
  path now falls back to the most-recent cached payload (regardless of
  TTL), and the response carries `stale=True` plus a human-readable
  `stale_reason` like `"APRA API returned 503 for <url>; serving cached
  payload from ~12 minute(s) ago"`. Mirrors abs-mcp 0.2.13.
- New `Cache.get_stale(key)` returns `(payload, cached_at_epoch)` ignoring
  TTL — the building block for the fallback path.
- New `_stale_signal` ContextVar in `client.py` propagates the staleness
  out through the discovery + fetch chain without threading return tuples
  through every helper. Per-tool-call isolation via ContextVar so
  concurrent MCP calls don't cross-contaminate.
- The discovery layer's existing stale signal (seed-manifest fallback)
  and the new byte-fetch signal are OR-merged; either trigger marks the
  response stale.
- The original `APRAAPIError` raise behaviour is preserved when there is
  no cached payload to fall back to.

### Trust contract

- New `DataResponse.truncated_at: int | None`. Mirrors the abs-mcp /
  rba-mcp / ato-mcp envelope — set when `latest()` or `top_n` caps a
  large response and carries the pre-truncation row count. Currently
  unused inside `build_response`; surfaces the field so agents can read
  it uniformly across all four sisters.

### Tests

- 267 unit tests (up from 263 — 4 new covering stale fallback)
- Zero-flake across 10 sequential runs

## [0.1.2] — 2026-05-13

### Bug fixes (real customer impact)

- **`latest()` was returning a single record for long-format datasets**
  (`INSURANCE_GENERAL`, `INSURANCE_GENERAL_HISTORICAL`, `LIFE_INSURANCE`,
  `LIFE_INSURANCE_HISTORICAL`). Root cause: `last_n=1` was implemented as
  "keep 1 per measure", but long-format datasets carry a single `value`
  measure with the semantic metric in the `data_item` dimension —
  collapsing to 1 record per measure threw away the whole table. Fix:
  detect long-format mode (one measure + period_column declared) and
  switch to "keep all records at the most recent period(s)". `latest()`
  on insurance datasets now returns hundreds of records (the latest
  quarter's worth) instead of one.
- **Period filters with quarter shorthand (`2025-Q4`) and bare years
  (`2024`) silently returned zero rows.** The source `period_column`
  stores ISO dates (`2025-12-31`) and string-comparing them against
  `"2025-Q4"` excluded everything (`Q` > `1` in ASCII). Fix: a new
  `_expand_period_input` normaliser converts user-supplied periods to
  ISO `YYYY-MM-DD` bounds before comparison. Supports `YYYY`, `YYYY-MM`,
  `YYYY-Qx` (and lowercase `q`), and ISO dates.

### UX

- **"Did you mean?" suggestions on unknown filter values.** Closest
  RapidFuzz match (WRatio ≥ 70) is offered in the error message:
  `Unknown value 'major' for filter 'sector'. Did you mean 'major_banks'?`
  Permissive dimensions (fund_name, data_item) still pass unknowns
  through unchanged.

### Documentation honesty fix

- Corrected `period_coverage` metadata on three snapshot datasets
  (`ADI_KEY_STATS`, `ADI_RISK_WEIGHTED_ASSETS`, `SUPER_FUND_LEVEL`).
  The APRA "centralised publication" XLSX is a SNAPSHOT of the latest
  reporting quarter, not the multi-year history the filename suggests.
  The YAML descriptions and `period_coverage` strings now say so
  explicitly. The insurance long-format datasets remain true time
  series (Sep 2023 → Dec 2025 for current; back to 2002/2008 for
  historical).

### Tests

- 263 unit tests (up from 229 — 34 new covering the fixes)
- 16 live integration tests
- Zero-flake across 10 sequential runs

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
