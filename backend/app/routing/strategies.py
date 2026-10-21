"""Edge-selection strategies.

Every strategy receives only HEALTHY candidates (health filtering happens in the engine)
and returns (edge, reason, scores). Ties are always broken deterministically by
(priority, edge_id) so identical inputs give identical outputs.

Metric semantics (see EdgeView):
  latency_ms    measured — EWMA of health-probe round-trip times
  load          measured — max(edge-reported active, client in-flight) / concurrency limit
  packet_loss   estimated — EWMA of simulated lost/sent segments reported by the edge
  distance_km   configured — static simulated distance from the client region
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass

INF = float("inf")


@dataclass
class EdgeView:
    edge_id: str
    host: str
    port: int
    healthy: bool = True
    latency_ms: float | None = None
    load: float | None = None
    packet_loss: float | None = None
    distance_km: float | None = None
    priority: int = 100

    @property
    def tiebreak(self) -> tuple[int, str]:
        return self.priority, self.edge_id


def normalize(values: dict[str, float | None]) -> dict[str, float]:
    """Min-max normalise to [0, 1]. Missing values count as worst (1.0); if all known values
    are equal (including all zero) they normalise to 0.0 so the metric does not discriminate."""
    known = [v for v in values.values() if v is not None]
    if not known:
        return {k: 0.0 for k in values}
    lo, hi = min(known), max(known)
    span = hi - lo
    return {k: (1.0 if v is None else (0.0 if span == 0 else (v - lo) / span)) for k, v in values.items()}


def _fmt(v: float | None, unit: str = "", digits: int = 1) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}{unit}"


def _argmin(edges: list[EdgeView], key) -> EdgeView:
    return min(edges, key=lambda e: (key(e), e.tiebreak))


class Strategy(ABC):
    name: str = ""
    label: str = ""

    @abstractmethod
    def choose(self, edges: list[EdgeView]) -> tuple[EdgeView, str, dict[str, float] | None]: ...


class RandomStrategy(Strategy):
    name, label = "random", "Random"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def reseed(self, seed: int | None) -> None:
        self.rng = random.Random(seed)

    def choose(self, edges):
        ordered = sorted(edges, key=lambda e: e.tiebreak)
        e = self.rng.choice(ordered)
        return e, f"uniform random choice among {len(ordered)} healthy edge(s)", None


class NearestStrategy(Strategy):
    name, label = "nearest", "Nearest replica"

    def choose(self, edges):
        e = _argmin(edges, lambda x: INF if x.distance_km is None else x.distance_km)
        return e, f"nearest configured replica ({_fmt(e.distance_km, ' km', 0)} from client)", \
            {x.edge_id: x.distance_km if x.distance_km is not None else INF for x in edges}


class LowestLatencyStrategy(Strategy):
    name, label = "lowest_latency", "Lowest latency"

    def choose(self, edges):
        if all(x.latency_ms is None for x in edges):
            e, _, s = NearestStrategy().choose(edges)
            return e, "no latency samples yet; fell back to nearest configured replica", s
        e = _argmin(edges, lambda x: INF if x.latency_ms is None else x.latency_ms)
        return e, f"lowest measured probe RTT ({_fmt(e.latency_ms, ' ms')})", \
            {x.edge_id: x.latency_ms if x.latency_ms is not None else INF for x in edges}


class LeastLoadedStrategy(Strategy):
    name, label = "least_loaded", "Least loaded"

    def choose(self, edges):
        e = _argmin(edges, lambda x: INF if x.load is None else x.load)
        return e, f"lowest load ({_fmt(None if e.load is None else e.load * 100, '%', 0)} of capacity)", \
            {x.edge_id: x.load if x.load is not None else INF for x in edges}


class HybridStrategy(Strategy):
    """score = w_lat * norm(latency) + w_load * norm(load) + w_loss * norm(packet_loss); lowest wins."""
    name, label = "hybrid", "Hybrid (latency + load + loss)"

    def __init__(self, latency_weight: float = 0.5, load_weight: float = 0.4, packet_loss_weight: float = 0.1):
        self.set_weights(latency_weight, load_weight, packet_loss_weight)

    def set_weights(self, latency_weight: float, load_weight: float, packet_loss_weight: float) -> None:
        ws = (latency_weight, load_weight, packet_loss_weight)
        if any(w < 0 for w in ws):
            raise ValueError("weights must be non-negative")
        total = sum(ws)
        if total <= 0:
            raise ValueError("at least one weight must be positive")
        self.weights = {"latency": ws[0] / total, "load": ws[1] / total, "packet_loss": ws[2] / total}

    def scores(self, edges: list[EdgeView]) -> dict[str, float]:
        nl = normalize({e.edge_id: e.latency_ms for e in edges})
        nd = normalize({e.edge_id: e.load for e in edges})
        np_ = normalize({e.edge_id: e.packet_loss for e in edges})
        w = self.weights
        return {e.edge_id: w["latency"] * nl[e.edge_id] + w["load"] * nd[e.edge_id] + w["packet_loss"] * np_[e.edge_id]
                for e in edges}

    def choose(self, edges):
        s = self.scores(edges)
        e = _argmin(edges, lambda x: round(s[x.edge_id], 9))
        return e, (f"lowest hybrid score {s[e.edge_id]:.3f} (RTT {_fmt(e.latency_ms, ' ms')}, "
                   f"load {_fmt(None if e.load is None else e.load * 100, '%', 0)}, "
                   f"loss {_fmt(None if e.packet_loss is None else e.packet_loss * 100, '%', 2)})"), s


class FailoverStrategy(Strategy):
    """Health-aware fallback: always use the highest-priority healthy edge (primary/backup)."""
    name, label = "failover", "Health-aware failover"

    def choose(self, edges):
        e = min(edges, key=lambda x: x.tiebreak)
        return e, f"highest-priority healthy edge (priority {e.priority})", {x.edge_id: float(x.priority) for x in edges}


STRATEGY_NAMES = ("random", "nearest", "lowest_latency", "least_loaded", "hybrid", "failover")
