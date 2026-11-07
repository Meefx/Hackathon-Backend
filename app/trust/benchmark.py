"""Small local sequential API benchmark, explicitly excluding live inference."""

import argparse
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.clock import FrozenClock
from app.trust.app import create_trust_app
from app.trust.demo import demo_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=50)
    args = parser.parse_args()
    if not 20 <= args.requests <= 1000:
        parser.error("requests must be between 20 and 1000")
    app = create_trust_app(demo_settings(), FrozenClock(datetime(2026, 11, 9, 3, tzinfo=UTC)))
    durations = []
    with TestClient(app) as client:
        token = client.post("/api/v1/trust/auth/demo-login", json={"role": "personal"}).json()[
            "access_token"
        ]
        for index in range(args.requests + 5):
            start = time.perf_counter()
            response = client.post(
                "/api/v1/trust/knowledge/search",
                json={"query": "polite reminder"},
                headers={
                    "Authorization": "Bearer " + token,
                    "Idempotency-Key": str(uuid4()),
                },
            )
            elapsed = (time.perf_counter() - start) * 1000
            response.raise_for_status()
            if index >= 5:
                durations.append(elapsed)
    durations.sort()
    print(
        json.dumps(
            {
                "profile": "local_sequential_testclient_pg_search_and_audit",
                "requests": len(durations),
                "warmup_requests": 5,
                "p50_ms": round(durations[len(durations) // 2], 2),
                "p95_ms": round(durations[int(len(durations) * 0.95) - 1], 2),
                "max_ms": round(durations[-1], 2),
                "live_inference": False,
                "production_load_test": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
