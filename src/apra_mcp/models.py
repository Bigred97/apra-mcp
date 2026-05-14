"""Pydantic v2 response models for apra-mcp.

Mirrors the response shape used by abs-mcp / rba-mcp / ato-mcp so a downstream
agent that calls multiple Australian government MCPs gets a uniform envelope.

APRA-specific differences:
- attribution names APRA and CC-BY 3.0 AU per APRA's licence (same as the
  data.gov.au mirrored datasets used by ato-mcp).
- DataResponse.source defaults to "Australian Prudential Regulation Authority"
- DataResponse.apra_url points back at the APRA landing page
- DataResponse.download_url surfaces the actual XLSX URL used (so callers can
  verify provenance — the discovery layer may have resolved to a different URL
  than the YAML seed).
- DataResponse.framework — set on insurance datasets where the AASB-17
  Q3-2023 break affects comparability.
- DataResponse.stale + stale_reason — true when the response was served from
  the bundled seed manifest (live scrape failed).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


_APRA_ATTRIBUTION = (
    "Source: Australian Prudential Regulation Authority. "
    "Licensed under Creative Commons Attribution 3.0 Australia "
    "(https://creativecommons.org/licenses/by/3.0/au/)."
)


class DatasetSummary(BaseModel):
    """Search-result shape: one row per curated APRA dataset."""
    id: str
    name: str
    description: str | None = None
    update_frequency: str | None = None      # "quarterly" / "annual"
    is_curated: bool = False


class ColumnDetail(BaseModel):
    """One queryable column in a curated table."""
    key: str
    source_column: str
    description: str | None = None
    unit: str | None = None
    role: str = "measure"                    # "dimension" | "measure" | "id"


class FrameworkInfo(BaseModel):
    """Reporting-framework metadata.

    Set on insurance datasets where APRA's AASB-17 implementation (Q3 2023)
    introduced a break in comparability. Surfaces in DataResponse so agents
    don't unknowingly splice incomparable series.
    """
    basis: str                               # "post-AASB17" | "pre-AASB17" | "current"
    break_date: str | None = None            # ISO date if applicable
    break_reason: str | None = None
    historical_dataset: str | None = None    # cross-reference, e.g. LIFE_INSURANCE_HISTORICAL


class DatasetDetail(BaseModel):
    """describe_dataset shape."""
    id: str
    name: str
    description: str
    is_curated: bool
    update_frequency: str | None = None
    period_coverage: str | None = None       # e.g. "Sept 2023 to Dec 2025"
    dimensions: list[ColumnDetail] = Field(default_factory=list)
    measures: list[ColumnDetail] = Field(default_factory=list)
    source_url: str                          # APRA landing page
    download_url: str | None = None
    framework: FrameworkInfo | None = None


class Observation(BaseModel):
    """One row of returned data."""
    period: str | None = None                # ISO date (quarter-end) or YYYY-Qx
    value: float | None = None
    measure: str | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    unit: str | None = None


class DataResponse(BaseModel):
    """get_data / latest / top_n shape — uniform across curated datasets.

    `records` carries either:
      - list of `Observation` (default "records" format), or
      - list of dicts shaped {measure, unit, observations: [...]}.
    """
    dataset_id: str
    dataset_name: str
    query: dict[str, Any] = Field(default_factory=dict)
    period: dict[str, str | None] = Field(default_factory=lambda: {"start": None, "end": None})
    unit: str | None = None
    row_count: int = 0
    records: list[Any] = Field(default_factory=list)
    csv: str | None = None
    source: str = "Australian Prudential Regulation Authority"
    attribution: str = _APRA_ATTRIBUTION
    retrieved_at: datetime
    source_url: str = Field(
        description=(
            "Canonical click-through URL. Same value as apra_url; both populated "
            "for backward compat."
        )
    )
    apra_url: str = Field(
        description=(
            "Click-through URL for this dataset's source page. apra-mcp legacy "
            "name — prefer source_url (canonical) for new code. Both fields are "
            "populated identically."
        )
    )
    download_url: str | None = None          # actual XLSX URL used (post-discovery)
    framework: FrameworkInfo | None = None
    stale: bool = False
    stale_reason: str | None = None
    # Set when `latest()` / `top_n` truncated a large response to a limit. The
    # full pre-truncation row count goes here so agents can detect + surface
    # the cap. Mirrors abs-mcp / rba-mcp / ato-mcp 0.2.x trust contract.
    truncated_at: int | None = None
    server_version: str = Field(default_factory=lambda: _get_server_version())


def _get_server_version() -> str:
    try:
        from importlib.metadata import version
        return version("apra-mcp")
    except Exception:
        return "0.0.0+unknown"
