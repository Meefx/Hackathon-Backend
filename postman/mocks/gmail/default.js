/** Local-only Google contract stand-in. Every credential and mailbox is synthetic. */
const http = require("http");
const SCOPE = "https://www.googleapis.com/auth/gmail.send";
const STATE_KEY = "gmail-sandbox-state";
const SCENARIOS = new Set(["success", "permission_denied", "server_error", "timeout"]);

function reply(res, status, data) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

function readBody(req) {
  if (req.body !== undefined) {
    return Promise.resolve(req.body);
  }
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      resolve(body);
    });
  });
}

function objectBody(body) {
  if (body && typeof body === "object" && !Buffer.isBuffer(body)) {
    return body;
  }
  try {
    return JSON.parse(String(body));
  } catch {
    return {};
  }
}

function initialState(scenario = "success") {
  return {
    scenario,
    token_requests: 0,
    send_requests: 0,
    accepted: 0,
  };
}

function validEmail(raw) {
  if (typeof raw !== "string" || !/^[A-Za-z0-9_-]+={0,2}$/.test(raw)) {
    return false;
  }
  const message = Buffer.from(raw, "base64url")
    .toString("utf8")
    .replace(/\r?\n[ \t]+/g, " ");
  return (
    /^From: sender@example\.test\r?$/m.test(message)
    && /^To: team@example\.test\r?$/m.test(message)
    && /^Subject: CollectFlow Gmail connection test\r?$/m.test(message)
    && /^Message-ID: <collectflow-test-[0-9a-f-]{36}@example\.test>\r?$/m.test(message)
    && !/^(Cc|Bcc):/mi.test(message)
    && message.includes("CollectFlow Gmail connection test. This is a synthetic demo message.")
  );
}

const server = http.createServer(async (req, res) => {
  const pendingBody = readBody(req);
  const path = req.url.split("?")[0];
  const state = (await pm.state.get(STATE_KEY)) || initialState();

  // Local test-control routes model failure conditions and expose side-effect counts.
  if (path === "/__control" && req.method === "POST") {
    const body = objectBody(await pendingBody);
    if (!SCENARIOS.has(body.scenario)) {
      return reply(res, 400, { error: "Unknown test scenario" });
    }
    const reset = initialState(body.scenario);
    await pm.state.set(STATE_KEY, reset);
    return reply(res, 200, reset);
  }
  if (path === "/__control" && req.method === "GET") {
    return reply(res, 200, state);
  }

  // @endpoint POST /token
  if (path === "/token" && req.method === "POST") {
    state.token_requests += 1;
    await pm.state.set(STATE_KEY, state);
    const body = await pendingBody;
    const form = body && typeof body === "object" && !Buffer.isBuffer(body)
      ? body
      : Object.fromEntries(new URLSearchParams(String(body)));
    if (
      form.grant_type !== "refresh_token"
      || form.client_id !== "synthetic-client"
      || form.client_secret !== "synthetic-secret"
      || form.refresh_token !== "synthetic-refresh"
    ) {
      return reply(res, 400, { error: "invalid_grant" });
    }
    return reply(res, 200, {
      access_token: "synthetic-access",
      token_type: "Bearer",
      expires_in: 3600,
      scope: SCOPE,
    });
  }

  // @endpoint POST /gmail/v1/users/me/messages/send
  if (path === "/gmail/v1/users/me/messages/send" && req.method === "POST") {
    state.send_requests += 1;
    await pm.state.set(STATE_KEY, state);
    const body = objectBody(await pendingBody);
    if (req.headers.authorization !== "Bearer synthetic-access") {
      return reply(res, 401, {
        error: { code: 401, message: "Invalid Credentials" },
      });
    }
    if (!validEmail(body.raw)) {
      return reply(res, 400, {
        error: { code: 400, message: "Invalid synthetic message" },
      });
    }
    if (state.scenario === "permission_denied") {
      return reply(res, 403, {
        error: { code: 403, message: "Insufficient Permission" },
      });
    }
    if (state.scenario === "server_error") {
      return reply(res, 500, {
        error: { code: 500, message: "Backend Error" },
      });
    }
    state.accepted += 1;
    await pm.state.set(STATE_KEY, state);
    if (state.scenario === "timeout") {
      await new Promise((resolve) => {
        setTimeout(resolve, 350);
      });
    }
    return reply(res, 200, {
      id: "synthetic-gmail-message",
      threadId: "synthetic-thread",
    });
  }

  return reply(res, 501, {
    error: "Operation is outside this local contract stand-in",
  });
});

server.listen(process.env.PORT || 4859);
