"""Operator reads of bounded, content-free runtime telemetry."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.config import get_settings
from app.modules.runs.activity import get_run_activity
from app.modules.runs.schemas import RunActivityPage

router = APIRouter(prefix="/api/v1/runs", tags=["Runs"])


@router.get("/{run_id}/activity", response_model=RunActivityPage)
def run_activity(
    run_id: str,
    after_sequence: Annotated[int, Query(ge=-1)] = -1,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> RunActivityPage:
    return get_run_activity(get_settings(), run_id, after_sequence, limit)
