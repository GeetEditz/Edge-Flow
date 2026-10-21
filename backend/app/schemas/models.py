"""Pydantic request models for the control-plane API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.caching.edge_cache import CACHE_POLICIES
from app.config import CDN_HOSTNAME
from app.core.objects import valid_object_id
from app.routing.strategies import STRATEGY_NAMES

StrategyName = Literal["random", "nearest", "lowest_latency", "least_loaded", "hybrid", "failover"]
PolicyName = Literal["lru", "lfu", "ttl", "none"]


class Weights(BaseModel):
    latency_weight: float = Field(0.5, ge=0, le=1000)
    load_weight: float = Field(0.4, ge=0, le=1000)
    packet_loss_weight: float = Field(0.1, ge=0, le=1000)

    @model_validator(mode="after")
    def _positive(self):
        if self.latency_weight + self.load_weight + self.packet_loss_weight <= 0:
            raise ValueError("at least one weight must be positive")
        return self


class RoutingUpdate(BaseModel):
    strategy: StrategyName
    weights: Weights | None = None


class CachePolicyUpdate(BaseModel):
    policy: PolicyName
    capacity_bytes: int = Field(4 * 1024 * 1024, ge=1024, le=1024 ** 3)
    ttl_seconds: float | None = Field(30.0, ge=0.1, le=86400)
    edge_ids: list[str] | None = None  # None = all edges


class ProfileUpdate(BaseModel):
    base_latency_ms: float | None = Field(None, ge=0, le=5000)
    jitter_ms: float | None = Field(None, ge=0, le=1000)
    bandwidth_mbps: float | None = Field(None, ge=0.1, le=100000)
    packet_loss: float | None = Field(None, ge=0, le=0.5)
    concurrency_limit: int | None = Field(None, ge=1, le=1024)


class InvalidateRequest(BaseModel):
    object_id: str

    @field_validator("object_id")
    @classmethod
    def _valid(cls, v: str) -> str:
        if not valid_object_id(v):
            raise ValueError("invalid object id")
        return v


class WarmRequest(BaseModel):
    object_ids: list[str] = Field(default_factory=list, max_length=500)


class OriginConfig(BaseModel):
    delay_ms: float = Field(ge=0, le=5000)


class WorkloadModel(BaseModel):
    total_requests: int = Field(300, ge=1, le=20000)
    concurrency: int = Field(16, ge=1, le=256)
    rate_rps: float = Field(0, ge=0, le=5000)
    distribution: Literal["uniform", "random", "zipf"] = "zipf"
    zipf_s: float = Field(1.1, ge=0.1, le=4)
    seed: int = Field(42, ge=0, le=2**31 - 1)
    objects: list[str] = Field(default_factory=list, max_length=1000)
    resolution: Literal["per_request", "dns"] = "per_request"
    hostname: str = CDN_HOSTNAME
    timeout_s: float = Field(10, ge=0.5, le=60)

    @field_validator("objects")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        bad = [o for o in v if not valid_object_id(o)]
        if bad:
            raise ValueError(f"invalid object ids: {bad[:3]}")
        return v


class FailureEvent(BaseModel):
    edge_id: str
    at_s: float = Field(3.0, ge=0, le=3600)
    restore_at_s: float | None = Field(None, ge=0, le=3600)

    @model_validator(mode="after")
    def _order(self):
        if self.restore_at_s is not None and self.restore_at_s <= self.at_s:
            raise ValueError("restore_at_s must be after at_s")
        return self


class DegradationEvent(BaseModel):
    edge_id: str
    at_s: float = Field(3.0, ge=0, le=3600)
    profile: ProfileUpdate


class ExperimentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    type: Literal["single", "routing_comparison", "cache_comparison", "failure", "degradation"] = "single"
    workload: WorkloadModel = Field(default_factory=WorkloadModel)
    routing_strategy: StrategyName = "hybrid"
    weights: Weights | None = None
    cache_policy: PolicyName = "lru"
    cache_capacity_bytes: int = Field(4 * 1024 * 1024, ge=1024, le=1024 ** 3)
    cache_ttl_seconds: float | None = Field(30.0, ge=0.1, le=86400)
    strategies: list[StrategyName] = Field(default_factory=lambda: ["random", "nearest", "lowest_latency",
                                                                     "least_loaded", "hybrid"])
    policies: list[PolicyName] = Field(default_factory=lambda: ["none", "lru", "lfu", "ttl"])
    repetitions: int = Field(1, ge=1, le=5)
    failure: FailureEvent | None = None
    degradation: DegradationEvent | None = None
    start: bool = False

    @model_validator(mode="after")
    def _consistent(self):
        if self.type == "failure" and not self.failure:
            raise ValueError("failure experiments require a 'failure' event")
        if self.type == "degradation" and not self.degradation:
            raise ValueError("degradation experiments require a 'degradation' event")
        if self.type == "routing_comparison" and not self.strategies:
            raise ValueError("routing_comparison needs at least one strategy")
        if self.type == "cache_comparison" and not self.policies:
            raise ValueError("cache_comparison needs at least one policy")
        return self


class ProbeRequest(BaseModel):
    object_id: str
    hostname: str = CDN_HOSTNAME

    @field_validator("object_id")
    @classmethod
    def _valid(cls, v: str) -> str:
        if not valid_object_id(v):
            raise ValueError("invalid object id")
        return v


assert set(STRATEGY_NAMES) == set(StrategyName.__args__)
assert set(CACHE_POLICIES) == set(PolicyName.__args__)
