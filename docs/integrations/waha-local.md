# Local WAHA sandbox

This sandbox runs the self-hosted WAHA Core release `2026.10.1` from the official
`devlikeapro/waha:latest-2026.10.1` image. The versioned tag targets Linux AMD64,
matching the current workspace. WAHA uses the GOWS engine and publishes only
`127.0.0.1:3002`, alongside the frontend on port 3000. Its independent Compose
project is `collectflow-waha-local`.

Use a team-owned WhatsApp test account and an explicitly consenting team-owned
recipient. Test content must be synthetic. Collection runs use `simulated_chat`.
The WAHA CLI provides a separate manual connectivity test with fixed canned text.

## Start

Requires Docker Engine with Compose v2 and Python 3. Run from the backend repository.

```sh
./scripts/waha-local.sh
```

The helper creates `.env.waha.local` with independent random API and dashboard
secrets. Dashboard and Swagger share the dashboard login on this origin. The helper
restricts permissions to mode `600`, preserves existing values
on rerun, validates Compose quietly, and waits for the authenticated `/health`
check. Git already ignores `.env.*`. The helper explicitly selects the dedicated
WAHA environment file. Before starting, it checks whether the selected host port
is available or already serves this authenticated WAHA instance. After startup,
it verifies authenticated `/health` and `/api/server/version` responses through
the host port before reporting the dashboard ready.

Open `.env.waha.local` in your local editor to access credentials. Keep its values
out of shared logs, screenshots, chat, and commits. The helper prints only status
and local URLs. For configuration preparation and validation alone, use these
commands.

```sh
./scripts/waha-local.sh init
./scripts/waha-local.sh config
```

## Pair the team test phone

1. Open [the WAHA dashboard](http://127.0.0.1:3002/dashboard).
2. Sign in with `WAHA_DASHBOARD_USERNAME` and `WAHA_DASHBOARD_PASSWORD` from
   `.env.waha.local`.
3. Configure the dashboard server at `http://127.0.0.1:3002` with the
   `WAHA_API_KEY` from that file.
4. Open the existing `default` session. Create and start it using GOWS if missing
   or stopped.
5. Display that session's QR in the dashboard. On the team test phone, open
   WhatsApp linked devices and scan it manually.
6. Wait until the session reports `WORKING`.

The API reference is available at [the local Swagger UI](http://127.0.0.1:3002/).
Both interfaces use `WAHA_DASHBOARD_USERNAME` and `WAHA_DASHBOARD_PASSWORD`.
Compose applies this same pair to Dashboard and Swagger, including configurations
created by an earlier helper. A shared pair allows the browser to reuse Basic
authentication when moving between the two pages. API requests authenticate through the
`X-Api-Key` header. QR material is hidden from container logs.

## Check connectivity

The host-side CLI uses the backend's existing `uv` environment and reads
`.env.waha.local` by default. It reports a bounded session status and can save the
QR locally for manual scanning.

```sh
uv sync --locked
uv run python -m app.integrations.channels.waha status
uv run python -m app.integrations.channels.waha qr
# Optional destination for the QR image
uv run python -m app.integrations.channels.waha qr --output data/waha/test-qr.png
```

The default QR destination is `data/waha/qr.png`, within the gitignored `data/`
directory. Treat QR images as temporary pairing credentials and delete them after
pairing.

In your local editor, set `WAHA_TEST_RECIPIENT` in `.env.waha.local` to the
consenting team's international phone number containing digits only, such as
`6281234567890`. The CLI adds the provider's chat suffix. Once the session is
`WORKING`, explicitly send one canned sandbox message.

```sh
uv run python -m app.integrations.channels.waha send-test
```

Each invocation requests one real WhatsApp test message. Verify receipt on the
team-owned phone. A provider acceptance response records acceptance only. Inspect
the recipient before each invocation and reconcile an ambiguous timeout before
trying again.

## Stop and resume

```sh
./scripts/waha-local.sh status
./scripts/waha-local.sh down
./scripts/waha-local.sh up
```

The named `collectflow-waha-local_waha_sessions` volume stores linked-device
sessions across container recreation. `down` retains it. Deleting this volume or
logging out the session requires pairing again. The session volume contains
WhatsApp authentication material and needs the same protection as credentials.

To update WAHA, review an official release and change the pinned tag in
`compose.waha.yaml` and the expected version in `scripts/waha-local.sh` together.
Run the helper with `down` followed by `up` to apply the update. For ARM64 hosts, choose the
official version-matched `arm-2026.10.1` image and verify the engine on that host.

## Troubleshooting

- This sandbox uses GOWS after the pinned WEBJS engine failed to refresh its QR
  with `Cmd.refreshQR is not a function`. GOWS connects through WebSocket and
  provides the same documented session status and QR endpoints used by the CLI.
- If Docker is unavailable, start Docker Desktop and rerun the helper. Configuration
  and generated credentials survive the failed start.
- If port 3002 is occupied, choose a free `WAHA_PORT` in `.env.waha.local` and
  update `WAHA_BASE_URL` to `http://127.0.0.1:<WAHA_PORT>`, then rerun the helper.
  Open the dashboard on the selected port. Existing matching WAHA instances pass
  the authenticated preflight check and support idempotent startup.
- If authentication fails, verify the matching credentials in `.env.waha.local`
  and use `./scripts/waha-local.sh down` followed by `./scripts/waha-local.sh up`
  after a deliberate credential edit. This applies the new credentials before
  the authenticated host checks run.
- If a session is missing, create `default` in the dashboard. If its QR expires,
  refresh it and scan the current QR. A `SCAN_QR_CODE` session still needs pairing.
- If health fails, inspect container status with the helper. Review Docker logs
  locally and redact sensitive material before sharing any excerpt.
  A running instance that fails the pre-start host check can be recovered with
  `./scripts/waha-local.sh down` followed by `./scripts/waha-local.sh up`.

## Official references

- [Release 2026.10.1](https://github.com/devlikeapro/waha/releases/tag/2026.10.1)
- [Official versioned image tag](https://hub.docker.com/r/devlikeapro/waha/tags?name=latest-2026.10.1)
- [Installation and session storage](https://waha.devlike.pro/docs/how-to/install/)
- [API, dashboard, and Swagger authentication](https://waha.devlike.pro/docs/how-to/security/)
- [Dashboard session management](https://waha.devlike.pro/docs/how-to/dashboard/)
- [Health checks](https://waha.devlike.pro/docs/how-to/observability/)
