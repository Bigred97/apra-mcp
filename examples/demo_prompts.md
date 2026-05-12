# Demo prompts

Paste any of these into Claude (Desktop, Code, or web) with apra-mcp configured.

## Banking — capital ratios

> **"What's Commonwealth Bank's CET1 ratio at the latest quarter?"**
>
> Expected resolution:
> - `latest("ADI_KEY_STATS", filters={"institution": "cba"}, measures="cet1_ratio")`
> - Returns one record: ~12.3% for 2025-Q4.

> **"List the top 10 Australian banks by total capital, latest quarter."**
>
> - `top_n("ADI_KEY_STATS", "total_capital", n=10)`
> - Returns CBA, ANZ, Westpac, NAB, Macquarie, then mid-tier banks.

> **"How has Macquarie Bank's total Tier 1 capital changed over the last 5 years?"**
>
> - `get_data("ADI_KEY_STATS", filters={"institution": "macquarie"}, measures="tier1_capital", start_period="2020-01-01")`

> **"Which mutual ADIs have the highest CET1 ratios?"**
>
> - `top_n("ADI_KEY_STATS", "cet1_ratio", n=10, filters={"mutual": "y"})`

## Banking — risk-weighted assets

> **"Break down ANZ's risk-weighted assets by risk type, latest."**
>
> - `latest("ADI_RISK_WEIGHTED_ASSETS", filters={"institution": "anz"})`
> - Returns ~10 measures: credit risk, operational risk, market risk, IRRBB, etc.

> **"Which banks have the largest market-risk RWA?"**
>
> - `top_n("ADI_RISK_WEIGHTED_ASSETS", "market_risk", n=5)`

## Superannuation — fund level

> **"Which super funds have the most members?"**
>
> - `top_n("SUPER_FUND_LEVEL", "total_member_accounts", n=10)`
> - AustralianSuper (3.7M), Australian Retirement Trust (2.5M), REST (2.2M)...

> **"What's AustralianSuper's median member age?"**
>
> - `latest("SUPER_FUND_LEVEL", filters={"fund_name": "australian_super"}, measures="median_member_age")`

> **"List industry-type super funds with for-profit status."**
>
> - `get_data("SUPER_FUND_LEVEL", filters={"fund_type": "industry", "licensee_profit_status": "for_profit"})`

## Insurance — general

> **"What's the latest Additional Tier 1 capital across the general insurance industry?"**
>
> - `get_data("INSURANCE_GENERAL", filters={"data_item": "Additional Tier 1 capital", "industry_segment": "Total industry"})`

> **"Compare gross written premium across direct insurers, reinsurers, and lenders mortgage insurers (post-AASB17)."**
>
> - `get_data("INSURANCE_GENERAL", filters={"data_item": "Allocation of reinsurance premiums"})`

## Insurance — life

> **"Latest gross premiums by product group across all statutory funds."**
>
> - `get_data("LIFE_INSURANCE", filters={"data_item": "Actual gross premiums accrued", "reporting_structure": "Total statutory funds"})`

## Cross-MCP composition

Combine with sister MCPs:

> **"For 2024, give me the cash rate (RBA), the CPI (ABS), and the major banks' average CET1 ratio (APRA), and put them on one timeline."**
>
> Resolves to three MCPs in parallel:
> - rba-mcp: `get_data("CASH_RATE", start_period="2024-01-01")`
> - abs-mcp: `get_data("CPI", start_period="2024-01-01")`
> - apra-mcp: `get_data("ADI_KEY_STATS", filters={"sector": "major_banks"}, measures="cet1_ratio", start_period="2024-01-01")`
