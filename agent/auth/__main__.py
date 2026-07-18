"""``python -m agent.auth <username> <role>`` — print a development JWT.

Kept as the entrypoint `make token` documents. The token it mints is a *demo*
token: it names no workspace, so it is refused by every tenant-scoped route
and cannot reach a real deployment (``ENVIRONMENT=prod`` disables the
equivalent HTTP endpoint too). It is for poking at the agent loop locally, not
for signing in.
"""

from __future__ import annotations

import argparse
import json

from agent.auth.tokens import create_access_token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.auth", description="Issue a demo Anchor JWT."
    )
    parser.add_argument("username", nargs="?", default="demo")
    parser.add_argument("role", nargs="?", default="user", choices=["admin", "user"])
    parser.add_argument("--expires-minutes", type=int, default=None)
    args = parser.parse_args(argv)

    token, expires_in = create_access_token(
        args.username, args.role, expires_minutes=args.expires_minutes
    )
    print(
        json.dumps(
            {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": expires_in,
                "role": args.role,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
