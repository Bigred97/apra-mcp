"""Server-level validation guards on each MCP tool.

Confirms each tool rejects nonsense input cleanly (with a ValueError carrying
a 'Try X' hint) rather than crashing partway through.
"""
from __future__ import annotations

import pytest

from apra_mcp import server


@pytest.mark.asyncio
async def test_search_datasets_empty_query():
    with pytest.raises(ValueError, match="query is required"):
        await server.search_datasets("")


@pytest.mark.asyncio
async def test_search_datasets_whitespace_query():
    with pytest.raises(ValueError, match="query is required"):
        await server.search_datasets("   ")


@pytest.mark.asyncio
async def test_search_datasets_non_string_query():
    with pytest.raises(ValueError, match="must be a string"):
        await server.search_datasets(123)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_search_datasets_limit_too_small():
    with pytest.raises(ValueError, match=">= 1"):
        await server.search_datasets("capital", limit=0)


@pytest.mark.asyncio
async def test_search_datasets_limit_is_bool():
    with pytest.raises(ValueError, match="positive integer"):
        await server.search_datasets("capital", limit=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_search_datasets_limit_is_float():
    with pytest.raises(ValueError, match="positive integer"):
        await server.search_datasets("capital", limit=1.5)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_describe_dataset_unknown_id():
    with pytest.raises(ValueError, match="not a curated"):
        await server.describe_dataset("DOES_NOT_EXIST")


@pytest.mark.asyncio
async def test_describe_dataset_bad_chars():
    with pytest.raises(ValueError, match="invalid characters"):
        await server.describe_dataset("../etc/passwd")


@pytest.mark.asyncio
async def test_describe_dataset_empty_id():
    with pytest.raises(ValueError, match="empty"):
        await server.describe_dataset("")


@pytest.mark.asyncio
async def test_describe_dataset_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        await server.describe_dataset(42)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_describe_dataset_returns_framework_for_insurance():
    d = await server.describe_dataset("LIFE_INSURANCE")
    assert d.framework is not None
    assert d.framework.basis == "post-AASB17"


@pytest.mark.asyncio
async def test_describe_dataset_no_framework_for_adi():
    d = await server.describe_dataset("ADI_KEY_STATS")
    assert d.framework is None


@pytest.mark.asyncio
async def test_describe_dataset_case_insensitive():
    d = await server.describe_dataset("adi_key_stats")
    assert d.id == "ADI_KEY_STATS"


@pytest.mark.asyncio
async def test_get_data_unknown_id():
    with pytest.raises(ValueError, match="not a curated"):
        await server.get_data("DOES_NOT_EXIST")


@pytest.mark.asyncio
async def test_get_data_filters_must_be_dict():
    with pytest.raises(ValueError, match="filters must be a dict"):
        await server.get_data(
            "ADI_KEY_STATS", filters=["institution", "cba"],  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_get_data_bad_period_format():
    with pytest.raises(ValueError, match="invalid format"):
        await server.get_data("ADI_KEY_STATS", start_period="?garbage?")


@pytest.mark.asyncio
async def test_get_data_period_swap():
    with pytest.raises(ValueError, match="before start_period"):
        await server.get_data(
            "ADI_KEY_STATS", start_period="2025-01-01", end_period="2020-01-01",
        )


@pytest.mark.asyncio
async def test_get_data_empty_measures_list():
    with pytest.raises(ValueError, match="empty list"):
        await server.get_data("ADI_KEY_STATS", measures=[])


@pytest.mark.asyncio
async def test_get_data_empty_measure_string():
    with pytest.raises(ValueError, match="empty"):
        await server.get_data("ADI_KEY_STATS", measures="")


@pytest.mark.asyncio
async def test_get_data_bad_format():
    with pytest.raises(ValueError, match="Unknown format"):
        await server.get_data("ADI_KEY_STATS", format="parquet")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_data_format_not_string():
    with pytest.raises(ValueError, match="format must be a string"):
        await server.get_data("ADI_KEY_STATS", format=42)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_data_measures_non_string_in_list():
    with pytest.raises(ValueError, match="must be strings"):
        await server.get_data(
            "ADI_KEY_STATS", measures=["cet1_ratio", 42],  # type: ignore[list-item]
        )


@pytest.mark.asyncio
async def test_get_data_period_non_string():
    # 12345 is out of the [1900, 2100] coerce-to-year range, so it's still rejected.
    with pytest.raises(ValueError, match="out of range"):
        await server.get_data(
            "ADI_KEY_STATS", start_period=12345,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_get_data_dataset_id_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        await server.get_data(42)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_data_dataset_id_with_pipe():
    with pytest.raises(ValueError, match="invalid characters"):
        await server.get_data("ADI|HACK")


@pytest.mark.asyncio
async def test_get_data_dataset_id_with_semicolon():
    with pytest.raises(ValueError, match="invalid characters"):
        await server.get_data("ADI;DROP")


@pytest.mark.asyncio
async def test_get_data_dataset_id_with_space():
    with pytest.raises(ValueError, match="invalid characters"):
        await server.get_data("AD I_KEY_STATS")


@pytest.mark.asyncio
async def test_latest_unknown_id():
    with pytest.raises(ValueError, match="not a curated"):
        await server.latest("NOPE")


@pytest.mark.asyncio
async def test_top_n_unknown_id():
    with pytest.raises(ValueError, match="not a curated"):
        await server.top_n("NOPE", "cet1_ratio")


@pytest.mark.asyncio
async def test_top_n_n_too_small():
    with pytest.raises(ValueError, match=">= 1"):
        await server.top_n("ADI_KEY_STATS", "cet1_ratio", n=0)


@pytest.mark.asyncio
async def test_top_n_n_is_bool():
    with pytest.raises(ValueError, match="positive integer"):
        await server.top_n("ADI_KEY_STATS", "cet1_ratio", n=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_top_n_measure_empty():
    with pytest.raises(ValueError, match="measure is required"):
        await server.top_n("ADI_KEY_STATS", "")


@pytest.mark.asyncio
async def test_top_n_direction_invalid():
    with pytest.raises(ValueError, match="direction must be"):
        await server.top_n("ADI_KEY_STATS", "cet1_ratio", direction="middle")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_curated_returns_sorted_ids():
    ids = server.list_curated()
    assert ids == sorted(ids)
    assert "ADI_KEY_STATS" in ids
    assert "LIFE_INSURANCE" in ids


# --- Int-year coercion (Wave 1 interop fix) ----------------------------------

def test_validate_period_accepts_int_year():
    """Bare int years are coerced to 'YYYY' string at the boundary."""
    assert server._validate_period(2024, "start_period") == "2024"
    assert server._validate_period(1907, "end_period") == "1907"
    assert server._validate_period(2100, "start_period") == "2100"


def test_validate_period_int_out_of_range_raises_helpful():
    """Int years outside [1900, 2100] raise with a useful hint, not a TypeError."""
    with pytest.raises(ValueError, match="out of range"):
        server._validate_period(1800, "start_period")
    with pytest.raises(ValueError, match="out of range"):
        server._validate_period(2200, "end_period")
    # Should still point users at the canonical forms.
    with pytest.raises(ValueError, match="YYYY"):
        server._validate_period(99, "start_period")


def test_validate_period_rejects_bool_with_hint():
    """bool is a subclass of int but must NOT be coerced silently."""
    with pytest.raises(ValueError, match="bool"):
        server._validate_period(True, "start_period")


@pytest.mark.asyncio
async def test_get_data_accepts_int_start_period():
    """get_data accepts int start_period (LLM clients often send JSON ints)."""
    # 1907 is well outside any APRA dataset's coverage, so this will yield zero
    # rows — but it must not raise on type. The fetch path will succeed because
    # the parsed-DataFrame cache is in play from earlier tests.
    try:
        resp = await server.get_data("ADI_KEY_STATS", start_period=2024)
        # int got coerced — confirm by inspecting the echoed query.
        assert resp.query.get("start_period") == "2024"
    except ValueError as e:
        # The only acceptable ValueError here is upstream fetch — NOT type.
        assert "must be a string" not in str(e)
        assert "type" not in str(e).lower() or "could not fetch" in str(e).lower()


# --- Strengthened ValueError messages (Wave 1 interop fix) -------------------

@pytest.mark.asyncio
async def test_unknown_dataset_suggests_close_match():
    """A near-miss dataset id surfaces a 'Did you mean ...?' hint."""
    with pytest.raises(ValueError, match="Did you mean") as excinfo:
        await server.get_data("ADI_KEYS")
    assert "ADI_KEY_STATS" in str(excinfo.value)


@pytest.mark.asyncio
async def test_period_format_error_includes_examples_and_try():
    """The canonical shape: <rejection>. Did you mean X?. Examples. Try <next-tool>."""
    with pytest.raises(ValueError) as excinfo:
        await server.get_data("ADI_KEY_STATS", start_period="?garbage?")
    msg = str(excinfo.value)
    assert "YYYY" in msg
    assert "Example" in msg or "Try" in msg or "example" in msg


@pytest.mark.asyncio
async def test_period_swap_error_includes_format_hint():
    """end < start error should remind users of the period formats."""
    with pytest.raises(ValueError, match="YYYY") as excinfo:
        await server.get_data(
            "ADI_KEY_STATS", start_period="2025-01-01", end_period="2020-01-01",
        )
    assert "before start_period" in str(excinfo.value)


@pytest.mark.asyncio
async def test_format_error_includes_did_you_mean():
    """A near-miss format value surfaces a 'Did you mean ...?' suggestion."""
    with pytest.raises(ValueError, match="Did you mean") as excinfo:
        await server.get_data("ADI_KEY_STATS", format="record")  # type: ignore[arg-type]
    assert "records" in str(excinfo.value)
