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
    with pytest.raises(ValueError, match="must be a string"):
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
