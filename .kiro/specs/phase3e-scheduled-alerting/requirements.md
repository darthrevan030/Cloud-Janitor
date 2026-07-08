# Requirements Document

## Introduction

This specification covers FEAT-2 (scheduled-scan run history and severity-based alerting) from the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`). It is one of six independently-shippable specs split out of the original bundled `phase3-ops-hardening` spec, per a principal-engineer design review (`.kiro/2026-07-08-phase-plans-audit.md`).

**This spec has no dependency on any other phase or spec.** It touches only `scheduler.py` and two new supporting modules (`core/scan_diff.py`, `core/notifiers/`). It does not depend on phase1-trust-hardening, phase2-persistent-state, or any of the other four phase3 splits (secret scanning, preflight/timeouts, run-scoped artifacts, plan preview, audit query) — it is a standalone addition to the standalone `JanitorScheduler`.

A CRITICAL defect was found in the original bundled design's scheduler wiring and is corrected here: the original design's history/notification snippet referenced `result` (the return value of `orchestrator.execute_audit()`) unconditionally, but `scheduler.py`'s actual `_run_scan()` only binds `result` inside its `try:` block — deliberately, so that the `except`/`finally` path (which exists specifically to handle `execute_audit()` raising) never touches an unbound name. Following the original design as written would crash with `UnboundLocalError` on exactly the failure path the requirement ("history is appended whether the pipeline succeeded or failed") exists to cover. This is fixed in Requirement 1 below and in `design.md`.

Full technical design is in `design.md` in this folder.

## Glossary

- **Scheduler**: `JanitorScheduler` (`scheduler.py`), the APScheduler-based cron-triggered scan runner.
- **Notifier**: A pluggable interface for sending an alert about new/escalated findings; this spec provides a Slack-webhook implementation.

## Requirements

### Requirement 1: Scheduled-Scan Run History and Severity-Based Alerting

**User Story:** As an operator relying on scheduled scans, I want each scheduled run's results persisted and to be notified when a scan finds new or worsened HIGH/CRITICAL findings, so that a critical exposure found overnight does not sit silently in a rotating log file until someone happens to read it.

#### Acceptance Criteria

1. WHEN `JanitorScheduler._run_scan()` completes, whether the audit pipeline succeeded, failed with `AuditResult.success=False`, or `execute_audit()` (or `Orchestrator(...)` construction) itself raised an exception, THE Scheduler SHALL append one JSON record to `output/logs/scheduler_history.jsonl` containing at minimum: `scan_id`, `timestamp_start`, `timestamp_end`, `status`, `total_findings`, `total_waste`, and `findings_snapshot` (a mapping of each current finding's `resource_id` to its `severity`, empty when no findings are available because the pipeline raised before producing any).
2. THE Scheduler SHALL retain at most the most recent 500 records in `scheduler_history.jsonl`, pruning the oldest beyond that count after each append.
3. THE codebase SHALL provide a `diff_high_severity_findings(previous_snapshot: dict[str, str], current_findings: list[dict]) -> list[dict]` function that returns every entry in `current_findings` whose `severity` is `HIGH` or `CRITICAL` and whose `resource_id` either does not appear as a key in `previous_snapshot`, or appears with a strictly lower severity than its current value.
4. THE codebase SHALL provide a `Notifier` abstract interface with a `notify(summary: str, findings: list[dict]) -> bool` method, and a `SlackWebhookNotifier` implementation that POSTs a JSON payload to the URL configured in `JANITOR_SLACK_WEBHOOK_URL` when that environment variable is set, using a short (10-second) request timeout and no internal retry loop (a single failed attempt is reported to the Scheduler as a failure, not retried in-process).
5. WHEN `diff_high_severity_findings()`, called with the previous run's `findings_snapshot` and the current run's findings, returns a non-empty list, THE Scheduler SHALL invoke every configured, non-muted (see criterion 9) Notifier with a summary message and that list of new/escalated findings.
6. WHEN `diff_high_severity_findings()` returns an empty list, THE Scheduler SHALL NOT invoke any Notifier, even if the current run has HIGH/CRITICAL findings, as long as none of them are new or escalated relative to the previous run.
7. IF a Notifier's `notify()` call raises an exception or returns `False`, THEN THE Scheduler SHALL log a WARNING identifying the Notifier and the failure, and SHALL NOT treat this as a scan failure — the `status` field written per criterion 1 SHALL reflect only the audit pipeline's own success/failure, independent of notification outcome.
8. IF `JANITOR_SLACK_WEBHOOK_URL` is not set, THEN THE Scheduler SHALL configure zero Notifiers and SHALL continue to persist run history per criterion 1 without attempting any notification call.
9. **Sustained-outage protection:** THE Scheduler SHALL track consecutive notification failures per Notifier. WHEN a given Notifier's `notify()` call fails (raises or returns `False`) 3 times consecutively, THE Scheduler SHALL mute that Notifier — skipping further `notify()` calls to it and logging a WARNING identifying the mute — until either a cooldown period of 1 hour has elapsed since the mute began, or the Scheduler process restarts. WHEN the cooldown period has elapsed, THE Scheduler SHALL attempt one `notify()` call to the muted Notifier on the next occasion `diff_high_severity_findings()` returns a non-empty list; a successful call un-mutes the Notifier and resets its consecutive-failure count to zero, while a failed call restarts the cooldown period. This bounds a sustained Slack outage to at most one blocking attempt per hour per Notifier, rather than one blocking attempt (up to the Notifier's own timeout) on every single qualifying scheduled run.
