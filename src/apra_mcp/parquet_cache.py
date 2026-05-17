"""On-disk Parquet cache for parsed DataFrames.

Mirrors `wgea-mcp` / `aihw-mcp` / `ato-mcp`'s parquet_cache module. APRA
quarterly statistics XLSX files are 15MB peak and 2-5s to re-parse on a
fresh Fly worker — combined with network fetch + JSON serialisation
this trips the 20s gateway budget on INSURANCE_GENERAL (~18k rows) and
ADI_PROPERTY_EXPOSURES.

Location: defaults to `~/.apra-mcp/parquet-cache/`, overridable via
`APRA_MCP_PARQUET_CACHE_DIR`.

TTL: 24h, matching APRA's quarterly publish cadence (well within the
window where a re-parse would yield identical bytes).
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_TTL_SECONDS = 24 * 60 * 60

_ENV_VAR = "APRA_MCP_PARQUET_CACHE_DIR"
_DEFAULT_DIR = Path.home() / ".apra-mcp" / "parquet-cache"


def cache_dir() -> Path:
    override = os.environ.get(_ENV_VAR)
    path = Path(override) if override else _DEFAULT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key_to_filename(key: tuple[Any, ...]) -> str:
    payload = repr(key).encode("utf-8")
    return hashlib.sha256(payload).hexdigest() + ".parquet"


def read_if_fresh(
    key: tuple[Any, ...], *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> pd.DataFrame | None:
    path = cache_dir() / _key_to_filename(key)
    if not path.is_file():
        return None
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > ttl_seconds:
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        return None


def write(key: tuple[Any, ...], df: pd.DataFrame) -> None:
    target = cache_dir() / _key_to_filename(key)
    tmp = target.with_suffix(".parquet.tmp")
    try:
        df.to_parquet(tmp, engine="pyarrow", compression="snappy", index=False)
        tmp.replace(target)
    except Exception:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


def reset_for_tests() -> None:
    d = cache_dir()
    for f in d.glob("*.parquet"):
        try:
            f.unlink()
        except OSError:
            pass
    for f in d.glob("*.parquet.tmp"):
        try:
            f.unlink()
        except OSError:
            pass
