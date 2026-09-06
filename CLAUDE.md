# apra-mcp

Sister MCP in the Australian Public Data stack. See `../CLAUDE.md` for
portfolio-wide conventions (tool surface, trust contract, quality bar, test
taxonomy, anti-patterns, release process — read it even though this session
may not auto-load it if started inside this repo). Use the `sister-release`
skill for releases.

## Source

| | |
|--|--|
| Source agency | Australian Prudential Regulation Authority (APRA) |
| Source URL | https://www.apra.gov.au/quarterly-statistics |
| Data format | XLSX (Excel) — quarterly statistical reports + data.gov.au mirror |
| Licence | CC-BY 3.0 Australia |
| Licence URL | https://creativecommons.org/licenses/by/3.0/au/ |
| Python module | `apra_mcp` |
| PyPI package | `apra-mcp` |
| GitHub | https://github.com/Bigred97/apra-mcp |

## Curated datasets (13)

ADI_KEY_STATS · ADI_RISK_WEIGHTED_ASSETS · SUPER_FUND_LEVEL · MYSUPER_PRODUCTS · INSURANCE_GENERAL (+ historical) · LIFE_INSURANCE (+ historical) · INSURANCE_HEALTH · QUARTERLY_SUPER_PERFORMANCE · ADI_PROPERTY_EXPOSURES · MONTHLY_BANKING_STATS · ADI_PERFORMANCE

## Repo-specific module set

Extras beyond the standard sister module set (see parent): `parsing.py` —
XLSX reader (long-format pivots common for ADI / super fund data);
`discovery.py` — CKAN auto-discovery for data.gov.au mirrors; `catalog.py` —
search ranking.

## Repo-specific gotchas

- **Licence is CC-BY 3.0 AU, NOT 4.0** — APRA-specific. Don't use the 4.0 attribution string by accident.
- Quarterly cadence — most datasets land within 6-8 weeks of quarter end. `stale` threshold is 6 months.
- AASB17 transition in 2023 split insurance datasets into pre-AASB17 historical and current series.
