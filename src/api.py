"""
ShadowSentinel — API layer.

Simulates the real integration point: a payment.dispute.created webhook
hitting the system. In production this would be a genuine Razorpay webhook;
here it's a POST endpoint you can call the same way to demo the full
pipeline live instead of just showing pre-computed JSON files.

Endpoints:
  GET  /health
  GET  /clusters                    -> all flagged clusters, sorted by risk
  GET  /order/{order_id}            -> which cluster (if any) an order belongs to
  POST /webhook/dispute             -> simulated payment.dispute.created handler
  GET  /dossier/{component_id}      -> generate + download the PDF dossier on demand

Run with:
  cd src
  uvicorn api:app --reload --port 8000

Then try:
  curl http://localhost:8000/health
  curl http://localhost:8000/clusters
  curl -X POST http://localhost:8000/webhook/dispute -H "Content-Type: application/json" -d '{"order_id": "ORD-xxxx"}'
  curl http://localhost:8000/dossier/CC-0008 --output dossier.pdf
"""

import csv
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from generate_dossier import build_dossier, load_orders as load_orders_for_dossier

DATA_DIR = os.environ.get("SHADOWSENTINEL_DATA_DIR", "../data")

app = FastAPI(title="ShadowSentinel API", version="0.1.0")

# Demo-only: permissive CORS so the local static frontend (opened as a file
# or served separately) can call this API during development/judging.
# A real deployment would restrict this to the actual frontend origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- loaded once at startup ----
_orders = {}
_components = []
_order_to_component = {}


@app.on_event("startup")
def load_data():
    global _orders, _components, _order_to_component
    with open(f"{DATA_DIR}/orders.csv") as f:
        _orders = {row["order_id"]: row for row in csv.DictReader(f)}

    report = json.load(open(f"{DATA_DIR}/graph_report.json"))
    _components = report["components"]

    _order_to_component = {}
    for c in _components:
        for oid in c["order_ids"]:
            _order_to_component[oid] = c["component_id"]

    print(f"Loaded {len(_orders)} orders, {len(_components)} clusters")


class DisputeWebhook(BaseModel):
    order_id: str
    event: str = "payment.dispute.created"


@app.get("/health")
def health():
    return {"status": "ok", "orders_loaded": len(_orders), "clusters_loaded": len(_components)}


@app.get("/clusters")
def list_flagged_clusters():
    flagged = [c for c in _components if c["flagged"]]
    flagged.sort(key=lambda c: -c["risk_score"])
    return {"count": len(flagged), "clusters": flagged}


@app.get("/order/{order_id}")
def get_order_cluster(order_id: str):
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"order_id {order_id} not found")

    component_id = _order_to_component.get(order_id)
    if not component_id:
        return {"order_id": order_id, "in_cluster": False, "risk_score": 0.0, "flagged": False}

    component = next(c for c in _components if c["component_id"] == component_id)
    return {
        "order_id": order_id,
        "in_cluster": True,
        "component_id": component_id,
        "risk_score": component["risk_score"],
        "flagged": component["flagged"],
        "why": component["why"],
    }


@app.post("/webhook/dispute")
def handle_dispute_webhook(payload: DisputeWebhook):
    """
    Simulates what happens when Razorpay (or any PSP) fires a
    payment.dispute.created webhook: look up the order, check if it's
    part of a flagged collusion cluster, and tell the caller whether to
    auto-generate an arbitration dossier or handle it as an isolated dispute.
    """
    if payload.order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"order_id {payload.order_id} not found")

    component_id = _order_to_component.get(payload.order_id)
    if not component_id:
        return {
            "order_id": payload.order_id,
            "event": payload.event,
            "action": "handle_as_isolated_dispute",
            "reason": "order is not part of any detected collusion cluster",
        }

    component = next(c for c in _components if c["component_id"] == component_id)
    if not component["flagged"]:
        return {
            "order_id": payload.order_id,
            "event": payload.event,
            "action": "handle_as_isolated_dispute",
            "component_id": component_id,
            "risk_score": component["risk_score"],
            "reason": "cluster exists but is below the flag threshold",
        }

    return {
        "order_id": payload.order_id,
        "event": payload.event,
        "action": "escalate_and_generate_dossier",
        "component_id": component_id,
        "risk_score": component["risk_score"],
        "why": component["why"],
        "dossier_url": f"/dossier/{component_id}",
    }


@app.get("/dossier/{component_id}")
def get_dossier(component_id: str):
    component = next((c for c in _components if c["component_id"] == component_id), None)
    if not component:
        raise HTTPException(status_code=404, detail=f"cluster {component_id} not found")

    outpath = f"{DATA_DIR}/dossier_{component_id}_live.pdf"
    build_dossier(component, _orders, outpath)
    return FileResponse(outpath, media_type="application/pdf",
                         filename=f"dossier_{component_id}.pdf")
