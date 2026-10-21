"""Simulated network profile of the client <-> edge path (Local Mode only).

Every value here is SIMULATED and labelled as such in the API. The delays are applied by
really sleeping inside the edge before bytes are written to the TCP socket, so the
latency the client measures includes them, but they do not come from a physical network.

Model per response of S bytes:
  propagation  = base_latency_ms + uniform(-jitter, +jitter)          (one RTT, >= 0)
  transmission = S * 8 / (bandwidth_mbps * 1e6)
  segments     = ceil(S / 1460) + 1   (+1 for the request segment)
  lost         ~ Binomial(segments, packet_loss)
  loss penalty = lost * max(2 * base_latency_ms, 10 ms)   (fast-retransmit style recovery)

In Mininet Mode the profile is all zeros and real link shaping (tc/netem) is used instead.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass

MSS = 1460


@dataclass
class NetworkProfile:
    base_latency_ms: float = 20.0
    jitter_ms: float = 2.0
    bandwidth_mbps: float = 100.0
    packet_loss: float = 0.0

    def validate(self) -> "NetworkProfile":
        if not 0 <= self.base_latency_ms <= 5000:
            raise ValueError("base_latency_ms must be within 0..5000")
        if not 0 <= self.jitter_ms <= 1000:
            raise ValueError("jitter_ms must be within 0..1000")
        if not 0.1 <= self.bandwidth_mbps <= 100_000:
            raise ValueError("bandwidth_mbps must be within 0.1..100000")
        if not 0 <= self.packet_loss <= 0.5:
            raise ValueError("packet_loss must be within 0..0.5")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    def propagation_s(self, rng: random.Random) -> float:
        j = rng.uniform(-self.jitter_ms, self.jitter_ms) if self.jitter_ms else 0.0
        return max(0.0, self.base_latency_ms + j) / 1000

    def transmission_s(self, size: int) -> float:
        return size * 8 / (self.bandwidth_mbps * 1e6)

    def loss(self, size: int, rng: random.Random) -> tuple[int, int, float]:
        """Return (segments, lost_segments, penalty_seconds)."""
        segments = math.ceil(size / MSS) + 1
        if self.packet_loss <= 0:
            return segments, 0, 0.0
        lost = sum(1 for _ in range(segments) if rng.random() < self.packet_loss)
        return segments, lost, lost * max(2 * self.base_latency_ms, 10.0) / 1000
