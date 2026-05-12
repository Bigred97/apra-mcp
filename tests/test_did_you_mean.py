"""Did-you-mean suggestions on unknown filter values."""
from __future__ import annotations

import pytest

from apra_mcp import curated


def test_close_typo_gets_suggestion():
    cd = curated.get("ADI_KEY_STATS")
    with pytest.raises(ValueError, match=r"Did you mean 'major_banks'\?"):
        curated.translate_filter_value(cd, "sector", "major")


def test_close_typo_on_alias():
    cd = curated.get("ADI_KEY_STATS")
    with pytest.raises(ValueError, match="Did you mean"):
        curated.translate_filter_value(cd, "sector", "majorbanks")


def test_far_unknown_value_no_suggestion():
    """A wildly different value gets no suggestion (only the valid-options list)."""
    cd = curated.get("ADI_KEY_STATS")
    try:
        curated.translate_filter_value(cd, "sector", "zzzzzz")
    except ValueError as e:
        assert "Did you mean" not in str(e), f"unexpected suggestion: {e}"


def test_existing_alias_still_returns_canonical():
    """The did-you-mean path doesn't disturb the happy path."""
    cd = curated.get("ADI_KEY_STATS")
    assert curated.translate_filter_value(cd, "sector", "major_banks") == "Major banks"


def test_existing_canonical_still_passes_through():
    cd = curated.get("ADI_KEY_STATS")
    assert curated.translate_filter_value(cd, "sector", "Major banks") == "Major banks"


def test_permissive_dimension_does_not_raise_for_unknown():
    """Permissive dims like fund_name still pass through unknowns silently."""
    cd = curated.get("SUPER_FUND_LEVEL")
    # fund_name is permissive — unknown values just get passed through
    out = curated.translate_filter_value(cd, "fund_name", "Random Brand New Fund")
    assert out == "Random Brand New Fund"


def test_error_message_includes_valid_options():
    cd = curated.get("ADI_KEY_STATS")
    try:
        curated.translate_filter_value(cd, "sector", "major")
    except ValueError as e:
        assert "major_banks" in str(e)
        assert "foreign_branch" in str(e) or "mutual_adis" in str(e)
