"""Real MCP-protocol-path tests for the `filters` parameter.

These tests go through ``fastmcp.Client`` (in-process JSON-RPC transport)
rather than calling the tool functions directly with a Python dict. That
distinction matters: a real MCP client sends `filters` as a JSON-encoded
*string* over the wire, and FastMCP validates that raw argument against the
tool's Pydantic-derived schema *before* the function body ever runs. Calling
``server.get_data(..., filters={"institution": "cba"})`` directly (as the
rest of the suite does) never exercises that validation step, which is
exactly how a `dict[str, Any] | None`-only annotation shipped a bug where
every real client sending `filters` as a string got a raw
`pydantic.ValidationError` (`dict_type`) instead of ever reaching the
existing lenient `_validate_filters` string-parsing branch.

Confirmed against pre-fix code (dict[str, Any] | None): calling
`client.call_tool("get_data", {"filters": '{"institution": "cba"}'})` raised
`fastmcp.exceptions.ToolError` wrapping:

    ValidationError: 1 validation error for call[get_data]
    filters
      Input should be a valid dictionary [type=dict_type, input_value='...', input_type=str]

With `filters: dict[str, Any] | str | None`, the same call now succeeds and
`_validate_filters` performs the json.loads() as it always intended to.
"""
from __future__ import annotations

import pytest
from fastmcp import Client

from apra_mcp import server
from apra_mcp.parsing import drop_blank_rows, read_xlsx


@pytest.fixture(autouse=True)
def patch_fetch_with_fixture(monkeypatch, adi_key_stats_xlsx):
    """Replace _fetch_and_parse with a fixture-backed version so these tests
    run without network (same pattern as tests/test_top_n.py)."""

    async def fake_fetch(cd, *, start_period=None, end_period=None):
        df = read_xlsx(
            adi_key_stats_xlsx,
            sheet=cd.sheet,
            header_row=cd.header_row,
            data_start_row=cd.data_start_row,
            period_source_column=cd.period_column if cd.layout == "wide" else None,
            start_period=start_period,
            end_period=end_period,
        )
        dim_source_cols = [c.source_column for c in cd.columns.values() if c.role == "dimension"]
        if dim_source_cols:
            df = drop_blank_rows(df, dim_source_cols)
        return df, f"https://test/{cd.id}.xlsx", False, None

    monkeypatch.setattr(server, "_fetch_and_parse", fake_fetch)
    yield


@pytest.mark.asyncio
async def test_get_data_accepts_json_string_filters_over_mcp_protocol():
    """A client sending `filters` as a JSON string (the real MCP wire shape)
    must succeed, not raise a Pydantic dict_type ValidationError."""
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_data",
            {"dataset_id": "ADI_KEY_STATS", "filters": '{"institution": "cba"}'},
        )
    assert result.structured_content is not None
    for rec in result.structured_content["records"]:
        assert rec["dimensions"].get("institution") == "Commonwealth Bank of Australia"


@pytest.mark.asyncio
async def test_latest_accepts_json_string_filters_over_mcp_protocol():
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "latest",
            {"dataset_id": "ADI_KEY_STATS", "filters": '{"institution": "cba"}'},
        )
    assert result.structured_content is not None
    assert result.structured_content["row_count"] >= 1


@pytest.mark.asyncio
async def test_top_n_accepts_json_string_filters_over_mcp_protocol():
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "top_n",
            {
                "dataset_id": "ADI_KEY_STATS",
                "measure": "total_capital",
                "n": 3,
                "filters": '{"sector": "major_banks"}',
            },
        )
    assert result.structured_content is not None
    assert result.structured_content["row_count"] <= 3


@pytest.mark.asyncio
async def test_get_data_rejects_malformed_json_string_filters_with_clear_hint():
    """Malformed JSON text should surface _validate_filters' hint, not a raw
    Pydantic error, confirming the string genuinely reaches the helper."""
    async with Client(server.mcp) as client:
        with pytest.raises(Exception, match="invalid JSON string"):
            await client.call_tool(
                "get_data",
                {"dataset_id": "ADI_KEY_STATS", "filters": "{not json}"},
            )


@pytest.mark.asyncio
async def test_get_data_still_accepts_native_dict_filters_over_mcp_protocol():
    """Guard against a regression in the other direction: a client sending a
    native JSON object (dict) for `filters` must keep working."""
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_data",
            {"dataset_id": "ADI_KEY_STATS", "filters": {"institution": "cba"}},
        )
    assert result.structured_content is not None
    for rec in result.structured_content["records"]:
        assert rec["dimensions"].get("institution") == "Commonwealth Bank of Australia"


def test_validate_filters_helper_still_parses_json_strings():
    """Unit-level guard on the underlying helper (fallback coverage,
    independent of the protocol-level tests above)."""
    from apra_mcp.server import _validate_filters

    assert _validate_filters('{"institution": "cba"}') == {"institution": "cba"}
    assert _validate_filters(None) == {}
    assert _validate_filters({"a": 1}) == {"a": 1}
    with pytest.raises(ValueError, match="invalid JSON string"):
        _validate_filters("{not json}")
