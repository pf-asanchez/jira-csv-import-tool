from __future__ import annotations

import argparse
import base64


def build_auth_headers(args: argparse.Namespace) -> dict[str, str]:
    if args.api_token:
        if not args.email:
            raise ValueError("--email is required when using --api-token")
        token = base64.b64encode(f"{args.email}:{args.api_token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    if args.bearer_token:
        return {"Authorization": f"Bearer {args.bearer_token}"}

    raise ValueError(
        "Authentication is required. Set JIRA_API_TOKEN (and JIRA_EMAIL) or "
        "JIRA_BEARER_TOKEN in .env, or pass --api-token/--bearer-token."
    )

