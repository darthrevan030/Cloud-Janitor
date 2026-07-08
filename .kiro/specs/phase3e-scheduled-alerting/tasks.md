# Implementation Plan: Scheduled-Scan Run History and Alerting (FEAT-2)

## Overview

This plan implements Requirement 1 (scheduled-scan run history and severity-based alerting), derived from backlog issue FEAT-2. This is a standalone, zero-dependency spec — it can be implemented independently of every other phase and every other phase3 split.

## Tasks

- [ ] 1. Create `core/scan_diff.py` with `diff_high_severity_findings()`
  - _Requirements: 1.3_

- [ ] 2. Write property test for severity escalation diff
  - **Property 2: Severity Escalation Diff Correctness**
  - **Validates: Requirements 1.3, 1.5, 1.6**

- [ ] 3. Write unit tests for `core/scan_diff.py` (`tests/test_scan_diff.py`)
  - Escalation diff scenarios (new HIGH, same HIGH twice, MEDIUM→HIGH, HIGH→CRITICAL, HIGH→HIGH no-op)
  - _Requirements: 1.3, 1.5, 1.6_

- [ ] 4. Create `core/notifiers/base.py` (`Notifier` ABC) and `core/notifiers/slack.py` (`SlackWebhookNotifier`, `build_notifiers_from_env()`)
  - `SlackWebhookNotifier.notify()` is a single attempt with a 10s timeout — no internal retry loop
  - _Requirements: 1.4, 1.8_

- [ ] 5. Write unit tests for notifiers (`tests/test_notifiers.py`)
  - `SlackWebhookNotifier.notify()` success (mocked `urlopen` returning 200), failure (non-2xx, `URLError`, timeout) all return `bool` without raising
  - `build_notifiers_from_env()` returns `[]` when `JANITOR_SLACK_WEBHOOK_URL` unset, one `SlackWebhookNotifier` when set
  - _Requirements: 1.4, 1.8_

- [ ] 6. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Wire history persistence into `JanitorScheduler._run_scan()` — safe against pipeline exceptions
  - [ ] 7.1 Pre-initialize `findings: list[dict] = []` outside the `try:` block, alongside the existing `total_findings`/`total_waste` defaults
    - This is the fix for the original bundled design's `UnboundLocalError` defect: the history/notification logic in the `finally` block must read from this safe local, never from `result` directly, since `result` is only bound inside the `try:` block and `Orchestrator(...)` construction or `execute_audit()` can raise before `result` is ever assigned
    - _Requirements: 1.1_

  - [ ] 7.2 Assign `findings = result.findings` (and derive `total_findings`/`total_waste` from it) only inside the `try:` block, immediately after `result = orchestrator.execute_audit()` succeeds
    - _Requirements: 1.1_

  - [ ] 7.3 In the `finally:` block, build `current_snapshot` from `findings` (never from `result`), append the history record, and prune beyond 500 records
    - _Requirements: 1.1, 1.2_

  - [ ] 7.4 Write unit tests proving the crash is fixed (`tests/test_scheduler_history.py`)
    - Mocked `Orchestrator.__init__` raising an exception → history record appended with `status="failed"`, `findings_snapshot={}`, no exception propagates out of `_run_scan()`
    - Mocked `execute_audit()` raising an exception (after `Orchestrator()` succeeds) → same safe behavior
    - History record written after both a successful and a `result.success=False` scan; `findings_snapshot` shape correct in each case
    - Pruning caps the file at 500 lines
    - _Requirements: 1.1, 1.2_

  - [ ] 7.5 Write property test for history-append safety
    - **Property 1: History Append Survives Pipeline Exceptions**
    - **Validates: Requirement 1.1**

- [ ] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Wire severity-based notification with circuit breaker into `JanitorScheduler`
  - [ ] 9.1 Add `_NotifierState` tracking (consecutive failures, `muted_until`) and `_notify_with_circuit_breaker()`
    - Mute a Notifier after `MUTE_AFTER_CONSECUTIVE_FAILURES = 3` consecutive failures; skip `notify()` calls while `muted_until` is in the future; attempt one call after the cooldown (`MUTE_COOLDOWN = 1 hour`) elapses; a successful call resets the failure count and un-mutes
    - _Requirements: 1.9_

  - [ ] 9.2 Call `diff_high_severity_findings()` in the `finally` block (only when `status == "success"`) and invoke `_notify_with_circuit_breaker()` for each configured Notifier when the diff is non-empty
    - _Requirements: 1.5, 1.6, 1.7_

  - [ ] 9.3 Write unit tests for circuit-breaker behavior (`tests/test_scheduler_notifier_circuit_breaker.py`)
    - 3 consecutive failures mute the notifier
    - A 4th qualifying scan within the cooldown window skips the call entirely (mocked `notify` not called)
    - After the cooldown elapses, exactly one attempt is made on the next qualifying scan
    - A successful attempt (whether the first ever, or the post-cooldown probe) resets the consecutive-failure count and un-mutes
    - A raising/`False`-returning notifier is logged as a WARNING and does not alter the history record's `status`
    - _Requirements: 1.7, 1.9_

  - [ ] 9.4 Write property test for the circuit breaker
    - **Property 3: Circuit Breaker Mute Threshold**
    - **Validates: Requirement 1.9**

- [ ] 10. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- This spec has no dependency on phase1-trust-hardening, phase2-persistent-state, or any of the other four phase3 splits (secret scanning, preflight/timeouts, run-scoped artifacts, plan preview, audit query)
- Task 7 (safe history append) is the CRITICAL fix from design review and must not be skipped or simplified — it is the entire point of this spec's "whether the pipeline succeeded or failed" requirement

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "4"] },
    { "id": 1, "tasks": ["2", "3", "5"] },
    { "id": 2, "tasks": ["7.1"] },
    { "id": 3, "tasks": ["7.2"] },
    { "id": 4, "tasks": ["7.3"] },
    { "id": 5, "tasks": ["7.4", "7.5", "9.1"] },
    { "id": 6, "tasks": ["9.2"] },
    { "id": 7, "tasks": ["9.3", "9.4"] }
  ]
}
```
