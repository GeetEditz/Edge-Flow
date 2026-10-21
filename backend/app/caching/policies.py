"""Byte-capacity cache policies: LRU, LFU and TTL.

All policies share the same contract (BaseCache):
  * capacity is measured in bytes and is never exceeded
  * objects larger than the capacity are rejected (put returns accepted=False)
  * expired entries are never returned by get(); they are purged lazily on access and
    eagerly before every insertion
  * every removal is reported as an Eviction with a reason: capacity | expired | invalidated

The classes are synchronous and contain no awaits, so on a single asyncio event loop each
method call is atomic with respect to other coroutines. Thread-safety is not provided.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

Clock = Callable[[], float]


@dataclass
class CacheEntry:
    key: str
    value: bytes
    content_type: str = "application/octet-stream"
    etag: str = ""
    created_at: float = 0.0
    expires_at: float | None = None
    last_access: float = 0.0
    frequency: int = 1

    @property
    def size(self) -> int:
        return len(self.value)

    def expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at


@dataclass
class Eviction:
    key: str
    size: int
    reason: str  # capacity | expired | invalidated
    at: float = field(default_factory=time.time)


@dataclass
class PutResult:
    accepted: bool
    evicted: list[Eviction]
    reason: str = ""


class BaseCache(ABC):
    name = "base"

    def __init__(self, capacity_bytes: int, ttl_seconds: float | None = None, clock: Clock = time.monotonic):
        if capacity_bytes <= 0:
            raise ValueError("capacity_bytes must be positive")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.capacity = capacity_bytes
        self.ttl = ttl_seconds
        self.clock = clock
        self._entries: dict[str, CacheEntry] = {}
        self.used_bytes = 0

    # -- hooks implemented by each policy
    @abstractmethod
    def _touch(self, entry: CacheEntry) -> None: ...

    @abstractmethod
    def _track(self, entry: CacheEntry) -> None: ...

    @abstractmethod
    def _untrack(self, entry: CacheEntry) -> None: ...

    @abstractmethod
    def _victim(self) -> CacheEntry: ...

    # -- public API
    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        e = self._entries.get(key)
        return e is not None and not e.expired(self.clock())

    def entries(self) -> list[CacheEntry]:
        return list(self._entries.values())

    def get(self, key: str) -> tuple[CacheEntry | None, list[Eviction]]:
        e = self._entries.get(key)
        if e is None:
            return None, []
        now = self.clock()
        if e.expired(now):
            self._remove(e)
            return None, [Eviction(key, e.size, "expired")]
        e.last_access = now
        e.frequency += 1
        self._touch(e)
        return e, []

    def put(self, key: str, value: bytes, content_type: str = "application/octet-stream",
            etag: str = "") -> PutResult:
        size = len(value)
        if size > self.capacity:
            return PutResult(False, [], "object larger than cache capacity")
        evicted: list[Eviction] = []
        if key in self._entries:
            self._remove(self._entries[key])
        evicted += self.purge_expired()
        while self.used_bytes + size > self.capacity:
            v = self._victim()
            self._remove(v)
            evicted.append(Eviction(v.key, v.size, "capacity"))
        now = self.clock()
        e = CacheEntry(key, value, content_type, etag, now, now + self.ttl if self.ttl else None, now, 1)
        self._entries[key] = e
        self.used_bytes += size
        self._track(e)
        assert self.used_bytes <= self.capacity
        return PutResult(True, evicted)

    def invalidate(self, key: str) -> Eviction | None:
        e = self._entries.get(key)
        if e is None:
            return None
        self._remove(e)
        return Eviction(key, e.size, "invalidated")

    def purge_expired(self) -> list[Eviction]:
        now = self.clock()
        out = []
        for e in [e for e in self._entries.values() if e.expired(now)]:
            self._remove(e)
            out.append(Eviction(e.key, e.size, "expired"))
        return out

    def clear(self) -> int:
        n = len(self._entries)
        for e in list(self._entries.values()):
            self._remove(e)
        return n

    def _remove(self, e: CacheEntry) -> None:
        del self._entries[e.key]
        self.used_bytes -= e.size
        self._untrack(e)


class LRUCache(BaseCache):
    """Evicts the least recently used entry."""
    name = "lru"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._order: OrderedDict[str, None] = OrderedDict()

    def _touch(self, e): self._order.move_to_end(e.key)
    def _track(self, e): self._order[e.key] = None
    def _untrack(self, e): self._order.pop(e.key, None)
    def _victim(self): return self._entries[next(iter(self._order))]


class LFUCache(BaseCache):
    """Evicts the least frequently used entry; ties broken by least recent use (O(1))."""
    name = "lfu"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._buckets: dict[int, OrderedDict[str, None]] = {}
        self._freq_of: dict[str, int] = {}
        self._min = 0

    def _add(self, key: str, f: int) -> None:
        self._buckets.setdefault(f, OrderedDict())[key] = None
        self._freq_of[key] = f

    def _drop(self, key: str) -> int:
        f = self._freq_of.pop(key)
        b = self._buckets[f]
        del b[key]
        if not b:
            del self._buckets[f]
        return f

    def _touch(self, e):
        f = self._drop(e.key)
        self._add(e.key, f + 1)
        if f == self._min and f not in self._buckets:
            self._min = f + 1

    def _track(self, e):
        self._add(e.key, 1)
        self._min = 1

    def _untrack(self, e):
        if e.key in self._freq_of:
            self._drop(e.key)
            if self._buckets and self._min not in self._buckets:
                self._min = min(self._buckets)

    def _victim(self):
        return self._entries[next(iter(self._buckets[self._min]))]


class TTLCache(BaseCache):
    """Entries expire ttl seconds after insertion; under pressure evicts the soonest-to-expire."""
    name = "ttl"

    def __init__(self, capacity_bytes: int, ttl_seconds: float | None = 30.0, clock: Clock = time.monotonic):
        super().__init__(capacity_bytes, ttl_seconds or 30.0, clock)

    def _touch(self, e): pass
    def _track(self, e): pass
    def _untrack(self, e): pass

    def _victim(self):
        return min(self._entries.values(), key=lambda e: (e.expires_at, e.key))


POLICIES: dict[str, type[BaseCache]] = {"lru": LRUCache, "lfu": LFUCache, "ttl": TTLCache}


def make_policy(name: str, capacity_bytes: int, ttl_seconds: float | None = None,
                clock: Clock = time.monotonic) -> BaseCache:
    try:
        cls = POLICIES[name]
    except KeyError:
        raise ValueError(f"unknown cache policy {name!r}") from None
    if cls is TTLCache:
        return cls(capacity_bytes, ttl_seconds, clock)
    return cls(capacity_bytes, None, clock)
