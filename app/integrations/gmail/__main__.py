"""Explicit Gmail OAuth and canned test commands, using backend configuration."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.config import Settings
from app.integrations.gmail.client import sandbox_configured
from app.integrations.gmail.oauth import GoogleOAuthClient
from app.integrations.gmail.sandbox import send_test
from app.shared.errors import DomainError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gmail API team connection sandbox")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = parser.add_subparsers(dest="command", required=True)
    authorize = commands.add_parser("authorize", help="Authorize a team Gmail account using the browser")
    authorize.add_argument(
        "--no-browser", action="store_true", help="Print the URL for your preferred browser profile"
    )
    commands.add_parser("status", help="Check OAuth access without sending email")
    send = commands.add_parser("send-test", help="Send one fixed synthetic message to the team")
    send.add_argument(
        "--request-id", type=UUID, required=True, help="Reuse this UUID for exact retries"
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
        if args.command == "authorize":
            GoogleOAuthClient(settings).authorize(open_browser=not args.no_browser)
            print(json.dumps({"status": "authorized"}))
        elif args.command == "status":
            GoogleOAuthClient(settings).access_token()
            print(
                json.dumps(
                    {"oauth": "authorized", "sandbox_configured": sandbox_configured(settings)}
                )
            )
        else:
            result = send_test(settings, args.request_id)
            print(json.dumps({"request_id": str(args.request_id), **result}))
        return 0
    except DomainError as error:
        print(f"{error.code} - {error.message}", file=sys.stderr)
    except ValidationError:
        print("gmail_configuration_invalid - Check backend Gmail settings", file=sys.stderr)
    except OSError:
        print("gmail_file_error - Gmail local files could not be accessed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
