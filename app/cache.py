from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class FetchResult(Generic[T]):
    data: T
    fetched_at: datetime
    cached: bool


@dataclass(frozen=True)
class _CacheEntry(Generic[T]):
    data: T
    fetched_at: datetime
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, _CacheEntry[T]] = {}

    def get(self, key: str) -> FetchResult[T] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= monotonic():
            self._entries.pop(key, None)
            return None
        return FetchResult(entry.data, entry.fetched_at, True)

    def put(self, key: str, data: T, fetched_at: datetime) -> FetchResult[T]:
        if self._ttl_seconds > 0:
            self._entries[key] = _CacheEntry(
                data=data,
                fetched_at=fetched_at,
                expires_at=monotonic() + self._ttl_seconds,
            )
        return FetchResult(data, fetched_at, False)
