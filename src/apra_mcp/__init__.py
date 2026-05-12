"""apra-mcp — MCP server for Australian Prudential Regulation Authority statistics."""
from __future__ import annotations

try:
    from importlib.metadata import version as _v
    __version__ = _v("apra-mcp")
except Exception:
    __version__ = "0.0.0+unknown"
