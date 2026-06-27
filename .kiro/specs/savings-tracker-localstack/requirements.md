# Requirements Document

## Introduction

This feature adds multiple capabilities to the Cloud Janitor project: (1) a persistent cumulative savings ledger that tracks cost savings across multiple audit-and-remediation runs, (2) LocalStack wiring so that Terraform execution in demo environments targets a local AWS emulator instead of real AWS infrastructure, (3) a SPEC_COMPLIANCE.md generator script that verifies task completion against actual repository artifacts, and (4) a streaming agent reasoning log that exposes step-by-step agent decisions in real time via structured JSON events and a Streamlit panel. The savings tracker and reasoning log are user-facing features; the LocalStack wiring and compliance generator are internal project infrastructure.

## Glossary

- **Savings_Tracker**: The module (`savings.py`) responsible for reading findings_store.json, computing savings from approved remediations, persisting results to savings_ledger.json, and exposing summary data via `get_savings_summary()`.
- **Savings_Ledger**: The persistent JSON file (`savings_ledger.json`) at project root that stores the lifetime history of remediated savings across all runs. This file is runtime-generated and excluded from version control via `.gitignore`.
- **Findings_Store**: The existing `findings_store.json` file that contains scan results, findings, severity, and cost estimates produced by the FinOps Auditor and SecOps Guard agents.
- **Run_Entry**: A single record within the savings ledger representing one completed remediation run.
- **Terraform_Executor**: The subprocess layer within `mcp_server/aws_janitor_mcp.py` that invokes Terraform commands (init, validate, apply) for infrastructure remediation.
- **LocalStack_Environment**: A Docker-based local AWS emulator used exclusively for demo execution of Terraform plans.
- **Orchestrator**: The `orchestrator.py` module that sequences the agent pipeline and invokes post-remediation logic via `_run_post_remediation_hook()`.
- **Compliance_Generator**: The script `generate_spec_compliance.py` at project root that reads task checkboxes from `.kiro/specs/tasks.md`, verifies corresponding file artifacts exist, and outputs `SPEC_COMPLIANCE.md`.
- **Post_Commit_Hook**: A Git post-commit hook that automatically invokes `generate_spec_compliance.py` after every commit.
- **Reasoning_Logger**: The logging subsystem within each agent that emits structured JSON event lines to `agent_reasoning.log` during audit execution.
- **FinOps_Auditor**: The FinOps Auditor agent (`agents/finops_auditor.py`) that detects financial waste in cloud resources.
- **SecOps_Guard**: The SecOps Guard agent (`agents/secops_guard.py`) that detects security vulnerabilities in cloud resources.
- **Remediation_Architect**: The Remediation Architect agent (`agents/remediation_architect.py`) that generates Terraform HCL for remediation and rollback.
- **Streamlit_Dashboard**: The Streamlit-based UI (`app.py`) that displays agent activity, findings, diffs, and audit trail.
- **Docker_Compose_Configuration**: The `docker-compose.yml` file at project root that defines container services for the demo environment.
- **Project_Configuration**: Project-level configuration files including `.gitignore` that control repository behavior.

## Requirements

### Requirement 1: Savings Ledger File Creation

**User Story:** As a Cloud Janitor operator, I want a savings ledger file created automatically after the first approved remediation, so that I have a persistent record of cost savings from the start.

#### Acceptance Criteria

1. WHEN the first remediation is approved and executed, THE Savings_Tracker SHALL create `savings_ledger.json` in the project root directory
2. THE Savings_Tracker SHALL write the savings ledger using the schema: `total_lifetime_savings` (float), `runs` (array of Run_Entry objects)
3. WHEN savings_ledger.json does not exist at write time, THE Savings_Tracker SHALL initialize it with `total_lifetime_savings` set to 0.0 and an empty `runs` array before appending the first Run_Entry
4. THE Project_Configuration SHALL add `savings_ledger.json` to `.gitignore` since it is runtime-generated data

### Requirement 2: Run Entry Recording

**User Story:** As a Cloud Janitor operator, I want each remediation run recorded with its constituent savings details, so that I can trace savings back to specific audit runs and resources.

#### Acceptance Criteria

1. WHEN a remediation run completes, THE Savings_Tracker SHALL append a Run_Entry containing: `run_id` (sourced from `scan_id` in findings_store.json), `timestamp` (sourced from `completed_at` in findings_store.json), `resources_remediated` (list of resource_id strings that were approved and executed), `monthly_savings_added` (float), and `cumulative_at_time` (float)
2. THE Savings_Tracker SHALL compute `monthly_savings_added` as the sum of `cost_estimate_monthly` from all findings in findings_store.json whose resource_id appears in the `resources_remediated` list
3. THE Savings_Tracker SHALL compute `cumulative_at_time` by recalculating from source: summing `monthly_savings_added` across all Run_Entry objects in the `runs` array (including the newly appended entry). THE Savings_Tracker SHALL NOT use incremental addition from the previous `total_lifetime_savings` value, so that corrupted individual entries self-heal on the next write.
4. WHEN a Run_Entry is appended, THE Savings_Tracker SHALL update the top-level `total_lifetime_savings` by recalculating the sum of all `monthly_savings_added` values across all entries in the `runs` array

### Requirement 3: Duplicate Run Prevention

**User Story:** As a Cloud Janitor operator, I want the savings tracker to reject duplicate run entries, so that re-running the same scan does not inflate savings figures.

#### Acceptance Criteria

1. WHEN the Savings_Tracker is invoked with a `run_id` that already exists in the `runs` array, THE Savings_Tracker SHALL skip appending and leave the ledger unchanged
2. THE Savings_Tracker SHALL compare the incoming `run_id` against all existing Run_Entry `run_id` values before writing
3. WHEN a duplicate `run_id` is detected, THE Savings_Tracker SHALL NOT modify the ledger file at all — the file's modification time (mtime) SHALL remain unchanged

### Requirement 4: Savings Summary API

**User Story:** As a Cloud Janitor operator, I want a programmatic interface to retrieve a summary of cumulative savings, so that the dashboard and external tools can display savings metrics.

#### Acceptance Criteria

1. THE Savings_Tracker SHALL expose a function `get_savings_summary()` that returns a dictionary with keys: `total_lifetime_monthly` (float), `total_lifetime_annual` (float), `total_runs` (int), `last_run_savings` (float)
2. THE Savings_Tracker SHALL compute `total_lifetime_annual` as `total_lifetime_monthly` multiplied by 12
3. WHEN no runs exist in the ledger, THE Savings_Tracker SHALL return `total_lifetime_monthly` as 0.0, `total_lifetime_annual` as 0.0, `total_runs` as 0, and `last_run_savings` as 0.0
4. THE Savings_Tracker SHALL compute `last_run_savings` from the most recent Run_Entry `monthly_savings_added` value

### Requirement 5: Orchestrator Integration

**User Story:** As a Cloud Janitor operator, I want savings tracking to execute automatically after each approved remediation, so that I do not need to manually trigger ledger updates.

#### Acceptance Criteria

1. WHEN the Orchestrator's `approve()` method successfully approves and executes a remediation, THE Orchestrator SHALL invoke the Savings_Tracker to record the run in the savings ledger
2. THE Savings_Tracker SHALL only count resources whose remediation was approved and executed, not resources that were merely detected or blocked
3. THE Savings_Tracker invocation SHALL occur exclusively within the Orchestrator's `approve()` method, after successful execution is confirmed. It SHALL NOT be invoked from `_run_post_remediation_hook` to avoid double-counting risk.

### Requirement 6: LocalStack Demo Environment

**User Story:** As a developer running the demo, I want Terraform commands to execute against LocalStack instead of real AWS, so that the demo can run infrastructure operations without cloud credentials or cost.

#### Acceptance Criteria

1. THE Terraform_Executor SHALL invoke `tflocal` in place of `terraform` for ALL subprocess calls in the codebase — this includes `terraform init`, `terraform validate`, and `terraform apply`. Specifically: (a) `mcp_server/aws_janitor_mcp.py` `validate_hcl()` function, and (b) `.kiro/hooks/pre-remediation.sh` which currently calls `terraform -chdir=... init` and `terraform -chdir=... validate`. No subprocess call in the codebase shall invoke the bare `terraform` binary.
2. THE Terraform_Executor SHALL depend on the `terraform-local` Python package (added to requirements.txt)
3. THE Docker_Compose_Configuration SHALL define a `localstack` service in `docker-compose.yml` that exposes port 4566 to the host and configures the `SERVICES` environment variable to include EC2, ElastiCache, S3, and EBS
4. THE Terraform_Executor SHALL invoke `tflocal apply -auto-approve` (not just `tflocal plan`) against the LocalStack_Environment during execution of the `make demo` target
5. IF `tflocal apply` returns a non-zero exit code, THEN THE Terraform_Executor SHALL surface the stderr output as an error message and halt the pipeline without proceeding to the approval gate

### Requirement 7: Make Demo Target

**User Story:** As a developer running the demo, I want a single `make demo` command to start the full demo environment from cold, so that setup is frictionless.

#### Acceptance Criteria

1. THE Makefile SHALL define a `demo` target in a `Makefile` at the project root
2. WHEN `make demo` is executed, THE Makefile SHALL start the LocalStack_Environment via `docker-compose up -d`
3. WHEN `make demo` is executed, THE Makefile SHALL poll the LocalStack health endpoint (`http://localhost:4566/_localstack/health`) every 2 seconds, up to 30 attempts (60 seconds total), printing a dot (`.`) to stdout on each attempt so the user sees progress
4. WHEN `make demo` is executed, THE Makefile SHALL run the full Cloud Janitor audit-and-remediation pipeline against LocalStack, including `tflocal apply -auto-approve`
5. IF the LocalStack health check does not return HTTP 200 within 30 attempts (60 seconds), THEN THE Makefile SHALL exit with a non-zero status and print an error message indicating the LocalStack_Environment failed to start

### Requirement 8: SPEC_COMPLIANCE.md Generator Script

**User Story:** As a project maintainer, I want an automated script that checks task completion against actual file artifacts in the repository, so that I can verify spec compliance without manual inspection.

#### Acceptance Criteria

1. THE Compliance_Generator SHALL be implemented as `generate_spec_compliance.py` in the project root
2. WHEN executed, THE Compliance_Generator SHALL read `.kiro/specs/tasks.md` and parse each task checkbox where `- [x]` indicates done, `- [ ]` indicates not done, and `- [-]` indicates partial
3. WHEN a task is marked done, THE Compliance_Generator SHALL verify that at least one corresponding file exists in the repository using the following mapping:
   - Tasks mentioning "requirements" map to `.kiro/specs/requirements.md`
   - Tasks mentioning "design" map to `.kiro/specs/design.md`
   - Tasks mentioning "fixture" map to the `fixtures/` directory
   - Tasks mentioning "mcp" or "MCP" map to `mcp_server/aws_janitor_mcp.py`
   - Tasks mentioning "FinOps" or "finops" map to `agents/finops_auditor.py`
   - Tasks mentioning "SecOps" or "secops" map to `agents/secops_guard.py`
   - Tasks mentioning "Remediation" or "remediation" map to `agents/remediation_architect.py`
   - Tasks mentioning "rollback" map to the `rollbacks/` directory
   - Tasks mentioning "findings_store" map to `findings_store.json`
   - Tasks mentioning "pre-remediation" map to `.kiro/hooks/pre-remediation.sh`
   - Tasks mentioning "post-remediation" map to `.kiro/hooks/post-remediation.sh`
   - Tasks mentioning "approval" map to the presence of the string "APPROVE" in any file under `agents/` or in `orchestrator.py`
   - Tasks mentioning "audit log" map to `audit.log` or the presence of an audit log writer in the codebase
   - Tasks mentioning "Streamlit" or "UI" or "app.py" map to `app.py`
   - Tasks mentioning "savings" map to `savings.py`
4. THE Compliance_Generator SHALL output a `SPEC_COMPLIANCE.md` file in the project root containing a table that marks each task as done, partial, or pending based on actual file existence
5. WHEN `python3 generate_spec_compliance.py` is executed, THE Compliance_Generator SHALL complete without errors
6. THE Post_Commit_Hook SHALL invoke `generate_spec_compliance.py` automatically after every commit AND run `git add SPEC_COMPLIANCE.md` so that the updated file is staged for inclusion. The hook command SHALL be: `python3 generate_spec_compliance.py && git add SPEC_COMPLIANCE.md`

### Requirement 9: Streaming Agent Reasoning Logger

**User Story:** As a Cloud Janitor operator, I want each agent to emit structured reasoning events to a log file, so that I can trace the decision-making process for each audit run.

#### Acceptance Criteria

1. THE FinOps_Auditor SHALL emit structured JSON log lines to `agent_reasoning.log` with the following event types: `check` (scan start and per-resource), `finding` (per flagged resource), `skip` (per resource below threshold), and `handoff` (scan complete)
2. THE SecOps_Guard SHALL emit structured JSON log lines to `agent_reasoning.log` with the following event types: `check` (scan start and per-rule), `finding` (per violation detected), and `handoff` (scan complete)
3. THE Remediation_Architect SHALL emit structured JSON log lines to `agent_reasoning.log` with the following event types: `check` (start and per-dependency), `decision` (per result and per HCL generated), and `handoff` (planning complete)
4. THE Reasoning_Logger SHALL format each log line as a JSON object containing keys: `timestamp` (ISO 8601 with UTC timezone), `agent` (string, max 64 characters), `event_type` (one of check, finding, skip, decision, handoff), `resource_id` (string, empty string if not resource-specific), and `message` (string, max 500 characters, plain-text explanation of the reasoning step)
5. WHEN a new audit run starts, THE Reasoning_Logger SHALL truncate `agent_reasoning.log` before writing new events
6. THE Reasoning_Logger SHALL append events sequentially during a run without overwriting prior lines within the same run
7. IF the Reasoning_Logger fails to write to `agent_reasoning.log` due to a file system error, THEN THE Reasoning_Logger SHALL continue agent execution without interruption and log the write failure to stderr
8. THE Project_Configuration SHALL add `agent_reasoning.log` to `.gitignore`
9. THE Reasoning_Logger SHALL ensure each line is valid JSON. IF an agent emits a line that is not valid JSON (due to a bug), consumers of the log SHALL skip that line and log the parse failure to stderr rather than crashing.

### Requirement 10: Reasoning Log Streamlit Panel

**User Story:** As a Cloud Janitor operator, I want to observe the reasoning log in the Streamlit dashboard in real time, so that I can watch agent decisions as they happen.

#### Acceptance Criteria

1. THE Streamlit_Dashboard SHALL poll `agent_reasoning.log` for new lines and render new events within 2 seconds of their emission. IF Streamlit >= 1.33 is available, THE Streamlit_Dashboard SHALL use `@st.fragment(run_every=1)` on the reasoning panel function so that only the panel reruns without resetting the rest of the app. IF Streamlit < 1.33, THE Streamlit_Dashboard SHALL run the audit pipeline in a background thread (`threading.Thread`, `daemon=True`) and poll from the main thread using `st.empty()` + `time.sleep(1)` inside a loop that checks a `session_state` flag. THE Streamlit_Dashboard SHALL NOT block the main Streamlit thread during polling.
2. THE Streamlit_Dashboard SHALL color-code events by type: `check` in grey (#9e9e9e), `finding` in amber (#ff9800), `skip` in light grey (#bdbdbd), `decision` in blue (#2196f3), and `handoff` in bold text
3. WHEN the emitting agent name in a new log event differs from the agent name in the immediately preceding displayed event, THE Streamlit_Dashboard SHALL display the new agent name as a section header above that event
4. WHILE the audit pipeline is executing, THE Streamlit_Dashboard SHALL auto-scroll the reasoning log display to the latest event
5. WHEN a new audit starts, THE Streamlit_Dashboard SHALL clear the previous reasoning display before rendering new events
6. IF a line read from `agent_reasoning.log` fails `json.loads()` parsing, THEN THE Streamlit_Dashboard SHALL skip that line silently and continue processing subsequent lines — it SHALL NOT crash or halt the display
