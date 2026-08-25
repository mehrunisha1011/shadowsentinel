# Architecture

## Components

**1. Data layer (`generate_data.py`)**
Generates synthetic orders with two categories deliberately mixed in:
- Collusion rings: shared device, fuzzy-rotating UPI VPA, tight geo-cluster,
  spanning 3+ merchants, always disputed as "item not received."
- False-positive traps: legit households that share a device and address
  across merchants (family members) but never dispute. Designed specifically
  to break a detector that only looks at device/geo overlap.
Ground truth (which orders belong to which ring/trap) is written to a
separate file the detector never reads.

**2. Graph construction (`build_graph.py`)**
Orders are nodes. Edges are added when two orders share:
- exact device fingerprint
- fuzzy-matched UPI VPA (same base identity, rotating suffix/provider)
- same delivery geo-cell (~60m)

**3. Component scoring**
Each connected component gets a weighted risk score from dispute behavior,
merchant spread, device reuse, and VPA rotation. Dispute fraction is weighted
highest (0.40) deliberately — a tight device/geo cluster with no disputes
(the family trap) should not score as fraud; the actual harm signal is what
should drive the flag, not just structural overlap.

**4. Evaluation (`evaluate.py`)**
The only script permitted to read ground truth. Computes order-level and
ring-level precision/recall, false-positive-trap leakage, and an economic
cost estimate (₹ value of wrongly-flagged legit orders vs. estimated loss
from missed fraud).

## Failure encountered and fixed

**Symptom:** at `min_signals=1` (an edge exists if ANY one signal matches),
one connected component ballooned to 37 orders spanning 5 merchants with a
risk score of 0.79 — flagged as a single giant ring. Inspecting it showed it
had silently merged a real fraud ring, a legit false-positive-trap household,
and unrelated legit orders, chained together through incidental geo-cell
overlap (many orders happened to fall in the same ~60m grid cell purely by
population density, with no device or VPA connection between them).

**Root cause:** geo-cluster overlap alone is a weak, common signal — it
means "two orders were delivered near each other," which is true of
thousands of unrelated legit orders in any dense area. Using it as a
graph-connecting signal on its own let it transitively chain unrelated
clusters into one.

**Fix:** require at least 2 of the 3 signal types to agree before drawing an
edge at all (`min_signals=2`). A single geo-cell match no longer creates a
connection by itself; it now only corroborates a device or VPA match that
already exists.

**Result:** re-running eval after the fix: 12/12 rings correctly separated,
0/6 false-positive traps wrongly flagged, versus 4/6 traps wrongly flagged
before the fix.

## Known limitations (stated honestly, not hidden)
- Fuzzy VPA matching is O(n²) — fine at hundreds/low-thousands of orders,
  needs prefix-bucketing before it would scale to production volume.
- Geo-cell threshold (60m) and signal weights were chosen by inspection on
  synthetic data, not tuned against a held-out validation set — this is a
  clearly labeled next step, not a finished calibration.
- Results above are on clean synthetic data. Harder adversarial cases
  (partial VPA rotation, rings with one non-disputing member) have not yet
  been tested.
