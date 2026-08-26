"""
ShadowSentinel — Multi-Modal Arbitration Dossier Compiler.

Takes a flagged cluster from graph_report.json, pulls the underlying orders
from orders.csv, and compiles a PDF evidence packet: case summary, the
graph's own risk explanation, an order-by-order table, and the signals that
triggered the flag.

Honesty note (say this in your pitch too): this is formatted FOR arbitration
submission, laid out the way a real dossier would be, but it is NOT a
certified VROL-compliant document — that would require verifying against
Visa/Mastercard's actual current arbitration spec, which is outside this
project's scope. Don't claim compliance you haven't verified.
"""

import argparse
import csv
import json
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak, HRFlowable)


def load_orders(path):
    orders = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            orders[row["order_id"]] = row
    return orders


def load_component(report_path, component_id):
    report = json.load(open(report_path))
    for c in report["components"]:
        if c["component_id"] == component_id:
            return c
    raise ValueError(f"component {component_id} not found in {report_path}")


def build_dossier(component, orders, outpath):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DossierTitle", fontSize=18, leading=22,
                               spaceAfter=6, textColor=colors.HexColor("#1a1a2e")))
    styles.add(ParagraphStyle(name="SectionHeader", fontSize=13, leading=16,
                               spaceBefore=14, spaceAfter=6,
                               textColor=colors.HexColor("#16213e")))
    styles.add(ParagraphStyle(name="SmallGrey", fontSize=8, leading=10,
                               textColor=colors.HexColor("#666666")))
    styles.add(ParagraphStyle(name="BodyJustify", parent=styles["Normal"],
                               alignment=4, spaceAfter=6))

    doc = SimpleDocTemplate(outpath, pagesize=letter,
                             topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                             leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    story = []

    # --- Header ---
    story.append(Paragraph("ShadowSentinel Collusion Dossier", styles["DossierTitle"]))
    story.append(Paragraph(
        f"Case ID: {component['component_id']} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Format: arbitration-submission layout (not a certified VROL document)",
        styles["SmallGrey"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc"), spaceBefore=8, spaceAfter=12))

    # --- Executive summary ---
    story.append(Paragraph("1. Executive Summary", styles["SectionHeader"]))
    story.append(Paragraph(
        f"This dossier documents a suspected friendly-fraud collusion cluster of "
        f"<b>{component['size']} orders</b> across <b>{len(component['merchants'])} merchants</b> "
        f"({', '.join(component['merchants'])}). The cluster was flagged by ShadowSentinel's "
        f"graph detector with a risk score of <b>{component['risk_score']}</b> "
        f"(flag threshold: {'0.50' if 'flag_threshold' not in component else component['flag_threshold']}).",
        styles["BodyJustify"]))
    story.append(Paragraph(f"<b>Reasoning:</b> {component['why']}", styles["BodyJustify"]))

    # --- Risk factor breakdown ---
    story.append(Paragraph("2. Risk Factor Breakdown", styles["SectionHeader"]))
    factor_data = [
        ["Factor", "Score", "Interpretation"],
        ["Dispute fraction", f"{component['dispute_fraction']:.2f}",
         "Share of cluster orders disputed as \u201citem not received\u201d"],
        ["Device fingerprint sharing", f"{component['device_sharing']:.2f}",
         "1.0 = all orders used the same device"],
        ["UPI VPA rotation", f"{component['vpa_rotation']:.2f}",
         "1.0 = one identity rotating many VPA handles"],
    ]
    t = Table(factor_data, colWidths=[1.8 * inch, 0.8 * inch, 3.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    story.append(t)

    signal_counts = component.get("edge_signal_counts", {})
    if signal_counts:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            "Edge signals within this cluster's graph: " +
            ", ".join(f"{k} ({v} links)" for k, v in signal_counts.items()),
            styles["SmallGrey"]))

    # --- Order-level evidence table ---
    story.append(Paragraph("3. Order-Level Evidence", styles["SectionHeader"]))
    order_rows = [["Order ID", "Merchant", "Date", "Amount (INR)", "Dispute Reason"]]
    for oid in component["order_ids"]:
        o = orders.get(oid)
        if not o:
            continue
        order_rows.append([
            oid, o["merchant_id"], o["order_date"].split("T")[0],
            f"{float(o['order_amount_inr']):.2f}",
            o["dispute_reason"] or "\u2014",
        ])
    t2 = Table(order_rows, colWidths=[1.3 * inch, 1.2 * inch, 0.9 * inch, 1.0 * inch, 1.6 * inch], repeatRows=1)
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    story.append(t2)

    # --- Device / telemetry appendix (mocked, clearly labeled) ---
    story.append(Paragraph("4. Device &amp; Delivery Telemetry (Appendix)", styles["SectionHeader"]))
    story.append(Paragraph(
        "The following telemetry is simulated for this synthetic dataset — in a production "
        "deployment this section would be populated from courier OTP/weight-scan logs and "
        "device SDK telemetry, not fabricated.", styles["SmallGrey"]))
    tele_rows = [["Order ID", "Device Fingerprint", "Delivery Coordinates"]]
    for oid in component["order_ids"][:15]:  # cap for page length
        o = orders.get(oid)
        if not o:
            continue
        tele_rows.append([oid, o["device_fingerprint"], f"{o['delivery_lat']}, {o['delivery_lon']}"])
    t3 = Table(tele_rows, colWidths=[1.3 * inch, 2.4 * inch, 2.3 * inch], repeatRows=1)
    t3.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    story.append(t3)

    # --- Footer disclaimer (kept with the preceding rule so it can't strand alone on a new page) ---
    from reportlab.platypus import KeepTogether
    footer = [
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")),
        Spacer(1, 6),
        Paragraph(
            "This document was auto-generated by ShadowSentinel from synthetic data for a "
            "hackathon prototype. It is formatted in the structure of an arbitration evidence "
            "packet but has not been validated against Visa/Mastercard VROL compliance "
            "requirements and is not a certified legal instrument.", styles["SmallGrey"]),
    ]
    story.append(KeepTogether(footer))

    doc.build(story)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="../data/graph_report.json")
    ap.add_argument("--orders", default="../data/orders.csv")
    ap.add_argument("--component-id", required=True, help="e.g. CC-0001")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    orders = load_orders(args.orders)
    component = load_component(args.report, args.component_id)
    outpath = args.out or f"../data/dossier_{args.component_id}.pdf"
    build_dossier(component, orders, outpath)
    print(f"Wrote {outpath}")


if __name__ == "__main__":
    main()
