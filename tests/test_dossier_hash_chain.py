"""
Tests for the evidence hash chain in generate_dossier.py — proves the chain
is internally consistent and that tampering with any single record's data
changes every hash from that point forward (the actual property a chain of
custody needs to have to be worth anything).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from generate_dossier import compute_evidence_hash_chain, verify_evidence_hash_chain


def make_component(order_ids):
    return {"order_ids": order_ids}


def make_orders():
    return {
        "ORD-1": {"merchant_id": "M_A", "order_date": "2026-01-01", "order_amount_inr": "1000",
                   "device_fingerprint": "dfp_x", "upi_vpa": "a@ybl"},
        "ORD-2": {"merchant_id": "M_B", "order_date": "2026-01-02", "order_amount_inr": "2000",
                   "device_fingerprint": "dfp_x", "upi_vpa": "a2@ybl"},
        "ORD-3": {"merchant_id": "M_C", "order_date": "2026-01-03", "order_amount_inr": "3000",
                   "device_fingerprint": "dfp_x", "upi_vpa": "a3@ybl"},
    }


def test_chain_has_one_entry_per_order_in_order():
    component = make_component(["ORD-1", "ORD-2", "ORD-3"])
    orders = make_orders()
    chain, head = compute_evidence_hash_chain(component, orders)
    assert [entry["order_id"] for entry in chain] == ["ORD-1", "ORD-2", "ORD-3"]


def test_chain_links_each_record_to_the_previous_hash():
    component = make_component(["ORD-1", "ORD-2", "ORD-3"])
    orders = make_orders()
    chain, head = compute_evidence_hash_chain(component, orders)
    assert chain[0]["prev_hash"] == "0" * 64  # genesis
    assert chain[1]["prev_hash"] == chain[0]["record_hash"]
    assert chain[2]["prev_hash"] == chain[1]["record_hash"]
    assert head == chain[2]["record_hash"]


def test_verify_passes_on_unmodified_data():
    component = make_component(["ORD-1", "ORD-2", "ORD-3"])
    orders = make_orders()
    chain, head = compute_evidence_hash_chain(component, orders)
    assert verify_evidence_hash_chain(component, orders, chain) is True


def test_tampering_with_a_single_record_breaks_the_chain_from_that_point():
    component = make_component(["ORD-1", "ORD-2", "ORD-3"])
    orders = make_orders()
    original_chain, original_head = compute_evidence_hash_chain(component, orders)

    tampered_orders = {k: dict(v) for k, v in orders.items()}
    tampered_orders["ORD-2"]["order_amount_inr"] = "999999"  # someone edited the amount
    tampered_chain, tampered_head = compute_evidence_hash_chain(component, tampered_orders)

    # the tampered record itself and everything after it must differ
    assert tampered_chain[1]["record_hash"] != original_chain[1]["record_hash"]
    assert tampered_chain[2]["record_hash"] != original_chain[2]["record_hash"]
    assert tampered_head != original_head
    # but the untouched first record is unaffected
    assert tampered_chain[0]["record_hash"] == original_chain[0]["record_hash"]

    # and verify_evidence_hash_chain correctly flags the mismatch against the original chain
    assert verify_evidence_hash_chain(component, tampered_orders, original_chain) is False
