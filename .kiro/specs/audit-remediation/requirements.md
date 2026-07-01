# Requirements Document

## Introduction

This specification covers the remediation of 14 confirmed audit findings from the Cloud Janitor project's Audit Report (2026-07-01). The findings span CRITICAL, HIGH, MEDIUM, and LOW severity levels and must be resolved to bring the codebase to production quality. Work is organized by severity — CRITICAL and HIGH findings represent functional correctness issues that break core product promises, while MEDIUM and LOW findings address robustness, maintainability, and operational readiness.

## Glossary

- **Orchestrator**: The central coordination module (`orchestrator.py`) that sequences agent execution, manages approval gates, and invokes Terraform operations.
- **Approval_Gate**: A rate-limiting mechanism that restricts the number of attempts a user can make to approve a remediation action before locking out further attempts.
- **TF_CMD**: An environment variable specifying the Terraform binary path used by the Orchestrator to execute infrastructure changes (defaults to `tflocal`).
- **Findings_Store**: The JSON file (`findings_store.json`) where FinOps and SecOps agents persist their scan results for downstream consumption.
- **Rollback_File**: A Terraform HCL file stored in the rollbacks directory that reverts a specific remediation to its prior state.
- **Pre_Remediation_Hook**: A validation step that checks rollback file integrity against the proposed remediation plan before allowing approval.
- **Streamlit_UI**: The web-based user interface (`app.py`) built on the Streamlit framework that provides interactive access to audit, approval, and rollback workflows.
- **Reasoning_Log**: A log file capturing agent decision-making traces for debugging and auditability purposes.
- **Savings_Tracker**: A module that records cost-savings metrics after successful remediations.
- **Schema_Version**: A version identifier embedded in the Findings_Store to ensure forward/backward compatibility as finding shapes evolve.

## Requirements

### Requirement 1: Rollback Path Approval Gate and Terraform Execution

**User Story:** As an operator, I want the rollback path to enforce rate-limited approval and actually execute Terraform, so that rollbacks are both safe and functional.

#### Acceptance Criteria

1. WHEN a rollback is requested, THE Orchestrator SHALL create or retrieve an Approval_Gate for the target resource before proceeding.
2. WHILE an Approval_Gate is active for a resource, THE Orchestrator SHALL enforce the same maximum-attempts rate limiting as the approve workflow (3 attempts before lockout), and SHALL only reset the lockout on an explicit operator reset action — not on process restart.
3. WHEN a rollback is confirmed and the Approval_Gate check passes, THE Orchestrator SHALL validate the Rollback_File by running `subprocess.run([TF_CMD, "validate"], ...)` with a timeout of 300 seconds. IF validation succeeds (exit code 0), THE Orchestrator SHALL execute `subprocess.run([TF_CMD, "apply", "-auto-approve"], ...)` against the validated Rollback_File.
4. IF the Terraform execution fails during rollback (non-zero exit code or timeout), THEN THE Orchestrator SHALL log the error including stderr output, preserve the Rollback_File unchanged, and return a RollbackResult with `success=False` and the error details including exit code.
5. IF the Approval_Gate is locked for a resource, THEN THE Orchestrator SHALL reject the rollback request and return a RollbackResult with `success=False` and an error message indicating the gate is locked and the number of failed attempts.
6. IF the Rollback_File does not exist for the target resource at the expected path (`rollbacks/<resource_id>.tf`), THEN THE Orchestrator SHALL reject the rollback request and return a RollbackResult with `success=False` and an error identifying the missing file path.

### Requirement 2: TF_CMD Environment Variable Validation

**User Story:** As a security engineer, I want the Terraform command path to be validated against an allowlist, so that arbitrary binary execution is prevented even if the process environment is compromised.

#### Acceptance Criteria

1. WHEN the Orchestrator initializes, THE Orchestrator SHALL extract the basename from the TF_CMD value (stripping any directory prefix) and validate it against an allowlist of permitted binary names (at minimum: `terraform`, `tflocal`).
2. IF the extracted basename of TF_CMD does not match any entry in the allowlist, THEN THE Orchestrator SHALL raise a configuration error indicating the rejected value and the list of permitted binary names, and refuse to start.
3. IF TF_CMD contains path separator characters (/ or \), THEN THE Orchestrator SHALL reject the value with a configuration error indicating that only bare binary names are permitted.
4. WHEN TF_CMD passes the allowlist check, THE Orchestrator SHALL resolve it to an absolute path via system PATH lookup and verify the target file exists and is executable before accepting it.
5. IF the resolved TF_CMD path does not exist or is not executable, THEN THE Orchestrator SHALL raise a configuration error indicating the binary name could not be found on PATH, and refuse to start.
6. THE project SHALL provide a repo-local wrapper script (`bin/tflocal`) that is a valid, executable binary named `tflocal` on PATH. For demo/dry-run use, the wrapper script SHALL accept the same subcommands as the real tflocal/terraform binaries (`validate`, `apply -auto-approve`) and, WHEN `JANITOR_DRY_RUN=1` is set in the environment, SHALL print the command it would have run and exit 0 instead of invoking Terraform. THE Orchestrator's TF_CMD allowlist SHALL NOT be widened to accommodate this — the wrapper satisfies the allowlist by being named `tflocal`, not by adding a new allowlist entry.

### Requirement 3: Streamlit UI Audit Delegation

**User Story:** As a developer, I want the "Run Audit" button to delegate to `Orchestrator.execute_audit()` directly, so that UI and orchestration logic cannot diverge.

#### Acceptance Criteria

1. WHEN the user clicks "Run Audit" in the Streamlit_UI, THE Streamlit_UI SHALL invoke `Orchestrator.execute_audit()` as the sole call that triggers the audit pipeline, and SHALL NOT directly call any private method or attribute (prefixed with `_`) of the Orchestrator instance to perform scanning, validation, or planning steps.
2. WHILE `execute_audit()` is running, THE Streamlit_UI SHALL display per-agent progress (idle, running, success, or failure for each of FinOps Auditor, SecOps Guard, and Remediation Architect) by consuming a status callback callable passed to the Orchestrator rather than accessing private agent instances or internal state.
3. IF `execute_audit()` returns an `AuditResult` with `success == False`, THEN THE Streamlit_UI SHALL display the value of `AuditResult.error` to the user and SHALL NOT invoke any additional Orchestrator methods to retry, re-scan, or modify the result.
4. WHEN `execute_audit()` returns an `AuditResult` with `success == True`, THE Streamlit_UI SHALL render the findings, plans, and blocked plans exclusively from the returned `AuditResult` fields without re-reading output files or calling private Orchestrator members.

### Requirement 4: Path Convention Alignment

**User Story:** As a developer, I want a single consistent path convention for all runtime artifacts, so that the UI reliably displays findings and diffs produced by the orchestrator.

#### Acceptance Criteria

1. THE Orchestrator SHALL write all runtime artifacts to the `output/` directory tree using the following subdirectory layout: `output/findings_store.json` for Findings_Store, `output/rollbacks/` for Rollback_Files, `output/logs/` for audit log and Reasoning_Log, and `output/policies/` for policy artifacts.
2. THE Streamlit_UI SHALL read all runtime artifacts from the same `output/` directory tree used by the Orchestrator, using the same path-configuration source defined in criterion 4.
3. WHEN the Orchestrator initializes, THE Orchestrator SHALL create the `output/`, `output/rollbacks/`, `output/logs/`, and `output/policies/` directories if they do not exist.
4. THE Orchestrator SHALL define all artifact paths in a single path-configuration source (constants module or config object) that is imported by both the Orchestrator and the Streamlit_UI, such that no artifact path is constructed via string literal outside that source.
5. IF the Orchestrator fails to create any required `output/` subdirectory during initialization, THEN THE Orchestrator SHALL halt execution and produce an error message indicating which directory could not be created and the underlying OS error.
6. IF the Streamlit_UI attempts to read an artifact file that does not yet exist under `output/`, THEN THE Streamlit_UI SHALL display a message indicating that no data is available for that artifact rather than raising an unhandled error.

### Requirement 5: Pre-Remediation Hook Full Validation

**User Story:** As an operator, I want the pre-remediation hook to validate all rollback files for a given audit run, so that no remediation proceeds with incomplete rollback coverage.

#### Acceptance Criteria

1. WHEN the Pre_Remediation_Hook runs, THE Orchestrator SHALL locate and validate the Rollback_File at `rollbacks/<resource_id>.tf` for every active (non-blocked) plan in the current audit run, not only the first match.
2. THE Pre_Remediation_Hook SHALL consider a Rollback_File valid only if the file exists, is non-empty (at least 1 byte), and the pre-remediation hook script exits with code 0 when passed the file path.
3. IF any active plan lacks a corresponding Rollback_File or the file fails validation, THEN THE Orchestrator SHALL block the entire remediation run and return an error listing each resource_id that is missing rollback coverage.
4. WHEN all Rollback_Files pass validation, THE Pre_Remediation_Hook SHALL return a validation result containing the list of validated file paths (one per plan) and an empty failures list.
5. IF the Pre_Remediation_Hook exceeds 60 seconds of total execution time, THEN THE Orchestrator SHALL abort validation, block remediation, and report a timeout error.

### Requirement 6: Persistent Approval Gates

**User Story:** As an operator, I want approval gate state to survive process restarts, so that rate-limiting protections cannot be bypassed by restarting the application.

#### Acceptance Criteria

1. WHEN an Approval_Gate attempt count changes or an Approval_Gate becomes locked, THE Orchestrator SHALL persist the updated gate state (resource_id, attempt count, locked status, and max_attempts) to a durable store before returning the result to the caller.
2. WHEN the Orchestrator initializes and a durable store file exists, THE Orchestrator SHALL load all previously persisted Approval_Gate states from the durable store and restore each gate's attempt count and locked status.
3. IF the durable store file does not exist on Orchestrator initialization, THEN THE Orchestrator SHALL start with an empty set of Approval_Gate states and create the durable store file on the first gate state change.
4. IF the durable store file exists but cannot be parsed (malformed content, I/O error, or missing required fields), THEN THE Orchestrator SHALL log a WARNING-level message identifying the failure reason, initialize all gates in a locked state, and reject all approval and rollback attempts until the operator deletes or replaces the durable store file and restarts the Orchestrator.
5. WHEN the Orchestrator persists gate state, THE Orchestrator SHALL write the complete set of all active gates atomically (write-then-rename) so that a crash mid-write does not corrupt the store.

### Requirement 7: Findings Store Schema Validation

**User Story:** As a developer, I want the findings store to include and enforce a schema version, so that incompatible finding shapes are detected early rather than causing silent failures.

#### Acceptance Criteria

1. THE Orchestrator SHALL write a `schema_version` field as a string in semantic versioning format (e.g., `"1.0.0"`) into the top-level object of the Findings_Store on every audit run.
2. WHEN the Orchestrator reads the Findings_Store, THE Orchestrator SHALL validate that the `schema_version` field exists and that its major version number matches the current expected major version.
3. IF the `schema_version` field is missing, THEN THE Orchestrator SHALL reject the store with an error indicating "schema_version field is missing."
4. IF the `schema_version` major version does not match the expected major version, THEN THE Orchestrator SHALL reject the store with an error indicating the found version and the expected version.
5. IF the `schema_version` minor version is higher than expected (same major), THEN THE Orchestrator SHALL log a WARNING and proceed with best-effort parsing.

### Requirement 8: Savings Tracker Broad Exception Handling

**User Story:** As a developer, I want savings tracking to never propagate exceptions after a successful remediation, so that a corrupted ledger does not undo completed infrastructure changes.

#### Acceptance Criteria

1. WHEN `savings_tracker.record_run()` is called after a successful remediation, THE Orchestrator SHALL wrap the call in a try/except that catches `Exception` (i.e., all exceptions deriving from `Exception`, including `json.JSONDecodeError`, `ValueError`, `KeyError`, `FileNotFoundError`, and `OSError`) rather than only `FileNotFoundError` and `OSError`.
2. IF `savings_tracker.record_run()` raises any `Exception` subclass, THEN THE Orchestrator SHALL log a message at WARNING level that includes the exception type and message, and continue execution without re-raising.
3. IF `savings_tracker.record_run()` raises any `Exception` subclass, THEN THE Orchestrator SHALL return `ApprovalResult(success=True)` for the approved resource, identical to the result returned when no exception occurs.

### Requirement 9: Resource ID Extraction Allowlist Validation

**User Story:** As a security engineer, I want resource ID extraction to use a positive-match allowlist, so that unexpected input formats are safely rejected rather than relying on a single negative check.

#### Acceptance Criteria

1. WHEN extracting a resource ID from a command string, THE Orchestrator SHALL validate the candidate against a full-match regex allowlist pattern permitting only alphanumeric characters, hyphens, underscores, colons, periods, and forward slashes, with a total length between 1 and 256 characters.
2. IF the candidate does not match the allowlist pattern, THEN THE Orchestrator SHALL return `None` and log a message at DEBUG level that includes the rejected value (truncated to 64 characters if longer).
3. IF the candidate after prefix stripping is empty or contains only whitespace, THEN THE Orchestrator SHALL return `None` without invoking the allowlist regex.

### Requirement 10: SPEC_COMPLIANCE.md and NL Audit Correction

**User Story:** As a developer, I want the NL Audit feature status to be accurately reflected in both code and compliance tracking, so that users are not presented with non-functional UI elements.

#### Acceptance Criteria

1. WHILE `execute_natural_language_audit()` is not callable on the Orchestrator instance (i.e., `hasattr(orchestrator, 'execute_natural_language_audit')` returns False), THE Streamlit_UI SHALL display an informational message stating that the natural-language audit feature is not yet available, and SHALL NOT attempt to invoke the method.
2. IF `execute_natural_language_audit()` raises an exception during execution, THEN THE Streamlit_UI SHALL display an error message indicating the failure reason and SHALL preserve any previously displayed audit state.
3. WHEN the NL Audit feature is callable on the Orchestrator (i.e., `hasattr(orchestrator, 'execute_natural_language_audit')` returns True) and the user submits a non-empty query string, THE Streamlit_UI SHALL call `Orchestrator.execute_natural_language_audit()` with the trimmed query and display the returned findings count and findings list.
4. THE SPEC_COMPLIANCE.md document SHALL reflect the NL Audit feature status as one of: "Pending" (no UI elements or backend exist), "Partial" (UI elements exist but backend method is missing or non-functional), or "Complete" (UI elements exist and backend method is implemented and callable), matching the actual state of the codebase at time of generation.

### Requirement 11: Reasoning Log Preservation

**User Story:** As a developer, I want historical reasoning logs to be preserved across audit runs, so that past agent decisions can be reviewed for debugging.

#### Acceptance Criteria

1. WHEN a new audit run begins, THE Orchestrator SHALL open the Reasoning_Log in append mode, preserving all previously written entries.
2. WHEN a new audit run begins, THE Orchestrator SHALL write a JSONL-formatted separator entry containing the fields `event_type` set to `"run_separator"`, `timestamp` in ISO 8601 UTC format, and `message` indicating the new run, before emitting any agent reasoning entries.
3. WHERE log rotation is configured, THE Orchestrator SHALL rotate the Reasoning_Log when the file size exceeds the configured threshold (default: 10 MB), renaming the current file with a numeric suffix and retaining a maximum of 5 rotated files before deleting the oldest.
4. IF the Reasoning_Log file does not exist when a new audit run begins, THEN THE Orchestrator SHALL create it and proceed with writing without raising an error.

### Requirement 12: Structured Error Telemetry

**User Story:** As an operator, I want errors to be captured with structured metadata, so that production failures can be diagnosed without manually correlating timestamps across logs.

#### Acceptance Criteria

1. WHEN an agent exception occurs, THE Orchestrator SHALL capture a structured error record containing the fields: `error_type` (exception class name), `message` (exception string), `traceback` (formatted stack trace truncated to 4096 characters), `timestamp` (ISO 8601 UTC), `agent_name` (string identifying the failing agent), and `error_category`.
2. THE Orchestrator SHALL write structured error records to the audit log as one JSON object per line (JSONL format).
3. THE Orchestrator SHALL classify errors into exactly one of these categories: `agent_failure` (exception within agent scan/plan logic), `terraform_failure` (non-zero exit from TF_CMD), `validation_failure` (schema, gate, or hook validation errors), `io_failure` (file system or network I/O errors).
4. THE Streamlit_UI SHALL surface the `error_category`, `agent_name`, and `message` fields from the structured error record when displaying errors to the user, rather than raw exception strings.

### Requirement 13: Explicit Agent Imports

**User Story:** As a developer, I want Phase B/C agent imports to be statically visible to type checkers, so that missing agents are caught at development time rather than silently becoming `None` at runtime.

#### Acceptance Criteria

1. THE Streamlit_UI SHALL import each Phase B/C agent (QueryInterpreter, RemediationExplainer, PolicySuggester, AnomalyDetector, DriftDetector, MultiAccountOrchestrator, JanitorScheduler) using standard `import` or `from ... import` statements rather than dynamic `globals()` or `__import__()` manipulation.
2. IF a Phase B/C agent module is unavailable at import time, THEN THE Streamlit_UI SHALL catch the `ImportError` individually per agent and assign the name to a typed `Optional` variable (e.g., `QueryInterpreter: type[agents.query_interpreter.QueryInterpreter] | None = None`) so that the fallback value is statically visible as `None`.
3. THE Streamlit_UI SHALL expose all imported Phase B/C agent names with type annotations such that running mypy or pyright in strict mode produces zero `type: ignore` annotations and zero "name is not defined" errors related to those agent symbols.
4. IF a new Phase B/C agent is added to the codebase, THEN THE Streamlit_UI SHALL require a corresponding explicit import statement; no registry list or loop-based dynamic import pattern shall be used to auto-discover agents.

### Requirement 14: Session-Isolated File Paths

**Status: DEFERRED** to post-hackathon prod-readiness milestone. Not part of the July 4 BuildFest submission. Do not implement acceptance criteria 14.1–14.5 in this remediation pass — implementation of Req 14 will be scoped as a separate future spec.

**User Story:** As an operator, I want concurrent Streamlit sessions to use isolated file paths, so that simultaneous users do not overwrite each other's audit artifacts.

#### Acceptance Criteria

1. WHEN a new Streamlit session is created, THE Orchestrator SHALL generate a session-scoped subdirectory under `output/` named with a universally unique identifier (UUID v4) that is unique per session.
2. WHILE a Streamlit session is active, THE Orchestrator SHALL read and write all runtime artifacts (Findings_Store, Rollback_Files, remediation plan, Reasoning_Log) within that session's isolated directory, and SHALL NOT read from or write to another session's directory.
3. WHILE a Streamlit session is active, THE Streamlit_UI SHALL display only the artifacts located within the current session's isolated directory.
4. WHEN a session ends (browser tab closed or WebSocket disconnected) or the session has been inactive for a configurable timeout (default: 60 minutes, minimum: 5 minutes, maximum: 1440 minutes), THE Orchestrator SHALL retain the session's artifacts for a configurable retention period (default: 24 hours, minimum: 1 hour, maximum: 168 hours) before deleting the session directory and its contents.
5. IF the Orchestrator fails to create the session-scoped subdirectory (due to filesystem errors or permissions), THEN THE Orchestrator SHALL return an error to the Streamlit_UI indicating session initialization failed, and SHALL NOT proceed with any audit operations for that session.
