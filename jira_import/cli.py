from __future__ import annotations

import argparse
import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(parser: argparse.ArgumentParser, name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        parser.error(f"{name} must be an integer; got {raw!r}")
        raise AssertionError("unreachable")


def _env_float(parser: argparse.ArgumentParser, name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        parser.error(f"{name} must be a number; got {raw!r}")
        raise AssertionError("unreachable")


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.board_id <= 0:
        parser.error("--board-id must be greater than 0.")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0.")
    if args.max_retries < 0:
        parser.error("--max-retries must be 0 or greater.")
    if args.retry_backoff_seconds <= 0:
        parser.error("--retry-backoff-seconds must be greater than 0.")
    if not str(args.base_url).startswith(("http://", "https://")):
        parser.error("--base-url must start with http:// or https://.")


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=".env")
    pre_args, _ = pre_parser.parse_known_args()
    load_dotenv(Path(pre_args.env_file))

    parser = argparse.ArgumentParser(description="Create Jira issues from CSV.")
    parser.add_argument(
        "--env-file",
        default=pre_args.env_file,
        help="Path to env file (default: .env)",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("JIRA_BASE_URL"),
        required=os.getenv("JIRA_BASE_URL") is None,
        help="Jira base URL, e.g. https://company.atlassian.net (or JIRA_BASE_URL)",
    )
    parser.add_argument(
        "--board-id",
        type=int,
        default=_env_int(parser, "JIRA_BOARD_ID", 368),
        help="Jira board ID (default: 368 or JIRA_BOARD_ID)",
    )
    parser.add_argument(
        "--csv",
        default=os.getenv("JIRA_CSV_NAME", "data/crm_admin_jira_import.csv"),
        help="Path or file name for source CSV (or JIRA_CSV_NAME)",
    )

    auth = parser.add_mutually_exclusive_group(required=False)
    auth.add_argument(
        "--api-token",
        default=os.getenv("JIRA_API_TOKEN"),
        help="Jira API token (with --email) or JIRA_API_TOKEN",
    )
    auth.add_argument(
        "--bearer-token",
        default=os.getenv("JIRA_BEARER_TOKEN"),
        help="Jira bearer token / PAT or JIRA_BEARER_TOKEN",
    )

    parser.add_argument(
        "--email",
        default=os.getenv("JIRA_EMAIL"),
        help="Jira account email (required with --api-token) or JIRA_EMAIL",
    )
    parser.add_argument(
        "--project-key",
        default=os.getenv("JIRA_PROJECT_KEY"),
        help="Project key override; if omitted it is resolved from board",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payloads without creating issues",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=_env_int(parser, "JIRA_TIMEOUT_SECONDS", 60),
        help="HTTP timeout per Jira request in seconds (default: 60 or JIRA_TIMEOUT_SECONDS)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=_env_int(parser, "JIRA_MAX_RETRIES", 3),
        help="Retry count for transient network timeouts (default: 3 or JIRA_MAX_RETRIES)",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=_env_float(parser, "JIRA_RETRY_BACKOFF_SECONDS", 1.5),
        help="Base retry backoff in seconds; scaled by attempt number (default: 1.5)",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop import on first failed row (default: continue and report failures)",
    )
    args = parser.parse_args()
    _validate_args(parser, args)
    return args
