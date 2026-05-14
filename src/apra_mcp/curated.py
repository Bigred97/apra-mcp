"""Hand-curated metadata for the top APRA datasets.

Each YAML under `data/curated/` describes one queryable table:
- which APRA landing page hosts it
- how to scrape the landing page for the current XLSX URL
- how to parse it (sheet name, header row, layout)
- which columns are dimensions (filterable) vs measures (returned values)
- plain-English aliases for APRA's verbose column names
- which filter values are accepted, what they mean
- search keywords folded into the fuzzy search haystack
- (insurance only) framework info documenting the AASB-17 break
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from aus_identity import (
    is_valid_postcode,
    normalize_state,
    postcode_to_state,
    state_full_name,
)


# Dim names whose values are state/region references. When `translate_filter_value`
# encounters one, the user input is run through aus_identity first so "NSW",
# "nsw", "New South Wales", "AU-NSW", "Tassie", and 4-digit postcodes all
# resolve to APRA's canonical long-form ("New South Wales").
_STATE_LIKE_DIM_NAMES = frozenset({"state_territory", "state", "region"})


def _normalise_state_to_full_name(user_value: str) -> str | None:
    """Try to resolve a user value to APRA's canonical long-form state name.

    Returns the long-form ("New South Wales", "Victoria", …) on success, or
    `None` if the input isn't recognisable as a state reference (caller
    falls back to existing dim_values / permissive logic).
    """
    s = user_value.strip()
    if not s:
        return None
    # Postcode route first (digits only).
    if s.isdigit() and is_valid_postcode(s):
        try:
            code = postcode_to_state(s)
        except ValueError:
            return None
    else:
        try:
            code = normalize_state(s)
        except ValueError:
            return None
    try:
        return state_full_name(code)
    except ValueError:
        return None


Layout = Literal["wide", "transposed"]


@dataclass(frozen=True)
class CuratedColumn:
    """One column in the source table that's exposed to users."""
    key: str
    source_column: str
    description: str | None = None
    unit: str | None = None
    role: str = "measure"                    # "dimension" | "measure" | "id"
    dtype: str | None = None
    permissive: bool = False                 # if True, accept any user value (no enum check)


@dataclass(frozen=True)
class CuratedDimensionValues:
    """Allowed values for a dimension, plus their canonical labels.

    `None` means free-form (e.g. ABN, fund_name) — anything goes.
    """
    values: dict[str, str] | None = None
    permissive: bool = False                 # if True, unknown values pass through


@dataclass(frozen=True)
class CuratedFramework:
    """Optional framework block — set on insurance datasets.

    `current_basis` is what the dataset's data is reported in (e.g. "post-AASB17").
    `break_date` is the cut-over date (ISO).
    `historical_dataset` cross-references the paired historical curated key.
    """
    current_basis: str
    break_date: str | None = None
    break_reason: str | None = None
    historical_dataset: str | None = None


@dataclass(frozen=True)
class CuratedDiscovery:
    """How to scrape the landing page for the current XLSX URL."""
    landing_url: str
    filename_pattern: str
    prefer_database: bool = False
    exclude_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class CuratedDataset:
    """One curated dataset (a single queryable view)."""
    id: str
    name: str
    description: str
    source_url: str                          # APRA landing page
    download_url: str                        # fallback XLSX URL (last resort)
    format: Literal["xlsx", "csv"]
    sheet: str | None
    header_row: int
    data_start_row: int | None
    layout: Layout
    period_coverage: str | None
    update_frequency: str | None
    cache_kind: str                          # "data"
    columns: dict[str, CuratedColumn]
    dimension_values: dict[str, CuratedDimensionValues]
    search_keywords: tuple[str, ...] = ()
    metric_label_column: str | None = None
    unit_column: str | None = None
    discovery: CuratedDiscovery | None = None
    framework: CuratedFramework | None = None
    # Date columns: when the source has a 'Reporting Date' style column we
    # want to treat as the `period`, this names it. The shaping layer will
    # extract the column's value into Observation.period instead of dimensions.
    period_column: str | None = None


_REGISTRY: dict[str, CuratedDataset] | None = None


def _yaml_dir() -> Path:
    try:
        ref = resources.files("apra_mcp").joinpath("data/curated")
        if ref.is_dir():
            return Path(str(ref))
    except (ModuleNotFoundError, AttributeError):
        pass
    here = Path(__file__).resolve().parent / "data" / "curated"
    if here.is_dir():
        return here
    raise FileNotFoundError("Could not locate apra_mcp/data/curated/")


def _parse_column(key: str, raw: dict) -> CuratedColumn:
    if not isinstance(raw, dict):
        raise ValueError(
            f"Column {key!r} must be a YAML mapping, got {type(raw).__name__}. "
            f"Example: '{key}: {{source_column: \"<SourceColumnName>\", "
            "role: \"measure\", unit: \"AUD millions\"}}'"
        )
    if "source_column" not in raw:
        raise ValueError(
            f"Column {key!r} missing required field 'source_column'. "
            f"Add it: '{key}: {{source_column: \"<exact header in the XLSX>\", "
            "role: \"measure\"|\"dimension\"|\"id\"}}'"
        )
    return CuratedColumn(
        key=key,
        source_column=str(raw["source_column"]),
        description=raw.get("description"),
        unit=raw.get("unit"),
        role=str(raw.get("role", "measure")),
        dtype=raw.get("dtype"),
        permissive=bool(raw.get("permissive", False)),
    )


def _parse_dimension_values(raw) -> CuratedDimensionValues:
    if raw is None:
        return CuratedDimensionValues(values=None)
    if isinstance(raw, dict):
        # Support both shapes:
        # 1) flat map: {alias: canonical, ...}
        # 2) explicit: {values: {alias: canonical}, permissive: bool}
        if "values" in raw and isinstance(raw["values"], dict):
            vals = {str(k): str(v) for k, v in raw["values"].items()}
            return CuratedDimensionValues(
                values=vals, permissive=bool(raw.get("permissive", False))
            )
        return CuratedDimensionValues(
            values={str(k): str(v) for k, v in raw.items()}, permissive=False,
        )
    raise ValueError(
        f"dimension_values must be a YAML mapping, got {type(raw).__name__}. "
        "Example: 'dimension_values: {sector: {cba: \"Commonwealth Bank of Australia\", "
        "nab: \"National Australia Bank\"}}'"
    )


def _parse_framework(raw) -> CuratedFramework | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"framework must be a YAML mapping, got {type(raw).__name__}. "
            "Example: 'framework: {current_basis: \"post-AASB17\", "
            "break_date: \"2023-09-30\", historical_dataset: "
            "\"LIFE_INSURANCE_HISTORICAL\"}'"
        )
    if "current_basis" not in raw:
        raise ValueError(
            "framework block missing required field 'current_basis'. "
            "Valid values: 'post-AASB17' or 'pre-AASB17'. "
            "Example: 'framework: {current_basis: \"post-AASB17\", "
            "break_date: \"2023-09-30\"}'"
        )
    return CuratedFramework(
        current_basis=str(raw["current_basis"]),
        break_date=raw.get("break_date"),
        break_reason=raw.get("break_reason"),
        historical_dataset=raw.get("historical_dataset"),
    )


def _parse_discovery(raw) -> CuratedDiscovery | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"discovery must be a YAML mapping, got {type(raw).__name__}. "
            "Example: 'discovery: {landing_url: \"https://www.apra.gov.au/...\", "
            "filename_pattern: \"<regex>\"}'"
        )
    if "landing_url" not in raw:
        raise ValueError(
            "discovery block missing required field 'landing_url'. "
            "Add it: 'discovery: {landing_url: "
            "\"https://www.apra.gov.au/<page-slug>\", filename_pattern: "
            "\"<regex>\"}'. This is the APRA landing page the scraper reads."
        )
    if "filename_pattern" not in raw:
        raise ValueError(
            "discovery block missing required field 'filename_pattern'. "
            "Add it: 'discovery: {landing_url: ..., filename_pattern: "
            "\"<regex matching the XLSX filename, e.g. "
            "'.*Quarterly.*\\\\.xlsx'>\"}'."
        )
    excludes = raw.get("exclude_patterns") or ()
    if isinstance(excludes, str):
        excludes = (excludes,)
    else:
        excludes = tuple(str(x) for x in excludes)
    return CuratedDiscovery(
        landing_url=str(raw["landing_url"]),
        filename_pattern=str(raw["filename_pattern"]),
        prefer_database=bool(raw.get("prefer_database", False)),
        exclude_patterns=excludes,
    )


def _load_one(path: Path) -> CuratedDataset:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path.name}: top-level must be a YAML mapping (key:value pairs), "
            f"got {type(raw).__name__}. Each curated YAML must declare at "
            "minimum 'id', 'name', 'source_url', 'download_url', 'format', "
            "'columns'. See data/curated/ADI_KEY_STATS.yaml for a complete "
            "working example."
        )

    columns: dict[str, CuratedColumn] = {}
    for key, col_raw in (raw.get("columns") or {}).items():
        columns[key] = _parse_column(key, col_raw)

    dim_values: dict[str, CuratedDimensionValues] = {}
    for key, val_raw in (raw.get("dimension_values") or {}).items():
        dim_values[key] = _parse_dimension_values(val_raw)

    fmt = str(raw.get("format", "xlsx")).lower()
    if fmt not in ("xlsx", "csv"):
        raise ValueError(
            f"{path.name}: format must be 'xlsx' or 'csv', got {fmt!r}. "
            "Valid options: 'xlsx' (every curated apra-mcp dataset in v0.1) "
            "or 'csv'. Omit the field to default to 'xlsx'."
        )

    layout = str(raw.get("layout", "wide")).lower()
    if layout not in ("wide", "transposed"):
        raise ValueError(
            f"{path.name}: layout must be 'wide' or 'transposed', got "
            f"{layout!r}. Valid options: 'wide' (one row per observation — "
            "every shipped dataset uses this) or 'transposed' (one column "
            "per period — reserved for the v0.2 SUPER_AGGREGATE / "
            "ADI_PROPERTY_EXPOSURES files). Omit to default to 'wide'."
        )

    return CuratedDataset(
        id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        source_url=str(raw["source_url"]),
        download_url=str(raw["download_url"]),
        format=fmt,  # type: ignore[arg-type]
        sheet=raw.get("sheet"),
        header_row=int(raw.get("header_row", 1)),
        data_start_row=raw.get("data_start_row"),
        layout=layout,  # type: ignore[arg-type]
        period_coverage=raw.get("period_coverage"),
        update_frequency=raw.get("update_frequency"),
        cache_kind=str(raw.get("cache_kind", "data")),
        columns=columns,
        dimension_values=dim_values,
        search_keywords=tuple(raw.get("search_keywords") or ()),
        metric_label_column=raw.get("metric_label_column"),
        unit_column=raw.get("unit_column"),
        discovery=_parse_discovery(raw.get("discovery")),
        framework=_parse_framework(raw.get("framework")),
        period_column=raw.get("period_column"),
    )


def _load_all() -> dict[str, CuratedDataset]:
    out: dict[str, CuratedDataset] = {}
    for path in sorted(_yaml_dir().glob("*.yaml")):
        cd = _load_one(path)
        if cd.id in out:
            raise ValueError(
                f"Duplicate curated id {cd.id!r} (from {path.name}). "
                "Each YAML file under data/curated/ must declare a unique "
                "top-level 'id' field. Rename one of the colliding files "
                "and update its 'id', or delete the duplicate."
            )
        out[cd.id] = cd
    return out


def get(dataset_id: str) -> CuratedDataset | None:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY.get(dataset_id.upper())


def list_ids() -> list[str]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return sorted(_REGISTRY.keys())


def list_all() -> list[CuratedDataset]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return [_REGISTRY[k] for k in sorted(_REGISTRY.keys())]


def reset_registry() -> None:
    """For tests."""
    global _REGISTRY
    _REGISTRY = None


def dimension_columns(cd: CuratedDataset) -> list[CuratedColumn]:
    return [c for c in cd.columns.values() if c.role == "dimension"]


def measure_columns(cd: CuratedDataset) -> list[CuratedColumn]:
    return [c for c in cd.columns.values() if c.role == "measure"]


def id_columns(cd: CuratedDataset) -> list[CuratedColumn]:
    return [c for c in cd.columns.values() if c.role == "id"]


def translate_filter_value(
    cd: CuratedDataset, dim_key: str, user_value: str
) -> str:
    """Translate a user-supplied dimension value to the source-column value.

    If the dim has an enumerated `dimension_values` map, the user can pass
    either an alias ('cba') or the canonical value ('Commonwealth Bank...').
    Free-form (no enum) and permissive dims pass values through unchanged.
    Unknown values raise ValueError with a "Did you mean?" suggestion when
    a near-match exists (RapidFuzz WRatio ≥ 70).

    State-shaped filters (`state_territory`, `state`, `region`) accept the
    full cross-source menu via `aus_identity` — short codes, full names,
    ISO 3166-2, aliases, and 4-digit postcodes all resolve to the canonical
    long-form name APRA uses internally (`New South Wales`, `Victoria`, …).
    """
    # aus_identity wire-in: state-shaped dim, free-form/permissive — normalise
    # the user input to the canonical long-form name APRA stores.
    if dim_key in _STATE_LIKE_DIM_NAMES:
        normalised = _normalise_state_to_full_name(user_value)
        if normalised is not None:
            return normalised
    dv = cd.dimension_values.get(dim_key)
    if dv is None or dv.values is None:
        return user_value
    if user_value in dv.values:
        return dv.values[user_value]
    if user_value in dv.values.values():
        return user_value
    if dv.permissive:
        return user_value
    valid = sorted(dv.values.keys())
    hint = _did_you_mean(user_value, valid)
    suggestion = f" Did you mean {hint!r}?" if hint else ""
    raise ValueError(
        f"Unknown value {user_value!r} for filter {dim_key!r} on dataset {cd.id!r}."
        f"{suggestion} Try one of: {', '.join(valid[:15])}"
        + ("..." if len(valid) > 15 else "")
    )


def _did_you_mean(user_value: str, candidates: list[str]) -> str | None:
    """Return the closest candidate string if it scores ≥ 70 on RapidFuzz WRatio."""
    if not candidates:
        return None
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        return None
    match = process.extractOne(user_value, candidates, scorer=fuzz.WRatio, score_cutoff=70)
    return match[0] if match else None


def resolve_measure_keys(
    cd: CuratedDataset, requested: str | list[str] | None
) -> list[str]:
    """Translate a user's measures= request into a list of measure keys.

    - None  → all measure columns
    - "foo" → ["foo"] (validated)
    - ["foo", "bar"] → ["foo", "bar"] (validated)

    For long-format datasets that use a single 'value' measure column plus
    a 'data_item' dimension, this just returns the value column.
    """
    measure_keys = [c.key for c in measure_columns(cd)]
    if requested is None:
        return measure_keys
    items: list[str]
    if isinstance(requested, str):
        items = [requested]
    elif isinstance(requested, list):
        if not requested:
            raise ValueError(
                "measures filter is an empty list. "
                "Pass at least one measure, or omit `measures` to return all."
            )
        items = [str(x) for x in requested]
    else:
        raise ValueError(
            f"measures must be a string or list of strings, got {type(requested).__name__}."
        )

    source_to_key = {c.source_column: c.key for c in cd.columns.values() if c.role == "measure"}
    valid_keys = set(measure_keys)
    out: list[str] = []
    for v in items:
        v_str = v.strip()
        if not v_str:
            raise ValueError(
                f"Empty measure key. Try one of: {', '.join(sorted(valid_keys)[:15])}"
            )
        if v_str in valid_keys:
            out.append(v_str)
        elif v_str in source_to_key:
            out.append(source_to_key[v_str])
        else:
            valid_hint = (
                ", ".join(sorted(valid_keys)[:15])
                if valid_keys
                else "(none — dataset has no curated measures)"
            )
            raise ValueError(
                f"Unknown measure {v!r} for dataset {cd.id!r}. "
                f"Try one of: {valid_hint}"
                + ("..." if len(valid_keys) > 15 else "")
            )
    seen: set[str] = set()
    return [k for k in out if not (k in seen or seen.add(k))]
