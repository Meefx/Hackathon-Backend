#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file="$repo_dir/.env.waha.local"
action=${1:-up}

case "$action" in
    init|up|config|status|down) ;;
    *) printf '%s\n' 'Usage: scripts/waha-local.sh [init|up|config|status|down]' >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
    printf '%s\n' 'Usage: scripts/waha-local.sh [init|up|config|status|down]' >&2
    exit 2
fi

# This dedicated local file is the only credential file managed by this helper.
waha_port=$(python3 - "$env_file" <<'PY'
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import sys

path = Path(sys.argv[1])
if path.is_symlink():
    sys.exit("Refusing a symlink for .env.waha.local.")

flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(path, flags, 0o600)
with os.fdopen(descriptor, "r+", encoding="utf-8") as file:
    fcntl.flock(file, fcntl.LOCK_EX)
    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
        sys.exit(".env.waha.local must be a regular file.")
    os.fchmod(file.fileno(), 0o600)
    contents = file.read()
    existing = {}
    for line in contents.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            sys.exit("Invalid assignment in .env.waha.local. Use KEY=value lines.")
        if key in existing:
            sys.exit(f"Duplicate configuration key {key} in .env.waha.local.")
        existing[key] = value.strip().strip("\"'")

    defaults = {
        "WAHA_PORT": "3002",
        "WAHA_BASE_URL": "http://127.0.0.1:3002",
        "WAHA_API_KEY": secrets.token_hex(32),
        "WAHA_SESSION": "default",
        "WAHA_TIMEOUT_SECONDS": "10",
        "WAHA_TEST_RECIPIENT": "",
        "WAHA_COLLECTION_ENABLED": "true",
        "WAHA_WEBHOOK_SECRET": secrets.token_hex(32),
        "WAHA_WEBHOOK_URL": "http://host.docker.internal:8000/api/v1/webhooks/waha",
        "WAHA_DASHBOARD_USERNAME": "waha-local",
        "WAHA_DASHBOARD_PASSWORD": secrets.token_hex(24),
    }
    required = set(defaults) - {"WAHA_TEST_RECIPIENT"}
    for key in required:
        if key in existing and not existing[key]:
            sys.exit(f"{key} must have a value in .env.waha.local.")

    settings = {**defaults, **existing}
    port = settings["WAHA_PORT"]
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
        sys.exit("WAHA_PORT must be an integer from 1 to 65535.")
    if settings["WAHA_BASE_URL"].rstrip("/") != f"http://127.0.0.1:{port}":
        sys.exit("Set WAHA_BASE_URL to http://127.0.0.1:<WAHA_PORT> in .env.waha.local.")

    additions = [f"{key}={value}\n" for key, value in defaults.items() if key not in existing]
    if additions:
        if not contents:
            file.write("# Local WAHA sandbox credentials. Keep this file private.\n")
        elif not contents.endswith("\n"):
            file.write("\n")
        file.writelines(additions)
        file.flush()
        os.fsync(file.fileno())
    print(port)
PY
)

if [ "$action" = init ]; then
    printf '%s\n' 'Local WAHA configuration is ready in .env.waha.local (mode 600).'
    exit 0
fi

compose() {
    env -u WAHA_PORT -u WAHA_DASHBOARD_USERNAME -u WAHA_DASHBOARD_PASSWORD \
        docker compose --project-name collectflow-waha-local \
        --env-file "$env_file" --file "$repo_dir/compose.waha.yaml" "$@"
}

verify_host() {
    python3 - "$env_file" "$waha_port" "$1" "${2:-}" <<'PY'
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import socket
import sys
import time

path, port_text, mode, running_container = sys.argv[1:]
port = int(port_text)
if mode == "before":
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            if not running_container:
                sys.exit(f"Port {port} is occupied. Choose a free WAHA_PORT and matching WAHA_BASE_URL.")
        else:
            sys.exit(0)

values = {}
for line in Path(path).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator:
        values[key.strip()] = value.strip().strip("\"'")

deadline = time.monotonic() + (15 if mode == "after" else 0)
while True:
    try:
        for endpoint, expected in (
            ("/health", {"status": "ok"}),
            ("/api/server/version", {"version": "2026.10.1"}),
        ):
            connection = HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", endpoint, headers={"X-Api-Key": values["WAHA_API_KEY"]})
                response = connection.getresponse()
                if response.status != 200:
                    raise ValueError("Unexpected HTTP status")
                body = json.loads(response.read(4096))
                if not isinstance(body, dict) or any(body.get(key) != value for key, value in expected.items()):
                    raise ValueError("Unexpected WAHA response")
            finally:
                connection.close()
    except (HTTPException, OSError, ValueError):
        if time.monotonic() >= deadline:
            sys.exit(
                f"Host authentication and WAHA version verification failed on port {port}. "
                "Check the published port and choose a free WAHA_PORT with matching WAHA_BASE_URL."
            )
        time.sleep(1)
    else:
        break
PY
}

compose config --quiet
case "$action" in
    up)
        running_container=$(compose ps --status running --quiet waha)
        verify_host before "$running_container"
        compose up --detach --wait --wait-timeout 180
        verify_host after
        printf '%s\n' \
            "WAHA dashboard is ready at http://127.0.0.1:$waha_port/dashboard." \
            'Use the dashboard credentials in .env.waha.local, then add its API key in the dashboard.' \
            'Open the default session (create/start it if needed), then scan its QR manually with the team test phone.'
        ;;
    config) printf '%s\n' 'WAHA Compose configuration is valid.' ;;
    status) compose ps ;;
    down) compose down ;;
esac
