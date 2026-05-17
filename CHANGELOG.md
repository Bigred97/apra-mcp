# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.5] - 2026-05-17

### Fixed — Silent-failure mode on unknown permissive filter values

`get_data()` previously returned zero rows silently when a user passed an
institution / fund_name value that wasn't a known alias and didn't exist in
the source data (e.g. `filters={"institution": "mars-bank"}` on
`ADI_KEY_STATS`). The downstream LLM saw an empty response and couldn't
distinguish "no data for this entity" from "you typoed the name".

Now `_apply_filters` (shaping.py) validates permissive dim values that have a
`dimension_values` alias map. A value is accepted if it matches an alias, a
canonical alias value, or appears in the source data column. Anything else
raises `ValueError` with a "Did you mean / Valid aliases" hint that names
the documented aliases and points at the wildcard escape hatch
(`{"institution": "macquarie*"}`).

Applies to all curated APRA datasets that pair `permissive: true` columns
with an alias map: ADI_KEY_STATS, ADI_RISK_WEIGHTED_ASSETS,
MONTHLY_BANKING_STATS (institution), SUPER_FUND_LEVEL (fund_name),
ADI_PROPERTY_EXPOSURES (property_type), and the insurance datasets.

Permissive dims with NO alias map (e.g. `abn`, `fund_trustee`) keep their
free-form pass-through — there's nothing actionable to suggest. Wildcard
substring queries (`'cba*'`, `'commonwealth~'`) also bypass validation.

Validation runs against the *original* (unfiltered) DataFrame so an earlier
filter (e.g. period) doesn't false-positive a later "unknown value" hint.

### Added — Permissive-filter validation regression tests

Six new tests in `tests/test_server_validation.py` pin the rule end-to-end:

- Unknown institution raises with "Did you mean / Valid aliases" hint
- Known alias `cba` still resolves to 7+ Commonwealth Bank rows
- Full legal name "Commonwealth Bank of Australia" still accepted
- Wildcard `'nonexistent_xyz*'` still skips validation (returns 0 rows)
- Unknown value inside a list filter raises
- Same validation applies to SUPER_FUND_LEVEL / fund_name

Three pre-existing edge-data tests that relied on silent zero-row behaviour
were updated to use the wildcard escape hatch.

## [0.8.4] - 2026-05-16

### Changed — Sanitise ValueError hints (no internal URLs or MCP-tool names)

User-facing ValueError messages now scrub internal-detail leaks and stop
echoing internal MCP-tool names:

- The `_fetch_and_parse` upstream-error message now scrubs any
  `apra.gov.au/sites/default/files/...` URL from the wrapped cause
  (via the new `_scrub_internal_urls` helper) before bubbling up.
  Rationale: those URLs rotate quarterly and aren't actionable for
  callers.
- The `cd.sheet is None` configuration error no longer references
  the internal YAML file path or schema example.
- Three measure-validation errors and the unknown-filter error in
  `shaping.py` no longer suggest `Try describe_dataset('X')`. They now
  point at the dataset's content ("See the dataset's measures list",
  "See the valid-filters list for 'X'") — actionable without assuming
  the caller has access to a sibling MCP tool.

Tool docstrings and `Annotated[Field]` descriptions are *not* in scope:
those document tool behaviour (which is the right place to reference
sibling tools), and removing them would degrade tool discovery.

### Added — Sanitisation regression tests

Four new tests in `tests/test_server_validation.py` pin the rule. They
fail-loud if a future refactor reintroduces `describe_dataset()` or
internal-source URLs into user-facing ValueError messages.

## [0.8.3] - 2026-05-16

### Changed — Filter pushdown to the XLSX read layer (memory + speed)

`read_xlsx` now uses openpyxl in read-only streaming mode and accepts
optional `period_source_column` / `start_period` / `end_period` arguments
that apply a row-skip predicate during iteration. This caps working
memory at the rows we *keep*, rather than the whole sheet:

- `latest("MYSUPER_PRODUCTS")` warm-byte cache → 5.63s/23MB peak → 1.81s/18MB peak (3x speedup).
- `latest("INSURANCE_HEALTH")` warm-byte cache → 1.09s/2MB peak → 0.33s/2MB peak.
- `latest("ADI_PERFORMANCE")` warm-byte cache → 4.14s/13MB peak → 2.35s/13MB peak (transposed layout — pushdown skipped, but read-only iteration still wins).
- Cold-byte parse of the 7MB historical GI XLSX: `pd.read_excel` peak ~70MB → row-skip peak ~15MB when a quarter-wide period bound is supplied.

The in-process DataFrame cache key now incorporates the period bounds so
filtered and unfiltered calls don't share entries.

### Added — Memory smoke tests in `tests/test_resilience.py`

Four new tests pin the working-memory bound for `read_xlsx`. They
exercise the historical GI / LI fixtures (which are the closest the
unit suite has to the real 7MB files) and assert tracemalloc peak under
a 16MB ceiling for both full-parse and pushdown-filtered paths. Catches
any future regression to a load-everything XLSX implementation.

## [0.8.2] - 2026-05-16

### Fixed — JSON-string `filters` parameter (portfolio-wide)

The MCP protocol JSON-encodes dict parameters before they reach the
server. `_validate_filters` was doing `isinstance(filters, dict)` before
parsing the JSON string, so every call of the form `get_data(filters=
{"region": "nsw"})` was rejected with `"filters must be a dict mapping
dimension to value, got str"`. This broke every filtered query from a
real MCP client. Fix: decode JSON-string filters before the type check.
Same fix landed across abs/ato/asic/aihw/wgea/aemo in coordinated patch
releases (asic 0.6.1, abs 0.9.2, ato 0.8.2, aihw 0.4.2, wgea 0.5.1,
aemo 0.4.2). Test assertions broadened to match either rejection form.

## [0.8.1] - 2026-05-16

### Added

- `@pytest.mark.live` integration tests for `MYSUPER_PRODUCTS`,
  `INSURANCE_HEALTH`, and `ADI_PERFORMANCE` with range-check assertions
  on documented measures. Brings these datasets up to the `ADI_KEY_STATS`
  test-discipline standard.

## [0.8.0] - 2026-05-16

### Added — MYSUPER_PRODUCTS (per-product default super performance, 11-year history)

- **`MYSUPER_PRODUCTS` curated dataset.** Per-product annual financial
  performance for every MySuper offering (the regulated default super
  products covering every Australian without explicit choice).
  ~80-100 products per year × 11 reporting years (June 2014 → June 2025).
- 18 columns: product identifiers (name, type, lifecycle strategy),
  fund identifiers (name, ABN, trustee, public-offer status, type),
  plus 10 financial measures in AUD '000s (total assets, member
  benefit inflows/outflows, employer & member contributions, benefit
  payments, investment income, investment + admin + operating
  expenses, net earnings after tax, net operating performance).

### Cross-MCP join key verified — Fund ABN

- Customer running `latest('MYSUPER_PRODUCTS', filters={'fund_name': 'AustralianSuper'})`
  gets `fund_abn = 65714394898`. Same ABN appears in
  `latest('SUPER_FUND_LEVEL', filters={'fund_name': 'AustralianSuper'})`
  → `abn = 65714394898`. Customers can now join MySuper product
  performance with fund-level demographics for the same fund.

### Customer-workflow extension (super persona)

- "Compare default-product returns vs fees across competitors" —
  previously impossible from SUPER_FUND_LEVEL alone (fund-level only,
  inaugural Jun 2024). MYSUPER_PRODUCTS provides 11 yrs of per-product
  detail.
- Customers: super fund strategy/competitive intelligence, employer
  plan sponsors comparing default options, financial advisers
  benchmarking client outcomes, Chant West / SuperRatings, AFR.
- Search: "mysuper", "default super", "super product comparison",
  "super fees" all hit MYSUPER_PRODUCTS at #1.

### Tests

- 288 unit tests passing. 10× zero-flake.
- `test_flow_list_curated_is_complete` expects 13 datasets.
- `test_live_list_curated_count` updated to 13.

## [0.7.0] - 2026-05-16

### Added — INSURANCE_HEALTH (Private Health Insurance Performance Statistics)

- **`INSURANCE_HEALTH` curated dataset.** Quarterly private health
  insurance performance database — every reporting period from
  September 2023 onward (~25,000 rows per release), covering all ~30
  registered Australian PHI funds (Medibank, Bupa, HCF, NIB, HBF, etc.).
  Long-format with 5 dimension columns (period, data_item, subject,
  category, stock_or_flow) and a single Value column in AUD.
- Closes the audit gap on PHI sector coverage: health-insurance analysts
  can now answer "what's industry total premium revenue?", "what's the
  claims ratio?", "which fund grew the most this quarter?".
- Uses existing apra-mcp XLSX parser (Database sheet, header_row 1,
  long format). No new code; YAML-only addition.
- Framework: post-AASB17 basis (effective 1 July 2023); pre-2023 data
  is reported on a different basis and not curated.
- Description includes a glossary of PHI-specific acronyms (HIB =
  Health Insurance Business, HRB = Health-Related Business, HRIB =
  Health-Related Insurance Business) and a list of high-value
  data_item names so clients can query directly without browsing the
  XLSX.

### Customer-value validation (live APRA fetch, 2026-05-16)

- Health-sector analyst: `latest('INSURANCE_HEALTH', filters={'data_item':'HIB premium revenue'})`
  → $8.20B industry total (Q4 2025).
- Claims ratio: `HIB insurance claims` $7.02B / `HIB premium revenue`
  $8.20B = 86% — realistic for Australian PHI.
- Hospital vs general treatment split: hospital $6.09B / general $2.11B
  (74% / 26%) consistent with public PHI sector reporting.
- Time series query: 10 quarters Sept 2023 → Dec 2025, no gaps.
- Search routing: "private health insurance", "health insurer",
  "phi", "medibank", "health fund financials" all hit
  INSURANCE_HEALTH at #1.

### Tests

- 288 unit tests passing. 10× zero-flake gauntlet. 16 live tests pass.
- `test_flow_list_curated_is_complete` updated to expect 12 datasets.
- Live `test_live_list_curated_count` updated to assert `len(ids) == 12`.

## [0.6.0] - 2026-05-16

### Added — ADI_PERFORMANCE 21-year industry-aggregate history (Wave 2)

- **`ADI_PERFORMANCE` curated dataset.** Industry-aggregate quarterly P&L
  and balance-sheet metrics for all Authorised Deposit-taking Institutions
  going back to **September 2004 — 86 quarters / 21 years of history**.
  Each record is a (metric, quarter, value) triple in AUD millions on
  consolidated-group basis.
- 26 metrics tracked, with plain-English aliases for the awkward
  XLSX-footnoted names: `nii` → Net interest income, `net_profit_after_tax`
  → "Net profit (loss) after taxa" (the trailing 'a' is a footnote marker),
  plus aliases for housing_loans, term_loans, deposits, borrowings,
  cash_and_liquid_assets, operating_income, operating_expenses, etc.
- Uses APRA's existing **transposed-layout XLSX** (Tab 1a of the Quarterly
  ADI Performance Statistics publication). No new parser code — the
  `melt_transposed` path added in 0.4.0 handles it.
- Closes the audit gap on "snapshot-only for APRA" — where `ADI_KEY_STATS`
  serves per-entity capital for the latest quarter, `ADI_PERFORMANCE` now
  serves industry-aggregate trends back to 2004.

### Customer-value validation (live APRA fetch, 2026-05-16)

- Bank analyst: `get_data('ADI_PERFORMANCE', filters={'metric':'nii'})`
  returns **86 quarters** Sept 2004 → Dec 2025. NII grew from $7,684m
  (Q3 2004) → $25,955m (Q4 2025), ~5.9% CAGR.
- Macroeconomist: `latest('ADI_PERFORMANCE', filters={'metric':'net_profit_after_tax'})`
  → $11,188m (Q4 2025).
- Housing market analyst: `housing_loans` from 2020+ returns 24 quarters
  showing growth from interest-paid line.
- Search routing: "bank historical", "bank profitability", "sector trend",
  "net interest income historical" all surface ADI_PERFORMANCE at the top.

### Tests

- 288 unit tests now (was 288 with the test_customer_flows expected set
  updated from 10 → 11). 10× zero-flake gauntlet.
- Live integration count assertion updated to 11.

## [0.5.1] - 2026-05-16

### Fixed

- `test_live_list_curated_count` updated to expect 10 datasets (was 7).
- `_get_server_version()` now guards against `None` return from
  `importlib.metadata.version()` (can occur with stale dist-info files in
  editable installs); falls back to `"0.0.0+unknown"` rather than returning
  `None`. Mirrors the defensive pattern used across the sister stack.
- CLAUDE.md curated dataset list updated to all 10 APRA datasets.

## [0.5.0] - 2026-05-16

### Added

- **3 new curated APRA datasets** — expands from 7 to 10 datasets:
  - `QUARTERLY_SUPER_PERFORMANCE`: Aggregate total assets (AUD billions) for
    APRA-regulated superannuation entities by fund type (Corporate, Industry,
    Public sector, Retail, Small APRA funds), quarterly from December 2004 to
    latest. Source: Quarterly Superannuation Statistics — KeyStats sheet.
  - `ADI_PROPERTY_EXPOSURES`: Quarterly commercial property exposure limits and
    actual exposures by property type (Office, Retail, Industrial, Land
    development, etc.) for all ADIs, from March 2004 to latest. Includes
    impaired exposures and specific provisions. Source: Quarterly ADI
    Property Exposures Statistics — Tab 1a.
  - `MONTHLY_BANKING_STATS`: Monthly snapshot of seven selected balance-sheet
    asset categories for every individual ADI (cash, trading securities,
    investment securities, loans, total assets, securitised assets). Source:
    Monthly Authorised Deposit-taking Institution Statistics — Table 1.

- **`layout: transposed` parser** — new parsing path in `parsing.py` via
  `melt_transposed()` for APRA pivot-table sheets where rows are entity
  categories and column headers are time periods. Periods in 'Mon YYYY'
  format (e.g. 'Dec 2024') are normalised to ISO YYYY-MM-DD. Used by
  `QUARTERLY_SUPER_PERFORMANCE` and `ADI_PROPERTY_EXPOSURES`.

- **`first_col_header_is_period` flag** — new YAML option for snapshot
  publications (like Monthly ADI Statistics) where the entity column's header
  is the reporting date. Server extracts the period, renames col 0 to the
  entity's source_column, and injects a synthetic `period` column so the
  standard shaping pipeline works without modification. Used by
  `MONTHLY_BANKING_STATS`.

## [0.4.1] - 2026-05-15

### Fixed

- APRA `*_HISTORICAL` dataset discovery: updated XLSX URL regex to match
  APRA's restructured site paths. `INSURANCE_GENERAL_HISTORICAL` and
  `LIFE_INSURANCE_HISTORICAL` now resolve to live data instead of falling
  back to bundled seed.

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
