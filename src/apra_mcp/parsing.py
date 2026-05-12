"""XLSX parsers for APRA quarterly publications.

APRA ships XLSX in two broad shapes:

1. **Long-format Database files** (General Insurance, Life Insurance — both
   current and historical). A single `Database` or `Data` sheet with one row
   per (date, data_item, dimensions...). Header row 1, ~14 columns. Ideal for
   filtering — the parser only needs to read it.

2. **Multi-tab presentation files** (ADI Centralised Publication, Super
   Fund-Level). Each Table N sheet is its own long-format slice, but headers
   land on row 3 with a title in row 1 and sometimes a units row in row 2.

We parse both via `read_xlsx(sheet, header_row)` — the curated YAML pins
the exact row. The header is normalised so embedded newlines + extra
whitespace don't break exact column-name matching downstream.
"""
from __future__ import annotations

import zipfile
from io import BytesIO

import pandas as pd


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
