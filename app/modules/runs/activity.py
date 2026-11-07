"""Read-only runtime activity, independent of durable workflow state."""

from dataclasses import asdict

from app.config import Settings
from app.db.connection import connect
from app.integrations.agents.contracts import RuntimeError
from app.integrations.hermes.runtime import create_hermes_runtime
from app.modules.runs import repository as repo
from app.modules.runs.schemas import RunActivityPage


def get_run_activity(
    settings: Settings, run_id: str, after_sequence: int = -1, limit: int = 100,
) -> RunActivityPage:
    with connect(settings.database_url) as connection:
        run = repo.require_run(connection, run_id)
        mode = run["actual_runtime_mode"] or run["requested_runtime_mode"]
        external_run_id = run["external_run_id"]
        queued = run["status"] == "queued"
    # Release the database transaction before subscribing to the external stream.
    page = RunActivityPage(source=mode, status="available", next_sequence=after_sequence)
    if mode == "simulated":
        return page
    if not external_run_id:
        page.status = "not_started" if queued else "unavailable"
        page.reason_code = None if queued else "runtime_activity_not_started"
        return page
    if not settings.hermes_api_base_url or not settings.hermes_api_key.get_secret_value():
        page.status, page.reason_code = "unavailable", "runtime_not_configured"
        return page
    try:
        replay = create_hermes_runtime(settings).get_activity(
            external_run_id, after_sequence=after_sequence, limit=limit,
        )
        return RunActivityPage.model_validate({
            "source": mode, "status": "available", **asdict(replay),
        })
    except RuntimeError as error:
        page.status, page.reason_code = "unavailable", error.code
    except ValueError:
        page.status, page.reason_code = "unavailable", "runtime_activity_invalid"
    return page
