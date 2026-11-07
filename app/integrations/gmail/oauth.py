"""Desktop OAuth with a loopback callback and private refresh-token storage."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.config import Settings
from app.shared.errors import DomainError

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
MAX_RESPONSE_BYTES = 65536
AUTHORIZATION_TIMEOUT_SECONDS = 180


def _token(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", value))


def _file_error() -> DomainError:
    return DomainError(
        "gmail_token_file_invalid", "Gmail token storage must be a private, valid local file", 503
    )


def read_refresh_token(settings: Settings) -> str:
    """Read backend configuration without a provider call or exposing credentials."""
    configured = settings.gmail_refresh_token.get_secret_value()
    if configured:
        if not _token(configured):
            raise DomainError("gmail_not_configured", "Check Gmail credential configuration", 503)
        return configured
    source_path = Path(settings.gmail_token_path)
    try:
        directory = os.open(source_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                source_path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        finally:
            os.close(directory)
    except FileNotFoundError:
        return ""
    except OSError:
        raise _file_error() from None
    try:
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid != os.getuid()
            ):
                raise _file_error()
            contents = source.read(MAX_RESPONSE_BYTES + 1)
        if len(contents) > MAX_RESPONSE_BYTES:
            raise _file_error()
        saved = json.loads(contents)
        if not isinstance(saved, dict) or not _token(saved.get("refresh_token")):
            raise _file_error()
        if saved.get("client_id") != settings.gmail_client_id:
            return ""
        return str(saved["refresh_token"])
    except (OSError, ValueError, UnicodeDecodeError):
        raise _file_error() from None


def _save_refresh_token(settings: Settings, refresh_token: str) -> None:
    destination = Path(settings.gmail_token_path)
    temporary_name = ".oauth-" + secrets.token_hex(16)
    directory = None
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(directory)
        if metadata.st_mode & 0o077 or metadata.st_uid != os.getuid():
            raise _file_error()
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "w") as output:
            json.dump(
                {"client_id": settings.gmail_client_id, "refresh_token": refresh_token}, output
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError:
        raise _file_error() from None
    finally:
        if directory is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)


class _CallbackServer(HTTPServer):
    callback_result: dict[str, str] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def setup(self) -> None:
        self.request.settimeout(2)
        super().setup()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != "/oauth/callback":
            self.send_error(404)
            return
        values = parse_qs(parsed.query, keep_blank_values=True)
        result = {
            key: values[key][0]
            for key in ("state", "code", "error")
            if len(values.get(key, [])) == 1
        }
        self.server.callback_result = result
        body = b"Gmail authorization received. Return to the backend terminal."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Callback URLs contain the one-time authorization code.
        pass


class GoogleOAuthClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def _require_client(self) -> None:
        if (
            not self.settings.gmail_client_id
            or not self.settings.gmail_client_secret.get_secret_value()
        ):
            raise DomainError(
                "gmail_not_configured",
                "Configure the Gmail Desktop OAuth client ID and secret",
                503,
            )

    def _exchange(self, payload: dict[str, str]) -> dict[str, Any]:
        self._require_client()
        payload = {
            **payload,
            "client_id": self.settings.gmail_client_id,
            "client_secret": self.settings.gmail_client_secret.get_secret_value(),
        }
        try:
            with (
                httpx.Client(
                    timeout=self.settings.gmail_timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream("POST", TOKEN_URL, data=payload) as response,
            ):
                if response.status_code in {400, 401, 403}:
                    raise DomainError(
                        "gmail_auth_rejected", "Google authorization failed. Reauthorize Gmail", 503
                    )
                if response.status_code >= 400:
                    raise DomainError(
                        "gmail_unavailable", "Google authorization is unavailable", 503
                    )
                if response.is_redirect:
                    raise self._invalid_response()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise self._invalid_response()
                data = json.loads(body)
        except httpx.HTTPError:
            raise DomainError(
                "gmail_unavailable", "Google authorization is unreachable", 503
            ) from None
        except (ValueError, UnicodeDecodeError):
            raise self._invalid_response() from None
        if (
            not isinstance(data, dict)
            or not _token(data.get("access_token"))
            or str(data.get("token_type", "")).lower() != "bearer"
        ):
            raise self._invalid_response()
        scope = data.get("scope")
        if scope is not None and (
            not isinstance(scope, str) or GMAIL_SEND_SCOPE not in scope.split()
        ):
            raise DomainError("gmail_scope_invalid", "Authorize the Gmail send permission", 503)
        return data

    @staticmethod
    def _invalid_response() -> DomainError:
        return DomainError(
            "gmail_response_invalid", "Google returned an invalid authorization response", 502
        )

    def access_token(self) -> str:
        self._require_client()
        refresh_token = read_refresh_token(self.settings)
        if not refresh_token:
            raise DomainError(
                "gmail_not_configured", "Authorize Gmail in the backend terminal", 503
            )
        data = self._exchange({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return str(data["access_token"])

    def authorize(self, *, open_browser: bool = True) -> None:
        """Ask the local operator to grant minimal send access in their browser."""
        self._require_client()
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        redirect_uri = f"http://127.0.0.1:{self.settings.gmail_oauth_port}/oauth/callback"
        authorization_url = (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "client_id": self.settings.gmail_client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": GMAIL_SEND_SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        try:
            with _CallbackServer(
                ("127.0.0.1", self.settings.gmail_oauth_port), _CallbackHandler
            ) as server:
                server.timeout = 1
                server.callback_result = None
                print("Open this URL in the browser profile for the team Gmail sender.")
                print(authorization_url, flush=True)
                if open_browser:
                    webbrowser.open(authorization_url)
                deadline = time.monotonic() + AUTHORIZATION_TIMEOUT_SECONDS
                while server.callback_result is None and time.monotonic() < deadline:
                    server.handle_request()
                result = server.callback_result
        except OSError:
            raise DomainError(
                "gmail_oauth_callback_unavailable", "The local Gmail callback could not start", 503
            ) from None
        if result is None:
            raise DomainError("gmail_oauth_timeout", "Gmail authorization timed out", 408)
        if not hmac.compare_digest(result.get("state", "").encode(), state.encode()):
            raise DomainError(
                "gmail_oauth_state_invalid", "Gmail authorization state was rejected", 403
            )
        if result.get("error") or not result.get("code"):
            raise DomainError("gmail_auth_rejected", "Gmail authorization was declined", 403)
        data = self._exchange(
            {
                "grant_type": "authorization_code",
                "code": result["code"],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        )
        refresh_token = data.get("refresh_token")
        if not _token(refresh_token):
            raise DomainError(
                "gmail_refresh_token_missing",
                "Google did not grant offline access. Reauthorize Gmail",
                503,
            )
        _save_refresh_token(self.settings, str(refresh_token))
