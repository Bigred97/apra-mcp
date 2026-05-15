"""FastMCP server entrypoint for apra-mcp.

Six tools, mirroring abs-mcp / rba-mcp / ato-mcp so an agent that uses all
four gets a uniform shape:

  - search_datasets     — fuzzy search curated APRA datasets
  - describe_dataset    — show columns, filters, allowed values for one dataset
  - get_data            — query a dataset with filters / measures / period
  - latest              — shortcut: last N observations per measure
  - top_n               — rank rows by a measure, return top/bottom N
  - list_curated        — enumerate the curated dataset IDs

The MCP shape stays plain-English: users pass `{"institution": "cba"}` instead
of the full legal name. Curated YAMLs do the translation.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from collections import OrderedDict
from typing import Annotated, Any, Literal

import pandas as pd
from fastmcp import FastMCP
from pydantic import Field

from . import catalog, curated
from .client import APRAAPIError, APRAClient, get_stale_signal, reset_stale_signal
from .discovery import DiscoverySpec, resolve_for_dataset
from .models import (
    ColumnDetail,
    DataResponse,
    DatasetDetail,
    DatasetSummary,
    FrameworkInfo,
    Observation,
)
from .parsing import (
    drop_blank_rows,
    melt_transposed,
    normalize_transposed_period,
    read_xlsx,
)
from .shaping import build_response

# Curated IDs are uppercase letters + digits + underscore.
_DATASET_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Period strings: YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Qx, or APRA's compound forms.
_PERIOD_PATTERN = re.compile(r"^[0-9A-Za-z-]{4,10}$")
_VALID_FORMATS = {"records", "series", "csv"}

mcp = FastMCP("apra-mcp")

_client: APRAClient | None = None
_client_lock = asyncio.Lock()

# Parsed-DataFrame cache. The byte cache short-circuits the network, but
# pandas/openpyxl still re-parses bytes on every warm call — for the 7MB GI
# historical that's ~3s of pure CPU. We cache the post-parse DataFrame
# in-process so repeat queries land in ~50ms. Bounded LRU.
_DF_CACHE_MAX_ENTRIES = 8
_df_cache: OrderedDict[tuple, pd.DataFrame] = OrderedDict()
_df_cache_lock = asyncio.Lock()


def reset_df_cache_for_tests() -> None:
    """Drop the parsed-DataFrame cache."""
    _df_cache.clear()


async def _get_client() -> APRAClient:
    global _client
    async with _client_lock:
        if _client is None:
            _client = APRAClient()
        return _client


async def reset_client_for_tests() -> None:
    """Drop the cached client. Tests that span event loops must clear it."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


def _fuzzy_suggest(query: str, candidates: list[str], cutoff: int = 60) -> str | None:
    """Return the closest candidate string if it scores >= cutoff on RapidFuzz WRatio.

    Falls back to None if rapidfuzz is unavailable or no match clears the bar.
    The 60 cutoff is loose enough to catch realistic typos (e.g. 'ADI_KEYS' ->
    'ADI_KEY_STATS') but tight enough to suppress spurious matches.
    """
    if not query or not candidates:
        return None
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        return None
    match = process.extractOne(query, candidates, scorer=fuzz.WRatio, score_cutoff=cutoff)
    return match[0] if match else None


def _curated_id_hint(user_id: str) -> str:
    """Build a 'Did you mean ...? Valid IDs: ...' fragment for unknown dataset IDs."""
    ids = curated.list_ids()
    if not ids:
        return " Try list_curated() to see available IDs."
    suggestion_str = _fuzzy_suggest(user_id.upper(), ids, cutoff=60)
    suggestion = f" Did you mean {suggestion_str!r}?" if suggestion_str else ""
    return (
        f"{suggestion} Valid IDs: {', '.join(ids[:10])}"
        + ("..." if len(ids) > 10 else "")
        + " Try list_curated() to enumerate all, or search_datasets('<topic>')."
    )


def _normalize_dataset_id(dataset_id: Any) -> str:
    if not isinstance(dataset_id, str):
        raise ValueError(
            f"dataset_id must be a string, got {type(dataset_id).__name__}. "
            "Try search_datasets('banks') or list_curated() to discover IDs. "
            "Example: dataset_id='ADI_KEY_STATS'."
        )
    norm = dataset_id.strip().upper()
    if not norm:
        raise ValueError(
            "dataset_id is empty. Try list_curated() to see available IDs, "
            "or search_datasets('banks'|'super'|'insurance'). "
            "Example: dataset_id='ADI_KEY_STATS'."
        )
    if not _DATASET_ID_PATTERN.match(norm):
        raise ValueError(
            f"dataset_id {dataset_id!r} contains invalid characters — "
            "apra-mcp IDs use uppercase letters, digits, and underscores "
            "(e.g. 'ADI_KEY_STATS', 'LIFE_INSURANCE'). "
            "Try list_curated() to see the full set."
        )
    return norm


def _validate_filters(filters: Any) -> dict[str, Any]:
    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ValueError(
            f"filters must be a dict, got {type(filters).__name__}. "
            "Example: {'institution': 'cba', 'sector': 'major_banks'}."
        )
    return filters


def _validate_period(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    # LLM clients routinely send JSON ints (e.g. {"start_period": 2024}). Coerce
    # 4-digit ints in a realistic year range to the canonical "YYYY" string at the
    # boundary so we don't surface a confusing type error downstream.
    if isinstance(value, bool):
        # bool is a subclass of int; reject it explicitly before the int branch.
        raise ValueError(
            f"{field_name} must be a string or int year, got bool. "
            f"Try {field_name}='2024' (year), '2024-Q4' (quarter), '2024-12-31' (date), "
            "or 2024 (int year)."
        )
    if isinstance(value, int):
        if 1900 <= value <= 2100:
            value = str(value)
        else:
            raise ValueError(
                f"{field_name} integer {value} out of range. "
                f"For year-only periods pass a 4-digit year like 2024, or use string "
                f"forms 'YYYY' (e.g. '2024'), 'YYYY-Qx' (e.g. '2024-Q4'), or "
                f"'YYYY-MM-DD' (e.g. '2024-12-31'). "
                f"Try {field_name}='2024'."
            )
    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must be a string or int year, got {type(value).__name__}. "
            f"Try {field_name}='2024' (year), '2024-Q4' (quarter), '2024-12-31' (date), "
            "or 2024 (int year)."
        )
    s = value.strip()
    if not s:
        return None
    if not _PERIOD_PATTERN.match(s):
        guess = s[:4] if s[:4].isdigit() else "2024"
        raise ValueError(
            f"{field_name} {value!r} has invalid format. "
            "Period formats: 'YYYY' (e.g. '2024'), 'YYYY-Qx' (e.g. '2024-Q4'), or "
            "'YYYY-MM-DD' (e.g. '2024-12-31'). "
            f"Did you mean {guess!r}? "
            f"Example: get_data('ADI_KEY_STATS', start_period='2024-Q1', end_period='2024-Q4')."
        )
    return s


def _validate_measures(measures: Any) -> str | list[str] | None:
    if measures is None:
        return None
    if isinstance(measures, str):
        s = measures.strip()
        if not s:
            raise ValueError(
                "measures is empty. Pass a measure key like 'cet1_ratio', "
                "or omit `measures` to return all curated measures."
            )
        return s
    if isinstance(measures, list):
        if not measures:
            raise ValueError(
                "measures is an empty list. Pass at least one measure, "
                "or omit `measures` to return all."
            )
        out: list[str] = []
        for m in measures:
            if not isinstance(m, str):
                raise ValueError(
                    f"measures list entries must be strings, got {type(m).__name__} "
                    f"({m!r}). Pass measure keys like 'cet1_ratio' or 'total_capital'. "
                    "Try describe_dataset('<id>') to list valid measure keys."
                )
            s = m.strip()
            if not s:
                raise ValueError(
                    "measures list contains an empty string. "
                    "Pass measure keys like ['cet1_ratio', 'tier1_ratio'], or omit "
                    "`measures` to return all curated measures. "
                    "Try describe_dataset('<id>') to list valid measure keys."
                )
            out.append(s)
        return out
    raise ValueError(
        f"measures must be a string or list of strings, got {type(measures).__name__}. "
        "Examples: measures='cet1_ratio', or measures=['cet1_ratio', 'tier1_ratio']. "
        "Try describe_dataset('<id>') to list valid measure keys."
    )


async def _resolve_download_url(
    cd: curated.CuratedDataset, client: APRAClient
) -> tuple[str, bool, str | None]:
    """Return (url, stale, stale_reason)."""
    spec: DiscoverySpec | None = None
    if cd.discovery is not None:
        spec = DiscoverySpec(
            landing_url=cd.discovery.landing_url,
            filename_pattern=cd.discovery.filename_pattern,
            prefer_database=cd.discovery.prefer_database,
            exclude_patterns=cd.discovery.exclude_patterns,
        )
    result = await resolve_for_dataset(
        client, cd.id, spec, yaml_default=cd.download_url
    )
    return result.url, result.stale, result.reason


async def _fetch_and_parse(
    cd: curated.CuratedDataset,
) -> tuple[pd.DataFrame, str, bool, str | None]:
    """Resolve URL, fetch bytes, parse to DataFrame. Returns (df, url_used, stale, stale_reason)."""
    client = await _get_client()
    url, stale, stale_reason = await _resolve_download_url(cd, client)
    try:
        body = await client.fetch_resource(url, kind="data")
    except APRAAPIError as e:
        raise ValueError(
            f"Could not fetch dataset {cd.id!r} from apra.gov.au ({e}). "
            f"The upstream URL was {url!r}. Try the call again — apra.gov.au is "
            "intermittent and the client retries with cached fallback on next "
            "warm call. If the failure persists, check the landing page "
            f"({cd.source_url}) is reachable, or run list_curated() to confirm "
            "the dataset id is still supported."
        ) from e

    # Content-aware cache key (mirrors ato-mcp's design).
    head = body[:8192]
    tail = body[-2048:] if len(body) > 8192 else b""
    body_sig = hashlib.sha256(head + tail).digest()
    cache_key = (
        url, cd.format, cd.sheet, cd.header_row, cd.data_start_row,
        len(body), body_sig,
    )

    async with _df_cache_lock:
        cached = _df_cache.get(cache_key)
        if cached is not None:
            _df_cache.move_to_end(cache_key)
            return cached, url, stale, stale_reason

    if cd.sheet is None:
        raise ValueError(
            f"Dataset {cd.id!r} declares format='xlsx' but has no sheet name "
            "in its curated YAML. Fix data/curated/" + cd.id +
            ".yaml: add a top-level 'sheet: <sheet-name>' field (e.g. "
            "'sheet: \"Table 1\"'). The other curated datasets in this "
            "package all set a sheet — use them as a template."
        )
    df = read_xlsx(
        body,
        sheet=cd.sheet,
        header_row=cd.header_row,
        data_start_row=cd.data_start_row,
    )

    # Post-read reshape for non-standard layouts.
    if cd.layout == "transposed":
        df = melt_transposed(df, entity_alias=cd.transposed_entity_alias)
    elif cd.first_col_header_is_period and len(df.columns) > 0:
        # The entity column's header is the period date (e.g. Monthly ADI Table 1).
        # Extract the period, rename col 0 to the entity's source_column, and
        # inject a synthetic period column so the standard shaping pipeline works.
        period_val = normalize_transposed_period(df.columns[0])
        period_src = cd.period_column or "period"
        entity_src = next(
            (
                c.source_column
                for c in cd.columns.values()
                if c.role in ("dimension", "id") and c.source_column != period_src
            ),
            None,
        )
        if entity_src:
            df = df.rename(columns={df.columns[0]: entity_src})
        if period_src not in df.columns:
            df[period_src] = period_val

    # Trim trailing blank rows where every dimension is NaN.
    dim_source_cols = [c.source_column for c in cd.columns.values() if c.role == "dimension"]
    if dim_source_cols:
        df = drop_blank_rows(df, dim_source_cols)

    async with _df_cache_lock:
        _df_cache[cache_key] = df
        _df_cache.move_to_end(cache_key)
        while len(_df_cache) > _DF_CACHE_MAX_ENTRIES:
            _df_cache.popitem(last=False)

    return df, url, stale, stale_reason


@mcp.tool
async def search_datasets(
    query: Annotated[
        str,
        Field(
            description=(
                "Free-text search query. Matches against dataset IDs, names, "
                "descriptions, and curated search keywords. Case-insensitive."
            ),
            examples=[
                "banks capital",
                "superannuation funds",
                "life insurance",
                "general insurance gwp",
                "cet1 ratio",
                "risk-weighted assets",
            ],
        ),
    ],
    limit: Annotated[
        int,
        Field(
            description="Maximum number of results to return, ranked by relevance.",
            examples=[5, 10],
            ge=1,
            le=50,
        ),
    ] = 10,
) -> list[DatasetSummary]:
    """Fuzzy-search the curated APRA dataset catalog.

    All datasets ship hand-curated in v0.1: per-bank capital ratios, per-bank
    risk-weighted assets, fund-by-fund superannuation, and post-AASB17
    life + general insurance (with separate historical archives for the
    pre-Q3-2023 reporting framework).

    Examples:
        # Find the dataset for bank capital ratios
        results = await search_datasets("bank capital cet1")
        # → [{id: 'ADI_KEY_STATS', name: 'ADI Key Statistics — entity-level...', ...}]

        # Discover what's available on insurance
        results = await search_datasets("insurance premium")

    Returns:
        List of DatasetSummary (id, name, description, update_frequency,
        is_curated), ranked by relevance.
    """
    if not isinstance(query, str):
        raise ValueError(
            f"query must be a string, got {type(query).__name__}. "
            "Try 'banks', 'capital', 'super', 'insurance', or 'life'."
        )
    if not query.strip():
        raise ValueError(
            "query is required. Try 'banks', 'capital', 'super', 'insurance', "
            "'life', or any other APRA topic."
        )
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(
            f"limit must be a positive integer, got {limit!r} "
            f"({type(limit).__name__}). Try limit=10 (default) or limit=20."
        )
    if limit < 1:
        raise ValueError(
            f"limit must be >= 1, got {limit}. Try limit=10 (default) or omit "
            "the argument."
        )
    return catalog.search(query, limit=limit)


@mcp.tool
async def describe_dataset(
    dataset_id: Annotated[
        str,
        Field(
            description=(
                "Curated dataset ID. Use search_datasets() to discover or "
                "list_curated() to enumerate. Case-insensitive."
            ),
            examples=[
                "ADI_KEY_STATS",
                "ADI_RISK_WEIGHTED_ASSETS",
                "SUPER_FUND_LEVEL",
                "INSURANCE_GENERAL",
                "LIFE_INSURANCE",
                "INSURANCE_GENERAL_HISTORICAL",
            ],
        ),
    ],
) -> DatasetDetail:
    """Describe a dataset's filterable dimensions, returnable measures, units, source, and (for insurance) framework break info.

    Use this before calling get_data on a new dataset — it tells you the
    valid filter keys ('institution', 'sector', 'data_item'), the valid
    enumerated filter values ('cba', 'major_banks'), the measure aliases
    ('cet1_ratio', 'total_capital'), and the canonical source URL.

    For insurance datasets, the response includes a `framework` block
    documenting the Q3-2023 AASB-17 break.

    Returns:
        DatasetDetail with id, name, description, period_coverage, list of
        dimensions, list of measures, source_url, download_url, and optional
        framework info.
    """
    norm_id = _normalize_dataset_id(dataset_id)
    cd = curated.get(norm_id)
    if cd is None:
        raise ValueError(
            f"Dataset {dataset_id!r} is not a curated apra-mcp dataset."
            + _curated_id_hint(norm_id)
        )
    dims_out = [
        ColumnDetail(
            key=c.key,
            source_column=c.source_column,
            description=c.description,
            unit=c.unit,
            role=c.role,
        )
        for c in cd.columns.values()
        if c.role in ("dimension", "id")
    ]
    measures_out = [
        ColumnDetail(
            key=c.key,
            source_column=c.source_column,
            description=c.description,
            unit=c.unit,
            role=c.role,
        )
        for c in cd.columns.values()
        if c.role == "measure"
    ]
    framework: FrameworkInfo | None = None
    if cd.framework:
        framework = FrameworkInfo(
            basis=cd.framework.current_basis,
            break_date=cd.framework.break_date,
            break_reason=cd.framework.break_reason,
            historical_dataset=cd.framework.historical_dataset,
        )
    return DatasetDetail(
        id=cd.id,
        name=cd.name,
        description=cd.description,
        is_curated=True,
        update_frequency=cd.update_frequency,
        period_coverage=cd.period_coverage,
        dimensions=dims_out,
        measures=measures_out,
        source_url=cd.source_url,
        download_url=cd.download_url,
        framework=framework,
    )


async def _get_data_impl(
    dataset_id: str,
    filters: Any,
    measures: Any,
    start_period: Any,
    end_period: Any,
    fmt: Any,
    last_n: int | None = None,
) -> DataResponse:
    # Reset the graceful-degradation flag at the start of each tool call so
    # we only report staleness introduced by THIS call's fetches.
    reset_stale_signal()
    norm_id = _normalize_dataset_id(dataset_id)
    cd = curated.get(norm_id)
    if cd is None:
        raise ValueError(
            f"Dataset {dataset_id!r} is not a curated apra-mcp dataset."
            + _curated_id_hint(norm_id)
        )
    filters_d = _validate_filters(filters)
    measures_v = _validate_measures(measures)
    start_v = _validate_period(start_period, "start_period")
    end_v = _validate_period(end_period, "end_period")
    if fmt is None:
        fmt_norm = "records"
    elif isinstance(fmt, str):
        fmt_norm = fmt.lower()
    else:
        raise ValueError(
            f"format must be a string, got {type(fmt).__name__}. "
            f"Valid options: {sorted(_VALID_FORMATS)}. "
            "Try format='records' (default), 'series', or 'csv'."
        )
    if fmt_norm not in _VALID_FORMATS:
        valid_sorted = sorted(_VALID_FORMATS)
        suggestion = _fuzzy_suggest(fmt_norm, valid_sorted, cutoff=60)
        suggest_msg = f"Did you mean {suggestion!r}? " if suggestion else ""
        raise ValueError(
            f"Unknown format {fmt!r}. {suggest_msg}"
            f"Valid options: {valid_sorted}. "
            "Try format='records' (default), 'series', or 'csv'."
        )
    if start_v and end_v and start_v > end_v:
        raise ValueError(
            f"end_period ({end_v}) is before start_period ({start_v}). "
            f"Try swapping them: start_period={end_v!r}, end_period={start_v!r}. "
            "Period formats: 'YYYY' (e.g. '2024'), 'YYYY-Qx' (e.g. '2024-Q4'), or "
            "'YYYY-MM-DD' (e.g. '2024-12-31')."
        )

    user_query: dict[str, Any] = {}
    if filters_d:
        user_query["filters"] = dict(filters_d)
    if measures_v is not None:
        user_query["measures"] = measures_v
    if start_v:
        user_query["start_period"] = start_v
    if end_v:
        user_query["end_period"] = end_v

    df, url_used, stale, stale_reason = await _fetch_and_parse(cd)
    # Merge the byte-fetch stale signal (set when apra.gov.au is unreachable
    # and we served a stale-cache fallback) with the discovery-layer signal.
    # Either trigger marks the response stale; the discovery reason wins if
    # both fired, because it's typically the more actionable "we couldn't
    # find a fresh URL" message.
    fetch_stale, fetch_reason = get_stale_signal()
    if fetch_stale and not stale:
        stale = True
        stale_reason = fetch_reason
    return build_response(
        cd=cd,
        df=df,
        filters=filters_d,
        measures=measures_v,
        start_period=start_v,
        end_period=end_v,
        fmt=fmt_norm,
        user_query=user_query,
        last_n=last_n,
        download_url=url_used,
        stale=stale,
        stale_reason=stale_reason,
    )


@mcp.tool
async def get_data(
    dataset_id: Annotated[
        str,
        Field(
            description="Curated dataset ID. Use search_datasets() / list_curated().",
            examples=["ADI_KEY_STATS", "SUPER_FUND_LEVEL", "LIFE_INSURANCE"],
        ),
    ],
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Dimension filters. Keys are plain-English aliases from the dataset's "
                "describe_dataset response. Values are matched against the source data; "
                "pass a list to OR across values. Permissive dimensions (e.g. institution, "
                "fund_name, data_item) accept any string — including substring search "
                "via trailing '*' (e.g. {'institution': 'macquarie*'})."
            ),
            examples=[
                {"institution": "cba"},
                {"sector": "major_banks"},
                {"institution": ["cba", "westpac", "nab", "anz"]},
                {"fund_name": "australian_super"},
                {"data_item": "Gross written premium", "industry_segment": "total_industry"},
            ],
        ),
    ] = None,
    measures: Annotated[
        str | list[str] | None,
        Field(
            description=(
                "Which measure(s) to return. Plain-English keys from describe_dataset. "
                "Omit to return all measures. For long-format datasets (insurance), the "
                "single measure is 'value' and the semantic metric lives in the "
                "'data_item' dimension filter."
            ),
            examples=[
                "cet1_ratio",
                ["cet1_ratio", "tier1_ratio", "total_capital_ratio"],
                "total_member_accounts",
                "value",
            ],
        ),
    ] = None,
    start_period: Annotated[
        str | int | None,
        Field(
            description=(
                "Inclusive start period. Format: 'YYYY-MM-DD' (e.g. '2024-01-01'), "
                "'YYYY-Qx' (e.g. '2024-Q1'), or 'YYYY'. Matched against the dataset's "
                "period_column (quarter-end date). Bare int years like 2024 are "
                "coerced to '2024' automatically."
            ),
            examples=["2024-01-01", "2024-Q1", "2024", 2024],
        ),
    ] = None,
    end_period: Annotated[
        str | int | None,
        Field(
            description="Inclusive end period. Same format as start_period.",
            examples=["2025-12-31", "2025-Q4", 2025],
        ),
    ] = None,
    format: Annotated[
        Literal["records", "series", "csv"],
        Field(
            description=(
                "Response shape. 'records' (default): flat list of observations. "
                "'series': grouped by measure. 'csv': pandas CSV string in `csv` field."
            ),
            examples=["records", "series", "csv"],
        ),
    ] = "records",
) -> DataResponse:
    """Query a curated APRA dataset and return observations.

    Examples:
        # CBA's CET1 ratio over time
        resp = await get_data(
            "ADI_KEY_STATS",
            filters={"institution": "cba"},
            measures="cet1_ratio",
        )

        # Major banks' total capital, last 5 quarters
        resp = await get_data(
            "ADI_KEY_STATS",
            filters={"sector": "major_banks"},
            measures="total_capital",
            start_period="2024-01-01",
        )

        # Total industry gross written premium (general insurance)
        resp = await get_data(
            "INSURANCE_GENERAL",
            filters={"data_item": "Gross written premium",
                     "industry_segment": "total_industry"},
        )

        # AustralianSuper member account counts
        resp = await get_data(
            "SUPER_FUND_LEVEL",
            filters={"fund_name": "australian_super"},
            measures=["total_member_accounts", "total_members_benefits"],
        )

    Returns:
        DataResponse with records (or csv), unit, period bounds, row_count,
        source URL, the actual download_url used, optional framework info
        (insurance only), and CC-BY 3.0 AU attribution.
    """
    return await _get_data_impl(
        dataset_id, filters, measures, start_period, end_period, format
    )


@mcp.tool
async def latest(
    dataset_id: Annotated[
        str,
        Field(
            description="Curated dataset ID.",
            examples=["ADI_KEY_STATS", "SUPER_FUND_LEVEL", "LIFE_INSURANCE"],
        ),
    ],
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description="Same filter shape as get_data. Useful for narrowing to one entity.",
            examples=[
                {"institution": "cba"},
                {"fund_name": "australian_super"},
                {"data_item": "Gross written premium", "industry_segment": "total_industry"},
            ],
        ),
    ] = None,
    measures: Annotated[
        str | list[str] | None,
        Field(
            description="Same as get_data.",
            examples=["cet1_ratio", "total_member_accounts", "value"],
        ),
    ] = None,
) -> DataResponse:
    """Return the most recent observation per measure for a dataset.

    Trims to the single latest period per measure across the filtered slice
    — useful for "what's CBA's current CET1?" style questions without having
    to think about start_period.

    Examples:
        # Latest CBA capital ratios
        resp = await latest("ADI_KEY_STATS", filters={"institution": "cba"})
    """
    return await _get_data_impl(
        dataset_id, filters, measures, None, None, "records", last_n=1
    )


@mcp.tool
async def top_n(
    dataset_id: Annotated[
        str,
        Field(
            description="Curated dataset ID.",
            examples=["ADI_KEY_STATS", "SUPER_FUND_LEVEL", "ADI_RISK_WEIGHTED_ASSETS"],
        ),
    ],
    measure: Annotated[
        str,
        Field(
            description=(
                "Plain-English measure key to rank by. Use describe_dataset() "
                "to see available measures."
            ),
            examples=["total_capital", "cet1_ratio", "total_member_accounts", "value"],
        ),
    ],
    n: Annotated[
        int,
        Field(
            description="How many top (or bottom) rows to return.",
            ge=1,
            le=500,
            examples=[5, 10, 20, 50],
        ),
    ] = 10,
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Optional dimension filters, same shape as get_data. "
                "Typically you'll pin a single period to make rank meaningful "
                "(e.g. {'period': '2025-12-31'} for the latest quarter)."
            ),
            examples=[
                {"period": "2025-12-31"},
                {"sector": "major_banks"},
                {"fund_type": "industry"},
            ],
        ),
    ] = None,
    direction: Annotated[
        Literal["top", "bottom"],
        Field(
            description=(
                "'top' returns the N rows with the LARGEST measure values "
                "(biggest bank, highest capital, largest fund). 'bottom' "
                "returns the SMALLEST."
            ),
            examples=["top", "bottom"],
        ),
    ] = "top",
) -> DataResponse:
    """Return the N rows with the largest (or smallest) value of a measure.

    The single most common agent workflow: "show me the top 10 X by Y". top_n
    does the rank server-side and returns only the requested rows.

    Examples:
        # Biggest 10 banks by total capital, latest quarter
        top_n("ADI_KEY_STATS", "total_capital", n=10,
              filters={"period": "2025-12-31"})

        # Most members per super fund (latest)
        top_n("SUPER_FUND_LEVEL", "total_member_accounts", n=10,
              filters={"period": "2025-12-31"})

        # 5 lowest CET1 ratios in the latest quarter
        top_n("ADI_KEY_STATS", "cet1_ratio", n=5, direction="bottom",
              filters={"period": "2025-12-31"})

    Returns:
        DataResponse with at most `n` records, sorted by `measure` in the
        requested direction. Other fields match get_data.
    """
    if not isinstance(measure, str) or not measure.strip():
        raise ValueError(
            "measure is required and must be a non-empty string. "
            "Use describe_dataset() to see available measure keys."
        )
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(
            f"n must be a positive integer, got {n!r} ({type(n).__name__}). "
            "Try n=10 (default) for the top-10 leaderboard."
        )
    if n < 1:
        raise ValueError(
            f"n must be >= 1, got {n}. Try n=10 (default) for the standard "
            "top-10 leaderboard."
        )
    if direction not in ("top", "bottom"):
        raise ValueError(
            f"direction must be 'top' or 'bottom', got {direction!r}. "
            "'top' returns largest values (default), 'bottom' returns smallest."
        )

    full = await _get_data_impl(
        dataset_id, filters, measure, None, None, "records", last_n=None,
    )
    valid = [r for r in full.records if isinstance(r, Observation) and r.value is not None]
    valid.sort(key=lambda r: r.value if r.value is not None else 0.0, reverse=(direction == "top"))
    top = valid[:n]
    return full.model_copy(update={"records": top, "row_count": len(top)})


@mcp.tool
def list_curated() -> list[str]:
    """List every curated dataset ID in this version of apra-mcp.

    Returns:
        Sorted list of dataset IDs.
    """
    return curated.list_ids()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
