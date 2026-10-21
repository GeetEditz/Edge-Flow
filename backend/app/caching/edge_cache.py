"""Per-edge cache front-end: policy selection, request coalescing and counters.

Cache status of a lookup (also sent to clients in the X-Cache header):
  HIT        served from this edge's cache
  MISS       not cached; this request fetched the object from the origin
  COALESCED  not cached, but an identical origin fetch was already in flight; this request
             waited for it instead of issuing a duplicate fetch
  BYPASS     caching disabled (policy "none"); every request is fetched from the origin

Eligible cache lookups = HIT + MISS + COALESCED (BYPASS is excluded: no lookup happens).
Cache-hit ratio = HIT / eligible lookups.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from .policies import BaseCache, Clock, Eviction, make_policy


@dataclass
class CachedObject:
    key: str
    value: bytes
    content_type: str
    etag: str


Fetcher = Callable[[str], Awaitable[CachedObject]]
CACHE_POLICIES = ("lru", "lfu", "ttl", "none")


class EdgeCache:
    def __init__(self, policy: str = "lru", capacity_bytes: int = 8 * 1024 * 1024,
                 ttl_seconds: float | None = 30.0, clock: Clock = time.monotonic):
        self._clock = clock
        self._inflight: dict[str, asyncio.Future] = {}
        self.evictions: deque[Eviction] = deque(maxlen=200)
        self.configure(policy, capacity_bytes, ttl_seconds)
        self.reset_counters()

    def configure(self, policy: str, capacity_bytes: int, ttl_seconds: float | None) -> None:
        """Switch policy/capacity. The cache restarts cold (contents are discarded)."""
        if policy not in CACHE_POLICIES:
            raise ValueError(f"unknown cache policy {policy!r}")
        self.policy = policy
        self.capacity = capacity_bytes
        self.ttl = ttl_seconds
        self._store: BaseCache | None = (None if policy == "none"
                                         else make_policy(policy, capacity_bytes, ttl_seconds, self._clock))

    def reset_counters(self) -> None:
        self.hits = self.misses = self.coalesced = self.bypass = 0
        self.origin_fetches = self.origin_bytes = self.origin_errors = 0
        self.evictions_capacity = self.expirations = self.invalidations = self.oversize_rejections = 0

    def _record(self, evs: list[Eviction]) -> None:
        for ev in evs:
            self.evictions.append(ev)
            if ev.reason == "capacity":
                self.evictions_capacity += 1
            elif ev.reason == "expired":
                self.expirations += 1
            else:
                self.invalidations += 1

    async def get_or_fetch(self, key: str, fetch: Fetcher) -> tuple[CachedObject, str]:
        if self._store is None:
            self.bypass += 1
            obj = await self._fetch(key, fetch)
            return obj, "BYPASS"
        entry, evs = self._store.get(key)
        self._record(evs)
        if entry is not None:
            self.hits += 1
            return CachedObject(key, entry.value, entry.content_type, entry.etag), "HIT"
        pending = self._inflight.get(key)
        if pending is not None:
            self.coalesced += 1
            return await asyncio.shield(pending), "COALESCED"
        self.misses += 1
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            obj = await self._fetch(key, fetch)
        except BaseException as exc:
            fut.set_exception(exc if isinstance(exc, Exception) else RuntimeError("fetch cancelled"))
            fut.exception()  # mark retrieved: followers (if any) still receive it
            raise
        finally:
            self._inflight.pop(key, None)
        if self._store is not None:  # policy may have been switched to "none" mid-fetch
            res = self._store.put(key, obj.value, obj.content_type, obj.etag)
            self._record(res.evicted)
            if not res.accepted:
                self.oversize_rejections += 1
        fut.set_result(obj)
        return obj, "MISS"

    async def _fetch(self, key: str, fetch: Fetcher) -> CachedObject:
        try:
            obj = await fetch(key)
        except Exception:
            self.origin_errors += 1
            raise
        self.origin_fetches += 1
        self.origin_bytes += len(obj.value)
        return obj

    def invalidate(self, key: str) -> bool:
        if self._store is None:
            return False
        ev = self._store.invalidate(key)
        if ev:
            self._record([ev])
        return ev is not None

    def clear(self) -> int:
        return self._store.clear() if self._store else 0

    def warm(self, key: str, obj: CachedObject) -> bool:
        if self._store is None:
            return False
        res = self._store.put(key, obj.value, obj.content_type, obj.etag)
        self._record(res.evicted)
        return res.accepted

    def entries(self) -> list[dict]:
        if self._store is None:
            return []
        now = self._clock()
        out = []
        for e in self._store.entries():
            out.append({"object_id": e.key, "size": e.size, "content_type": e.content_type,
                        "frequency": e.frequency, "age_s": round(now - e.created_at, 2),
                        "idle_s": round(now - e.last_access, 2),
                        "ttl_remaining_s": None if e.expires_at is None else round(max(0.0, e.expires_at - now), 2),
                        "expired": e.expired(now)})
        return out

    def stats(self) -> dict:
        eligible = self.hits + self.misses + self.coalesced
        return {
            "policy": self.policy, "capacity_bytes": self.capacity, "ttl_seconds": self.ttl,
            "used_bytes": self._store.used_bytes if self._store else 0,
            "objects": len(self._store) if self._store else 0,
            "hits": self.hits, "misses": self.misses, "coalesced": self.coalesced, "bypass": self.bypass,
            "eligible_lookups": eligible, "hit_ratio": (self.hits / eligible) if eligible else None,
            "origin_fetches": self.origin_fetches, "origin_bytes": self.origin_bytes,
            "origin_errors": self.origin_errors, "evictions": self.evictions_capacity,
            "expirations": self.expirations, "invalidations": self.invalidations,
            "oversize_rejections": self.oversize_rejections, "inflight_fetches": len(self._inflight),
        }
