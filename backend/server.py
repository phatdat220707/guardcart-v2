"""Portable one-process entry point for local use and container hosting."""

import argparse
import json
import os

from backend.ai.config import AiSettings
from backend.settings import ApiSettings, ROOT


def parse_port(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535.")
    return int(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--check", action="store_true", help="Validate configuration without starting a server or calling AI.")
    args = parser.parse_args(argv)
    try:
        port = parse_port(os.environ.get("PORT", "8000"))
        transport = ApiSettings.from_environment()
        ai = AiSettings.from_environment(ROOT / ".env")
    except (ValueError, OSError):
        print("Invalid GuardCart configuration. Check PORT, transport settings and backend AI configuration; do not share credentials.")
        return 2
    if args.check:
        print(json.dumps({"configuration": "valid", "environment": transport.environment, "port": port,
                          "ai_enabled": ai.enabled, "workers": 1, "provider_calls": 0}))
        return 0
    import uvicorn

    # No forwarded headers are trusted. TLS termination belongs to the hosting proxy;
    # the app uses explicit HTTPS origins and Secure cookies in production.
    uvicorn.run("backend.main:app", host=args.host, port=port, workers=1,
                proxy_headers=False, access_log=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
