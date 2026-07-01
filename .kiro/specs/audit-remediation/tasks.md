# Implementation Plan: Audit Remediation

## Overview

This plan implements 13 audit remediation findings (Req 14 is deferred) organized into incremental coding tasks. Work flows from foundational modules (`core/paths.py` with directory creation, `core/error_telemetry.py`) through security validation, approval/rollback hardening, data integrity, and UI contract alignment. Each task builds on prior steps, ending with integration wiring.

## Tasks

- [ ] 1. Create foundational modules and path configuration
  - [ ] 1.1 Create `core/paths.py` centralized path configuration with directory creation helper
    - Create `core/__init__.py` if it does not exist
    - Implement `core/paths.py` with all path constants as specified in the design: `PROJECT_ROOT`, `OUTPUT_DIR`, `ROLLBACKS_DIR`, `LOGS_DIR`, `POLICIES_DIR`, `FINDINGS_STORE_PATH`, `AUDIT_LOG_PATH`, `REASONING_LOG_PATH`, `APPROVAL_GATES_PATH`, `SAVINGS_LEDGER_PATH`, `HOOKS_DIR`, `REQUIRED_DIRS`
    - Implement `ensure_output_dirs()` helper function that creates all directories listed in `REQUIRED_DIRS` (`output/`, `output/rollbacks/`, `output/logs/`, `output/policies/`), raising a descriptive `RuntimeError` on failure that identifies which directory could not be created and the underlying OS error
    - _Requirements: 4.1, 4.3, 4.4, 4.5_

  - [ ] 1.2 Create `core/error_telemetry.py` structured error module
    - Implement `ERROR_CATEGORIES` set, `build_error_record()` function, and `write_error_record()` function
    - `build_error_record` must produce a dict with fields: `error_type`, `message`, `traceback` (max 4096 chars), `timestamp` (ISO 8601 UTC), `agent_name`, `error_category`
    - `write_error_record` must append exactly one JSONL line to the target log path
    - _Requirements: 12.1, 12.2, 12.3_

  - [ ] 1.3 Create `bin/tflocal` wrapper script
    - Write the repo-local bash wrapper script at `bin/tflocal`
    - Script must: print command and exit 0 when `JANITOR_DRY_RUN=1`, otherwise delegate to real tflocal/terraform binary on PATH (skipping itself)
    - Ensure script is executable
    - _Requirements: 2.6_

  - [ ] 1.4 Write smoke tests for `bin/tflocal` wrapper (`tests/test_bin_tflocal.py`)
    - Test that `JANITOR_DRY_RUN=1 bin/tflocal validate` exits 0 and stdout contains `[DRY RUN]`
    - Test that `JANITOR_DRY_RUN=1 bin/tflocal apply -auto-approve` exits 0 and stdout contains the full command string
    - Test that the self-skip logic does not recurse into itself (run with only `bin/` on PATH, verify it does not loop — expect graceful error or fallback to terraform)
    - Use `subprocess.run()` with explicit `env` dict to control `JANITOR_DRY_RUN` and `PATH`
    - _Requirements: 2.6_

  - [ ] 1.5 Write property tests for `core/error_telemetry.py`
    - **Property 11: Structured Error Record Completeness**
    - **Property 12: JSONL Error Record Format**
    - **Validates: Requirements 12.1, 12.2, 12.3**

  - [ ] 1.6 Write unit tests for path configuration and directory creation (`tests/test_path_config.py`)
    - Test that `ensure_output_dirs()` creates all `REQUIRED_DIRS` when they do not exist
    - Test that `ensure_output_dirs()` raises descriptive error when directory creation fails (mocked `os.makedirs` raising `OSError`)
    - Test that `REQUIRED_DIRS` contains exactly `[OUTPUT_DIR, ROLLBACKS_DIR, LOGS_DIR, POLICIES_DIR]`
    - Test that UI displays "no data available" message when artifact file is missing
    - _Requirements: 4.1, 4.3, 4.5, 4.6_

- [ ] 2. Implement security validation layer
  - [ ] 2.1 Implement TF_CMD validation in Orchestrator
    - Add `TF_CMD_ALLOWLIST = {"terraform", "tflocal"}` constant
    - Implement `_validate_tf_cmd()` function: reject path separators, validate basename against allowlist, resolve via `shutil.which()`, raise `RuntimeError` on any failure
    - Call `_validate_tf_cmd()` during Orchestrator `__init__`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 2.2 Write property test for TF_CMD validation
    - **Property 2: TF_CMD Validation Partition**
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [ ] 2.3 Write unit tests for TF_CMD PATH resolution (`tests/test_tf_cmd_validation.py`)
    - Test PATH resolution with mocked `shutil.which` returning a valid path
    - Test PATH resolution with mocked `shutil.which` returning `None` (binary not found)
    - Test rejection of values containing `/` or `\` path separators
    - Test rejection of basename not in allowlist (e.g., `"evil_binary"`)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 2.4 Implement resource ID extraction with allowlist validation
    - Add `_RESOURCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_:./]{1,256}$")` constant
    - Implement `_extract_resource_id_from_command()` method: validate prefix, reject empty/whitespace, validate against allowlist regex, return `None` and log DEBUG on failure
    - _Requirements: 9.1, 9.2, 9.3_

  - [ ] 2.5 Write property test for resource ID extraction
    - **Property 4: Resource ID Extraction Allowlist**
    - **Validates: Requirements 9.1, 9.2, 9.3**

- [ ] 3. Implement persistent approval gates
  - [ ] 3.1 Implement `ApprovalGateStore` class
    - Create or update `agents/approval_gate.py` with `ApprovalGateStore` class
    - Implement `load()`: parse JSON, handle corruption (log WARNING, set `__corrupted__` flag, lock all gates)
    - Implement `save()`: atomic write-then-rename using `tempfile.mkstemp` + `os.replace`
    - Implement `get_gate()`, `set_gate()`, `is_corrupted` property
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 3.2 Integrate `ApprovalGateStore` into Orchestrator approval/rollback flow
    - Load gate store on Orchestrator init; check `is_corrupted` before any gate access
    - Persist gate state on every attempt count change or lockout
    - Enforce max 3 attempts before lockout; reject when locked with descriptive error
    - _Requirements: 1.1, 1.2, 1.5, 6.1, 6.2_

  - [ ] 3.3 Write property tests for approval gate persistence
    - **Property 8: Approval Gate Persistence Round Trip**
    - **Property 9: Corrupted Gate Store Locks All Gates**
    - Property 9 test must call Orchestrator's approve/rollback methods with a real resource_id against a corrupted store and assert rejection — not just assert `store.is_corrupted == True` after a bad load. Test the guard (Req 6.4 integration), not just the flag.
    - **Validates: Requirements 6.1, 6.2, 6.4**

  - [ ] 3.4 Write property test for gate lockout invariant
    - **Property 1: Approval Gate Lockout Invariant**
    - **Validates: Requirements 1.2, 1.5**

  - [ ] 3.5 Write unit tests for atomic write failure (`tests/test_gate_persistence.py`)
    - Test that atomic write-then-rename persists gate state correctly on success
    - Test behavior when `os.replace` is mocked to raise `OSError` (verify temp file cleanup, no corruption)
    - Test that loading a corrupted store (invalid JSON, missing `"gates"` key) results in `is_corrupted == True`
    - Test that a fresh store (no file on disk) initializes with empty gates
    - _Requirements: 6.1, 6.4, 6.5_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement rollback path with Terraform execution
  - [ ] 5.1 Implement Terraform validate + apply rollback flow
    - When rollback is confirmed and gate check passes: run `subprocess.run([TF_CMD, "validate"], ...)` with 300s timeout
    - On validate success: run `subprocess.run([TF_CMD, "apply", "-auto-approve"], ...)` with 300s timeout
    - On failure: return `RollbackResult(success=False, exit_code=..., error=stderr)`
    - Preserve Rollback_File unchanged on any failure
    - Check rollback file existence at `rollbacks/<resource_id>.tf` before proceeding
    - _Requirements: 1.3, 1.4, 1.6_

  - [ ] 5.2 Write property test for rollback failure propagation
    - **Property 3: Rollback Failure Error Propagation**
    - **Validates: Requirements 1.4, 1.6**

  - [ ] 5.3 Write unit tests for rollback Terraform sequence (`tests/test_rollback_flow.py`)
    - Test validate → apply sequence with mocked subprocess returning exit 0 for both
    - Test validate failure (mocked subprocess exit 1 with stderr) returns `RollbackResult(success=False)` with exit code and stderr
    - Test apply failure after successful validate preserves Rollback_File unchanged (check mtime)
    - Test missing rollback file returns `RollbackResult(success=False)` identifying missing path
    - _Requirements: 1.3, 1.4, 1.6_

- [ ] 6. Implement pre-remediation hook full validation
  - [ ] 6.1 Implement `_run_pre_remediation_hook_full()` method
    - Iterate all active plans, validate rollback file existence + non-empty + hook script exit 0
    - Enforce 60-second total timeout across all plans
    - Return `(validated_paths, failures)` tuple; block remediation if any failures
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 6.2 Write property tests for pre-remediation hook
    - **Property 6: Pre-Remediation Hook Coverage**
    - **Property 7: Pre-Remediation Hook Success**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**

  - [ ] 6.3 Write unit tests for hook timeout (`tests/test_pre_hook.py`)
    - Test that exceeding 60s total execution time raises `TimeoutError` and blocks remediation (use mocked `time.monotonic`)
    - Test that a plan with an empty rollback file is reported in failures
    - Test that a plan with hook script exit code != 0 is reported in failures
    - Test successful validation of all plans returns correct `validated_paths` list
    - _Requirements: 5.1, 5.2, 5.3, 5.5_

- [ ] 7. Implement data integrity features
  - [ ] 7.1 Implement findings store schema versioning
    - Add `SCHEMA_VERSION = "1.0.0"` constant
    - Implement `_write_findings_store()`: include `schema_version` field in top-level JSON
    - Implement `_validate_schema_version()`: reject missing field, reject major mismatch, warn on higher minor
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ] 7.2 Implement reasoning log append mode with separator
    - Modify reasoning logger to open log in append mode (`mode="a"`)
    - Implement `start_run()`: write JSONL separator entry with `event_type="run_separator"`, ISO 8601 UTC `timestamp`, and `message` field
    - Create file if it does not exist; handle `OSError` gracefully
    - _Requirements: 11.1, 11.2, 11.4_

  - [ ] 7.3 Write property test for schema version validation
    - **Property 10: Schema Version Validation**
    - **Property 15: Findings Store Schema Version Presence**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

  - [ ] 7.4 Write property test for reasoning log append preservation
    - **Property 13: Reasoning Log Append Preservation**
    - **Validates: Requirements 11.1, 11.2**

  - [ ] 7.5 Write unit tests for schema version WARNING (`tests/test_schema_version.py`)
    - Test that a higher minor version (e.g., `"1.5.0"` when expected is `"1.0.0"`) logs a WARNING and passes validation
    - Test that a major version mismatch (e.g., `"2.0.0"` when expected is `"1.0.0"`) is rejected with descriptive error
    - Test that a missing `schema_version` field is rejected with "schema_version field is missing"
    - Test that an invalid format string (e.g., `"abc"`) is rejected
    - _Requirements: 7.2, 7.3, 7.4, 7.5_

  - [ ] 7.6 Write unit tests for reasoning log rotation (`tests/test_reasoning_log.py`)
    - Test log rotation triggers at 10MB threshold (mock file size)
    - Test that rotation renames current file with numeric suffix
    - Test that maximum 5 rotated files are retained (oldest deleted)
    - Test that a new run appends separator without destroying existing content
    - _Requirements: 11.1, 11.2, 11.3_

- [ ] 8. Implement savings tracker broad exception handling
  - [ ] 8.1 Wrap `savings_tracker.record_run()` with broad exception handling
    - Replace narrow `except (FileNotFoundError, OSError)` with `except Exception`
    - Log WARNING with exception type and message on catch
    - Return `ApprovalResult(success=True)` regardless of exception
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ] 8.2 Write property test for savings tracker exception swallowing
    - **Property 5: Savings Tracker Exception Swallowing**
    - **Validates: Requirements 8.1, 8.2, 8.3**

- [ ] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Implement UI–Orchestrator contract alignment
  - [ ] 10.1 Refactor `app.py` audit delegation to use public Orchestrator API only
    - "Run Audit" button must call `Orchestrator.execute_audit(status_callback=...)` exclusively
    - Remove any direct calls to private methods/attributes (prefixed with `_`)
    - Render findings, plans, and blocked plans from `AuditResult` fields only
    - Display `AuditResult.error` on failure without retrying
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 10.2 Write unit tests for UI delegation (`tests/test_ui_delegation.py`)
    - Test that "Run Audit" flow calls only public Orchestrator methods (inspect call args, no `_` prefixed calls)
    - Test that `AuditResult.error` is displayed on failure
    - Test that successful result renders findings from `AuditResult` fields only
    - _Requirements: 3.1, 3.3, 3.4_

  - [ ] 10.3 Implement NL audit delegation with feature detection
    - Check `hasattr(orchestrator, 'execute_natural_language_audit')` before rendering NL audit UI
    - Display informational message when feature is unavailable
    - Display error message on exception; preserve existing audit state
    - Call method with trimmed non-empty query when available
    - _Requirements: 10.1, 10.2, 10.3_

  - [ ] 10.4 Write unit tests for NL audit feature detection (`tests/test_nl_audit.py`)
    - Test that when `execute_natural_language_audit` is missing (`hasattr` returns False), UI shows "not yet available" message
    - Test that when method raises an exception, UI displays error and preserves prior audit state
    - Test that when method is available and query is non-empty, it is called with the trimmed query
    - Test that empty/whitespace-only query does not invoke the method
    - _Requirements: 10.1, 10.2, 10.3_

  - [ ] 10.5 Implement explicit Phase B/C agent imports
    - Replace any dynamic import patterns with individual `try/except ImportError` blocks per agent
    - Assign `Optional[type] = None` on `ImportError` for each agent: QueryInterpreter, RemediationExplainer, PolicySuggester, AnomalyDetector, DriftDetector, MultiAccountOrchestrator, JanitorScheduler
    - Ensure type annotations are visible to mypy/pyright
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

  - [ ] 10.6 Write unit tests for agent ImportError handling (`tests/test_agent_imports.py`)
    - Test that a missing agent module results in the name being `None` (mock `ImportError`)
    - Test that all 7 Phase B/C agents are imported individually (not via registry loop)
    - Test that type annotations are `Optional[type]` for fallback values
    - _Requirements: 13.1, 13.2, 13.3_

  - [ ] 10.7 Update `app.py` to import paths from `core/paths.py`
    - Replace any hardcoded path strings in `app.py` with imports from `core/paths.py`
    - Handle missing artifact files gracefully with "no data available" message
    - _Requirements: 4.2, 4.6_

  - [ ] 10.8 Update Orchestrator to use `core/paths.py` and call `ensure_output_dirs()`
    - Replace hardcoded path strings in `orchestrator.py` with imports from `core/paths.py`
    - Call `ensure_output_dirs()` during Orchestrator `__init__`, halting with descriptive error on failure
    - _Requirements: 4.1, 4.3, 4.4, 4.5_

- [ ] 11. Wire structured error telemetry into Orchestrator
  - [ ] 11.1 Integrate `core/error_telemetry.py` into Orchestrator error handling
    - Import `build_error_record` and `write_error_record` from `core/error_telemetry`
    - Implement `_classify_error()` method for error categorization: `context="tf_validate"/"tf_apply"/"tf_plan"` → `"terraform_failure"`, `isinstance(exc, (OSError, IOError, PermissionError))` → `"io_failure"`, `context="schema_check"/"gate_check"/"hook_validation"/"resource_id_check"` → `"validation_failure"`, default → `"agent_failure"`
    - Wrap agent execution with try/except that builds and writes structured error records
    - Write JSONL errors to audit log path
    - _Requirements: 12.1, 12.2, 12.3_

  - [ ] 11.2 Write unit tests for `_classify_error()` (`tests/test_error_classification.py`)
    - Test `context="tf_validate"` → returns `"terraform_failure"`
    - Test `isinstance(exc, OSError)` → returns `"io_failure"`
    - Test `context="schema_check"` → returns `"validation_failure"`
    - Test default fallback (unknown context + non-IO exception) → returns `"agent_failure"`
    - Test `context="tf_apply"` → returns `"terraform_failure"`
    - Test `context="hook_validation"` → returns `"validation_failure"`
    - _Requirements: 12.3_

  - [ ] 11.3 Surface structured error fields in Streamlit UI
    - Display `error_category`, `agent_name`, and `message` from structured error records in UI error displays
    - Replace raw exception string displays with structured field rendering
    - _Requirements: 12.4_

- [ ] 12. Update SPEC_COMPLIANCE.md
  - [ ] 12.1 Update SPEC_COMPLIANCE.md to reflect NL Audit feature status
    - Set NL Audit status to "Partial" (UI elements exist with feature detection, but backend method is not yet implemented)
    - _Requirements: 10.4_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (15 properties across 11 test modules)
- Unit tests validate specific scenarios, edge cases, and integration points
- Requirement 14 (Session-Isolated File Paths) is DEFERRED and excluded from this plan
- All tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- `hooks/pre-remediation.sh` already exists in the repo — no task needed to create it
- Directory creation (`ensure_output_dirs()`) lives in `core/paths.py` alongside the `REQUIRED_DIRS` constant it operates on; the Orchestrator calls it at `__init__` time (task 10.8)
- **WARNING**: Do not manually invoke the Orchestrator against real `output/` paths until task 10.8 lands — `ensure_output_dirs()` is implemented in wave 0 (task 1.1) but not wired into `Orchestrator.__init__` until wave 6 (task 10.8). Between waves 4–6, code that writes to `output/` subdirectories exists but the directories are not auto-created at startup. Automated tests use `tmp_path`/mocked filesystems and are unaffected.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "1.5", "1.6", "2.1", "2.4"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.5", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5"] },
    { "id": 4, "tasks": ["5.1", "6.1", "7.1", "7.2", "8.1"] },
    { "id": 5, "tasks": ["5.2", "5.3", "6.2", "6.3", "7.3", "7.4", "7.5", "7.6", "8.2"] },
    { "id": 6, "tasks": ["10.5", "10.8"] },
    { "id": 7, "tasks": ["10.1", "10.3", "10.7", "11.1"] },
    { "id": 8, "tasks": ["10.2", "10.4", "10.6", "11.2", "11.3", "12.1"] }
  ]
}
```
