import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required")
@pytest.mark.parametrize("export_dashboard_credentials", [False, True])
def test_local_dashboard_and_swagger_share_one_basic_auth_pair(
    tmp_path, export_dashboard_credentials
):
    root = Path(__file__).resolve().parents[3]
    compose = tmp_path / "compose.waha.yaml"
    shutil.copyfile(root / "compose.waha.yaml", compose)
    config = tmp_path / ".env.waha.local"
    config.write_text(
        "WAHA_PORT=3002\n"
        "WAHA_API_KEY=synthetic-api-key\n"
        "WAHA_DASHBOARD_USERNAME=synthetic-dashboard-user\n"
        "WAHA_DASHBOARD_PASSWORD=synthetic-dashboard-password\n"
        "WHATSAPP_SWAGGER_USERNAME=legacy-swagger-user\n"
        "WHATSAPP_SWAGGER_PASSWORD=legacy-swagger-password\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WAHA_", "WHATSAPP_"))
    }
    if export_dashboard_credentials:
        environment.update(
            WAHA_DASHBOARD_USERNAME="exported-dashboard-user",
            WAHA_DASHBOARD_PASSWORD="exported-dashboard-password",
        )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(config),
            "--file",
            str(compose),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    credentials = json.loads(result.stdout)["services"]["waha"]["environment"]
    assert credentials["WHATSAPP_SWAGGER_USERNAME"] == credentials["WAHA_DASHBOARD_USERNAME"]
    assert credentials["WHATSAPP_SWAGGER_PASSWORD"] == credentials["WAHA_DASHBOARD_PASSWORD"]
    assert credentials["WAHA_DASHBOARD_PASSWORD"] != credentials["WAHA_API_KEY"]
    assert credentials["WAHA_API_KEY"] == "synthetic-api-key"
