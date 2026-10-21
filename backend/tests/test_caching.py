import asyncio

import pytest

from app.caching.edge_cache import CachedObject, EdgeCache
from app.caching.policies import LFUCache, LRUCache, TTLCache, make_policy


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def b(n: int) -> bytes:
    return b"x" * n


def test_lru_evicts_least_recently_used():
    c = LRUCache(300)
    for k in "abc":
        assert c.put(k, b(100)).accepted
    c.get("a")  # a becomes most recent; b is now LRU
    res = c.put("d", b(100))
    assert [e.key for e in res.evicted] == ["b"]
    assert "b" not in c and {"a", "c", "d"} <= {e.key for e in c.entries()}


def test_lru_evicts_multiple_to_fit_large_object():
    c = LRUCache(300)
    for k in "abc":
        c.put(k, b(100))
    res = c.put("big", b(250))
    assert [e.key for e in res.evicted] == ["a", "b", "c"]
    assert c.used_bytes == 250


def test_lfu_evicts_least_frequent_then_least_recent():
    c = LFUCache(300)
    for k in "abc":
        c.put(k, b(100))
    for _ in range(3):
        c.get("a")
    c.get("b")
    # frequencies: a=4, b=2, c=1 -> c evicted
    assert [e.key for e in c.put("d", b(100)).evicted] == ["c"]
    # now d=1, b=2 -> d evicted next
    assert [e.key for e in c.put("e", b(100)).evicted] == ["d"]
    # tie between e(1) and new f(1): e is older -> evicted
    c.get("b")
    assert [e.key for e in c.put("f", b(100)).evicted] == ["e"]


def test_lfu_min_frequency_tracking_after_invalidation():
    c = LFUCache(300)
    c.put("a", b(100))
    c.put("b", b(100))
    c.get("a")
    c.get("b")
    c.invalidate("a")
    c.put("c", b(100))
    c.put("d", b(100))
    assert c.put("e", b(100)).evicted[0].key == "c"  # c freq 1 and older than d


def test_ttl_entries_expire_and_are_not_served():
    clock = FakeClock()
    c = TTLCache(1000, ttl_seconds=10, clock=clock)
    c.put("a", b(10))
    clock.t += 9.9
    assert c.get("a")[0] is not None
    clock.t += 0.2
    entry, evs = c.get("a")
    assert entry is None and evs[0].reason == "expired"
    assert "a" not in c and c.used_bytes == 0


def test_ttl_evicts_soonest_expiring_under_pressure():
    clock = FakeClock()
    c = TTLCache(200, ttl_seconds=10, clock=clock)
    c.put("old", b(100))
    clock.t += 5
    c.put("new", b(100))
    assert [e.key for e in c.put("x", b(100)).evicted] == ["old"]


def test_expired_entries_purged_before_capacity_eviction():
    clock = FakeClock()
    c = TTLCache(200, ttl_seconds=10, clock=clock)
    c.put("a", b(100))
    clock.t += 6
    c.put("b", b(100))
    clock.t += 5  # a expired, b alive
    res = c.put("c", b(100))
    assert [(e.key, e.reason) for e in res.evicted] == [("a", "expired")]


@pytest.mark.parametrize("policy", ["lru", "lfu", "ttl"])
def test_capacity_never_exceeded_and_oversize_rejected(policy):
    c = make_policy(policy, 1000, 30)
    import random
    rng = random.Random(1)
    for i in range(500):
        c.put(f"k{rng.randrange(50)}", b(rng.randrange(1, 400)))
        assert c.used_bytes <= c.capacity
        assert c.used_bytes == sum(e.size for e in c.entries())
    before = c.used_bytes
    res = c.put("huge", b(1001))
    assert not res.accepted and res.evicted == [] and c.used_bytes == before


def test_replacing_existing_key_updates_size():
    c = LRUCache(500)
    c.put("a", b(100))
    c.put("a", b(300))
    assert c.used_bytes == 300 and len(c) == 1


def test_invalidation_and_clear():
    c = LRUCache(500)
    c.put("a", b(100))
    c.put("b", b(100))
    ev = c.invalidate("a")
    assert ev.reason == "invalidated" and "a" not in c
    assert c.invalidate("missing") is None
    assert c.clear() == 1 and c.used_bytes == 0


def test_invalid_configuration_rejected():
    with pytest.raises(ValueError):
        LRUCache(0)
    with pytest.raises(ValueError):
        make_policy("fifo", 100)


# ------------------------------------------------------------------ EdgeCache (async)

def obj(key: str, n: int = 100) -> CachedObject:
    return CachedObject(key, b(n), "application/octet-stream", "etag")


async def test_edge_cache_miss_then_hit_counts():
    cache = EdgeCache("lru", 10_000)
    calls = []

    async def fetch(k):
        calls.append(k)
        return obj(k)

    assert (await cache.get_or_fetch("a", fetch))[1] == "MISS"
    assert (await cache.get_or_fetch("a", fetch))[1] == "HIT"
    s = cache.stats()
    assert calls == ["a"] and s["hits"] == 1 and s["misses"] == 1 and s["hit_ratio"] == 0.5
    assert s["origin_fetches"] == 1 and s["origin_bytes"] == 100


async def test_concurrent_misses_are_coalesced_into_one_origin_fetch():
    cache = EdgeCache("lru", 10_000)
    calls = 0
    gate = asyncio.Event()

    async def fetch(k):
        nonlocal calls
        calls += 1
        await gate.wait()
        return obj(k)

    tasks = [asyncio.create_task(cache.get_or_fetch("a", fetch)) for _ in range(20)]
    await asyncio.sleep(0.01)
    gate.set()
    results = await asyncio.gather(*tasks)
    statuses = sorted(s for _, s in results)
    assert calls == 1
    assert statuses.count("MISS") == 1 and statuses.count("COALESCED") == 19
    assert all(o.value == b(100) for o, _ in results)
    assert cache.stats()["eligible_lookups"] == 20


async def test_coalesced_waiters_receive_fetch_errors():
    cache = EdgeCache("lru", 10_000)
    gate = asyncio.Event()

    async def fetch(k):
        await gate.wait()
        raise RuntimeError("origin down")

    tasks = [asyncio.create_task(cache.get_or_fetch("a", fetch)) for _ in range(3)]
    await asyncio.sleep(0.01)
    gate.set()
    res = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in res)
    assert cache.stats()["origin_errors"] == 1 and cache.stats()["inflight_fetches"] == 0


async def test_disabled_cache_bypasses_every_request():
    cache = EdgeCache("none", 10_000)
    n = 0

    async def fetch(k):
        nonlocal n
        n += 1
        return obj(k)

    for _ in range(3):
        assert (await cache.get_or_fetch("a", fetch))[1] == "BYPASS"
    s = cache.stats()
    assert n == 3 and s["bypass"] == 3 and s["eligible_lookups"] == 0 and s["hit_ratio"] is None


async def test_edge_cache_oversize_object_served_but_not_cached():
    cache = EdgeCache("lru", 2048)

    async def fetch(k):
        return obj(k, 4096)

    o, st = await cache.get_or_fetch("big", fetch)
    assert st == "MISS" and len(o.value) == 4096
    assert cache.stats()["oversize_rejections"] == 1 and cache.stats()["used_bytes"] == 0
    assert (await cache.get_or_fetch("big", fetch))[1] == "MISS"
