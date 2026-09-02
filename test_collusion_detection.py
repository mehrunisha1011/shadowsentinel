"""
Regression test for the bug documented in ARCHITECTURE.md: connecting orders
on ANY single shared signal let incidental geo-cell overlap chain a real
fraud ring together with an unrelated legit household (the false-positive
trap), because geo overlap alone is common and weak.

This test builds a minimal, hand-crafted version of exactly that scenario
and asserts:
  1. At min_signals=1 (the old, broken behavior) the ring and the trap DO
     get merged into one connected component.
  2. At min_signals=2 (the fix) they are correctly kept separate.

If this test ever starts failing, it means someone reintroduced the
over-merging bug.
"""
import networkx as nx

from build_graph import build_graph, score_component


def make_order(order_id, device, vpa, lat, lon, merchant, amount, disputed):
    return {
        "order_id": order_id,
        "merchant_id": merchant,
        "device_fingerprint": device,
        "upi_vpa": vpa,
        "delivery_lat": lat,
        "delivery_lon": lon,
        "order_amount_inr": amount,
        "dispute_filed": disputed,
        "dispute_reason": "item_not_received" if disputed else "",
    }


def build_ring_and_trap_scenario():
    """3-order fraud ring + 2-order legit household, incidentally at the
    same delivery geo-cell but sharing no device or VPA with each other."""
    orders = {}

    # --- the fraud ring: shared device, fuzzy-rotating VPA, 3 merchants, always disputed ---
    ring_geo = (12.93520, 77.62450)
    for i, merchant in enumerate(["M_A", "M_B", "M_C"]):
        oid = f"RING-{i}"
        orders[oid] = make_order(
            oid, device="dfp_shared_ring_device",
            vpa=f"rahulk9{i}@ybl", lat=ring_geo[0], lon=ring_geo[1],
            merchant=merchant, amount=20000, disputed=True,
        )

    # --- the innocent trap: different device, different VPA identity,
    # never disputes, but happens to deliver to the SAME geo cell as the ring ---
    for i, merchant in enumerate(["M_D", "M_E"]):
        oid = f"TRAP-{i}"
        orders[oid] = make_order(
            oid, device="dfp_shared_trap_device",
            vpa=f"priyasharma{i}@oksbi", lat=ring_geo[0], lon=ring_geo[1],
            merchant=merchant, amount=2000, disputed=False,
        )

    return orders


def test_min_signals_1_over_merges_ring_and_trap():
    orders = build_ring_and_trap_scenario()
    G = build_graph(orders, min_signals=1)
    components = list(nx.connected_components(G))
    big_components = [c for c in components if len(c) > 1]

    # the bug: everything ends up in ONE component because geo alone connects them
    assert len(big_components) == 1
    assert big_components[0] == set(orders.keys())


def test_min_signals_2_correctly_separates_ring_from_trap():
    orders = build_ring_and_trap_scenario()
    G = build_graph(orders, min_signals=2)
    components = [c for c in nx.connected_components(G) if len(c) > 1]

    ring_ids = {f"RING-{i}" for i in range(3)}
    trap_ids = {f"TRAP-{i}" for i in range(2)}

    assert ring_ids in components
    assert trap_ids in components
    # and critically, they must NOT be merged into one
    assert ring_ids != trap_ids
    for c in components:
        assert not (ring_ids & c and trap_ids & c), "ring and trap were merged into one component"


def test_scoring_ranks_disputing_ring_above_non_disputing_trap():
    orders = build_ring_and_trap_scenario()
    G = build_graph(orders, min_signals=2)

    ring_ids = {f"RING-{i}" for i in range(3)}
    trap_ids = {f"TRAP-{i}" for i in range(2)}

    ring_score = score_component(orders, ring_ids, G)
    trap_score = score_component(orders, trap_ids, G)

    assert ring_score["risk_score"] > trap_score["risk_score"]
    # the trap must land below a reasonable flag threshold despite its
    # tight device/geo overlap, because it never disputes
    assert trap_score["risk_score"] < 0.5
    assert ring_score["risk_score"] >= 0.5
