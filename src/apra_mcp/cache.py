"""SQLite-backed HTTP cache with per-read TTL and corruption self-heal.

Ported from ato-mcp. Cache kinds tuned for APRA's quarterly cadence:
- "data":      quarterly APRA releases — 7 days is conservative.
- "landing":   APRA landing-page HTML — 6 hours (cheap re-validation via ETag).
- "discovery": resolved download URLs (the discovery layer's own cache) — 6h.
- "catalog":   reserved for future data.gov.au CKAN fallback — 1 hour.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Literal

import aiosqlite

CacheKind = Literal["data", "landing", "discovery", "catalog"]

DEFAULT_DB_PATH = Path.home() / ".apra-mcp" / "cache.db"

TTL: dict[CacheKind, timedelta] = {
    "data": timedelta(days=7),
    "landing": timedelta(hours=6),
    "discovery": timedelta(hours=6),
    "catalog": timedelta(hours=1),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    cache_key  TEXT PRIMARY KEY,
    payload    BLOB NOT NULL,
    cached_at  REAL NOT NULL,
    kind       TEXT NOT NULL,
    etag       TEXT,
    last_modified TEXT
);
CREATE INDEX IF NOT EXISTS idx_kind_cached_at ON http_cache(kind, cached_at);
"""


class Cache:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                await self._init_schema()
            except sqlite3.DatabaseError:
                # Pre-existing cache.db is corrupt — drop and recreate.
                self.db_path.unlink(missing_ok=True)
                await self._init_schema()
            self._initialized = True

    async def _init_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.executescript(_SCHEMA)
            await conn.commit()

    async def get(self, key: str, ttl: timedelta) -> bytes | None:
        await self._ensure_init()
        cutoff = time.time() - ttl.total_seconds()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT payload FROM http_cache WHERE cache_key = ? AND cached_at >= ?",
                (key, cutoff),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

    async def get_with_validators(
        self, key: str, ttl: timedelta
    ) -> tuple[bytes | None, str | None, str | None]:
        """Return (payload, etag, last_modified). All None if missing/expired."""
        await self._ensure_init()
        cutoff = time.time() - ttl.total_seconds()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT payload, etag, last_modified FROM http_cache "
                "WHERE cache_key = ? AND cached_at >= ?",
                (key, cutoff),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None, None, None
        return row[0], row[1], row[2]

    async def get_validators_any_age(
        self, key: str
    ) -> tuple[str | None, str | None]:
        """Return (etag, last_modified) ignoring TTL.

        Used by conditional GETs: even if the cached body is "stale" per TTL,
        its validators may still be valid — a 304 response means we can keep
        using the stale body and just bump cached_at.
        """
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT etag, last_modified FROM http_cache WHERE cache_key = ?",
                (key,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    async def set(
        self,
        key: str,
        value: bytes,
        kind: CacheKind,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO http_cache (cache_key, payload, cached_at, kind, etag, last_modified)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    cached_at = excluded.cached_at,
                    kind = excluded.kind,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified
                """,
                (key, value, time.time(), kind, etag, last_modified),
            )
            await conn.commit()

    async def touch(self, key: str) -> None:
        """Bump cached_at to now, without overwriting payload.

        Used after a 304 Not Modified — the body is still valid, only the
        freshness clock needs resetting.
        """
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE http_cache SET cached_at = ? WHERE cache_key = ?",
                (time.time(), key),
            )
            await conn.commit()

    async def clear(self, kind: CacheKind | None = None) -> None:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            if kind:
                await conn.execute("DELETE FROM http_cache WHERE kind = ?", (kind,))
            else:
                await conn.execute("DELETE FROM http_cache")
            await conn.commit()

    async def get_cached_at(self, key: str) -> float | None:
        """Return cached_at unix timestamp for a key, or None if missing."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT cached_at FROM http_cache WHERE cache_key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else None
