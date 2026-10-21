import pytest

from app.routing.engine import RoutingEngine
from app.routing.strategies import EdgeView, HybridStrategy, normalize


def fleet(**overrides) -> list[EdgeView]:
    base = [
        EdgeView("india", "127.0.0.1", 1, latency_ms=20, load=0.9, packet_loss=0.01, distance_km=800, priority=1),
        EdgeView("sg", "127.0.0.1", 2, latency_ms=45, load=0.1, packet_loss=0.0, distance_km=3200, priority=2),
        EdgeView("usa", "127.0.0.1", 3, latency_ms=110, load=0.0, packet_loss=0.02, distance_km=13900, priority=3),
    ]
    for e in base:
        for k, v in overrides.get(e.edge_id, {}).items():
            setattr(e, k, v)
    return base


def test_normalize_min_max_and_edge_cases():
    assert normalize({"a": 10, "b": 20, "c": 15}) == {"a": 0.0, "b": 1.0, "c": 0.5}
    assert normalize({"a": 0, "b": 0}) == {"a": 0.0, "b": 0.0}  # all equal (incl. zero) -> no discrimination
    assert normalize({"a": 5, "b": None}) == {"a": 0.0, "b": 1.0}  # missing = worst
    assert normalize({"a": None, "b": None}) == {"a": 0.0, "b": 0.0}


@pytest.mark.parametrize("strategy,expected", [
    ("nearest", "india"), ("lowest_latency", "india"), ("least_loaded", "usa"), ("failover", "india"),
])
def test_deterministic_strategies(strategy, expected):
    d = RoutingEngine(strategy).select(fleet())
    assert d.edge_id == expected and d.reason


def test_hybrid_score_formula():
    h = HybridStrategy(0.5, 0.4, 0.1)
    s = h.scores(fleet())
    # india: lat 0, load 1, loss 0.5 -> 0.4 + 0.05
    assert s["india"] == pytest.approx(0.45)
    # sg: lat (45-20)/90, load 0.1/0.9, loss 0
    assert s["sg"] == pytest.approx(0.5 * 25 / 90 + 0.4 * (0.1 / 0.9))
    # usa: lat 1, load 0, loss 1
    assert s["usa"] == pytest.approx(0.6)
    assert RoutingEngine("hybrid").select(fleet()).edge_id == "sg"


def test_hybrid_weights_are_normalised_and_validated():
    h = HybridStrategy(2, 2, 0)
    assert h.weights == {"latency": 0.5, "load": 0.5, "packet_loss": 0.0}
    with pytest.raises(ValueError):
        HybridStrategy(-1, 1, 1)
    with pytest.raises(ValueError):
        HybridStrategy(0, 0, 0)


def test_latency_only_hybrid_matches_lowest_latency():
    eng = RoutingEngine("hybrid", weights={"latency_weight": 1, "load_weight": 0, "packet_loss_weight": 0})
    assert eng.select(fleet()).edge_id == "india"


def test_ties_broken_by_priority_then_id():
    edges = fleet(india={"latency_ms": 50, "priority": 5}, sg={"latency_ms": 50, "priority": 2},
                  usa={"latency_ms": 50, "priority": 2})
    assert RoutingEngine("lowest_latency").select(edges).edge_id == "sg"  # priority 2, 'sg' < 'usa'
    assert RoutingEngine("hybrid").select(fleet(india={"latency_ms": 1, "load": 0, "packet_loss": 0},
                                                sg={"latency_ms": 1, "load": 0, "packet_loss": 0})).edge_id == "india"


def test_unhealthy_edges_excluded_when_alternative_exists():
    for name in ("random", "nearest", "lowest_latency", "least_loaded", "hybrid", "failover"):
        eng = RoutingEngine(name, seed=1)
        for _ in range(30):
            d = eng.select(fleet(india={"healthy": False}, usa={"healthy": False}))
            assert d.edge_id == "sg" and d.excluded == ["india", "usa"]
            assert "excluded unhealthy" in d.reason


def test_failover_falls_back_in_priority_order():
    eng = RoutingEngine("failover")
    assert eng.select(fleet(india={"healthy": False})).edge_id == "sg"
    assert eng.select(fleet(india={"healthy": False}, sg={"healthy": False})).edge_id == "usa"


def test_no_healthy_edge_returns_none():
    d = RoutingEngine("hybrid").select(fleet(india={"healthy": False}, sg={"healthy": False}, usa={"healthy": False}))
    assert d.edge_id is None and "no healthy" in d.reason


def test_missing_metrics_handled_safely():
    edges = fleet(india={"latency_ms": None, "load": None, "packet_loss": None},
                  sg={"latency_ms": None, "packet_loss": None}, usa={"latency_ms": None, "packet_loss": None})
    d = RoutingEngine("lowest_latency").select(edges)
    assert d.edge_id == "india" and "fell back" in d.reason
    assert RoutingEngine("least_loaded").select(edges).edge_id == "usa"  # india's unknown load counts as worst
    assert RoutingEngine("hybrid").select(edges).edge_id in {"sg", "usa"}


def test_random_is_reproducible_with_seed_and_uses_all_edges():
    a = [RoutingEngine("random", seed=7).select(fleet()).edge_id for _ in range(1)]
    e1, e2 = RoutingEngine("random", seed=7), RoutingEngine("random", seed=7)
    seq1 = [e1.select(fleet()).edge_id for _ in range(60)]
    seq2 = [e2.select(fleet()).edge_id for _ in range(60)]
    assert seq1 == seq2 and set(seq1) == {"india", "sg", "usa"} and a


def test_unknown_strategy_rejected_and_decisions_recorded():
    eng = RoutingEngine("nearest")
    with pytest.raises(ValueError):
        eng.set_strategy("magic")
    eng.select(fleet())
    assert eng.counts["india"] == 1 and len(eng.recent) == 1
    eng.select(fleet(), record=False)
    assert len(eng.recent) == 1
