"""Tests for DedupCache."""

import tempfile

import pytest

from ccbot.core.dedup import DedupCache


class TestDedupCache:
    """Test cases for DedupCache."""

    def test_check_new_key(self):
        """New key should return False (not duplicate)."""
        cache = DedupCache()
        assert cache.check("msg_1") is False
        assert cache.check("msg_1") is True  # Now it's duplicate

    def test_check_duplicate(self):
        """Duplicate key should return True."""
        cache = DedupCache()
        cache.check("msg_1")
        assert cache.check("msg_1") is True

    def test_peek_without_adding(self):
        """Peek should not add key."""
        cache = DedupCache()
        assert cache.peek("msg_1") is False
        assert cache.peek("msg_1") is False  # Still not present

    def test_max_size_eviction(self):
        """Old entries should be evicted when max size reached."""
        cache = DedupCache(max_size=3)
        cache.check("msg_1")
        cache.check("msg_2")
        cache.check("msg_3")
        cache.check("msg_4")  # Should evict msg_1

        assert cache.peek("msg_1") is False  # Evicted
        assert cache.peek("msg_2") is True
        assert cache.peek("msg_3") is True
        assert cache.peek("msg_4") is True

    @pytest.mark.asyncio
    async def test_persist_and_load(self):
        """Cache should persist to disk and load back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DedupCache()
            cache.check("msg_1")
            cache.check("msg_2")

            # Persist
            await cache.persist(tmpdir, "test")

            # Load into new cache
            cache2 = DedupCache()
            loaded = await cache2.load(tmpdir, "test")

            assert loaded == 2
            assert cache2.peek("msg_1") is True
            assert cache2.peek("msg_2") is True

    @pytest.mark.asyncio
    async def test_load_nonexistent(self):
        """Loading non-existent file should return 0."""
        cache = DedupCache()
        loaded = await cache.load("/nonexistent/path", "test")
        assert loaded == 0

    def test_zero_ttl_no_expiration(self):
        """TTL of 0 means no expiration."""
        cache = DedupCache(ttl_ms=0)
        cache.check("msg_1")

        # Should never expire
        assert cache.peek("msg_1") is True

        # Cleanup should not remove anything
        removed = cache._cleanup_expired()
        assert removed == 0
        assert len(cache) == 1
