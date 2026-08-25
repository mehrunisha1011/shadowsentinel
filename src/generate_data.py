"""
ShadowSentinel — synthetic data generator.

Produces:
  data/orders.csv       -> feature set the detector sees (NO ground-truth label in it)
  data/ground_truth.json -> held-out labels, used only for eval / precision-recall

Design goals:
  - Ring orders: same/near-identical device fingerprint + fuzzy-rotating UPI VPA
    + tight delivery geo-cluster, spread across >=3 merchants, each followed by a
    "friendly fraud" dispute (delivered -> chargeback claiming item not received).
  - Noise: legit customers who coincidentally share a delivery cluster (apartment
    block) or a device (family members) but are NOT part of a ring. This is the
    deliberate false-positive trap — a real ops team would get burned by these,
    and your eval has to show you don't.
  - Everything is seeded (--seed) for reproducibility.
"""

import argparse
import csv
import json
import random
import string
import uuid
from datetime import datetime, timedelta

from faker import Faker

fake = Faker("en_IN")

MERCHANTS = ["M_ElectroHub", "M_FashionNest", "M_HomeBazaar", "M_GadgetKart", "M_TrendMart"]

# rough India-wide lat/lon bounding boxes for a few metro clusters (fake but plausible)
GEO_CLUSTERS = {
    "Bengaluru_Koramangala": (12.9352, 77.6245),
    "Mumbai_Andheri": (19.1197, 72.8468),
    "Delhi_Dwarka": (28.5921, 77.0460),
    "Chennai_Adyar": (13.0012, 80.2565),
    "Hyderabad_Gachibowli": (17.4400, 78.3489),
}


def jitter(lat, lon, meters=30):
    # ~0.00001 deg ~= 1.1m at the equator, good enough for fake data
    d = meters / 111000.0
    return round(lat + random.uniform(-d, d), 6), round(lon + random.uniform(-d, d), 6)


def fuzzy_vpa_family(base_name):
    """Generate a rotating family of fuzzy UPI VPAs, e.g. rahul.k91 / rahul.k92 / rahulk91"""
    handles = ["ybl", "okhdfcbank", "oksbi", "paytm", "ibl"]
    variants = []
    for _ in range(4):
        noise = random.choice(["", str(random.randint(1, 99)), "_" + fake.random_letter()])
        name = base_name.replace(" ", "").lower() + noise
        variants.append(f"{name}@{random.choice(handles)}")
    return variants


def device_fp(seed_str=None):
    if seed_str is None:
        seed_str = uuid.uuid4().hex
    return "dfp_" + "".join(random.choices(string.hexdigits.lower()[:16], k=20))


def gen_legit_order(order_id, merchant, date, shared_device=None, shared_geo=None):
    lat, lon = shared_geo if shared_geo else random.choice(list(GEO_CLUSTERS.values()))
    lat, lon = jitter(lat, lon, meters=random.randint(20, 3000))
    return {
        "order_id": order_id,
        "merchant_id": merchant,
        "customer_name": fake.name(),
        "device_fingerprint": shared_device or device_fp(),
        "upi_vpa": f"{fake.user_name()}@{random.choice(['ybl','oksbi','paytm','okhdfcbank'])}",
        "delivery_lat": lat,
        "delivery_lon": lon,
        "order_amount_inr": round(random.uniform(499, 45000), 2),
        "order_date": date.isoformat(),
        "dispute_filed": random.random() < 0.03,  # baseline organic dispute rate
        "dispute_reason": "item_not_as_described" if random.random() < 0.5 else "",
    }


def gen_ring(ring_id, n_orders, start_date):
    """A friendly-fraud collusion ring spanning multiple merchants."""
    base_name = fake.name()
    device = device_fp()
    geo_center = random.choice(list(GEO_CLUSTERS.values()))
    vpa_pool = fuzzy_vpa_family(base_name)
    merchants_used = random.sample(MERCHANTS, k=min(n_orders, len(MERCHANTS)))

    orders = []
    for i in range(n_orders):
        merchant = merchants_used[i % len(merchants_used)]
        lat, lon = jitter(*geo_center, meters=random.randint(5, 80))  # ring stays tight
        date = start_date + timedelta(days=random.randint(0, 20))
        orders.append({
            "order_id": f"ORD-{uuid.uuid4().hex[:10]}",
            "merchant_id": merchant,
            "customer_name": base_name if random.random() < 0.5 else fake.name(),  # some use aliases
            "device_fingerprint": device,
            "upi_vpa": random.choice(vpa_pool),
            "delivery_lat": lat,
            "delivery_lon": lon,
            "order_amount_inr": round(random.uniform(8000, 60000), 2),  # rings target higher-value items
            "order_date": date.isoformat(),
            "dispute_filed": True,
            "dispute_reason": "item_not_received",
        })
    return ring_id, orders


def gen_family_false_positive_trap(trap_id, start_date):
    """
    Legit case designed to LOOK like a ring: same household device fingerprint,
    same delivery address, across 2 merchants, but NO disputes filed and NO fuzzy
    VPA rotation. A naive device/geo-only detector will false-positive on this.
    """
    device = device_fp()
    geo_center = random.choice(list(GEO_CLUSTERS.values()))
    family_names = [fake.name(), fake.name()]  # two household members
    orders = []
    for i in range(3):
        lat, lon = jitter(*geo_center, meters=random.randint(2, 15))
        date = start_date + timedelta(days=random.randint(0, 10))
        orders.append({
            "order_id": f"ORD-{uuid.uuid4().hex[:10]}",
            "merchant_id": random.choice(MERCHANTS),
            "customer_name": random.choice(family_names),
            "device_fingerprint": device,
            "upi_vpa": f"{random.choice(family_names).replace(' ','').lower()}@oksbi",
            "delivery_lat": lat,
            "delivery_lon": lon,
            "order_amount_inr": round(random.uniform(500, 5000), 2),
            "order_date": date.isoformat(),
            "dispute_filed": False,
            "dispute_reason": "",
        })
    return trap_id, orders


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legit", type=int, default=800, help="number of standalone legit orders")
    ap.add_argument("--rings", type=int, default=12, help="number of collusion rings")
    ap.add_argument("--ring-size-min", type=int, default=4)
    ap.add_argument("--ring-size-max", type=int, default=9)
    ap.add_argument("--fp-traps", type=int, default=6, help="false-positive trap households")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", type=str, default="../data")
    args = ap.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)

    all_orders = []
    ground_truth = {"rings": {}, "fp_traps": {}}

    start = datetime(2026, 1, 1)

    # 1. standalone legit orders (some share incidental geo clusters, most fully independent)
    for i in range(args.legit):
        merchant = random.choice(MERCHANTS)
        date = start + timedelta(days=random.randint(0, 200))
        # ~15% chance this legit order lands in a busy apartment geo-cluster (noise)
        shared_geo = random.choice(list(GEO_CLUSTERS.values())) if random.random() < 0.15 else None
        all_orders.append(gen_legit_order(f"ORD-{uuid.uuid4().hex[:10]}", merchant, date, shared_geo=shared_geo))

    # 2. collusion rings
    for r in range(args.rings):
        ring_id = f"RING-{r+1:03d}"
        n = random.randint(args.ring_size_min, args.ring_size_max)
        ring_start = start + timedelta(days=random.randint(0, 180))
        _, orders = gen_ring(ring_id, n, ring_start)
        for o in orders:
            all_orders.append(o)
        ground_truth["rings"][ring_id] = [o["order_id"] for o in orders]

    # 3. false-positive traps (label these explicitly as NOT fraud, for eval)
    for t in range(args.fp_traps):
        trap_id = f"TRAP-{t+1:03d}"
        trap_start = start + timedelta(days=random.randint(0, 180))
        _, orders = gen_family_false_positive_trap(trap_id, trap_start)
        for o in orders:
            all_orders.append(o)
        ground_truth["fp_traps"][trap_id] = [o["order_id"] for o in orders]

    random.shuffle(all_orders)

    # write orders.csv (the detector only ever sees this file)
    fieldnames = list(all_orders[0].keys())
    with open(f"{args.outdir}/orders.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_orders)

    # write ground_truth.json (held out, eval-only)
    with open(f"{args.outdir}/ground_truth.json", "w") as f:
        json.dump(ground_truth, f, indent=2)

    n_ring_orders = sum(len(v) for v in ground_truth["rings"].values())
    n_trap_orders = sum(len(v) for v in ground_truth["fp_traps"].values())
    print(f"Generated {len(all_orders)} orders total:")
    print(f"  {args.legit} standalone legit")
    print(f"  {n_ring_orders} ring orders across {args.rings} rings")
    print(f"  {n_trap_orders} false-positive-trap orders across {args.fp_traps} households")
    print(f"Wrote {args.outdir}/orders.csv and {args.outdir}/ground_truth.json")


if __name__ == "__main__":
    main()
