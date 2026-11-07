# Gmail API team sandbox

The backend provides a Gmail API connection sandbox using OAuth 2.0 with the
`gmail.send` scope. An operator authorizes a team account locally, checks token
access, and sends a fixed synthetic connection-test email to one configured
team-owned recipient. The Integrations screen displays configuration separately
from live health. Collection tasks use the existing synthetic dispatch workflow.

## Google setup

1. Select or create a project in Google Cloud Console and enable Gmail API.
2. Configure Google Auth Platform branding and audience. For an external app in
   Testing, add the team sender account under test users.
3. Configure the Gmail send permission
   `https://www.googleapis.com/auth/gmail.send`.
4. Create an OAuth client with application type **Desktop app**. The local
   authorization helper uses a loopback callback with PKCE.
5. Copy the client ID and client secret into backend `.env`. Configure the sender
   as the authorized Gmail address or one of its configured send-as aliases, and
   choose an explicitly consenting team test recipient.

```dotenv
GMAIL_CLIENT_ID=your-desktop-client.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your-client-secret
GMAIL_SENDER_EMAIL=team-sender@gmail.com
GMAIL_TEST_RECIPIENT=team-recipient@example.com
GMAIL_REFRESH_TOKEN=
GMAIL_TOKEN_PATH=data/gmail/oauth.json
GMAIL_OAUTH_PORT=8765
GMAIL_TIMEOUT_SECONDS=10
```

Credential values belong in the backend environment. `.env` and `data/` are
gitignored. The authorization helper stores the refresh token in a private
mode-600 file inside a mode-700 directory. API status exposes sanitized flags.

## Authorize on the backend host

Run from the backend repository on the same computer as your browser.

```sh
uv sync --locked
uv run python -m app.integrations.gmail authorize
uv run python -m app.integrations.gmail status
```

The helper opens Google authorization and waits up to three minutes on
`http://127.0.0.1:8765/oauth/callback`. Select the team sender account and grant
Gmail send access. If browser opening is unavailable, open the displayed Google
URL manually on that computer. Choose a free `GMAIL_OAUTH_PORT` if needed.

For multiple Chrome profiles, request a URL that you can copy into the sender's
profile and complete authorization there.

```sh
uv run python -m app.integrations.gmail authorize --no-browser
```

`status` refreshes OAuth access and reports whether all sandbox configuration is
present. Restart a host API after changing `.env`, then refresh Integrations.
Its `gmail` entry uses `execution_mode=sandbox`. Configured dependencies report
`health=unknown` because registry reads perform configuration checks.

An explicitly configured `GMAIL_REFRESH_TOKEN` takes precedence over the saved
file. Clear or update a stale environment token when reauthorizing. Google apps
in external Testing typically receive seven-day refresh tokens for Gmail scopes.
Reauthorize after expiry or revocation.

## Send one synthetic test

Generate a UUID, save it for this logical test, then supply it to the sender.

```sh
uv run python -c 'from uuid import uuid4; print(uuid4())'
uv run python -m app.integrations.gmail send-test \
  --request-id 00000000-0000-4000-8000-000000000001
```

Replace the example UUID with the generated value. Reuse the same UUID for an
exact retry. A fresh UUID requests an intentional new email. The subject is
`CollectFlow Gmail connection test` and the body identifies a synthetic demo.
Recipient and content come from backend configuration and fixed code.

`submitted` means Gmail accepted the request. Verify the recipient mailbox to
confirm delivery. The private request journal beside the token file records
admission before the send call. Exact accepted retries return the recorded Gmail
message ID. Concurrent, incomplete or ambiguous requests fail closed. Gmail
offers no idempotency-key parameter on `messages.send`, so the local journal must
be retained for its duplicate protection. Reconcile an uncertain outcome in the
sender's Sent folder before intentionally sending another test.

## Docker Compose

Run `authorize` on the host so the browser can reach its loopback callback. For
the API running in Compose, provision `GMAIL_REFRESH_TOKEN` from your private
saved token through the backend environment or your secret manager. Compose
forwards Gmail client credentials, sender, recipient and timeout to the backend.

```sh
docker compose up -d --build backend
```

This command recreates the backend with its updated configuration. The host-side
CLI uses the saved file whenever `GMAIL_REFRESH_TOKEN` is empty. Preserve the
host's request journal when rerunning tests. The CLI also accepts an explicit
environment file before its command.

```sh
uv run python -m app.integrations.gmail --env-file .env.gmail.local status
```

## Verification

Focused tests cover OAuth refresh and callback validation, private token
storage, MIME encoding, header injection, provider rejection, ambiguous timeout,
durable duplicate suppression, registry serialization and the existing channel
dispatch boundary.

```sh
uv run pytest tests/unit/integrations/test_gmail*.py \
  tests/integration/test_channel_skeletons.py
```

The optional local Postman simulation exercises the real Gmail client over HTTP
using synthetic credentials and mailboxes. Start it in one backend terminal.

```sh
postman simulation run postman/simulations/gmail.sim.yaml --port 4859 --no-history
```

Run the HTTP suite from a second backend terminal.

```sh
GMAIL_SIMULATION_URL=http://127.0.0.1:4859 \
  uv run pytest tests/integration/test_gmail_simulation.py -q
```

The four scenarios cover success, permission denial, server error and acceptance
before a response timeout. Each verifies durable suppression of a repeated
request ID. Tests skip by default when `GMAIL_SIMULATION_URL` is unset. The
test-only transport forwards the two fixed Google endpoints to the loopback
stand-in. Stop the simulation with Ctrl+C after the run. Provider assumptions
and observed evidence are recorded in `postman/simulations/gmail.context.yaml`.

Live authorization and recipient delivery require the team's Google client and
account consent.

### Observed checks on 7 November 2026

- The Gmail-focused suite plus channel-boundary and architecture checks passed
  82 tests, including manual browser-profile authorization. The final
  repository-wide Ruff and mypy checks passed. Compose
  configuration validation and Git whitespace checks passed.
- Four real HTTP scenarios passed against the local Postman stand-in. Its
  request counts showed one refresh and one send for each logical test despite
  exact retries. The default full suite skips these opt-in scenarios.
- Frontend production build, TypeScript checks and both Gmail browser tests
  passed. The earlier full fixture browser run passed 44 tests.
- The latest full backend run reported 638 passed and 4 skipped. It emitted
  the existing Starlette TestClient deprecation warning.
- Live evidence covers Desktop OAuth authorization, refresh-token exchange and
  the running local API reporting the Gmail sandbox as configured. Refresh-token
  storage has private mode-600 permissions. Email delivery evidence remains the
  synthetic HTTP simulation and its controlled failure scenarios.

## Official references

- [Gmail send method](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Creating MIME messages and base64url encoding](https://developers.google.com/workspace/gmail/api/guides/sending)
- [Desktop OAuth and loopback authorization](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Refresh-token expiry rules](https://developers.google.com/identity/protocols/oauth2#expiration)
