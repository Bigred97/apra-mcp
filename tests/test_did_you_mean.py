"""Did-you-mean suggestions on unknown filter values."""
from __future__ import annotations

import pytest

from apra_mcp import curated, server


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


# --- 0.1.4 regression: error-message sweep --------------------------------
#
# Every ValueError on the public tool surface should suggest the correction,
# not just describe the rejection. Three regression tests cover the highest-
# impact upgrades in this release: dataset-id "Did you mean?", measures-list
# type errors carrying example syntax, and the upstream-fetch error pointing
# at the landing page so the agent can sanity-check connectivity.


@pytest.mark.asyncio
async def test_unknown_dataset_id_suggests_close_match():
    """A near-miss dataset id should get a 'Did you mean ADI_KEY_STATS?' hint
    plus the list of valid IDs — not just "Try list_curated()"."""
    with pytest.raises(ValueError) as exc_info:
        await server.describe_dataset("ADI_KEYSTATS")  # missing underscore
    msg = str(exc_info.value)
    assert "Did you mean" in msg, f"missing did-you-mean hint: {msg}"
    assert "ADI_KEY_STATS" in msg, f"missing closest match: {msg}"
    assert "list_curated" in msg, f"missing tool pointer: {msg}"


@pytest.mark.asyncio
async def test_unknown_dataset_id_on_get_data_lists_valid_ids():
    """Even when the typo is too far for a fuzzy match, the valid-ID list
    must be embedded in the message so the agent has alternatives."""
    with pytest.raises(ValueError) as exc_info:
        await server.get_data("XYZQ_TOTALLY_MADE_UP")
    msg = str(exc_info.value)
    assert "Valid IDs:" in msg, f"missing valid-ID enumeration: {msg}"
    # At least one real dataset must be referenced as an alternative.
    assert "ADI_KEY_STATS" in msg or "LIFE_INSURANCE" in msg, (
        f"no real datasets enumerated: {msg}"
    )


@pytest.mark.asyncio
async def test_measures_non_string_in_list_carries_example():
    """The 'measures list entries must be strings' message must show an
    example of the correct shape and pointer at describe_dataset()."""
    with pytest.raises(ValueError) as exc_info:
        await server.get_data(
            "ADI_KEY_STATS", measures=["cet1_ratio", 42],  # type: ignore[list-item]
        )
    msg = str(exc_info.value)
    assert "cet1_ratio" in msg or "total_capital" in msg, (
        f"no example measure key in error: {msg}"
    )
    assert "describe_dataset" in msg, (
        f"missing pointer to describe_dataset(): {msg}"
    )
