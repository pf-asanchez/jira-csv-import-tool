#!/usr/bin/env python3
"""Import Jira issues from a CSV file."""

from __future__ import annotations

import logging
from pathlib import Path

from jira_import.auth import build_auth_headers
from jira_import.cli import parse_args
from jira_import.importer import (
    process_rows,
    read_csv_rows,
    resolve_project_key_for_import,
)
from jira_import.jira_client import JiraClient
from jira_import.logging_utils import configure_logging, log_event


def resolve_csv_path(raw_csv_path: str) -> Path:
    csv_path = Path(raw_csv_path)
    if csv_path.exists():
        return csv_path

    # Backward-compatible fallback for legacy bare filenames.
    if not csv_path.is_absolute() and len(csv_path.parts) == 1:
        candidate = Path("data") / csv_path.name
        if candidate.exists():
            return candidate

    return csv_path


def main() -> int:
    configure_logging()
    args = parse_args()
    csv_path = resolve_csv_path(args.csv)
    if not csv_path.exists():
        log_event(logging.ERROR, "csv_not_found", path=str(csv_path))
        return 1

    try:
        auth_headers = build_auth_headers(args)
    except ValueError as exc:
        log_event(logging.ERROR, "invalid_auth_config", error=str(exc))
        return 1

    client = JiraClient(
        base_url=args.base_url,
        auth_headers=auth_headers,
        timeout_seconds=args.timeout_seconds,
        max_retries=max(0, args.max_retries),
        retry_backoff_seconds=max(0.1, args.retry_backoff_seconds),
    )

    try:
        me = client.validate_auth()
    except RuntimeError as exc:
        log_event(logging.ERROR, "authentication_failed", error=str(exc))
        return 1

    display_name = me.get("displayName", "<unknown user>")
    log_event(logging.INFO, "authenticated", user=display_name)

    try:
        project_key = resolve_project_key_for_import(client, args)
    except RuntimeError as exc:
        log_event(logging.ERROR, "configuration_resolution_failed", error=str(exc))
        return 1

    rows = read_csv_rows(csv_path)
    if not rows:
        log_event(logging.INFO, "no_rows_found", path=str(csv_path))
        return 0

    log_event(logging.INFO, "using_project", project_key=project_key)

    created, failed = process_rows(
        args=args,
        client=client,
        rows=rows,
        project_key=project_key,
    )

    log_event(logging.INFO, "import_complete", success=created, failed=failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
