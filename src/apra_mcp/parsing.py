"""XLSX parsers for APRA quarterly publications.

APRA ships XLSX in two broad shapes:

1. **Long-format Database files** (General Insurance, Life Insurance — both
   current and historical). A single `Database` or `Data` sheet with one row
   per (date, data_item, dimensions...). Header row 1, ~14 columns. Ideal for
   filtering — the parser only needs to read it.

2. **Multi-tab presentation files** (ADI Centralised Publication, Super
   Fund-Level). Each Table N sheet is its own long-format slice, but headers
   land on row 3 with a title in row 1 and sometimes a units row in row 2.

3. **Transposed pivot tables** (Super Performance KeyStats, ADI Property
   Exposures). Rows are entity categories, column headers are time periods.
   Call `melt_transposed()` after `read_xlsx()` to normalise to long format.

We parse all three via `read_xlsx(sheet, header_row)` — the curated YAML pins
the exact row. The header is normalised so embedded newlines + extra
whitespace don't break exact column-name matching downstream.
"""
from __future__ import annotations

import calendar
import re as _re
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd

_MONTH_ABBR: dict[str, int] = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_MON_YYYY_RE = _re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")


class ParseError(Exception):
    """Raised when an APRA resource can't be parsed."""


def read_xlsx(
    body: bytes,
    *,
    sheet: str,
    header_row: int,
    data_start_row: int | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Read one sheet from an XLSX as a DataFrame.

    Args:
        body: raw bytes of the .xlsx file.
        sheet: sheet name (must exist).
        header_row: 1-indexed row containing column headers (matches Excel's
            row numbering and the convention used in curated YAMLs).
        data_start_row: 1-indexed first row of data. Defaults to header_row + 1.
            Set this when there are blank/spacer rows between header and data.
        max_rows: cap on data rows returned. Useful when APRA tables have
            trailing "Notes" rows after the data block.

    Returns:
        DataFrame indexed 0..N-1, with column names normalised but otherwise
        identical to the source headers (renaming to plain-English aliases
        happens in shaping.py).
    """
    if not body:
        raise ParseError("empty XLSX body")
    if header_row < 1:
        raise ParseError(f"header_row must be 1-indexed (>=1), got {header_row}")
    if data_start_row is not None and data_start_row < header_row + 1:
        raise ParseError(
            f"data_start_row ({data_start_row}) must be > header_row ({header_row})"
        )

    pandas_header = header_row - 1

    try:
        df = pd.read_excel(
            BytesIO(body),
            sheet_name=sheet,
            header=pandas_header,
            engine="openpyxl",
        )
    except ValueError as e:
        raise ParseError(f"sheet {sheet!r} not found in workbook: {e}") from e
    except (KeyError, OSError, zipfile.BadZipFile) as e:
        raise ParseError(f"could not parse XLSX (corrupt or truncated body): {e}") from e

    if data_start_row is not None:
        skip_after_header = data_start_row - header_row - 1
        if skip_after_header > 0:
            df = df.iloc[skip_after_header:].reset_index(drop=True)

    if max_rows is not None and len(df) > max_rows:
        df = df.iloc[:max_rows].reset_index(drop=True)

    df.columns = [_normalize_header(c) for c in df.columns]
    return df


def _normalize_header(c):
    """Normalize an XLSX column header — collapse internal whitespace runs.

    APRA headers occasionally arrive with embedded newlines or duplicate
    spaces (`"Common Equity  Tier 1 capital"`, `"Total Tier 1\\ncapital"`).
    We collapse internal runs of whitespace (including newlines) to a single
    space and strip leading/trailing whitespace. Curated YAMLs spell columns
    in canonical (single-space) form.
    """
    if not isinstance(c, str):
        return c
    parts = c.replace("\r", " ").replace("\n", " ").split()
    return " ".join(parts)


def normalize_transposed_period(v: Any) -> str | None:
    """Convert a period label from a transposed APRA table to ISO YYYY-MM-DD.

    Handles:
    - pandas Timestamp / datetime → strftime('%Y-%m-%d')
    - 'Mar 2025' / 'Dec 2004' style month-year strings → last day of that month
    - Already-ISO 'YYYY-MM-DD' strings → pass through
    """
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        try:
            return v.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return None
    s = str(v).strip()
    if not s:
        return None
    m = _MON_YYYY_RE.match(s)
    if m:
        month_name = m.group(1).capitalize()
        year = int(m.group(2))
        month_num = _MONTH_ABBR.get(month_name)
        if month_num:
            last_day = calendar.monthrange(year, month_num)[1]
            return f"{year}-{month_num:02d}-{last_day:02d}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def _is_period_column_header(col: Any) -> bool:
    """Return True if a column name looks like a time-period label.

    Matches pandas Timestamps, datetimes, and 'Mon YYYY' strings.
    """
    if isinstance(col, (pd.Timestamp, datetime)):
        return True
    if not isinstance(col, str):
        return False
    return bool(_MON_YYYY_RE.match(col.strip()))


def melt_transposed(df: pd.DataFrame, entity_alias: str) -> pd.DataFrame:
    """Convert a pivot-table XLSX sheet to long-format DataFrame.

    APRA publishes some datasets in a transposed layout: rows are entity
    categories (fund types, property types) and column headers are time
    periods. This function melts them into (entity_alias, 'period', 'value')
    triples.

    The first column is treated as the entity dimension and renamed to
    `entity_alias`. All columns whose header passes `_is_period_column_header`
    are melted into period + value pairs. Other columns are discarded.

    Period headers are normalised to ISO YYYY-MM-DD via
    `normalize_transposed_period`.
    """
    if df.empty:
        return df

    entity_col = df.columns[0]
    period_cols = [c for c in df.columns[1:] if _is_period_column_header(c)]

    if not period_cols:
        return df

    df_work = df.copy()
    df_work = df_work.rename(columns={entity_col: entity_alias})
    df_work = df_work.dropna(subset=[entity_alias]).reset_index(drop=True)

    df_long = df_work.melt(
        id_vars=[entity_alias],
        value_vars=period_cols,
        var_name="period",
        value_name="value",
    )
    df_long["period"] = df_long["period"].apply(normalize_transposed_period)
    return df_long.reset_index(drop=True)


def drop_blank_rows(df: pd.DataFrame, key_columns: list[str]) -> pd.DataFrame:
    """Drop rows where every column in `key_columns` is NaN.

    Used to trim trailing footnote / blank rows that APRA sometimes leaves
    after the data block.
    """
    present = [c for c in key_columns if c in df.columns]
    if not present:
        return df
    keep_mask = ~df[present].isna().all(axis=1)
    return df.loc[keep_mask].reset_index(drop=True)
