"""Server-level validation guards on each MCP tool.

Confirms each tool rejects nonsense input cleanly (with a ValueError carrying
a 'Try X' hint) rather than crashing partway through.
"""
from __future__ import annotations

import ast
import pathlib
import re

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
    with pytest.raises(ValueError, match="filters must be"):
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


# --- ValueError sanitisation guards -----------------------------------------
# These pin the boundary between actionable hints and internal-detail leaks.
# Customer-facing ValueError messages must not echo:
#   - apra.gov.au/sites/* URLs (rotate quarterly, not actionable)
#   - MCP-tool names like describe_dataset()/list_curated() in `Try X` hints
#     (those reference the agent's tool registry, which the caller may not
#     have direct access to — point at the dataset's *content* instead)
# Field descriptions and tool docstrings are exempt — they document the
# tool's behaviour rather than suggesting a correction.


@pytest.mark.asyncio
async def test_measure_error_omits_internal_tool_names():
    """The `measure is required` hint must not name internal MCP tools."""
    with pytest.raises(ValueError) as excinfo:
        await server.top_n("ADI_KEY_STATS", "")
    msg = str(excinfo.value)
    assert "describe_dataset" not in msg, f"leaks tool name: {msg}"
    assert "list_curated" not in msg, f"leaks tool name: {msg}"


@pytest.mark.asyncio
async def test_measures_list_error_omits_internal_tool_names():
    """Bad-typed entry in measures list — hint must not name MCP tools."""
    with pytest.raises(ValueError) as excinfo:
        await server.get_data(
            "ADI_KEY_STATS", measures=["cet1_ratio", 42],  # type: ignore[list-item]
        )
    msg = str(excinfo.value)
    assert "describe_dataset" not in msg, f"leaks tool name: {msg}"


@pytest.mark.asyncio
async def test_unknown_filter_error_omits_internal_tool_names():
    """Unknown filter key — hint must not direct users at describe_dataset()."""
    with pytest.raises(ValueError) as excinfo:
        await server.get_data("ADI_KEY_STATS", filters={"made_up": "x"})
    msg = str(excinfo.value)
    assert "describe_dataset" not in msg, f"leaks tool name: {msg}"
    # Must still steer the caller toward the corrective info.
    assert "Valid filters" in msg or "Did you mean" in msg


def test_scrub_internal_urls_replaces_apra_paths():
    """The URL scrubber masks apra.gov.au/sites paths regardless of arg style."""
    text = (
        "apra.gov.au returned 503 for "
        "https://www.apra.gov.au/sites/default/files/2026-03/whatever.xlsx"
    )
    out = server._scrub_internal_urls(text)
    assert "apra.gov.au/sites" not in out
    assert "<source>" in out


# --- 0.8.5 regression: silent-failure fix for permissive filters -------------
#
# Permissive dims with `dimension_values` (e.g. ADI_KEY_STATS / institution,
# SUPER_FUND_LEVEL / fund_name) used to pass unknown values through and emit
# zero rows. Now `_validate_permissive_value` rejects unknown values with a
# "Did you mean / Valid aliases" hint, while known aliases and full source
# names still resolve correctly. Wildcard substring (`'cba*'`) is unchanged.


@pytest.fixture
def _patch_fetch_for_permissive_tests(
    monkeypatch,
    adi_key_stats_xlsx,
    super_fund_level_xlsx,
):
    """Replace `_fetch_and_parse` with a fixture-driven loader.

    These tests pin the validation behaviour — they don't need (and shouldn't
    require) a real apra.gov.au fetch. Mirrors the pattern in test_customer_flows.
    """
    from apra_mcp.parsing import drop_blank_rows, read_xlsx

    fixtures = {
        "ADI_KEY_STATS": adi_key_stats_xlsx,
        "SUPER_FUND_LEVEL": super_fund_level_xlsx,
    }

    async def fake_fetch(cd, *, start_period=None, end_period=None):
        body = fixtures.get(cd.id)
        if body is None:
            raise RuntimeError(f"No fixture for {cd.id}")
        df = read_xlsx(
            body, sheet=cd.sheet,
            header_row=cd.header_row, data_start_row=cd.data_start_row,
            period_source_column=cd.period_column if cd.layout == "wide" else None,
            start_period=start_period, end_period=end_period,
        )
        dim_source_cols = [
            c.source_column for c in cd.columns.values() if c.role == "dimension"
        ]
        if dim_source_cols:
            df = drop_blank_rows(df, dim_source_cols)
        return df, f"https://test/{cd.id}.xlsx", False, None

    monkeypatch.setattr(server, "_fetch_and_parse", fake_fetch)


@pytest.mark.asyncio
async def test_get_data_unknown_institution_raises_with_hint(
    _patch_fetch_for_permissive_tests,
):
    """An unknown bank alias must raise a clean ValueError that names the
    aliases — not silently produce zero rows (the pre-0.8.5 bug).
    """
    with pytest.raises(ValueError) as excinfo:
        await server.get_data(
            "ADI_KEY_STATS", filters={"institution": "mars-bank"},
        )
    msg = str(excinfo.value)
    assert "mars-bank" in msg, f"echoed value missing: {msg}"
    # Must carry one of the two documented correction signals.
    assert "Did you mean" in msg or "Valid aliases" in msg, (
        f"missing correction hint: {msg}"
    )
    # The canonical alias list must be referenced so the caller can self-correct.
    assert "cba" in msg or "westpac" in msg, f"alias hint missing: {msg}"


@pytest.mark.asyncio
async def test_get_data_known_alias_cba_returns_rows(
    _patch_fetch_for_permissive_tests,
):
    """Regression check: the valid `cba` alias still resolves to CBA rows."""
    r = await server.get_data(
        "ADI_KEY_STATS", filters={"institution": "cba"},
    )
    assert r.row_count >= 7, f"expected at least 7 CBA rows, got {r.row_count}"
    # Every record we got back must actually be Commonwealth Bank — confirms
    # the alias translation still works after the validation wire-in.
    for rec in r.records:
        assert (
            rec.dimensions.get("institution") == "Commonwealth Bank of Australia"
        ), f"non-CBA record leaked: {rec.dimensions}"


@pytest.mark.asyncio
async def test_get_data_full_legal_name_still_works(
    _patch_fetch_for_permissive_tests,
):
    """Full legal names (not aliases) must still be accepted — the validation
    treats source-data values as valid even when they're not in the alias map.
    """
    r = await server.get_data(
        "ADI_KEY_STATS",
        filters={"institution": "Commonwealth Bank of Australia"},
    )
    assert r.row_count >= 7


@pytest.mark.asyncio
async def test_get_data_wildcard_still_skips_validation(
    _patch_fetch_for_permissive_tests,
):
    """A trailing-`*` substring query bypasses the alias validation entirely —
    even a completely unknown prefix is allowed (it just matches no rows).
    """
    r = await server.get_data(
        "ADI_KEY_STATS",
        filters={"institution": "nonexistent_xyz_wildcard*"},
    )
    assert r.row_count == 0


@pytest.mark.asyncio
async def test_get_data_unknown_in_list_raises(
    _patch_fetch_for_permissive_tests,
):
    """An unknown value inside a list filter raises (not just bare strings)."""
    with pytest.raises(ValueError, match="mars-bank"):
        await server.get_data(
            "ADI_KEY_STATS",
            filters={"institution": ["cba", "mars-bank"]},
        )


@pytest.mark.asyncio
async def test_get_data_unknown_fund_name_raises(
    _patch_fetch_for_permissive_tests,
):
    """The same validation applies on SUPER_FUND_LEVEL / fund_name."""
    with pytest.raises(ValueError) as excinfo:
        await server.get_data(
            "SUPER_FUND_LEVEL", filters={"fund_name": "definitely-not-a-fund"},
        )
    msg = str(excinfo.value)
    assert "definitely-not-a-fund" in msg
    assert "Did you mean" in msg or "Valid aliases" in msg


# ----- transport-agnostic error hints (mirrors rba-mcp's guard) -----
#
# Error messages must not reference MCP-tool names (e.g. `describe_dataset()`,
# `search_datasets()`, `list_curated()`). An error from the apra_mcp package
# should read the same whether the caller is an MCP client, a REST gateway,
# or a Python script calling the functions directly.

_SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "apra_mcp"


def _extract_user_facing_strings() -> list[tuple[pathlib.Path, int, str]]:
    """Walk every .py under src/apra_mcp/, parse the AST, and yield only the
    string arguments to `raise <SomeExc>(...)` calls — these are the strings
    users actually see in error reports.
    """
    out: list[tuple[pathlib.Path, int, str]] = []
    for py in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc if isinstance(node.exc, ast.Call) else None
            if call is None:
                continue
            for arg in call.args:
                pieces: list[str] = []
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    pieces.append(arg.value)
                elif isinstance(arg, ast.JoinedStr):
                    for v in arg.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            pieces.append(v.value)
                elif isinstance(arg, ast.BinOp):
                    stack: list[ast.AST] = [arg]
                    while stack:
                        cur = stack.pop()
                        if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                            pieces.append(cur.value)
                        elif isinstance(cur, ast.BinOp):
                            stack.append(cur.left)
                            stack.append(cur.right)
                        elif isinstance(cur, ast.JoinedStr):
                            for v in cur.values:
                                stack.append(v)
                if pieces:
                    out.append((py, node.lineno, "".join(pieces)))
    return out


def test_no_mcp_tool_refs_in_error_strings():
    """No error message references an MCP tool by name
    (`describe_dataset(...)`, `search_datasets(...)`, `list_curated(...)`).
    The hint must suggest what to do (look up valid keys, retry, etc.)
    without naming a specific transport's API surface.
    """
    pat = re.compile(r"\b(describe_dataset|search_datasets|list_curated)\s*\(")
    offenders: list[str] = []
    for path, lineno, text in _extract_user_facing_strings():
        if pat.search(text):
            offenders.append(f"{path.relative_to(_SRC_ROOT.parent.parent)}:{lineno}: {text!r}")
    assert not offenders, (
        "User-facing error messages reference MCP tool names — "
        "these are transport-specific and shouldn't leak through ValueError. "
        "Replace with transport-agnostic hints (e.g. 'See the valid-options list "
        f"for X').\n  {chr(10).join(offenders)}"
    )
