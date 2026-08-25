"""
ShadowSentinel — evaluator.

This is the ONLY script allowed to read ground_truth.json. It measures
graph_report.json's flags against it. Never feed ground_truth.json into
build_graph.py — that would be training on the answer key.

Reports:
  - Order-level precision / recall / F1
  - Ring-level recall (did we catch each real ring, at least partially?)
  - False-positive trap performance (did we avoid flagging the family cases?)
  - Economic false-positive cost, using order_amount_inr as the customer-LTV
    proxy for wrongly-blocked legit orders, and a configurable avg fraud loss
    per missed ring order.
"""

import argparse
import csv
import json


def load_orders_amount_map(orders_path):
    amounts = {}
    with open(orders_path) as f:
        for row in csv.DictReader(f):
            amounts[row["order_id"]] = float(row["order_amount_inr"])
    return amounts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="../data/graph_report.json")
    ap.add_argument("--ground-truth", default="../data/ground_truth.json")
    ap.add_argument("--orders", default="../data/orders.csv")
    ap.add_argument("--avg-fraud-loss-multiplier", type=float, default=1.0,
                     help="multiplier on order_amount_inr to estimate loss per missed fraud order")
    args = ap.parse_args()

    report = json.load(open(args.report))
    gt = json.load(open(args.ground_truth))
    amounts = load_orders_amount_map(args.orders)

    fraud_order_ids = set()
    for ring_orders in gt["rings"].values():
        fraud_order_ids.update(ring_orders)

    trap_order_ids = set()
    for trap_orders in gt["fp_traps"].values():
        trap_order_ids.update(trap_orders)

    flagged_components = [c for c in report["components"] if c["flagged"]]
    flagged_order_ids = set()
    for c in flagged_components:
        flagged_order_ids.update(c["order_ids"])

    # order-level confusion counts, restricted to orders that appear in some
    # multi-order cluster (orders with zero graph connections were never
    # candidates in the first place -- that's a separate recall ceiling to report)
    all_clustered_ids = set()
    for c in report["components"]:
        all_clustered_ids.update(c["order_ids"])

    tp = flagged_order_ids & fraud_order_ids
    fp = flagged_order_ids - fraud_order_ids
    fn = fraud_order_ids - flagged_order_ids

    precision = len(tp) / len(flagged_order_ids) if flagged_order_ids else 0.0
    recall = len(tp) / len(fraud_order_ids) if fraud_order_ids else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # ring-level: did we flag AT LEAST one component covering >=50% of a ring's orders?
    rings_caught = 0
    ring_detail = {}
    for ring_id, ring_orders in gt["rings"].items():
        ring_set = set(ring_orders)
        best_overlap = 0
        for c in flagged_components:
            overlap = len(ring_set & set(c["order_ids"]))
            best_overlap = max(best_overlap, overlap)
        caught = best_overlap / len(ring_set) >= 0.5
        rings_caught += caught
        ring_detail[ring_id] = {"size": len(ring_set), "best_overlap": best_overlap, "caught": caught}

    # false-positive trap check: did we flag any trap household?
    traps_wrongly_flagged = 0
    trap_detail = {}
    for trap_id, trap_orders in gt["fp_traps"].items():
        trap_set = set(trap_orders)
        flagged_any = any(len(trap_set & set(c["order_ids"])) > 0 for c in flagged_components)
        traps_wrongly_flagged += flagged_any
        trap_detail[trap_id] = {"wrongly_flagged": flagged_any}

    # economic cost model
    fp_legit_orders = fp - trap_order_ids  # fp orders that are neither fraud nor a labeled trap
    fp_cost = sum(amounts.get(oid, 0) for oid in fp) # $ cost of blocking/alienating legit-looking orders
    fn_cost = sum(amounts.get(oid, 0) * args.avg_fraud_loss_multiplier for oid in fn)  # missed fraud loss
    total_transacted = sum(amounts.values())
    fp_cost_pct = (fp_cost / total_transacted * 100) if total_transacted else 0.0

    result = {
        "order_level": {
            "flagged": len(flagged_order_ids),
            "true_positives": len(tp),
            "false_positives": len(fp),
            "false_negatives": len(fn),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "ring_level": {
            "total_rings": len(gt["rings"]),
            "rings_caught_50pct_overlap": rings_caught,
            "detail": ring_detail,
        },
        "false_positive_traps": {
            "total_traps": len(gt["fp_traps"]),
            "traps_wrongly_flagged": traps_wrongly_flagged,
            "detail": trap_detail,
        },
        "economic_cost": {
            "false_positive_cost_inr": round(fp_cost, 2),
            "false_negative_cost_inr_est": round(fn_cost, 2),
            "false_positive_cost_pct_of_volume": round(fp_cost_pct, 3),
        },
    }

    print(json.dumps(result, indent=2))
    with open("../data/eval_report.json", "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
