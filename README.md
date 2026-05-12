# apra-mcp

[![tests](https://github.com/Bigred97/apra-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/Bigred97/apra-mcp/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/apra-mcp.svg)](https://pypi.org/project/apra-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/apra-mcp.svg)](https://pypi.org/project/apra-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Glama MCP server quality](https://glama.ai/mcp/servers/Bigred97/apra-mcp/badges/score.svg)](https://glama.ai/mcp/servers/Bigred97/apra-mcp)

**MCP server for Australian Prudential Regulation Authority statistics.** Plain-English access to per-bank capital ratios, fund-by-fund superannuation, and post-AASB17 life + general insurance — every prudentially-regulated entity in Australia, every quarter, from a single `uvx` command.

```text
"What's CBA's CET1 ratio?"
"Which super fund has the most members?"
"Top 10 banks by total capital, latest quarter"
"Gross written premium for the general insurance industry, post-AASB17"
"Largest life insurance product groups by claims"
```

Sister to [abs-mcp](https://github.com/Bigred97/abs-mcp), [rba-mcp](https://github.com/Bigred97/rba-mcp), [ato-mcp](https://github.com/Bigred97/ato-mcp), and [au-weather-mcp](https://github.com/Bigred97/au-weather-mcp).

---

## Install

```bash
uvx --upgrade apra-mcp
```

### Claude Desktop

```json
{
  "mcpServers": {
    "apra": { "command": "uvx", "args": ["--upgrade", "apra-mcp"] }
  }
}
```

### Claude Code

```bash
claude mcp add apra --command uvx --args -- --upgrade apra-mcp
```

---

## What it exposes

Six tools, all plain-English in, structured out:

| Tool                | Purpose                                                       |
|---------------------|---------------------------------------------------------------|
| `search_datasets`   | Fuzzy-search the curated catalog by keyword                   |
| `describe_dataset`  | List a dataset's filterable dimensions and returnable measures |
| `get_data`          | Query with `filters`, `measures`, period range, output format |
| `latest`            | Last observation per measure (shortcut)                       |
| `top_n`             | Rank rows by a measure, return top (or bottom) N              |
| `list_curated`      | Enumerate the curated dataset IDs                             |

Every response is the same shape — `dataset_id`, `dataset_name`, `query`, `period`, `unit`, `row_count`, `records`, `apra_url`, `download_url`, `framework` (insurance only), `attribution`, `stale` flag, `server_version` — across every curated dataset.

---

## Curated datasets (7 in v0.1)

| ID                              | What it is                                                                  | Period             |
|---------------------------------|-----------------------------------------------------------------------------|--------------------|
| `ADI_KEY_STATS`                 | Per-bank CET1 / Tier 1 / Total capital + RWA                                | latest quarter snapshot |
| `ADI_RISK_WEIGHTED_ASSETS`      | Per-bank RWA by risk type (credit / operational / market / IRRBB)            | latest quarter snapshot |
| `SUPER_FUND_LEVEL`              | Fund-by-fund members, benefits, demographics                                | latest quarter snapshot |
| `INSURANCE_GENERAL`             | Long-format general insurance (post-AASB17, ~24k rows × 10 quarters)         | Sep 2023 → latest  |
| `INSURANCE_GENERAL_HISTORICAL`  | General insurance archive (pre-AASB17)                                       | Dec 2002 → Jun 2023|
| `LIFE_INSURANCE`                | Long-format life insurance (post-AASB17, ~10k rows × 10 quarters)            | Sep 2023 → latest  |
| `LIFE_INSURANCE_HISTORICAL`     | Life insurance archive (pre-AASB17)                                          | Jun 2008 → Jun 2023|

> **Snapshot vs time-series.** ADI and Super datasets ship the most recent
> reporting quarter only (APRA refreshes the file each quarter). The four
> insurance datasets are long time series in a single file. Pass
> `start_period` / `end_period` as ISO dates (`2025-12-31`), bare years
> (`2024`), year-months (`2025-06`), or quarter shorthand (`2025-Q4`) — all
> normalised internally.

---

## Reliability — 3-tier URL resolution

APRA publishes XLSX at date-versioned paths that change every quarter. apra-mcp resolves them through three tiers:

1. **Live scrape** — fetch the canonical APRA landing page (with ETag conditional-GET so refreshes between releases cost zero bytes), regex-extract the .xlsx href matching the dataset's filename pattern, pick the latest-dated match. Cached 6h.
2. **Bundled seed manifest** — when the live scrape fails, fall back to `data/seed_urls.json` shipped in the wheel. CI refreshes the manifest daily. The response is flagged `stale: true` with an honest reason.
3. **YAML default** — last-resort URL from the curated YAML.

Net effect: a fresh `uvx apra-mcp` always gets the current quarter; a 3-month-old install still works because the seed manifest is refreshed and `--upgrade` pulls a new wheel.

---

## Framework break (insurance only)

APRA changed the reporting framework on 1 July 2023 (AASB 17 Insurance Contracts + capital framework revision). Pre- and post-break data are **not directly comparable** — APRA's own guidance is explicit. apra-mcp ships paired datasets:

- `INSURANCE_GENERAL` (post-AASB17) + `INSURANCE_GENERAL_HISTORICAL` (pre-AASB17)
- `LIFE_INSURANCE` (post-AASB17) + `LIFE_INSURANCE_HISTORICAL` (pre-AASB17)

Every response on an insurance dataset includes a `framework` block surfacing the break + a `historical_dataset` cross-reference, so agents see the warning before splicing series.

---

## Attribution

Data sourced from the Australian Prudential Regulation Authority. Licensed under [Creative Commons Attribution 3.0 Australia (CC BY 3.0 AU)](https://creativecommons.org/licenses/by/3.0/au/). apra-mcp is MIT-licensed; APRA's data carries the upstream CC-BY 3.0 AU licence, echoed in every response's `attribution` field.

---

## Sister packages

- [abs-mcp](https://github.com/Bigred97/abs-mcp) — ABS census + economic statistics
- [rba-mcp](https://github.com/Bigred97/rba-mcp) — RBA F-tables (cash rate, FX rates, mortgage rates)
- [ato-mcp](https://github.com/Bigred97/ato-mcp) — ATO tax statistics + ACNC charities register
- **apra-mcp** — this one. Banks, super, insurance.
- [au-weather-mcp](https://github.com/Bigred97/au-weather-mcp) — Australian weather

---

## Development

```bash
git clone https://github.com/Bigred97/apra-mcp.git
cd apra-mcp
uv venv
uv pip install -e ".[dev]"
pytest                  # unit tests
pytest -m live          # integration tests against apra.gov.au
```

Issues and contributions welcome: [github.com/Bigred97/apra-mcp/issues](https://github.com/Bigred97/apra-mcp/issues).
