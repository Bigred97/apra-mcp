"""Curated YAML loader contract tests.

These hit the actual YAMLs shipped with the package — if anyone breaks
one, this suite catches it before the wheel ships.
"""
from __future__ import annotations

import pytest

from apra_mcp import curated


def test_at_least_seven_curated_datasets():
    ids = curated.list_ids()
    assert len(ids) >= 7, f"expected at least 7 curated datasets, got {ids}"


def test_expected_curated_ids():
    ids = set(curated.list_ids())
    for expected in (
        "ADI_KEY_STATS",
        "ADI_RISK_WEIGHTED_ASSETS",
        "SUPER_FUND_LEVEL",
        "INSURANCE_GENERAL",
        "INSURANCE_GENERAL_HISTORICAL",
        "LIFE_INSURANCE",
        "LIFE_INSURANCE_HISTORICAL",
    ):
        assert expected in ids, f"missing curated dataset: {expected}"


def test_every_curated_dataset_has_required_fields():
    for cd in curated.list_all():
        assert cd.id, f"missing id in {cd}"
        assert cd.name, f"missing name on {cd.id}"
        assert cd.description, f"missing description on {cd.id}"
        assert cd.source_url.startswith("https://"), f"bad source_url on {cd.id}"
        assert cd.download_url.startswith("https://"), f"bad download_url on {cd.id}"
        assert cd.format == "xlsx", f"format must be xlsx for v0.1, got {cd.format} on {cd.id}"
        assert cd.sheet, f"xlsx dataset {cd.id} missing sheet name"
        assert cd.header_row >= 1, f"bad header_row on {cd.id}"
        assert cd.layout in ("wide", "transposed"), f"unknown layout {cd.layout!r} on {cd.id}"
        roles = {c.role for c in cd.columns.values()}
        assert "measure" in roles, f"dataset {cd.id} declares no measure columns"
        assert "dimension" in roles, f"dataset {cd.id} declares no dimension columns"


def test_no_duplicate_curated_ids():
    ids = curated.list_ids()
    assert len(ids) == len(set(ids)), f"duplicate IDs in registry: {ids}"


def test_column_keys_are_unique_within_dataset():
    for cd in curated.list_all():
        keys = [c.key for c in cd.columns.values()]
        assert len(keys) == len(set(keys)), f"duplicate column keys in {cd.id}: {keys}"


def test_dimension_values_reference_real_columns():
    for cd in curated.list_all():
        col_keys = {c.key for c in cd.columns.values()}
        for dim_key in cd.dimension_values:
            assert dim_key in col_keys, (
                f"{cd.id}: dimension_values entry {dim_key!r} doesn't match any column"
            )


def test_translate_filter_value_for_known_alias():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    out = curated.translate_filter_value(cd, "institution", "cba")
    assert out == "Commonwealth Bank of Australia"


def test_translate_filter_value_passthrough_canonical():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    out = curated.translate_filter_value(cd, "sector", "Major banks")
    assert out == "Major banks"


def test_translate_filter_value_sector_alias():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    assert curated.translate_filter_value(cd, "sector", "major_banks") == "Major banks"


def test_translate_filter_value_permissive_passthrough():
    """Permissive dimensions accept any value — used for fund_name, data_item."""
    cd = curated.get("SUPER_FUND_LEVEL")
    assert cd is not None
    out = curated.translate_filter_value(cd, "fund_name", "Some Brand New Fund Pty Ltd")
    assert out == "Some Brand New Fund Pty Ltd"


def test_translate_filter_value_unknown_strict_raises():
    """Non-permissive enums reject unknown values."""
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    with pytest.raises(ValueError, match="Unknown value"):
        curated.translate_filter_value(cd, "sector", "atlantis_banks")


# ---- aus-identity cross-source normalisation on state_territory ----


def test_state_territory_accepts_short_code():
    """`state_territory='NSW'` resolves to APRA's canonical 'New South Wales'."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    assert (
        curated.translate_filter_value(cd, "state_territory", "NSW")
        == "New South Wales"
    )


def test_state_territory_accepts_lowercase_short_code():
    """`state_territory='nsw'` (lowercase) also resolves to 'New South Wales'."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    assert (
        curated.translate_filter_value(cd, "state_territory", "nsw")
        == "New South Wales"
    )


def test_state_territory_accepts_full_name():
    """`state_territory='Queensland'` is already in canonical form."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    assert (
        curated.translate_filter_value(cd, "state_territory", "Queensland")
        == "Queensland"
    )


def test_state_territory_accepts_iso_3166_form():
    """`state_territory='AU-VIC'` resolves to 'Victoria'."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    assert (
        curated.translate_filter_value(cd, "state_territory", "AU-VIC")
        == "Victoria"
    )


def test_state_territory_accepts_postcode_routing():
    """`state_territory='3000'` (Melbourne CBD) routes to 'Victoria'."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    assert (
        curated.translate_filter_value(cd, "state_territory", "3000")
        == "Victoria"
    )


def test_state_territory_postcode_in_act_routes_correctly():
    """`state_territory='2600'` (Parliament House) routes to ACT, not NSW."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    assert (
        curated.translate_filter_value(cd, "state_territory", "2600")
        == "Australian Capital Territory"
    )


def test_state_territory_non_state_input_falls_through():
    """A value that isn't a state name or postcode is passed through unchanged
    (permissive dim, free-form). The downstream filter just won't match any
    rows — same behaviour as before aus_identity."""
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None
    # 'Total Australia' is a known APRA bucket; not a state, but permissive.
    assert (
        curated.translate_filter_value(cd, "state_territory", "Total Australia")
        == "Total Australia"
    )


def test_resolve_measure_keys_none_returns_all():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    keys = curated.resolve_measure_keys(cd, None)
    assert "cet1_ratio" in keys
    assert "total_capital_ratio" in keys


def test_resolve_measure_keys_single():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    assert curated.resolve_measure_keys(cd, "cet1_ratio") == ["cet1_ratio"]


def test_resolve_measure_keys_list_dedupes():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    out = curated.resolve_measure_keys(cd, ["cet1_ratio", "tier1_ratio", "cet1_ratio"])
    assert out == ["cet1_ratio", "tier1_ratio"]


def test_resolve_measure_keys_empty_list_raises():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    with pytest.raises(ValueError, match="empty list"):
        curated.resolve_measure_keys(cd, [])


def test_resolve_measure_keys_unknown_raises():
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    with pytest.raises(ValueError, match="Unknown measure"):
        curated.resolve_measure_keys(cd, "alien_metric")


def test_resolve_measure_keys_source_column_passthrough():
    """Raw source-column names also resolve to their alias."""
    cd = curated.get("ADI_KEY_STATS")
    assert cd is not None
    out = curated.resolve_measure_keys(cd, "Common Equity Tier 1 capital ratio")
    assert out == ["cet1_ratio"]


def test_insurance_datasets_have_framework():
    """The four insurance datasets must declare a framework block."""
    for did in ("LIFE_INSURANCE", "LIFE_INSURANCE_HISTORICAL", "INSURANCE_GENERAL", "INSURANCE_GENERAL_HISTORICAL"):
        cd = curated.get(did)
        assert cd is not None
        assert cd.framework is not None, f"{did} missing framework block"
        assert cd.framework.current_basis in ("post-AASB17", "pre-AASB17")


def test_non_insurance_datasets_have_no_framework():
    """ADI / Super datasets have no framework break."""
    for did in ("ADI_KEY_STATS", "ADI_RISK_WEIGHTED_ASSETS", "SUPER_FUND_LEVEL"):
        cd = curated.get(did)
        assert cd is not None
        assert cd.framework is None, f"{did} should not have a framework block"


def test_post_aasb17_datasets_reference_historical():
    """post-AASB17 datasets cross-reference their _HISTORICAL counterpart."""
    cd = curated.get("LIFE_INSURANCE")
    assert cd is not None and cd.framework is not None
    assert cd.framework.historical_dataset == "LIFE_INSURANCE_HISTORICAL"
    cd = curated.get("INSURANCE_GENERAL")
    assert cd is not None and cd.framework is not None
    assert cd.framework.historical_dataset == "INSURANCE_GENERAL_HISTORICAL"


def test_all_datasets_have_discovery_spec():
    """Every curated dataset declares how to scrape its landing page."""
    for cd in curated.list_all():
        assert cd.discovery is not None, f"{cd.id} missing discovery block"
        assert cd.discovery.landing_url.startswith("https://www.apra.gov.au/"), (
            f"{cd.id}: discovery landing_url must be apra.gov.au"
        )
        assert cd.discovery.filename_pattern, f"{cd.id}: empty filename_pattern"


def test_all_datasets_have_period_column():
    """Every curated dataset names its period column for date filtering."""
    for cd in curated.list_all():
        assert cd.period_column, f"{cd.id} missing period_column"
        # The period_column must reference a real source_column in the YAML
        sources = {c.source_column for c in cd.columns.values()}
        assert cd.period_column in sources, (
            f"{cd.id}: period_column {cd.period_column!r} not in any column's source_column"
        )


def test_unknown_dataset_returns_none():
    assert curated.get("DOES_NOT_EXIST") is None


def test_get_is_case_insensitive():
    assert curated.get("adi_key_stats") is not None
    assert curated.get("ADI_KEY_STATS") is not None


def test_reset_registry_reloads():
    curated.list_ids()
    curated.reset_registry()
    ids = curated.list_ids()
    assert len(ids) >= 7
