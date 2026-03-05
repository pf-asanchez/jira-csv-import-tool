# Jira CSV Import

Bulk-create Jira issues from a CSV file with optional status transition after creation.

This project uses Jira Cloud REST APIs to:
- authenticate (`/rest/api/3/myself`)
- resolve project from board (optional)
- create issues in batches (up to 50)
- transition each created issue to a target status from the CSV

## Quick Start

1. Create `.env` from example and fill Jira credentials/settings.

```bash
copy .env.example .env
```

2. (Optional) Update `JIRA_CSV_NAME` in `.env`, or pass `--csv` when running.

3. Run a dry run first (no issues created):

```bash
python jira_csv_import.py --dry-run --csv data/jira_import_template.csv
```

4. Run the real import:

```bash
python jira_csv_import.py --csv data/jira_import_template.csv
```

5. Check results in console and `data/import.log`.

## What This Supports

- CSV-driven issue creation
- `--dry-run` payload preview (no issues created)
- retries for transient Jira/network failures
- board-based project resolution, with optional explicit project override
- per-row failure logging while continuing import (or stop on first error)

## Project Structure

- `jira_csv_import.py`: entrypoint
- `jira_import/cli.py`: CLI and `.env` loading
- `jira_import/auth.py`: Basic or Bearer auth header building
- `jira_import/issue_fields.py`: CSV -> Jira field mapping (main customization point)
- `jira_import/importer.py`: CSV read + batch create + status transitions
- `jira_import/jira_client.py`: Jira API client + retry logic
- `data/jira_import_template.csv`: starter template
- `data/import.log`: JSON-line runtime log output

## Prerequisites

- Python 3.10+
- Jira Cloud site URL
- One auth method:
  - API token + email, or
  - Bearer token / PAT

## Configuration

Create `.env` from `.env.example` and set values:

```env
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=your.email@company.com
JIRA_API_TOKEN=your_token
# or: JIRA_BEARER_TOKEN=your_bearer_token

JIRA_BOARD_ID=368
JIRA_CSV_NAME=jira_import_template.csv
# Optional when board has multiple projects:
# JIRA_PROJECT_KEY=ABC
```

Notes:
- `JIRA_CSV_NAME` can be a full path, a relative path, or a bare filename.
- If you pass a bare filename (for example `my.csv`) and it does not exist in the current directory, the script tries `data/my.csv`.

## How To Run

From the repo root:

```bash
python jira_csv_import.py
```

With explicit CSV:

```bash
python jira_csv_import.py --csv data/crm_admin_jira_import.csv
```

With explicit project key override:

```bash
python jira_csv_import.py --project-key TA
```

Dry run (prints payloads only):

```bash
python jira_csv_import.py --dry-run
```

Useful runtime flags:
- `--stop-on-error`
- `--timeout-seconds`
- `--max-retries`
- `--retry-backoff-seconds`
- `--board-id`
- `--base-url`

## CSV Format

Current expected columns (see `data/jira_import_template.csv`):

- `Project` (currently not used by field mapping)
- `Issue Type`
- `Status` (used after create for transition)
- `Summary` (required)
- `Parent` (optional; issue key)
- `Complexity` (optional; mapped to Jira custom field)
- `Description`
- `Label` (comma or semicolon-separated)

Behavior:
- `Summary` is required; blank summary fails that row.
- `Description` is converted to Atlassian Document Format (ADF).
- `Label` supports `a,b,c` or `a;b;c`.
- `Status` attempts transition after issue creation.

## How To Choose Which CSV File To Import

You have two ways:

1. CLI flag (highest priority)

```bash
python jira_csv_import.py --csv data/sis_jira_import.csv
```

2. Environment variable default

```env
JIRA_CSV_NAME=data/sis_jira_import.csv
```

If neither is provided, default is `data/crm_admin_jira_import.csv`.

## How To Add or Remove Fields Per Jira Project

All field mapping is implemented in `jira_import/issue_fields.py` inside `build_issue_fields(...)`.

### Current Mapping

- `project` -> selected project key
- `summary` -> CSV `Summary`
- `issuetype.name` -> CSV `Issue Type` (default `Task`)
- `description` -> CSV `Description` (ADF)
- `labels` -> CSV `Label`
- `parent.key` -> CSV `Parent`
- `customfield_15400` -> CSV `Complexity`

### Remove a Field

Example: remove `Complexity` mapping for a project that does not use it.

1. Open `jira_import/issue_fields.py`.
2. In `build_issue_fields(...)`, remove or guard this block:

```python
if complexity:
    fields[COMPLEXITY_FIELD_KEY] = {"value": complexity}
```

### Add a Field

Example: add a Jira custom dropdown field `customfield_12345` using CSV column `Team`.

1. Add the column header `Team` in your CSV.
2. In `build_issue_fields(...)`:

```python
team = get_row_value(row, "Team")
if team:
    fields["customfield_12345"] = {"value": team}
```

### Project-Specific Field Sets

If different Jira projects require different fields, branch by `project_key`:

```python
if project_key == "TA":
    # TA-only fields
    pass
elif project_key == "ABC":
    # ABC-only fields
    pass
```

Recommended pattern:
- keep shared/base fields in one dict
- apply project-specific additions/removals afterward
- fail fast with clear errors if a required CSV column is missing

### Finding the Correct Jira Field ID

For custom fields, use Jira field IDs like `customfield_12345` (not display names). Confirm IDs from Jira admin/API before mapping.

## Logging and Exit Codes

- Logs are JSON lines to console and `data/import.log`.
- Exit codes:
  - `0`: completed with no failed rows
  - `1`: configuration/auth/input failure before import
  - `2`: import completed with one or more row failures

## Troubleshooting

- `csv_not_found`: verify `--csv` path or `JIRA_CSV_NAME`.
- `authentication_failed`: check token/email/base URL.
- `Board has multiple projects`: pass `--project-key`.
- transition errors: confirm workflow allows transition from current status.
- custom field errors: field ID/type likely mismatched to payload structure.
