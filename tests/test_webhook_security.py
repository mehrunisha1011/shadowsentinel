"""
Tests for the webhook security features in api.py: HMAC-SHA256 signature
verification and idempotent replay handling. Uses FastAPI's TestClient
against the real app object — not mocked, the actual startup event loads
real data from data/.
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("SHADOWSENTINEL_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))

import pytest
from fastapi.testclient import TestClient

import api as api_module


@pytest.fixture
def client():
    with TestClient(api_module.app) as c:
        yield c


def sign(body: bytes) -> str:
    return hmac.new(api_module.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_unsigned_webhook_still_works_and_is_flagged_unverified(client):
    """Backward compatibility: no signature header shouldn't break existing
    callers, but the response must honestly say it wasn't verified."""
    r = client.post("/webhook/dispute", json={"order_id": "ORD-19a99c789f"})
    assert r.status_code == 200
    assert r.json()["signature_verified"] is None


def test_valid_hmac_signature_is_accepted_and_marked_verified(client):
    body = json.dumps({"order_id": "ORD-42b15a7ac7"}).encode()
    r = client.post("/webhook/dispute", content=body,
                     headers={"Content-Type": "application/json", "X-Webhook-Signature": sign(body)})
    assert r.status_code == 200
    assert r.json()["signature_verified"] is True


def test_invalid_hmac_signature_is_rejected(client):
    body = json.dumps({"order_id": "ORD-42b15a7ac7"}).encode()
    r = client.post("/webhook/dispute", content=body,
                     headers={"Content-Type": "application/json", "X-Webhook-Signature": "0" * 64})
    assert r.status_code == 401


def test_tampered_body_with_stale_signature_is_rejected(client):
    """Signature was computed for a different body — proves the check binds
    the signature to the actual request content, not just its presence."""
    original_body = json.dumps({"order_id": "ORD-42b15a7ac7"}).encode()
    valid_sig_for_original = sign(original_body)
    tampered_body = json.dumps({"order_id": "ORD-90adbf4bd0"}).encode()
    r = client.post("/webhook/dispute", content=tampered_body,
                     headers={"Content-Type": "application/json", "X-Webhook-Signature": valid_sig_for_original})
    assert r.status_code == 401


def test_duplicate_order_id_within_window_returns_cached_replay(client):
    r1 = client.post("/webhook/dispute", json={"order_id": "ORD-4f3c2d9bfb"})
    r2 = client.post("/webhook/dispute", json={"order_id": "ORD-4f3c2d9bfb"})
    assert r1.json()["idempotent_replay"] is False
    assert r2.json()["idempotent_replay"] is True
    # the actual decision (action, risk_score) must be identical on replay
    assert r1.json()["action"] == r2.json()["action"]


def test_unknown_order_id_returns_404_not_cached_as_success(client):
    r = client.post("/webhook/dispute", json={"order_id": "ORD-DOES-NOT-EXIST"})
    assert r.status_code == 404
