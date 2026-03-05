from __future__ import annotations

from typing import Any

from jira_import.jira_client import JiraClient

COMPLEXITY_FIELD_KEY = "customfield_15400"


def normalize_labels(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p]


def get_row_value(row: dict[str, str], key: str, default: str = "") -> str:
    return (row.get(key) or default).strip()


def resolve_project_key(client: JiraClient, board_id: int) -> str:
    projects = client.board_projects(board_id)
    if not projects:
        raise RuntimeError(f"No projects found for board {board_id}")
    if len(projects) > 1:
        keys = ", ".join(p.get("key", "<unknown>") for p in projects)
        raise RuntimeError(
            f"Board {board_id} has multiple projects ({keys}). Provide --project-key to select one."
        )
    key = projects[0].get("key")
    if not key:
        raise RuntimeError(f"Board {board_id} returned a project without key: {projects[0]}")
    return key


def to_adf(text: str) -> dict[str, Any]:
    if not text:
        return {"type": "doc", "version": 1, "content": []}

    paragraphs: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            paragraphs.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                }
            )
        else:
            paragraphs.append({"type": "paragraph", "content": []})

    return {"type": "doc", "version": 1, "content": paragraphs}


def build_issue_fields(
    row: dict[str, str],
    project_key: str,
) -> dict[str, Any]:
    issue_type = get_row_value(row, "Issue Type", "Task") or "Task"
    summary = get_row_value(row, "Summary")
    description = get_row_value(row, "Description")
    parent = get_row_value(row, "Parent")
    complexity = get_row_value(row, "Complexity")
    labels = normalize_labels(get_row_value(row, "Label"))

    if not summary:
        raise ValueError("Missing required 'Summary'")

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "description": to_adf(description),
    }

    if labels:
        fields["labels"] = labels

    if parent:
        fields["parent"] = {"key": parent}

    if complexity:
        fields[COMPLEXITY_FIELD_KEY] = {"value": complexity}

    return fields


def transition_issue_to_status(client: JiraClient, issue_key: str, target_status: str) -> None:
    desired = target_status.strip()
    if not desired:
        return

    current_status = client.issue_status(issue_key)
    if current_status.lower() == desired.lower():
        return

    transitions = client.issue_transitions(issue_key)
    for transition in transitions:
        transition_id = transition.get("id")
        to_status = transition.get("to", {})
        to_name = to_status.get("name") if isinstance(to_status, dict) else None
        if not isinstance(transition_id, str) or not isinstance(to_name, str):
            continue
        if to_name.lower() == desired.lower():
            client.transition_issue(issue_key, transition_id)
            return

    available = []
    for transition in transitions:
        to_status = transition.get("to", {})
        to_name = to_status.get("name") if isinstance(to_status, dict) else None
        if isinstance(to_name, str) and to_name:
            available.append(to_name)
    available_text = ", ".join(available) if available else "<none>"
    raise RuntimeError(
        f"No transition from '{current_status or '<unknown>'}' to '{desired}'. "
        f"Available transitions: {available_text}"
    )
