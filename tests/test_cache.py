"""Cache layer tests."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path

import pytest

from apra_mcp.cache import Cache, TTL


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


@pytest.mark.asyncio
async def test_set_get_within_ttl(temp_db: Path):
    cache = Cache(temp_db)
    await cache.set("key1", b"hello", kind="data")
    got = await cache.get("key1", ttl=timedelta(minutes=5))
    assert got == b"hello"


@pytest.mark.asyncio
async def test_get_past_ttl_returns_none(temp_db: Path):
    cache = Cache(temp_db)
    await cache.set("key1", b"hello", kind="data")
    await asyncio.sleep(0.02)
    got = await cache.get("key1", ttl=timedelta(microseconds=1))
    assert got is None


@pytest.mark.asyncio
async def test_get_missing_key_returns_none(temp_db: Path):
    cache = Cache(temp_db)
    got = await cache.get("nope", ttl=timedelta(hours=1))
    assert got is None


@pytest.mark.asyncio
async def test_set_overwrites_existing(temp_db: Path):
    cache = Cache(temp_db)
    await cache.set("key1", b"first", kind="data")
    await cache.set("key1", b"second", kind="data")
    got = await cache.get("key1", ttl=timedelta(hours=1))
    assert got == b"second"


@pytest.mark.asyncio
async def test_clear_all(temp_db: Path):
    cache = Cache(temp_db)
    await cache.set("k1", b"a", kind="data")
    await cache.set("k2", b"b", kind="landing")
    await cache.clear()
    assert await cache.get("k1", ttl=timedelta(hours=1)) is None
    assert await cache.get("k2", ttl=timedelta(hours=1)) is None


@pytest.mark.asyncio
async def test_clear_by_kind(temp_db: Path):
    cache = Cache(temp_db)
    await cache.set("k1", b"a", kind="data")
    await cache.set("k2", b"b", kind="landing")
    await cache.clear(kind="data")
    assert await cache.get("k1", ttl=timedelta(hours=1)) is None
    assert await cache.get("k2", ttl=timedelta(hours=1)) == b"b"


@pytest.mark.asyncio
async def test_corrupt_db_silent_rebuild(temp_db: Path):
    """Corrupt cache.db → cache.set() should drop and recreate it."""
    temp_db.parent.mkdir(parents=True, exist_ok=True)
    temp_db.write_bytes(b"this is not a sqlite database at all\x00\xff\xfe")
    cache = Cache(temp_db)
    await cache.set("k1", b"hello", kind="data")
    got = await cache.get("k1", ttl=timedelta(hours=1))
    assert got == b"hello"


@pytest.mark.asyncio
async def test_concurrent_writes_dont_corrupt(temp_db: Path):
    cache = Cache(temp_db)
    async def write_one(i: int) -> None:
        await cache.set(f"key_{i}", str(i).encode(), kind="data")
    await asyncio.gather(*(write_one(i) for i in range(50)))
    for i in range(50):
        got = await cache.get(f"key_{i}", ttl=timedelta(hours=1))
        assert got == str(i).encode(), f"key_{i} mismatch"


@pytest.mark.asyncio
async def test_ttl_constants_defined():
    for kind in ("data", "landing", "discovery", "catalog"):
        assert kind in TTL, f"TTL missing for kind {kind!r}"
        assert TTL[kind].total_seconds() > 0


@pytest.mark.asyncio
async def test_large_payload_roundtrip(temp_db: Path):
    """Payloads up to 10MB (biggest APRA file) must roundtrip without truncation."""
    cache = Cache(temp_db)
    payload = b"x" * (10 * 1024 * 1024)
    await cache.set("big", payload, kind="data")
    got = await cache.get("big", ttl=timedelta(hours=1))
    assert got == payload
    assert len(got) == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_binary_safe(temp_db: Path):
    cache = Cache(temp_db)
    payload = bytes(range(256)) * 100
    await cache.set("binary", payload, kind="data")
    got = await cache.get("binary", ttl=timedelta(hours=1))
    assert got == payload


@pytest.mark.asyncio
async def test_etag_storage_and_retrieval(temp_db: Path):
    """ETags get stored and retrieved alongside the payload."""
    cache = Cache(temp_db)
    await cache.set("k1", b"hello", kind="landing", etag='"abc123"', last_modified="Wed, 01 Jan 2026 00:00:00 GMT")
    body, etag, lm = await cache.get_with_validators("k1", ttl=timedelta(hours=1))
    assert body == b"hello"
    assert etag == '"abc123"'
    assert lm == "Wed, 01 Jan 2026 00:00:00 GMT"


@pytest.mark.asyncio
async def test_etag_persists_past_ttl(temp_db: Path):
    """get_validators_any_age returns ETag even after TTL expiry — needed for 304 path."""
    cache = Cache(temp_db)
    await cache.set("k1", b"hello", kind="landing", etag='"abc123"')
    await asyncio.sleep(0.02)
    etag, _ = await cache.get_validators_any_age("k1")
    assert etag == '"abc123"'


@pytest.mark.asyncio
async def test_touch_bumps_freshness(temp_db: Path):
    """touch() updates cached_at without changing payload."""
    cache = Cache(temp_db)
    await cache.set("k1", b"hello", kind="landing")
    # Force the cached_at to be old
    await asyncio.sleep(0.05)
    got_before = await cache.get("k1", ttl=timedelta(milliseconds=10))
    assert got_before is None, "expected stale"
    await cache.touch("k1")
    got_after = await cache.get("k1", ttl=timedelta(hours=1))
    assert got_after == b"hello"


@pytest.mark.asyncio
async def test_get_validators_missing_key_returns_none_pair(temp_db: Path):
    cache = Cache(temp_db)
    etag, lm = await cache.get_validators_any_age("missing")
    assert etag is None and lm is None


@pytest.mark.asyncio
async def test_set_without_validators_stores_nulls(temp_db: Path):
    """Calling set without etag/last_modified stores NULLs, not empty strings."""
    cache = Cache(temp_db)
    await cache.set("k1", b"hello", kind="data")
    etag, lm = await cache.get_validators_any_age("k1")
    assert etag is None
    assert lm is None


@pytest.mark.asyncio
async def test_cached_at_set_on_write(temp_db: Path):
    cache = Cache(temp_db)
    before = time.time()
    await cache.set("k1", b"hello", kind="data")
    after = time.time()
    ts = await cache.get_cached_at("k1")
    assert ts is not None
    assert before - 1 <= ts <= after + 1


@pytest.mark.asyncio
async def test_cached_at_missing_returns_none(temp_db: Path):
    cache = Cache(temp_db)
    assert await cache.get_cached_at("missing") is None


@pytest.mark.asyncio
async def test_overwrite_with_new_etag(temp_db: Path):
    """Set then re-set with a new etag swaps the etag too."""
    cache = Cache(temp_db)
    await cache.set("k1", b"v1", kind="landing", etag='"v1"')
    await cache.set("k1", b"v2", kind="landing", etag='"v2"')
    body, etag, _ = await cache.get_with_validators("k1", ttl=timedelta(hours=1))
    assert body == b"v2"
    assert etag == '"v2"'
