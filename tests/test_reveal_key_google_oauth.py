"""Regression: reveal_key used to 404 on `google_oauth_client_secret`.

A rewrite of the endpoint (see PR history for cowork/api/v1/endpoints/
settings.py) replaced the old field_map — which had a direct
"google_oauth_client_secret" -> "google_oauth_client_secret" entry — with a
lookup restricted to UI_TYPE_TO_PROVIDER (anthropic/openai/gemini/
openai-compatible only). Google's OAuth client secret isn't an LLM provider
key, so any UI affordance to reveal the stored secret (e.g. to re-paste it
into another provider's console) started 404ing.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from cowork.server import app

client = TestClient(app)


def test_reveal_key_returns_stored_google_oauth_client_secret():
    secret = "gocspx-regression-test-secret"
    put = client.put(
        "/api/v1/settings/google_oauth_client_secret",
        json={"value": secret},
    )
    assert put.status_code == 200

    r = client.get("/api/v1/settings/reveal-key/google_oauth_client_secret")
    assert r.status_code == 200
    assert r.json()["value"] == secret


def test_reveal_key_still_404s_on_a_truly_unknown_name():
    r = client.get("/api/v1/settings/reveal-key/not-a-real-key")
    assert r.status_code == 404
