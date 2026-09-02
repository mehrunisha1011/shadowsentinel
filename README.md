# ShadowSentinel
Cross-Merchant Friendly-Fraud Collusion Graph & Auto-Dossier Compiler

Built for Razorpay AI Buildathon 2026 — Track 02: AI Risk Manager

## The problem
Friendly fraud: a syndicate orders high-value goods, accepts delivery, then
claims "item not received" and files a chargeback — rotating UPI handles and
devices across multiple merchants so no single merchant sees the pattern.

## Approach
Most naive detectors check one order in isolation (IP country, single device
flag). That misses cross-merchant collusion by design. ShadowSentinel instead
builds a **graph** across orders and looks for connected clusters that share
device fingerprints, fuzzy-rotating UPI handles, and tight delivery geography
— then scores each cluster on an explainable, weighted formula (not a
black-box classifier) so every flag comes with a human-readable reason.

## Pipeline
```
src/generate_data.py   -> synthetic orders + held-out ground truth
src/build_graph.py     -> graph construction + component risk scoring
src/evaluate.py        -> precision/recall/F1 against ground truth (eval-only)
```

## Running it
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd src
python3 generate_data.py --outdir ../data
python3 build_graph.py --orders ../data/orders.csv --out ../data/graph_report.json
python3 evaluate.py --report ../data/graph_report.json --ground-truth ../data/ground_truth.json --orders ../data/orders.csv
```

## Key design decision: multi-signal edges
Early version connected orders on ANY single shared signal (device OR geo OR
VPA). This over-merged unrelated orders: incidental geo overlap (two
strangers ordering from the same apartment block) chained a real fraud ring,
a legit household, and unrelated orders into one 37-node blob. Fixed by
requiring **at least 2 independent signals** to agree before drawing an edge.
See `ARCHITECTURE.md` for the full before/after.

## Status
Day 1 result on synthetic data: 12/12 rings detected, 0/6 false-positive
household traps wrongly flagged. This is a clean-data result and is being
treated as a starting point, not a claim — next step is adversarial/noisy
test cases before this number goes in the pitch.

## Not yet built
- Neo4j persistence (currently networkx, in-memory)

