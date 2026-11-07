"""Explicit local WAHA commands. No command accepts collection text or account IDs."""

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.integrations.channels.waha.client import WahaClient
from app.shared.errors import DomainError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local WAHA connection sandbox")
    parser.add_argument("--env-file", type=Path, default=Path(".env.waha.local"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show sanitized session status")
    qr = commands.add_parser("qr", help="Save the pairing QR image to a private local file")
    qr.add_argument("--output", type=Path, default=Path("data/waha/qr.png"))
    commands.add_parser("send-test", help="Send one fixed synthetic message to WAHA_TEST_RECIPIENT")
    args = parser.parse_args(argv)
    try:
        settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
        client = WahaClient(settings)
        if args.command == "status":
            print(json.dumps(client.session_status()))
        elif args.command == "qr":
            image = client.qr_image()
            args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(image)
            print(f"QR saved to {args.output.resolve()}")
        else:
            result = client.send_test_message()
            print(json.dumps({"status": result["status"]}))
        return 0
    except DomainError as error:
        print(f"{error.code} - {error.message}", file=sys.stderr)
    except ValidationError:
        print("waha_configuration_invalid - Check local WAHA configuration", file=sys.stderr)
    except OSError:
        print("waha_file_error - Local WAHA file could not be read or written", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
