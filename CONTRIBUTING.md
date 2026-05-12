# Contributing

Pull requests welcome. The goal: keep apra-mcp's surface uniform with its
sister packages (abs-mcp, rba-mcp, ato-mcp) so an agent that uses all four
gets a consistent shape.

## Setup

```bash
git clone https://github.com/Bigred97/apra-mcp.git
cd apra-mcp
uv venv
uv pip install -e ".[dev]"
pytest                  # 229 unit tests, ~12s
pytest -m live          # 16 live integration tests against apra.gov.au, ~20s
```

## Adding a curated dataset

Drop one YAML file into `src/apra_mcp/data/curated/`. The schema:

```yaml
id: NEW_DATASET                    # SCREAMING_SNAKE_CASE
name: Human-readable title
description: |
  Paragraph describing the dataset, including period coverage and any
  caveats. Surfaces in describe_dataset() and search results.
period_coverage: "September 2023 → latest quarter"
update_frequency: quarterly
source_url: https://www.apra.gov.au/...      # the landing page
download_url: https://www.apra.gov.au/...    # initial XLSX URL (fallback)
format: xlsx
sheet: Database
header_row: 1
layout: wide
cache_kind: data
period_column: Reporting Period              # the source-column name
search_keywords:
  - keyword
  - other keyword
discovery:                                   # required for live URL resolution
  landing_url: https://www.apra.gov.au/...
  filename_pattern: '(?i)pattern\s+to\s+match'
  prefer_database: true                      # optional
  exclude_patterns:                          # optional
    - '(?i)historical'
    - '(?i)specifications'
framework:                                   # optional, insurance-only
  current_basis: post-AASB17
  break_date: "2023-09-30"
  break_reason: ...
  historical_dataset: PAIRED_HISTORICAL_KEY
columns:
  alias:
    source_column: "Exact source header"
    description: User-facing column documentation.
    role: dimension                          # dimension | measure | id
    dtype: string                            # int | float | string | date
    permissive: true                         # optional, allows wildcard match
dimension_values:                            # optional, alias maps
  alias:
    user_alias: "Canonical Value In Source"
```

Then:
1. Add a fixture (head-only 80–200 row XLSX) to `tests/fixtures/`
2. Add the dataset's URL to `src/apra_mcp/data/seed_urls.json`
3. Add tests in the existing test files (test_curated.py confirms loading;
   test_customer_flows.py runs an end-to-end flow)
4. Run `pytest` 10 times for zero-flake confirmation

## Discovery filename_pattern

The discovery layer scrapes the landing page HTML and regex-matches the
decoded filename of every `<a href="...xlsx">`. Test with:

```python
from apra_mcp.discovery import resolve_via_scrape, DiscoverySpec
from apra_mcp.client import APRAClient
spec = DiscoverySpec(landing_url="https://...", filename_pattern=r"...")
async with APRAClient() as c:
    url = await resolve_via_scrape(c, spec)
```

When multiple files match, the one with the latest-dated filename wins.
Use `exclude_patterns` to skip historical or specifications variants.

## Style

- Mirror the patterns in existing files. Consistency across the four MCPs
  matters more than micro-optimisations.
- Every MCP tool parameter must use `Annotated[Type, Field(description=...,
  examples=[...])]`. This is what gives the package its Glama
  tool-definition-quality score.
- No new dependencies beyond `fastmcp`, `httpx`, `pydantic`, `rapidfuzz`,
  `pandas`, `openpyxl`, `aiosqlite`, `PyYAML`.
- No defensive code for impossible scenarios — trust internal types.
- Default to no comments. Add one only when the *why* is non-obvious.

## Reporting bugs

Open an issue: https://github.com/Bigred97/apra-mcp/issues

Especially helpful:

- "APRA changed the shape of dataset X" — paste the error message from
  `_apply_aliases`; the schema fingerprint guard prints the first 6
  columns it actually saw.
- "Live scrape returned the wrong file" — paste the resolved URL and the
  filename_pattern it used.
