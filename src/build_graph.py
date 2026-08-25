"""
ShadowSentinel — Graph-Velocity Sentinel (core detector).

Reads data/orders.csv ONLY (never ground_truth.json — that's eval-only).
Builds a graph where orders are nodes and edges represent a shared signal:
  - EXACT device fingerprint match
  - FUZZY UPI VPA match (same base handle family, different suffix/provider)
  - TIGHT geo-cluster match (same ~50m delivery cell)

Then scores each connected component on ring-likelihood using a weighted,
inspectable formula (not a black-box classifier) so every flag can be
explained to a human reviewer.

Outputs:
  data/graph_report.json  -> one entry per connected component with its
                              score, signals, member order_ids, and a
                              human-readable "why" explanation.
"""

import argparse
import csv
import json
from collections import defaultdict
from difflib import SequenceMatcher

import networkx as nx

GEO_CELL_METERS = 60  # orders within this radius are considered "same cell"


def load_orders(path):
    orders = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            row["delivery_lat"] = float(row["delivery_lat"])
            row["delivery_lon"] = float(row["delivery_lon"])
            row["order_amount_inr"] = float(row["order_amount_inr"])
            row["dispute_filed"] = row["dispute_filed"] == "True"
            orders[row["order_id"]] = row
    return orders


def geo_cell(lat, lon, meters=GEO_CELL_METERS):
    # snap to a grid cell of ~`meters` size
    deg = meters / 111000.0
    return (round(lat / deg), round(lon / deg))


def vpa_base(vpa):
    """Strip the @handle and any trailing digits/underscore noise to get a base identity."""
    name = vpa.split("@")[0]
    return name.rstrip("0123456789_")


def vpa_similar(vpa_a, vpa_b, threshold=0.82):
    if vpa_a == vpa_b:
        return True
    base_a, base_b = vpa_base(vpa_a), vpa_base(vpa_b)
    if not base_a or not base_b:
        return False
    return SequenceMatcher(None, base_a, base_b).ratio() >= threshold


def build_graph(orders, min_signals=2):
    """
    min_signals: how many independent signal types must agree before two
    orders get an edge at all. Geo-cluster overlap alone is common and weak
    (two strangers in the same apartment block) — connecting on ANY single
    signal lets weak geo overlap chain unrelated orders into one giant
    component (verified empirically: this merged a real ring with a legit
    household and unrelated orders at min_signals=1). Requiring corroboration
    from a second signal (device or VPA) before drawing an edge is the fix.
    """
    G = nx.Graph()
    for oid in orders:
        G.add_node(oid)

    by_device = defaultdict(list)
    by_geocell = defaultdict(list)
    for oid, o in orders.items():
        by_device[o["device_fingerprint"]].append(oid)
        by_geocell[geo_cell(o["delivery_lat"], o["delivery_lon"])].append(oid)

    pair_signals = defaultdict(set)  # frozenset({oid1,oid2}) -> {"device","geo","vpa"}

    for group in by_device.values():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pair_signals[frozenset((group[i], group[j]))].add("device")

    for group in by_geocell.values():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pair_signals[frozenset((group[i], group[j]))].add("geo")

    # fuzzy VPA is O(n^2) worst case — fine at this data scale (hundreds-low thousands).
    # For production scale you'd bucket by first-2-char prefix first; noted in README as a known scaling limit.
    ids = list(orders.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if vpa_similar(orders[ids[i]]["upi_vpa"], orders[ids[j]]["upi_vpa"]):
                pair_signals[frozenset((ids[i], ids[j]))].add("vpa")

    for pair, reasons in pair_signals.items():
        if len(reasons) >= min_signals:
            a, b = tuple(pair)
            G.add_edge(a, b, reasons=list(reasons))

    return G


def score_component(orders, component, G):
    members = list(component)
    if len(members) < 2:
        return None

    merchants = {orders[m]["merchant_id"] for m in members}
    disputes = [orders[m]["dispute_filed"] for m in members]
    dispute_fraction = sum(disputes) / len(members)

    devices = {orders[m]["device_fingerprint"] for m in members}
    device_sharing = 1 - (len(devices) / len(members))  # closer to 1 = heavy device reuse

    vpa_bases = {vpa_base(orders[m]["upi_vpa"]) for m in members}
    vpa_rotation = 1 - (len(vpa_bases) / len(members))  # closer to 1 = same identity, many VPAs

    reason_tally = defaultdict(int)
    for a, b in G.subgraph(members).edges():
        for r in G[a][b]["reasons"]:
            reason_tally[r] += 1

    merchant_spread = min(len(merchants) / 3.0, 1.0)  # normalize: 3+ merchants = full weight

    # Weighted, inspectable score. Weights chosen so that dispute behavior
    # (the actual harm signal) dominates — a tight device/geo cluster with
    # NO disputes (see: family false-positive trap) should NOT score high.
    risk_score = (
        0.40 * dispute_fraction
        + 0.25 * merchant_spread
        + 0.20 * device_sharing
        + 0.15 * vpa_rotation
    )

    why_parts = []
    if dispute_fraction > 0:
        why_parts.append(f"{sum(disputes)}/{len(members)} orders disputed as friendly-fraud")
    if len(merchants) >= 3:
        why_parts.append(f"spans {len(merchants)} merchants ({', '.join(sorted(merchants))})")
    if device_sharing > 0.3:
        why_parts.append(f"{len(devices)} device fingerprint(s) across {len(members)} orders")
    if vpa_rotation > 0.3:
        why_parts.append(f"{len(vpa_bases)} VPA identity base(s), rotating handles")
    if not why_parts:
        why_parts.append("weak/incidental overlap only")

    return {
        "component_id": None,  # filled by caller
        "order_ids": members,
        "size": len(members),
        "merchants": sorted(merchants),
        "risk_score": round(risk_score, 4),
        "dispute_fraction": round(dispute_fraction, 3),
        "device_sharing": round(device_sharing, 3),
        "vpa_rotation": round(vpa_rotation, 3),
        "edge_signal_counts": dict(reason_tally),
        "why": "; ".join(why_parts),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", default="../data/orders.csv")
    ap.add_argument("--out", default="../data/graph_report.json")
    ap.add_argument("--flag-threshold", type=float, default=0.5)
    ap.add_argument("--min-signals", type=int, default=2)
    args = ap.parse_args()

    orders = load_orders(args.orders)
    G = build_graph(orders, min_signals=args.min_signals)

    components = [c for c in nx.connected_components(G) if len(c) > 1]
    reports = []
    for idx, comp in enumerate(components):
        r = score_component(orders, comp, G)
        if r:
            r["component_id"] = f"CC-{idx+1:04d}"
            r["flagged"] = r["risk_score"] >= args.flag_threshold
            reports.append(r)

    reports.sort(key=lambda r: -r["risk_score"])

    with open(args.out, "w") as f:
        json.dump({"flag_threshold": args.flag_threshold, "components": reports}, f, indent=2)

    n_flagged = sum(1 for r in reports if r["flagged"])
    print(f"{len(reports)} multi-order clusters found, {n_flagged} flagged at threshold {args.flag_threshold}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
