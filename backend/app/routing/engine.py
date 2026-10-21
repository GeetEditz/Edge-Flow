"""Routing engine: health filtering + pluggable strategy + decision log."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field

from .strategies import (STRATEGY_NAMES, EdgeView, FailoverStrategy, HybridStrategy, LeastLoadedStrategy,
                         LowestLatencyStrategy, NearestStrategy, RandomStrategy, Strategy)


@dataclass
class RoutingDecision:
    edge_id: str | None
    strategy: str
    reason: str
    scores: dict[str, float] | None = None
    candidates: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["scores"]:
            d["scores"] = {k: (None if v == float("inf") else round(v, 4)) for k, v in d["scores"].items()}
        return d


class RoutingEngine:
    def __init__(self, strategy: str = "hybrid", seed: int | None = None, weights: dict | None = None):
        self.hybrid = HybridStrategy(**(weights or {}))
        self.random = RandomStrategy(seed)
        self._strategies: dict[str, Strategy] = {
            "random": self.random, "nearest": NearestStrategy(), "lowest_latency": LowestLatencyStrategy(),
            "least_loaded": LeastLoadedStrategy(), "hybrid": self.hybrid, "failover": FailoverStrategy(),
        }
        self.set_strategy(strategy)
        self.recent: deque[RoutingDecision] = deque(maxlen=200)
        self.counts: Counter[str] = Counter()

    @property
    def strategies(self) -> list[dict]:
        return [{"name": n, "label": self._strategies[n].label} for n in STRATEGY_NAMES]

    def set_strategy(self, name: str, weights: dict | None = None) -> None:
        if name not in self._strategies:
            raise ValueError(f"unknown routing strategy {name!r}; choose from {', '.join(STRATEGY_NAMES)}")
        if weights:
            self.hybrid.set_weights(weights.get("latency_weight", 0), weights.get("load_weight", 0),
                                    weights.get("packet_loss_weight", 0))
        self.strategy = name

    def config(self) -> dict:
        w = self.hybrid.weights
        return {"strategy": self.strategy, "weights": {"latency_weight": w["latency"], "load_weight": w["load"],
                                                       "packet_loss_weight": w["packet_loss"]}}

    def select(self, edges: list[EdgeView], strategy: str | None = None, record: bool = True) -> RoutingDecision:
        name = strategy or self.strategy
        healthy = [e for e in edges if e.healthy]
        excluded = sorted(e.edge_id for e in edges if not e.healthy)
        if not healthy:
            d = RoutingDecision(None, name, "no healthy edge available", None, [], excluded)
        else:
            edge, reason, scores = self._strategies[name].choose(healthy)
            if excluded:
                reason += f"; excluded unhealthy: {', '.join(excluded)}"
            d = RoutingDecision(edge.edge_id, name, reason, scores, sorted(e.edge_id for e in healthy), excluded)
        if record:
            self.recent.append(d)
            self.counts[d.edge_id or "none"] += 1
        return d
