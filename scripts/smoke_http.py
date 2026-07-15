"""Ad-hoc HTTP smoke check against a running Agent API.

Not part of the pytest suite - this is the script used while developing to
exercise the real HTTP surface end to end.

    python scripts/smoke_http.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

DOC_DIR = Path("data/documents")


def show(label: str, response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {"<non-json>": response.text[:200]}
    print(f"{label:44s} HTTP {response.status_code}  {json.dumps(body)[:300]}")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    failures = 0

    with httpx.Client(base_url=base, timeout=180.0) as client:
        show("GET /health", client.get("/health"))

        user_token = show(
            "POST /auth/token (user)",
            client.post("/auth/token", json={"username": "alice", "role": "user"}),
        )["access_token"]
        admin_token = show(
            "POST /auth/token (admin)",
            client.post("/auth/token", json={"username": "root", "role": "admin"}),
        )["access_token"]

        user_auth = {"Authorization": f"Bearer {user_token}"}
        admin_auth = {"Authorization": f"Bearer {admin_token}"}

        sample = DOC_DIR / "hr_leave_policy.pdf"
        with sample.open("rb") as handle:
            body = show(
                "POST /ingest (no auth -> 401)",
                client.post("/ingest", files={"file": (sample.name, handle, "application/pdf")}),
            )
            if body.get("error") != "missing_token":
                failures += 1
                print("  !! expected missing_token")

        with sample.open("rb") as handle:
            body = show(
                "POST /ingest (user -> 403)",
                client.post(
                    "/ingest",
                    files={"file": (sample.name, handle, "application/pdf")},
                    headers=user_auth,
                ),
            )
            if body.get("error") != "insufficient_role":
                failures += 1
                print("  !! expected insufficient_role")

        with sample.open("rb") as handle:
            body = show(
                "POST /ingest (admin -> 200)",
                client.post(
                    "/ingest",
                    files={"file": (sample.name, handle, "application/pdf")},
                    headers=admin_auth,
                ),
            )
            if not body.get("chunks_created"):
                failures += 1
                print("  !! no chunks created")

        show(
            "POST /ingest (not a pdf -> 400)",
            client.post(
                "/ingest",
                files={"file": ("notes.txt", b"just some plain text", "text/plain")},
                headers=admin_auth,
            ),
        )

        for pdf in sorted(DOC_DIR.glob("*.pdf")):
            with pdf.open("rb") as handle:
                show(
                    f"POST /ingest ({pdf.name})",
                    client.post(
                        "/ingest",
                        files={"file": (pdf.name, handle, "application/pdf")},
                        headers=admin_auth,
                    ),
                )

    print(f"\n{'FAILURES: ' + str(failures) if failures else 'all smoke checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
