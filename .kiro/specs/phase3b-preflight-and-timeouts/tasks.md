# Implementation Plan: Backend Preflight and Configurable Timeouts (BUG-3 + TECH-1)

## Overview

This plan implements 2 requirements derived from 2 backlog issues (BUG-3, TECH-1), bundled together because both are small, independent ops-hygiene fixes with no coupling to each other or to any other spec. Work flows from the foundational `core/health.py` and `core/timeouts.py` modules through Orchestrator wiring for each.

## Tasks

- [x] 1. Create foundational modules
  - [x] 1.1 Add an optional `config` parameter to `_make_client()` in `mcp_server/backends/aws_provider.py`
    - Backward compatible: defaults to `None`, every existing call site is unaffected
    - _Requirements: 1.3_

  - [x] 1.2 Create `core/health.py`
    - Implement `HealthStatus` dataclass (`reachable`, `environment`, `mode`, `detail`, `endpoint`), `check_backend_health()`, `_check_localstack()` (GET `{AWS_ENDPOINT_URL}/_localstack/health`, 5s timeout, positive-match check: `services.ec2 == "available"`), `_check_sts()` (constructs an explicit `botocore.config.Config(connect_timeout=5, read_timeout=5)` and passes it to `_make_client()`; classifies `ClientError`/`NoCredentialsError` as `mode="invalid_credentials"` and connection-level failures as `mode="unreachable"`)
    - _Requirements: 1.1, 1.2, 1.3, 1.7_

  - [x] 1.3 Write unit tests for `core/health.py` (`tests/test_health.py`)
    - Mocked `urllib.request.urlopen` returning HTTP 200 with `ec2: "available"` → reachable
    - Mocked `urlopen` returning HTTP 200 with `ec2: "unavailable"` → not reachable
    - Mocked `urlopen` returning HTTP 200 with `ec2: "starting"` → not reachable (regression test for the negative-match bug caught in design review)
    - Mocked `urlopen` raising `URLError`/timeout → not reachable, no exception propagates
    - Mocked STS `get_caller_identity` success in real-AWS mode → `mode="healthy"`
    - Mocked STS raising `ClientError`/`NoCredentialsError` → `mode="invalid_credentials"`
    - Mocked STS raising `EndpointConnectionError`/`OSError`/`TimeoutError` → `mode="unreachable"`
    - Assert `_check_sts()` passes a `Config` with `connect_timeout=5, read_timeout=5` to `_make_client()` (mock and inspect the `config` kwarg)
    - _Requirements: 1.2, 1.3, 1.7_

  - [x] 1.4 Write property tests for health preflight
    - **Property 1: Health Preflight Blocks By Default**
    - **Property 2: Health Preflight Override Invariant**
    - **Property 3: LocalStack Positive-Match Partition**
    - **Property 4: Health Failure Classification Partition**
    - **Validates: Requirements 1.2, 1.4, 1.5, 1.7**

  - [x] 1.5 Create `core/timeouts.py`
    - Implement `get_timeout(name)` with the 5-entry `_DEFAULTS` table (`JANITOR_TF_INIT_TIMEOUT`, `JANITOR_TF_APPLY_TIMEOUT`, `JANITOR_TF_VALIDATE_TIMEOUT`, `JANITOR_HOOK_TIMEOUT`, `JANITOR_LLM_TIMEOUT` — deliberately excludes a timeout for `_run_pre_remediation_hook_full()`, which is never called by `execute_audit()` in production; see design.md), invalid-value fallback with WARNING, 1800s ceiling clamp with WARNING (LLM timeout exempt from ceiling)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 1.6 Write property test for timeout validation
    - **Property 5: Timeout Validation Partition**
    - **Validates: Requirements 2.3, 2.4**

  - [x] 1.7 Write unit tests for `core/timeouts.py` (`tests/test_timeouts.py`)
    - One test per env var name confirming its correct default
    - Non-numeric, zero, negative, and above-ceiling values for each Terraform/hook var
    - `JANITOR_LLM_TIMEOUT` above 1800 is NOT clamped (only Terraform/hook vars have the ceiling)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Wire backend reachability preflight into the Orchestrator
  - [x] 3.1 Call `check_backend_health()` at the top of `execute_audit()`
    - Block (return `AuditResult(success=False, error_category="backend_unreachable"` or `"credentials_invalid"` depending on `health.mode`, ...), log `"health_check_failed"` audit entry) unless `JANITOR_SKIP_HEALTH_CHECK=1`, in which case log a WARNING and proceed
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 1.7_

  - [x] 3.2 Write unit tests for Orchestrator preflight wiring (`tests/test_orchestrator_health_preflight.py`)
    - Unreachable backend + no override → zero calls to `self._finops.scan()` (mock and assert not called), `AuditResult.success == False`, `error_category` reflects the health `mode`
    - Unreachable backend + `JANITOR_SKIP_HEALTH_CHECK=1` → `self._finops.scan()` is called
    - Reachable backend → pipeline proceeds unchanged, no behavior regression vs. current tests
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 1.7_

  - [x] 3.3 Add the readiness indicator to the Streamlit UI
    - Render `check_backend_health()`'s result above the "Run Audit" button using the identical function the Orchestrator calls; render four distinct states (ready / unreachable / invalid-credentials / checking)
    - _Requirements: 1.8_

- [x] 4. Implement configurable subprocess/LLM timeouts
  - [x] 4.1 Replace hardcoded `timeout=` literals in `orchestrator.py` with `get_timeout(...)` calls
    - Lines 924, 1551 → `JANITOR_TF_INIT_TIMEOUT`; lines 945, 1574 → `JANITOR_TF_APPLY_TIMEOUT`; line 1123 (inside `_run_pre_remediation_hook()`) → `JANITOR_TF_VALIDATE_TIMEOUT`; line 1168 (inside `_run_post_remediation_hook()`) → `JANITOR_HOOK_TIMEOUT`
    - Do NOT modify `_run_pre_remediation_hook_full()`'s internal 60s timeout (line 1189) — out of scope, see design.md
    - _Requirements: 2.1, 2.6_

  - [x] 4.2 Replace `core/llm_client.py`'s hardcoded `_TIMEOUT = 30` with `get_timeout("JANITOR_LLM_TIMEOUT")` read at `get_client()` call time
    - _Requirements: 2.2_

  - [x] 4.3 Add `subprocess.TimeoutExpired` handling alongside each existing non-zero-exit-code check in `approve()`, `_handle_confirm_rollback()`, `_run_pre_remediation_hook()`, and `_run_post_remediation_hook()`
    - Log WARNING with operation name, configured timeout, resource_id; write a structured error record (`terraform_failure` or `validation_failure`); return the same failure result shape as the non-zero-exit path
    - _Requirements: 2.5_

  - [x] 4.4 Write unit tests for timeout-fired handling (`tests/test_timeout_handling.py`)
    - Mocked `subprocess.run` raising `TimeoutExpired` for init, apply, validate (`_run_pre_remediation_hook`), and post-remediation hook call sites each return a failure result without propagating the exception
    - _Requirements: 2.5, 2.6_

- [x] 5. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- This spec has no dependency on phase1-trust-hardening, phase2-persistent-state, or any of the other five phase3 splits — BUG-3 and TECH-1 can each be implemented independently of the other, though bundled here for shipping convenience since both are small

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.5"] },
    { "id": 1, "tasks": ["1.2", "1.6", "1.7"] },
    { "id": 2, "tasks": ["1.3", "1.4"] },
    { "id": 3, "tasks": ["3.1", "4.1", "4.2"] },
    { "id": 4, "tasks": ["3.2", "3.3", "4.3"] },
    { "id": 5, "tasks": ["4.4"] }
  ]
}
```
