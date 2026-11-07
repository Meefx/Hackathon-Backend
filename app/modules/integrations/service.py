from app.shared.clock import utcnow

ROLES = ("manager", "collection", "knowledge", "qa", "improvement", "copilot")
DESCRIPTIONS = {
    "manager": "Coordinates account tasks and summarizes validated outcomes.",
    "collection": "Prepares respectful Indonesian message candidates.",
    "knowledge": "Retrieves approved, versioned knowledge sources.",
    "qa": "Records advisory quality findings; backend owns dispatch approval.",
    "improvement": "Proposes changes for human review without changing policy.",
    "copilot": "Creates an account brief for a human collector.",
}


def integrations(settings):
    now = utcnow().isoformat()

    def row(id, kind, implemented, configured, mode, caps, reason=None):
        return {
            "id": id,
            "kind": kind,
            "implemented": implemented,
            "configured": configured,
            "health": "healthy" if configured and implemented else "unavailable",
            "execution_mode": mode,
            "capabilities": caps,
            "disabled_reason": reason,
            "checked_at": now,
        }

    hermes = bool(settings.hermes_api_base_url and settings.hermes_api_key.get_secret_value())
    knowledge_fixture = settings.knowledge_mode == "demo_fixture" and settings.demo_fixture_enabled
    knowledge_module = settings.knowledge_mode == "in_process" and bool(
        settings.knowledge_service_factory
    )
    knowledge = knowledge_fixture or knowledge_module
    mcp = bool(settings.mcp_bearer_token.get_secret_value() and settings.mcp_token_expires_at)
    items = [
        row(
            "simulated_runtime",
            "runtime",
            True,
            settings.demo_fixture_enabled,
            "simulated",
            ["follow_up", "collector_brief", "improvement_review"],
        ),
        row(
            "hermes",
            "runtime",
            True,
            hermes,
            "hermes" if hermes else "unavailable",
            ["runs_api"],
            None if hermes else "runtime_unavailable",
        ),
        row(
            "knowledge",
            "knowledge",
            True,
            knowledge,
            "in_process" if knowledge else "unavailable",
            ["demo_fixture"]
            if knowledge_fixture
            else ["knowledge_module"]
            if knowledge_module
            else [],
            None if knowledge else "knowledge_unavailable",
        ),
        row(
            "mcp",
            "mcp",
            True,
            mcp,
            "in_process" if mcp else "unavailable",
            ["streamable_http"],
            None if mcp else "credential_not_configured",
        ),
        row(
            "policy_classifier",
            "guardrail",
            False,
            False,
            "unavailable",
            [],
            "Live classifier is not configured; Hermes drafts are quarantined.",
        ),
        row(
            "simulated_chat",
            "channel",
            True,
            True,
            "simulated",
            ["synthetic_dispatch", "synthetic_inbound"],
        ),
    ]
    for name in ("whatsapp", "email", "phone"):
        items.append(row(name, "channel", False, False, "unavailable", [], "channel_unavailable"))
    # Configured means config exists, not an unperformed live health check.
    for item in items:
        if (
            item["id"] in ("hermes", "mcp") or item["id"] == "knowledge" and knowledge_module
        ) and item["configured"]:
            item["health"] = "unknown"
    return {"items": items}


def agents(settings):
    return {
        "items": [
            {
                "id": role,
                "role": role,
                "name": role.title(),
                "description": DESCRIPTIONS[role],
                "configured": settings.demo_fixture_enabled,
                "available": settings.demo_fixture_enabled,
                "runtime_mode": "simulated" if settings.demo_fixture_enabled else "unavailable",
                "capabilities": [
                    "demo_fixture",
                    {
                        "manager": "orchestrate",
                        "collection": "draft_candidate",
                        "knowledge": "retrieve_approved_context",
                        "qa": "qa_report",
                        "improvement": "improvement_review",
                        "copilot": "collector_brief",
                    }[role],
                ],
                "unavailable_reason": None
                if settings.demo_fixture_enabled
                else "runtime_unavailable",
                "checked_at": utcnow().isoformat(),
            }
            for role in ROLES
        ]
    }
