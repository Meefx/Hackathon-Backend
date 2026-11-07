"""Run a synthetic API demonstration with explicit scripted review identities."""

import argparse
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.clock import FrozenClock
from app.trust.app import create_trust_app
from app.trust.settings import TrustSettings


def demo_settings():
    """Demo-only ephemeral keys. Never printed, persisted or used in production."""
    return TrustSettings(
        os.environ.get(
            "TRUST_DATABASE_URL", "postgresql://graceline@127.0.0.1:55432/graceline_trust"
        ),
        secrets.token_urlsafe(48),
        secrets.token_urlsafe(48),
        demo_auth_enabled=True,
    )


def run_scenario(client):
    tokens = {}

    def call(role, method, path, data=None, key=None):
        if role not in tokens:
            response = client.post("/auth/demo-login", json={"role": role, "principal": "cf"})
            response.raise_for_status()
            tokens[role] = response.json()["access_token"]
        response = client.request(
            method,
            path,
            json=data,
            headers={
                "Authorization": "Bearer " + tokens[role],
                "Idempotency-Key": key or str(uuid4()),
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("code", "DEMO_ACTION_FAILED"))
        return response.json()

    doc = call(
        "knowledge",
        "POST",
        "/knowledge/documents",
        {
            "title": "Synthetic respectful contact SOP",
            "text": "# Contact policy\n## Respectful reminders\nProvide a polite reminder and offer a human officer.\n## Scope\nOnly verified borrower contacts may be used.",
            "source_uri": "synthetic://demo/contact-policy",
            "source_class": "synthetic",
            "source_version": "demo-v1",
            "source_locator": "Contact policy",
            "review_status": "synthetic",
            "effective_from": "2026-01-01T00:00:00Z",
            "criticality": "critical",
            "access_roles": ["knowledge", "qa", "compliance", "operator", "personal", "manager"],
        },
    )
    call("qa", "POST", f"/knowledge/documents/{doc['id']}/qa", {})
    call(
        "compliance",
        "POST",
        f"/knowledge/documents/{doc['id']}/approve",
        {
            "note": "Scripted human role for synthetic demonstration only",
        },
    )
    search_key = str(uuid4())
    before = call("personal", "POST", "/knowledge/search", {"query": "polite reminder"}, search_key)
    template = call(
        "knowledge",
        "POST",
        "/templates",
        {
            "title": "Respectful reminder",
            "body": "Your {{product_name}} payment is {{amount_due}}. Reply HUMAN for an officer.",
            "document_ids": [doc["id"]],
            "evidence_weights": {doc["id"]: 1},
        },
    )
    call("qa", "POST", f"/templates/{template['id']}/qa", {})
    call(
        "compliance",
        "POST",
        f"/templates/{template['id']}/approve",
        {
            "note": "Scripted human role approves exact synthetic template",
        },
    )
    case = call("operator", "POST", "/cases", {"is_synthetic": True})
    key = str(uuid4())
    first = call(
        "operator", "POST", f"/cases/{case['id']}/outreach", {"template_id": template["id"]}, key
    )
    replay = call(
        "operator", "POST", f"/cases/{case['id']}/outreach", {"template_id": template["id"]}, key
    )
    impact = call(
        "compliance",
        "POST",
        f"/knowledge/documents/{doc['id']}/retire",
        {
            "note": "Withdraw source in controlled synthetic scenario",
        },
    )
    # Reuse the search key deliberately: retired knowledge must not come from a cache.
    after = call("personal", "POST", "/knowledge/search", {"query": "polite reminder"}, search_key)
    case2 = call("operator", "POST", "/cases", {"is_synthetic": True})
    blocked = call(
        "operator", "POST", f"/cases/{case2['id']}/outreach", {"template_id": template["id"]}
    )
    audit = call("compliance", "GET", "/audit/verify")
    report = {
        "simulation_mode": True,
        "human_approvals": "scripted demo roles, not legal approval",
        "classifier_profile": "mock-policy-v1",
        "semantic_embeddings": False,
        "source_id": doc["id"],
        "template_id": template["id"],
        "retrieved_before_retirement": any(
            r["document_id"] == doc["id"] for r in before["results"]
        ),
        "retrieved_after_retirement": any(r["document_id"] == doc["id"] for r in after["results"]),
        "first_attempt": first["status"],
        "duplicate_returns_same_attempt": first["attempt_id"] == replay["attempt_id"],
        "suspended_template_ids": impact["suspended_template_ids"],
        "attempt_after_retirement": blocked["status"],
        "audit": audit,
    }
    assert report["retrieved_before_retirement"] is True
    assert report["retrieved_after_retirement"] is False
    assert first["status"] == "DELIVERED"
    assert blocked["status"] == "NOT_SENT"
    assert report["duplicate_returns_same_attempt"] is True
    assert audit["valid"] is True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Serve the local interactive console")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    clock = FrozenClock(datetime(2026, 11, 9, 3, tzinfo=UTC))
    app = create_trust_app(demo_settings(), clock)
    if args.serve:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        return
    with TestClient(app) as client:
        report = run_scenario(client)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
