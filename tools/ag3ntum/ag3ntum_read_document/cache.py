"""
Cache manager for ReadDocument tool.

Provides file-based caching for expensive operations (PDF extraction, Office conversion).
Uses content hash + params for cache keys.
"""
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CacheConfig, get_config
from .exceptions import CacheError

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached extraction result."""

    content: str
    metadata: dict
    created_at: float
    file_hash: str
    params_hash: str


@dataclass
class CacheStats:
    """Cache statistics."""

    total_entries: int
    total_size_bytes: int
    oldest_entry_age_days: float
    hits: int
    misses: int


class CacheManager:
    """
    File-based cache for document extraction results.

    Structure:
        {cache_dir}/
        ├── index.json           # Quick lookup index
        ├── pdfs/{hash_prefix}/{hash}.json
        ├── office/{hash_prefix}/{hash}.json
        └── archives/{hash_prefix}/{hash}.json
    """

    def __init__(self, config: CacheConfig | None = None):
        """
        Initialize cache manager.

        Args:
            config: Cache configuration (uses global if not provided)
        """
        self.config = config or get_config().cache
        self.cache_dir = self.config.directory_path
        self.hits = 0
        self.misses = 0
        self._index: dict[str, dict] | None = None

        if self.config.enabled:
            self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create cache directory structure."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            for subdir in ("pdfs", "office", "archives"):
                (self.cache_dir / subdir).mkdir(exist_ok=True)
            logger.debug(f"Cache directory ready: {self.cache_dir}")
        except Exception as e:
            logger.warning(f"Failed to create cache directory: {e}")

    def _get_index_path(self) -> Path:
        """Get path to cache index file."""
        return self.cache_dir / "index.json"

    def _load_index(self) -> dict[str, dict]:
        """Load cache index from disk."""
        if self._index is not None:
            return self._index

        index_path = self._get_index_path()
        if not index_path.exists():
            self._index = {}
            return self._index

        try:
            with open(index_path) as f:
                self._index = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load cache index: {e}")
            self._index = {}

        return self._index

    def _save_index(self) -> None:
        """Save cache index to disk."""
        if self._index is None:
            return

        try:
            index_path = self._get_index_path()
            with open(index_path, "w") as f:
                json.dump(self._index, f)
        except Exception as e:
            logger.warning(f"Failed to save cache index: {e}")

    def _get_cache_path(self, category: str, cache_key: str) -> Path:
        """Get path for a cache entry."""
        prefix = cache_key[:2]  # First 2 chars as subdirectory
        return self.cache_dir / category / prefix / f"{cache_key}.json"

    def compute_cache_key(self, file_path: Path, params: dict) -> str:
        """
        Compute deterministic cache key for a file + params combination.

        Uses:
        - First 64KB of file content
        - File size
        - File modification time
        - Relevant params

        Args:
            file_path: Path to the file
            params: Extraction parameters

        Returns:
            SHA256 hash as hex string
        """
        hasher = hashlib.sha256()

        try:
            # File content sample
            with open(file_path, "rb") as f:
                content_sample = f.read(65536)  # First 64KB
            hasher.update(content_sample)

            # File stats
            stat = file_path.stat()
            hasher.update(str(stat.st_size).encode())
            hasher.update(str(stat.st_mtime_ns).encode())

            # Relevant params (sorted for consistency)
            # Filter to only cache-relevant params
            # Note: All params that affect extracted content MUST be included here
            # - pages: PDF page selection
            # - sheet/rows/columns: Tabular data selection (not cached, but included for safety)
            # - mode/archive_path/pattern: Archive extraction parameters
            # - include_metadata: Affects output formatting for PDF/Office
            cache_params = {
                k: v
                for k, v in params.items()
                if k in ("pages", "sheet", "rows", "columns", "mode", "pattern", "archive_path", "include_metadata")
            }
            param_str = json.dumps(cache_params, sort_keys=True)
            hasher.update(param_str.encode())

            return hasher.hexdigest()

        except Exception as e:
            logger.warning(f"Failed to compute cache key: {e}")
            # Return a unique key that won't match anything
            return hashlib.sha256(os.urandom(32)).hexdigest()

    def get(self, category: str, cache_key: str) -> CacheEntry | None:
        """
        Retrieve a cached entry.

        Args:
            category: Cache category (pdfs, office, archives)
            cache_key: Cache key from compute_cache_key()

        Returns:
            CacheEntry if found and valid, None otherwise
        """
        if not self.config.enabled:
            return None

        cache_path = self._get_cache_path(category, cache_key)

        if not cache_path.exists():
            self.misses += 1
            return None

        try:
            with open(cache_path) as f:
                data = json.load(f)

            # Check TTL
            created_at = data.get("created_at", 0)
            age_days = (time.time() - created_at) / 86400
            if age_days > self.config.ttl_days:
                logger.debug(f"Cache entry expired: {cache_key} (age: {age_days:.1f} days)")
                self._remove_entry(category, cache_key)
                self.misses += 1
                return None

            self.hits += 1
            logger.debug(f"Cache hit: {category}/{cache_key}")

            return CacheEntry(
                content=data["content"],
                metadata=data.get("metadata", {}),
                created_at=created_at,
                file_hash=data.get("file_hash", ""),
                params_hash=data.get("params_hash", ""),
            )

        except Exception as e:
            logger.warning(f"Failed to read cache entry: {e}")
            self.misses += 1
            return None

    def put(
        self,
        category: str,
        cache_key: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """
        Store an entry in the cache.

        Args:
            category: Cache category
            cache_key: Cache key
            content: Extracted content to cache
            metadata: Optional metadata to cache
        """
        if not self.config.enabled:
            return

        cache_path = self._get_cache_path(category, cache_key)

        try:
            # Ensure directory exists
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "content": content,
                "metadata": metadata or {},
                "created_at": time.time(),
                "file_hash": cache_key[:32],
                "params_hash": cache_key[32:],
            }

            with open(cache_path, "w") as f:
                json.dump(data, f)

            # Update index
            index = self._load_index()
            index[cache_key] = {
                "category": category,
                "created_at": data["created_at"],
                "size": len(content),
            }
            self._save_index()

            logger.debug(f"Cached: {category}/{cache_key} ({len(content)} chars)")

            # Check if cleanup needed
            self._maybe_cleanup()

        except Exception as e:
            logger.warning(f"Failed to write cache entry: {e}")

    def _remove_entry(self, category: str, cache_key: str) -> None:
        """Remove a cache entry."""
        try:
            cache_path = self._get_cache_path(category, cache_key)
            if cache_path.exists():
                cache_path.unlink()

            index = self._load_index()
            if cache_key in index:
                del index[cache_key]
                self._save_index()

        except Exception as e:
            logger.warning(f"Failed to remove cache entry: {e}")

    def _maybe_cleanup(self) -> None:
        """Check cache size and cleanup if needed."""
        try:
            # Calculate total size
            total_size = sum(
                f.stat().st_size
                for f in self.cache_dir.rglob("*.json")
                if f.is_file()
            )

            max_size = self.config.max_size_mb * 1024 * 1024

            if total_size > max_size:
                self._cleanup_lru(total_size - max_size)

        except Exception as e:
            logger.warning(f"Cache cleanup check failed: {e}")

    def _cleanup_lru(self, bytes_to_free: int) -> None:
        """Remove oldest entries until bytes_to_free is achieved."""
        logger.info(f"Cache cleanup: need to free {bytes_to_free} bytes")

        index = self._load_index()
        if not index:
            return

        # Sort entries by creation time (oldest first)
        sorted_entries = sorted(
            index.items(),
            key=lambda x: x[1].get("created_at", 0),
        )

        freed = 0
        removed_count = 0

        for cache_key, entry_info in sorted_entries:
            if freed >= bytes_to_free:
                break

            category = entry_info.get("category", "pdfs")
            cache_path = self._get_cache_path(category, cache_key)

            if cache_path.exists():
                size = cache_path.stat().st_size
                cache_path.unlink()
                freed += size
                removed_count += 1

            del index[cache_key]

        self._save_index()
        logger.info(f"Cache cleanup: removed {removed_count} entries, freed {freed} bytes")

    def clear(self) -> None:
        """Clear all cache entries."""
        if not self.cache_dir.exists():
            return

        try:
            for json_file in self.cache_dir.rglob("*.json"):
                json_file.unlink()

            self._index = {}
            self._save_index()
            logger.info("Cache cleared")

        except Exception as e:
            logger.warning(f"Failed to clear cache: {e}")

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        total_entries = 0
        total_size = 0
        oldest_age = 0.0

        now = time.time()

        try:
            for json_file in self.cache_dir.rglob("*.json"):
                if json_file.name == "index.json":
                    continue
                if json_file.is_file():
                    total_entries += 1
                    total_size += json_file.stat().st_size

                    try:
                        with open(json_file) as f:
                            data = json.load(f)
                        created_at = data.get("created_at", now)
                        age_days = (now - created_at) / 86400
                        oldest_age = max(oldest_age, age_days)
                    except Exception:
                        pass

        except Exception as e:
            logger.warning(f"Failed to compute cache stats: {e}")

        return CacheStats(
            total_entries=total_entries,
            total_size_bytes=total_size,
            oldest_entry_age_days=oldest_age,
            hits=self.hits,
            misses=self.misses,
        )


# Global cache manager instance
_cache_manager: CacheManager | None = None


def get_cache_manager() -> CacheManager:
    """Get the global cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager
