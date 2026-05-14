"""Translate a parsed DataFrame into the public DataResponse shape.

APRA's quarterly publications all use a wide long-format layout: one row per
(period, entity/data_item, dimensions...), one or more value columns. The
shaping layer:

1. Renames source columns to plain-English aliases per the curated YAML.
2. Coerces dtypes (dates → ISO string, numeric measures → float, IDs → string).
3. Filters by user-supplied dimension values.
4. Resolves measures to a list of measure-column keys.
5. Filters by period range when the dataset declares a period_column.
6. Emits an Observation per (row × measure) cell.

The result is uniform across every curated dataset — same Observation shape
every time, regardless of which APRA publication it came from.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .curated import (
    CuratedColumn,
    CuratedDataset,
    dimension_columns,
    id_columns,
    measure_columns,
    resolve_measure_keys,
    translate_filter_value,
)
from .models import DataResponse, FrameworkInfo, Observation


def _safe_value(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return str(v)


def _normalize_period_cell(v: Any) -> str | None:
    """Convert a 'Reporting Date' cell value to an ISO yyyy-mm-dd string.

    APRA period cells arrive as either pandas Timestamps (from XLSX date cells)
    or strings. Normalise to 'YYYY-MM-DD' for clean comparisons + display.
    """
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        try:
            return v.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return None
    s = str(v).strip()
    if not s:
        return None
    # 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DD'
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def _apply_aliases(df: pd.DataFrame, cd: CuratedDataset) -> pd.DataFrame:
    """Rename source columns to their curated aliases.

    All curated columns must exist in the parsed DataFrame; missing ones
    indicate APRA changed the file's shape and the YAML needs updating.
    """
    rename_map: dict[str, str] = {}
    for col in cd.columns.values():
        if col.source_column in df.columns:
            rename_map[col.source_column] = col.key
    missing = [
        c.source_column for c in cd.columns.values() if c.source_column not in df.columns
    ]
    if missing:
        sample_cols = list(df.columns)[:6]
        raise ValueError(
            f"Dataset {cd.id!r} expected these columns but they were not in "
            f"the parsed table: {missing[:5]}{'...' if len(missing) > 5 else ''}. "
            f"Saw these column headers instead (first 6): {sample_cols}. "
            "The upstream file may have changed shape — flag at "
            "https://github.com/Bigred97/apra-mcp/issues."
        )
    out = df.rename(columns=rename_map)
    # Drop columns we don't ship (keeps response payloads tight).
    keep = [c.key for c in cd.columns.values() if c.key in out.columns]
    return out[keep].copy()


def _coerce_dtypes(df: pd.DataFrame, cd: CuratedDataset) -> pd.DataFrame:
    for col in cd.columns.values():
        if col.dtype and col.key in df.columns:
            try:
                if col.dtype in ("int", "integer"):
                    df[col.key] = pd.to_numeric(df[col.key], errors="coerce").astype("Int64")
                elif col.dtype in ("float", "number"):
                    df[col.key] = pd.to_numeric(df[col.key], errors="coerce")
                elif col.dtype in ("string", "str"):
                    df[col.key] = _to_clean_string(df[col.key])
                elif col.dtype in ("date", "period"):
                    df[col.key] = df[col.key].map(_normalize_period_cell)
            except (ValueError, TypeError):
                pass
    return df


def _to_clean_string(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        rounded = series.dropna()
        if not rounded.empty and (rounded.astype("float64") % 1 == 0).all():
            return series.astype("Int64").astype("string")
    return series.astype("string").str.strip()


def _apply_filters(
    df: pd.DataFrame, cd: CuratedDataset, filters: dict[str, Any]
) -> pd.DataFrame:
    """Filter rows by user-supplied dimension values.

    Permissive dimensions (e.g. fund_name, abn) skip the alias check and
    accept any value the user supplies, including substring matches via
    a trailing '*' wildcard or '~contains' shorthand.
    """
    if not filters:
        return df

    valid_dim_keys = {c.key for c in cd.columns.values() if c.role in ("dimension", "id")}
    out = df
    for user_key, user_val in filters.items():
        if user_key not in valid_dim_keys:
            valid = sorted(valid_dim_keys)
            raise ValueError(
                f"Unknown filter {user_key!r} for dataset {cd.id!r}. "
                f"Try one of: {', '.join(valid[:15])}"
            )
        col_def = cd.columns.get(user_key)
        permissive_col = bool(col_def and col_def.permissive)

        if isinstance(user_val, list):
            if not user_val:
                raise ValueError(
                    f"Filter {user_key!r} has an empty list. "
                    "Pass at least one value, or omit the filter."
                )
            resolved = [translate_filter_value(cd, user_key, str(v).strip()) for v in user_val]
            mask = out[user_key].astype("string").isin(resolved)
        else:
            v_str = str(user_val).strip()
            # Wildcard substring match: 'cba*' or '*cba*' or 'cba~'
            if permissive_col and (v_str.endswith("*") or v_str.startswith("*") or "~" in v_str):
                needle = v_str.replace("*", "").replace("~", "").strip()
                if not needle:
                    raise ValueError(
                        f"Filter {user_key!r}: wildcard value reduced to empty "
                        "after stripping '*' / '~'. Pass a substring to match, "
                        "e.g. {'institution': 'macquarie*'} or "
                        "{'institution': 'commonwealth~'}."
                    )
                mask = out[user_key].astype("string").str.contains(
                    needle, case=False, na=False, regex=False,
                )
            else:
                resolved = translate_filter_value(cd, user_key, v_str)
                mask = out[user_key].astype("string") == str(resolved)
        out = out.loc[mask]
    return out.reset_index(drop=True)


def _apply_period_range(
    df: pd.DataFrame, cd: CuratedDataset,
    start_period: str | None, end_period: str | None,
) -> pd.DataFrame:
    """Filter rows by start_period / end_period against cd.period_column.

    User-supplied periods may be ISO dates ("2025-12-31"), bare years
    ("2025"), year-month strings ("2025-12"), or quarter shorthand
    ("2025-Q4"). All of these get normalised to ISO YYYY-MM-DD bounds
    before comparison against the source's quarter-end date strings.
    """
    if not cd.period_column or not (start_period or end_period):
        return df
    period_alias: str | None = None
    for c in cd.columns.values():
        if c.source_column == cd.period_column:
            period_alias = c.key
            break
    if period_alias is None or period_alias not in df.columns:
        return df
    series = df[period_alias].astype("string")
    if start_period:
        norm = _expand_period_input(start_period, bound="start")
        df = df.loc[series >= norm]
        series = df[period_alias].astype("string")
    if end_period:
        norm = _expand_period_input(end_period, bound="end")
        df = df.loc[series <= norm]
    return df.reset_index(drop=True)


def _expand_period_input(value: str, *, bound: str) -> str:
    """Expand a user-supplied period string to an ISO YYYY-MM-DD bound.

    Accepted forms:
      - "YYYY-MM-DD" → passthrough
      - "YYYY"       → start=YYYY-01-01, end=YYYY-12-31
      - "YYYY-MM"    → start=YYYY-MM-01, end=last-day-of-month
      - "YYYY-Qx"    → start=quarter-start, end=quarter-end
      - anything else → passthrough (best-effort lexical compare)
    """
    if not value:
        return value
    s = value.strip()
    # Already an ISO date
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    # Bare year
    if len(s) == 4 and s.isdigit():
        return f"{s}-01-01" if bound == "start" else f"{s}-12-31"
    # Quarter shorthand: YYYY-Qx (case-insensitive)
    if len(s) == 7 and s[4] == "-" and s[5] in ("Q", "q"):
        year = s[:4]
        try:
            q = int(s[6])
        except ValueError:
            return s
        if 1 <= q <= 4:
            if bound == "start":
                month = {1: "01", 2: "04", 3: "07", 4: "10"}[q]
                return f"{year}-{month}-01"
            else:
                # Quarter-end dates align with APRA's quarter-end reporting.
                end_md = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
                return f"{year}-{end_md}"
    # Year-month: YYYY-MM
    if len(s) == 7 and s[4] == "-" and s[5:].isdigit():
        try:
            m = int(s[5:7])
        except ValueError:
            return s
        if 1 <= m <= 12:
            if bound == "start":
                return f"{s}-01"
            last_day = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}[m]
            # Don't bother with leap-year February — APRA period_columns always
            # carry quarter-end dates (Mar/Jun/Sep/Dec), so this end-of-month
            # bound is generous; a Feb 29 in source data still compares ≤ "Feb 28".
            # If that ever becomes a real case, switch to calendar.monthrange.
            return f"{s}-{last_day:02d}"
    return s


def shape_wide(
    df: pd.DataFrame,
    cd: CuratedDataset,
    measures: list[str],
) -> list[Observation]:
    """One Observation per (row, measure) cell."""
    if df.empty:
        return []
    period_alias: str | None = None
    if cd.period_column:
        for c in cd.columns.values():
            if c.source_column == cd.period_column:
                period_alias = c.key
                break

    dims = [c.key for c in dimension_columns(cd) if c.key != period_alias]
    ids = [c.key for c in id_columns(cd)]
    dim_keys = dims + ids
    measure_by_key = {c.key: c for c in measure_columns(cd)}

    records: list[Observation] = []
    for _, row in df.iterrows():
        dim_vals: dict[str, Any] = {}
        for k in dim_keys:
            if k in df.columns:
                v = _safe_str(row[k])
                if v is not None:
                    dim_vals[k] = v
        period_val = (
            _safe_str(row[period_alias]) if period_alias and period_alias in df.columns else None
        )
        for mk in measures:
            mc = measure_by_key.get(mk)
            if mc is None:
                continue
            cell = row[mk] if mk in df.columns else None
            value = _safe_value(cell)
            if value is None:
                continue
            records.append(
                Observation(
                    period=period_val,
                    value=value,
                    measure=mk,
                    dimensions=dim_vals,
                    unit=mc.unit,
                )
            )
    return records


def records_to_csv(records: list[Observation]) -> str:
    if not records:
        return ""
    dim_keys: list[str] = []
    seen: set[str] = set()
    for r in records:
        for k in r.dimensions:
            if k not in seen:
                seen.add(k)
                dim_keys.append(k)
    cols = ["period", "measure", "value", "unit", *dim_keys]
    df = pd.DataFrame(
        [
            {
                "period": r.period,
                "measure": r.measure,
                "value": r.value,
                "unit": r.unit,
                **{k: r.dimensions.get(k) for k in dim_keys},
            }
            for r in records
        ],
        columns=cols,
    )
    return df.to_csv(index=False)


def records_to_series(records: list[Observation]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for r in records:
        key = r.measure or "value"
        g = groups.setdefault(key, {"measure": key, "unit": r.unit, "observations": []})
        g["observations"].append(
            {
                "period": r.period,
                "value": r.value,
                "dimensions": r.dimensions,
            }
        )
    return list(groups.values())


def build_response(
    *,
    cd: CuratedDataset,
    df: pd.DataFrame,
    filters: dict[str, Any],
    measures: str | list[str] | None,
    start_period: str | None,
    end_period: str | None,
    fmt: str,
    user_query: dict[str, Any],
    last_n: int | None = None,
    download_url: str | None = None,
    stale: bool = False,
    stale_reason: str | None = None,
) -> DataResponse:
    """Single entrypoint shaping uses to build a DataResponse."""
    if df is None or df.empty:
        framework_empty: FrameworkInfo | None = None
        if cd.framework:
            framework_empty = FrameworkInfo(
                basis=cd.framework.current_basis,
                break_date=cd.framework.break_date,
                break_reason=cd.framework.break_reason,
                historical_dataset=cd.framework.historical_dataset,
            )
        return DataResponse(
            dataset_id=cd.id,
            dataset_name=cd.name,
            query=user_query,
            row_count=0,
            records=[],
            csv="" if fmt == "csv" else None,
            retrieved_at=datetime.now(timezone.utc),
            apra_url=cd.source_url,
            download_url=download_url,
            framework=framework_empty,
            stale=stale,
            stale_reason=stale_reason,
        )
    renamed = _apply_aliases(df, cd)
    coerced = _coerce_dtypes(renamed, cd)
    filtered = _apply_filters(coerced, cd, filters)
    period_filtered = _apply_period_range(filtered, cd, start_period, end_period)

    measure_keys = resolve_measure_keys(cd, measures)

    records = shape_wide(period_filtered, cd, measure_keys)

    if last_n is not None and last_n > 0 and records:
        measure_keys = list({r.measure for r in records if r.measure})
        long_format = (
            len(measure_keys) == 1 and cd.period_column is not None
        )
        if long_format:
            # Long-format (insurance) datasets carry a single "value" measure;
            # the semantic metric lives in the data_item dimension. "Latest"
            # therefore means "all records at the most recent period(s)",
            # not "tail N per measure" (which would always return ≤1 record).
            periods_sorted = sorted({r.period for r in records if r.period})
            if periods_sorted:
                target_periods = set(periods_sorted[-last_n:])
                records = [r for r in records if r.period in target_periods]
        else:
            # Wide-format: tail-N per measure (sorted by period asc).
            per_measure: dict[str, list[Observation]] = {}
            for r in records:
                per_measure.setdefault(r.measure or "", []).append(r)
            records = []
            for k, obs in per_measure.items():
                obs.sort(key=lambda o: o.period or "")
                records.extend(obs[-last_n:])

    response_unit: str | None = None
    if records:
        units = {r.unit for r in records if r.unit}
        if len(units) == 1:
            response_unit = next(iter(units))

    period_start = start_period
    period_end = end_period
    if (period_start is None or period_end is None) and records:
        periods = sorted({r.period for r in records if r.period})
        if periods:
            period_start = period_start or periods[0]
            period_end = period_end or periods[-1]

    if fmt == "csv":
        out_records: list[Observation] | list[dict[str, Any]] = []
        csv_text: str | None = records_to_csv(records)
    elif fmt == "series":
        out_records = records_to_series(records)
        csv_text = None
    else:
        out_records = records
        csv_text = None

    framework: FrameworkInfo | None = None
    if cd.framework:
        framework = FrameworkInfo(
            basis=cd.framework.current_basis,
            break_date=cd.framework.break_date,
            break_reason=cd.framework.break_reason,
            historical_dataset=cd.framework.historical_dataset,
        )

    return DataResponse(
        dataset_id=cd.id,
        dataset_name=cd.name,
        query=user_query,
        period={"start": period_start, "end": period_end},
        unit=response_unit,
        row_count=len(records),
        records=out_records,
        csv=csv_text,
        retrieved_at=datetime.now(timezone.utc),
        apra_url=cd.source_url,
        download_url=download_url,
        framework=framework,
        stale=stale,
        stale_reason=stale_reason,
    )
