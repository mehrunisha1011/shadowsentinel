"""
ShadowSentinel — Economic False-Positive Cost Modeler.

The graph detector gives every cluster a risk score. This module answers
the actual business question: at what risk-score threshold should we
auto-flag a cluster, given that blocking a real customer costs more than
the order itself (they leave and stop buying), while missing a real fraud
ring costs the order amount PLUS chargeback/network penalty fees?

Reads graph_report.json (all clusters, with risk scores) and ground_truth.json
(eval-only) + orders.csv, sweeps thresholds, and reports total cost at each
threshold so a real threshold decision can be justified with numbers instead
of a hunch.

Cost assumptions (stated explicitly, not hidden in code):
  - False positive: order_amount * LTV_MULTIPLIER + REVIEW_COST_PER_CASE
    (LTV_MULTIPLIER models lost future purchases from an alienated real
    customer, not just the one refunded order)
  - False negative: order_amount * (1 + CHARGEBACK_PENALTY_RATE)
    (the merchant loses the goods AND pays a card-network chargeback fee)
These are illustrative defaults, not benchmarked against real Razorpay/Visa
fee schedules — say that plainly in the pitch if asked.
"""

import argparse
import csv
import json

LTV_MULTIPLIER = 4.0          # a wrongly-blocked loyal customer's lifetime value lost, as a multiple of one order
REVIEW_COST_PER_CASE = 150.0  # INR cost of a human analyst reviewing one flagged case
CHARGEBACK_PENALTY_RATE = 0.30  # card-network penalty on top of the lost goods, per missed fraud order


def load_orders(path):
    amounts = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            amounts[row["order_id"]] = float(row["order_amount_inr"])
    return amounts


def cost_at_threshold(components, fraud_ids, amounts, threshold):
    flagged_ids = set()
    for c in components:
        if c["risk_score"] >= threshold:
            flagged_ids.update(c["order_ids"])

    tp = flagged_ids & fraud_ids
    fp = flagged_ids - fraud_ids
    fn = fraud_ids - flagged_ids

    fp_cost = sum(amounts.get(oid, 0) * LTV_MULTIPLIER + REVIEW_COST_PER_CASE for oid in fp)
    fn_cost = sum(amounts.get(oid, 0) * (1 + CHARGEBACK_PENALTY_RATE) for oid in fn)
    review_cost_tp = sum(REVIEW_COST_PER_CASE for _ in tp)  # correctly flagged cases still cost a review
    total_cost = fp_cost + fn_cost + review_cost_tp

    return {
        "threshold": round(threshold, 2),
        "flagged": len(flagged_ids),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "fp_cost_inr": round(fp_cost, 2),
        "fn_cost_inr": round(fn_cost, 2),
        "review_cost_inr": round(review_cost_tp, 2),
        "total_cost_inr": round(total_cost, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="../data/graph_report.json")
    ap.add_argument("--ground-truth", default="../data/ground_truth.json")
    ap.add_argument("--orders", default="../data/orders.csv")
    ap.add_argument("--out-json", default="../data/cost_curve.json")
    ap.add_argument("--out-chart", default="../data/cost_curve.png")
    args = ap.parse_args()

    report = json.load(open(args.report))
    gt = json.load(open(args.ground_truth))
    amounts = load_orders(args.orders)

    fraud_ids = set()
    for ring_orders in gt["rings"].values():
        fraud_ids.update(ring_orders)

    components = report["components"]
    thresholds = [round(t * 0.05, 2) for t in range(0, 21)]  # 0.00 to 1.00 step 0.05
    curve = [cost_at_threshold(components, fraud_ids, amounts, t) for t in thresholds]

    best = min(curve, key=lambda r: r["total_cost_inr"])

    result = {
        "assumptions": {
            "ltv_multiplier": LTV_MULTIPLIER,
            "review_cost_per_case_inr": REVIEW_COST_PER_CASE,
            "chargeback_penalty_rate": CHARGEBACK_PENALTY_RATE,
        },
        "recommended_threshold": best["threshold"],
        "recommended_threshold_stats": best,
        "full_curve": curve,
    }

    with open(args.out_json, "w") as f:
        json.dump(result, f, indent=2)

    # chart
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]

    ts = [r["threshold"] for r in curve]
    fp_costs = [r["fp_cost_inr"] for r in curve]
    fn_costs = [r["fn_cost_inr"] for r in curve]
    total_costs = [r["total_cost_inr"] for r in curve]

    BG = "#0f1220"
    GRID = "#2a2e45"
    TEXT = "#e8e9f3"
    ORANGE = "#f4845f"
    BLUE = "#5da9e9"
    GREEN = "#5ee6a0"
    GOLD = "#f2c14e"

    fig, ax = plt.subplots(figsize=(9, 5.5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    ax.plot(ts, fp_costs, label="False-positive cost (alienated customers)",
            color=ORANGE, linewidth=2, marker="o", markersize=3.5)
    ax.plot(ts, fn_costs, label="False-negative cost (missed fraud)",
            color=BLUE, linewidth=2, marker="o", markersize=3.5)
    ax.plot(ts, total_costs, label="Total cost", color=GREEN, linewidth=3.2, zorder=5)

    ax.axvline(best["threshold"], color=GOLD, linestyle="--", linewidth=1.5, alpha=0.8)
    ax.scatter([best["threshold"]], [best["total_cost_inr"]], color=GOLD, s=90,
               zorder=6, edgecolor=BG, linewidth=1.5)
    ax.annotate(
        f"Optimal: {best['threshold']}\n\u20b9{best['total_cost_inr']:,.0f}",
        xy=(best["threshold"], best["total_cost_inr"]),
        xytext=(best["threshold"] + 0.08, best["total_cost_inr"] + max(total_costs) * 0.12),
        color=GOLD, fontsize=10, fontweight="bold",
        arrowprops=dict(arrowstyle="->", color=GOLD, lw=1.2),
    )

    ax.set_xlabel("Risk score flag threshold", color=TEXT, fontsize=11)
    ax.set_ylabel("Cost (INR)", color=TEXT, fontsize=11)
    ax.set_title("ShadowSentinel \u2014 Economic Cost vs. Flag Threshold",
                 color=TEXT, fontsize=15, fontweight="bold", pad=16)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"\u20b9{x/1000:,.0f}K" if x >= 1000 else f"\u20b9{x:,.0f}"))
    ax.tick_params(colors=TEXT, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.6)
    ax.set_axisbelow(True)

    legend = ax.legend(fontsize=9, facecolor=BG, edgecolor=GRID, labelcolor=TEXT, loc="upper center")

    fig.tight_layout()
    fig.savefig(args.out_chart, dpi=160, facecolor=BG)

    print(f"Recommended threshold: {best['threshold']}  (total cost: INR {best['total_cost_inr']:,.2f})")
    print(f"  -> {best['true_positives']} correctly flagged, {best['false_positives']} false positives, {best['false_negatives']} missed")
    print(f"Wrote {args.out_json} and {args.out_chart}")


if __name__ == "__main__":
    main()
