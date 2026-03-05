from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jira_import.issue_fields import (
    build_issue_fields,
    get_row_value,
    resolve_project_key,
    transition_issue_to_status,
)
from jira_import.jira_client import JiraClient
from jira_import.logging_utils import log_event

BULK_MAX_ISSUES = 50


@dataclass(frozen=True)
class PendingIssue:
    row_index: int
    fields: dict[str, Any]
    summary: str
    desired_status: str


def _format_element_error(element_error: dict[str, Any]) -> str:
    messages: list[str] = []
    for message in element_error.get("errorMessages", []):
        if isinstance(message, str) and message:
            messages.append(message)
    field_errors = element_error.get("errors", {})
    if isinstance(field_errors, dict):
        for field_name, field_message in field_errors.items():
            messages.append(f"{field_name}: {field_message}")
    return "; ".join(messages) if messages else "Unknown Jira bulk error"


def flush_bulk_create_batch(
    client: JiraClient,
    pending: list[PendingIssue],
) -> tuple[int, int]:
    if not pending:
        return 0, 0

    response = client.create_issues_bulk([item.fields for item in pending])
    failed_by_index: dict[int, str] = {}
    for error_item in response.get("errors", []):
        if not isinstance(error_item, dict):
            continue
        failed_index = error_item.get("failedElementNumber")
        if not isinstance(failed_index, int):
            continue
        element_error = error_item.get("elementErrors", {})
        message = (
            _format_element_error(element_error)
            if isinstance(element_error, dict)
            else str(element_error or "Unknown Jira bulk error")
        )
        failed_by_index[failed_index] = message

    issues = response.get("issues", [])
    issue_cursor = 0
    created = 0
    failed = 0

    for batch_index, item in enumerate(pending):
        if batch_index in failed_by_index:
            failed += 1
            log_event(
                logging.ERROR,
                "row_failed",
                row=item.row_index,
                error=failed_by_index[batch_index],
            )
            continue

        issue_key = "<unknown>"
        if issue_cursor < len(issues) and isinstance(issues[issue_cursor], dict):
            issue_key = issues[issue_cursor].get("key", "<unknown>")
            issue_cursor += 1

        if item.desired_status:
            if issue_key == "<unknown>":
                failed += 1
                log_event(
                    logging.ERROR,
                    "row_failed",
                    row=item.row_index,
                    error=(
                        "issue created but key not returned; "
                        f"could not transition to {item.desired_status!r}"
                    ),
                )
                continue
            try:
                transition_issue_to_status(client, issue_key, item.desired_status)
            except Exception as exc:  # noqa: BLE001 - keep row-level failures isolated
                failed += 1
                log_event(
                    logging.ERROR,
                    "row_failed",
                    row=item.row_index,
                    issue_key=issue_key,
                    error=f"could not set status to {item.desired_status!r}: {exc}",
                )
                continue

        log_event(
            logging.INFO,
            "row_created",
            row=item.row_index,
            issue_key=issue_key,
            summary=item.summary,
        )
        created += 1

    return created, failed


def mark_pending_batch_failed(
    pending: list[PendingIssue],
    exc: Exception,
) -> int:
    for item in pending:
        log_event(logging.ERROR, "row_failed", row=item.row_index, error=str(exc))
    return len(pending)


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def resolve_project_key_for_import(
    client: JiraClient,
    args: argparse.Namespace,
) -> str:
    return args.project_key or resolve_project_key(client, args.board_id)


def flush_pending_batch(client: JiraClient, pending: list[PendingIssue]) -> tuple[int, int]:
    try:
        return flush_bulk_create_batch(client, pending)
    except Exception as exc:  # noqa: BLE001 - batch-level failure should report all rows
        return 0, mark_pending_batch_failed(pending, exc)


def process_rows(
    args: argparse.Namespace,
    client: JiraClient,
    rows: list[dict[str, str]],
    project_key: str,
) -> tuple[int, int]:
    created = 0
    failed = 0
    pending: list[PendingIssue] = []

    for index, row in enumerate(rows, start=2):
        try:
            fields = build_issue_fields(
                row=row,
                project_key=project_key,
            )

            if args.dry_run:
                log_event(logging.INFO, "dry_run_row", row=index, fields=fields)
                created += 1
                continue

            pending.append(
                PendingIssue(
                    row_index=index,
                    fields=fields,
                    summary=fields["summary"],
                    desired_status=get_row_value(row, "Status"),
                )
            )
            if len(pending) >= BULK_MAX_ISSUES:
                batch_created, batch_failed = flush_pending_batch(client, pending)
                created += batch_created
                failed += batch_failed
                pending = []
                if args.stop_on_error and batch_failed > 0:
                    break
        except Exception as exc:  # noqa: BLE001 - keep row-level failures isolated
            failed += 1
            log_event(logging.ERROR, "row_failed", row=index, error=str(exc))
            if args.stop_on_error:
                break

    if pending and not args.dry_run and not (args.stop_on_error and failed > 0):
        batch_created, batch_failed = flush_pending_batch(client, pending)
        created += batch_created
        failed += batch_failed

    return created, failed
