from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib import parse
from urllib import error, request

from jira_import.logging_utils import log_event


@dataclass
class JiraClient:
    base_url: str
    auth_headers: dict[str, str]
    timeout_seconds: int = 60
    max_retries: int = 3
    retry_backoff_seconds: float = 1.5

    @staticmethod
    def _retry_after_seconds(headers: Any) -> float | None:
        if headers is None:
            return None
        retry_after = headers.get("Retry-After")
        if retry_after is None:
            return None
        try:
            value = float(str(retry_after).strip())
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _escape_jql_phrase(value: str) -> str:
        escaped_chars: list[str] = []
        for ch in value:
            codepoint = ord(ch)
            if ch == "\\":
                escaped_chars.append("\\\\")
                continue
            if ch == '"':
                escaped_chars.append('\\"')
                continue
            # JQL rejects raw control characters. Keep printable characters as-is.
            if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
                escaped_chars.append(f"\\u{codepoint:04x}")
                continue
            escaped_chars.append(ch)
        return "".join(escaped_chars)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        default_attempts = self.max_retries + 1
        transient_http_codes = {429, 502, 503, 504}
        # Jira Cloud can intermittently rate-limit long-running imports.
        # Give 429 a larger retry budget while still remaining bounded.
        rate_limit_attempts = max(default_attempts, 10)
        attempt = 0
        while True:
            attempt += 1
            headers = {
                "Accept": "application/json",
                **self.auth_headers,
            }
            data = None
            if payload is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(payload).encode("utf-8")

            req = request.Request(url=url, method=method.upper(), headers=headers, data=data)
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    body = resp.read().decode("utf-8")
                    if not body:
                        return {}
                    return json.loads(body)
            except error.HTTPError as exc:
                raw_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in transient_http_codes:
                    allowed_attempts = rate_limit_attempts if exc.code == 429 else default_attempts
                    if attempt < allowed_attempts:
                        retry_after = self._retry_after_seconds(exc.headers)
                        delay = (
                            retry_after
                            if retry_after is not None
                            else self.retry_backoff_seconds * attempt
                        )
                        if exc.code == 429:
                            delay = max(delay, 2.0)
                        log_event(
                            logging.WARNING,
                            "jira_request_retry",
                            method=method.upper(),
                            path=path,
                            attempt=attempt,
                            attempts=allowed_attempts,
                            delay_seconds=round(delay, 2),
                            status=exc.code,
                        )
                        time.sleep(delay)
                        continue
                message = raw_body
                try:
                    parsed = json.loads(raw_body)
                    message = json.dumps(parsed, indent=2)
                except json.JSONDecodeError:
                    pass
                raise RuntimeError(
                    f"Jira API error {exc.code} for {method.upper()} {path}:\n{message}"
                ) from exc
            except (error.URLError, TimeoutError, socket.timeout) as exc:
                if attempt >= default_attempts:
                    raise RuntimeError(
                        f"Network error calling Jira ({method.upper()} {path}) after {default_attempts} attempts: {exc}"
                    ) from exc
                delay = self.retry_backoff_seconds * attempt
                log_event(
                    logging.WARNING,
                    "jira_request_retry",
                    method=method.upper(),
                    path=path,
                    attempt=attempt,
                    attempts=default_attempts,
                    delay_seconds=round(delay, 2),
                    error=str(exc),
                )
                time.sleep(delay)

    def validate_auth(self) -> dict[str, Any]:
        return self._request("GET", "/rest/api/3/myself")

    def board_projects(self, board_id: int) -> list[dict[str, Any]]:
        result = self._request("GET", f"/rest/agile/1.0/board/{board_id}/project")
        return result.get("values", [])

    def create_issues_bulk(self, fields_list: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/rest/api/3/issue/bulk",
            payload={"issueUpdates": [{"fields": fields} for fields in fields_list]},
        )

    def project_issue_summaries(self, project_key: str) -> set[str]:
        summaries: set[str] = set()
        start_at = 0
        max_results = 100
        jql = f'project = "{project_key}" AND summary IS NOT EMPTY'

        while True:
            encoded_jql = parse.quote_plus(jql)
            path = (
                "/rest/api/3/search/jql"
                f"?jql={encoded_jql}"
                f"&startAt={start_at}"
                f"&maxResults={max_results}"
                "&fields=summary"
            )
            result = self._request(
                "GET",
                path,
            )
            issues = result.get("issues", [])
            if not isinstance(issues, list) or not issues:
                break

            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                fields = issue.get("fields", {})
                if not isinstance(fields, dict):
                    continue
                summary = fields.get("summary")
                if isinstance(summary, str) and summary.strip():
                    summaries.add(summary.strip())

            start_at += len(issues)
            total = result.get("total")
            if isinstance(total, int) and start_at >= total:
                break

        return summaries

    def existing_summaries_for_candidates(
        self,
        project_key: str,
        candidate_summaries: set[str],
    ) -> set[str]:
        def normalize_summary(value: str) -> str:
            return " ".join(value.split()).casefold()

        normalized_to_original: dict[str, str] = {}
        for summary in candidate_summaries:
            normalized = normalize_summary(summary)
            if normalized and normalized not in normalized_to_original:
                normalized_to_original[normalized] = " ".join(summary.split())

        found: set[str] = set()
        max_results = 200
        max_clauses_per_query = 12
        max_jql_length = 6000
        remaining = set(normalized_to_original.keys())
        normalized_items = list(normalized_to_original.items())
        index = 0

        while index < len(normalized_items) and remaining:
            clauses: list[str] = []
            consumed = 0
            estimated_length = 0
            while index + consumed < len(normalized_items) and consumed < max_clauses_per_query:
                normalized_candidate, candidate = normalized_items[index + consumed]
                if normalized_candidate not in remaining:
                    consumed += 1
                    continue
                escaped_candidate = self._escape_jql_phrase(candidate)
                clause = f'summary ~ "\\"{escaped_candidate}\\""'
                projected = estimated_length + len(clause) + (4 if clauses else 0)
                if clauses and projected > max_jql_length:
                    break
                clauses.append(clause)
                estimated_length = projected
                consumed += 1

            if consumed == 0:
                break

            index += consumed
            if not clauses:
                continue

            jql = f'project = "{project_key}" AND ({(" OR ").join(clauses)}) ORDER BY created DESC'
            encoded_jql = parse.quote_plus(jql)
            path = (
                "/rest/api/3/search/jql"
                f"?jql={encoded_jql}"
                "&startAt=0"
                f"&maxResults={max_results}"
                "&fields=summary"
            )
            result = self._request("GET", path)
            issues = result.get("issues", [])
            if not isinstance(issues, list) or not issues:
                continue

            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                fields = issue.get("fields", {})
                if not isinstance(fields, dict):
                    continue
                summary = fields.get("summary")
                if not isinstance(summary, str):
                    continue
                normalized_summary = normalize_summary(summary)
                if normalized_summary in remaining:
                    found.add(summary.strip())
                    remaining.remove(normalized_summary)

        return found

    def issue_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        result = self._request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
        return result.get("transitions", [])

    def issue_status(self, issue_key: str) -> str:
        issue = self._request("GET", f"/rest/api/3/issue/{issue_key}?fields=status")
        fields = issue.get("fields", {})
        status = fields.get("status", {}) if isinstance(fields, dict) else {}
        name = status.get("name") if isinstance(status, dict) else None
        return name if isinstance(name, str) else ""

    def transition_issue(self, issue_key: str, transition_id: str) -> None:
        self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            payload={"transition": {"id": transition_id}},
        )
