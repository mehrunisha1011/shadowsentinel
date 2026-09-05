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
                                        (HMAC-signed if X-Webhook-Signature is
                                        sent; idempotent on repeated order_id)
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
import hashlib
import hmac
import json
import os
import time

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from generate_dossier import build_dossier, load_orders as load_orders_for_dossier

DATA_DIR = os.environ.get("SHADOWSENTINEL_DATA_DIR", "../data")

# DEMO-ONLY webhook secret. In a real deployment this would be a per-merchant
# secret issued out-of-band (e.g. shown once in a dashboard, stored server-side
# only) and never shipped in frontend JS. It's exposed here deliberately so the
# live demo can compute a real signature client-side — see frontend/index.html.
# This is stated plainly rather than pretending it's production-secure.
WEBHOOK_SECRET = os.environ.get("SHADOWSENTINEL_WEBHOOK_SECRET", "demo-shared-secret-not-for-production")

IDEMPOTENCY_WINDOW_SECONDS = 60

app = FastAPI(title="ShadowSentinel API", version="0.2.0")

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
_idempotency_cache = {}  # order_id -> (timestamp, response_dict)


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


def verify_hmac_signature(raw_body: bytes, signature: str) -> bool:
    """Constant-time HMAC-SHA256 verification, same pattern real payment
    webhooks (Razorpay, Stripe, etc.) use to prove a request actually came
    from the sender and wasn't forged or tampered in transit."""
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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
async def handle_dispute_webhook(request: Request, x_webhook_signature: str | None = Header(default=None)):
    """
    Simulates what happens when Razorpay (or any PSP) fires a
    payment.dispute.created webhook: look up the order, check if it's
    part of a flagged collusion cluster, and tell the caller whether to
    auto-generate an arbitration dossier or handle it as an isolated dispute.

    Security & reliability, demonstrated for real (not just described):
    - If X-Webhook-Signature is sent, it's verified with HMAC-SHA256 against
      the raw request body. An invalid signature is rejected with 401.
      No signature header at all is still accepted (so this doesn't break
      any existing caller) but the response is honestly flagged
      "signature_verified": false rather than silently treated as trusted.
    - Idempotency: firing the same order_id twice within 60 seconds returns
      the exact same cached response instead of reprocessing it — this is
      the standard pattern for safely handling webhook retries/duplicates.
    """
    raw_body = await request.body()

    signature_verified = None  # None = no signature sent at all
    if x_webhook_signature is not None:
        signature_verified = verify_hmac_signature(raw_body, x_webhook_signature)
        if not signature_verified:
            raise HTTPException(status_code=401, detail="invalid webhook signature")

    try:
        payload = DisputeWebhook.model_validate_json(raw_body)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid request body")

    # --- idempotency check ---
    now = time.time()
    cached = _idempotency_cache.get(payload.order_id)
    if cached and (now - cached[0]) < IDEMPOTENCY_WINDOW_SECONDS:
        replay = dict(cached[1])
        replay["idempotent_replay"] = True
        return replay

    if payload.order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"order_id {payload.order_id} not found")

    component_id = _order_to_component.get(payload.order_id)

    if not component_id:
        result = {
            "order_id": payload.order_id,
            "event": payload.event,
            "action": "handle_as_isolated_dispute",
            "reason": "order is not part of any detected collusion cluster",
            "signature_verified": signature_verified,
            "idempotent_replay": False,
        }
        _idempotency_cache[payload.order_id] = (now, result)
        return result

    component = next(c for c in _components if c["component_id"] == component_id)

    if not component["flagged"]:
        result = {
            "order_id": payload.order_id,
            "event": payload.event,
            "action": "handle_as_isolated_dispute",
            "component_id": component_id,
            "risk_score": component["risk_score"],
            "reason": "cluster exists but is below the flag threshold",
            "signature_verified": signature_verified,
            "idempotent_replay": False,
        }
        _idempotency_cache[payload.order_id] = (now, result)
        return result

    result = {
        "order_id": payload.order_id,
        "event": payload.event,
        "action": "escalate_and_generate_dossier",
        "component_id": component_id,
        "risk_score": component["risk_score"],
        "why": component["why"],
        "dossier_url": f"/dossier/{component_id}",
        "signature_verified": signature_verified,
        "idempotent_replay": False,
    }
    _idempotency_cache[payload.order_id] = (now, result)
    return result


@app.get("/dossier/{component_id}")
def get_dossier(component_id: str):
    component = next((c for c in _components if c["component_id"] == component_id), None)
    if not component:
        raise HTTPException(status_code=404, detail=f"cluster {component_id} not found")

    outpath = f"{DATA_DIR}/dossier_{component_id}_live.pdf"
    build_dossier(component, _orders, outpath)
    return FileResponse(outpath, media_type="application/pdf",
                         filename=f"dossier_{component_id}.pdf")
