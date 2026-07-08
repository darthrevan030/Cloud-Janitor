# Requirements Document

## Introduction

This specification covers BUG-3 (backend reachability preflight) and TECH-1 (configurable subprocess/LLM timeouts) from the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`). It is one of six independently-shippable specs split out of the original bundled `phase3-ops-hardening` spec, per a principal-engineer design review (`.kiro/2026-07-08-phase-plans-audit.md`).

BUG-3 and TECH-1 are bundled together in this spec — not because they are coupled, but because both are small, independent ops-hygiene fixes with no dependency on each other, on any other phase, or on the other four phase3 splits (secret scanning, run-scoped artifacts, plan preview, scheduled alerting, audit query). Either half can be implemented and merged without the other landing first.

**This spec has zero dependencies on any other phase or spec.**

Two corrections were applied during the design review before this split was made:

- The original design's health-check reference code (`_check_sts()`) did not actually apply the 5-second timeout its own requirement mandated — `aws_provider._make_client()` has no timeout/`Config` parameter. This is fixed in Requirement 1 below and in `design.md`.
- TECH-1's originally proposed `JANITOR_HOOK_TOTAL_TIMEOUT` variable targeted `_run_pre_remediation_hook_full()`, a method never called by `execute_audit()` in production — only exercised by its own unit tests (`execute_audit()` calls the differently-named `_run_pre_remediation_hook()`). That timeout has been dropped from this spec's scope; see the note under Requirement 2.

Full technical design is in `design.md` in this folder.

## Glossary

- **Orchestrator**: The central coordination module (`src/cloud_janitor/orchestrator/orchestrator.py`) that sequences agent execution, manages approval gates, and invokes Terraform operations.
- **Health_Probe**: A cheap reachability check against the active backend (LocalStack's health endpoint in Sandbox_Mode, AWS STS `GetCallerIdentity` in Real_AWS_Mode), run before an audit begins.
- **Sandbox_Mode**: Any runtime condition that is not Real_AWS_Mode (fixture backend, or `aws` backend pointed at LocalStack via `AWS_ENDPOINT_URL`). Definition reused from `phase1-trust-hardening/requirements.md`.
- **Real_AWS_Mode**: The runtime condition where `JANITOR_BACKEND=aws` and `AWS_ENDPOINT_URL` is unset. Definition reused from `phase1-trust-hardening/requirements.md`.
- **TF_CMD**: An environment variable specifying the Terraform binary path used by the Orchestrator (defaults to `tflocal`). Definition reused from `audit-remediation/requirements.md`.

## Requirements

### Requirement 1: Backend Reachability Preflight Before Audit Execution

**User Story:** As an operator, I want Cloud Janitor to verify the active backend is reachable before running an audit, so that a misconfigured or unreachable environment fails fast with a clear message instead of surfacing as a confusing deep failure mid-scan.

#### Acceptance Criteria

1. WHEN `Orchestrator.execute_audit()` is called, THE Orchestrator SHALL perform a Health_Probe against the active backend before invoking `self._finops.scan()` or any other agent method.
2. WHILE the runtime is in Sandbox_Mode, THE Health_Probe SHALL issue an HTTP GET to `{AWS_ENDPOINT_URL, defaulting to http://localhost:4566}/_localstack/health` with a request timeout of 5 seconds, and SHALL consider the backend reachable only if the response is HTTP 200 and its JSON body's `"ec2"` service key is exactly `"available"` — mirroring the existing `Makefile:9` LocalStack health-check pattern's positive-match semantics exactly (the Makefile greps for `"ec2": "available"`, not for the absence of `"unavailable"`; LocalStack's health payload has intermediate states such as `"starting"` that a negative-match check would incorrectly treat as reachable).
3. WHILE the runtime is in Real_AWS_Mode, THE Health_Probe SHALL call AWS STS `GetCallerIdentity` via a client constructed with an explicit 5-second connect timeout and an explicit 5-second read timeout (not boto3's much larger, retry-multiplied default), and SHALL consider the backend reachable only if the call returns successfully.
4. IF the Health_Probe determines the backend is not reachable AND the environment variable `JANITOR_SKIP_HEALTH_CHECK` is not set to `"1"`, THEN THE Orchestrator SHALL NOT invoke any agent scan method, SHALL write an audit entry with action `"health_check_failed"`, and SHALL return an `AuditResult` with `success=False` and an error message identifying the unreachable backend and the probe failure detail.
5. IF the Health_Probe determines the backend is not reachable AND `JANITOR_SKIP_HEALTH_CHECK=1`, THEN THE Orchestrator SHALL log a WARNING identifying that the check was overridden and SHALL proceed with `execute_audit()` as if the probe had succeeded.
6. IF the Health_Probe determines the backend is reachable, THEN THE Orchestrator SHALL proceed with `execute_audit()` with no change to existing pipeline behavior.
7. THE Health_Probe's result SHALL distinguish "backend unreachable" (LocalStack not started, network failure, DNS failure) from "credentials invalid" (STS call reached AWS but was rejected — e.g. `AccessDenied`, expired/missing credentials) as two distinct failure classifications, so that the Orchestrator's structured error record and the Streamlit UI can render each differently rather than collapsing both into one generic I/O-failure category. See Requirement 1, criterion 4 of `design.md`'s `HealthStatus` shape.
8. THE Streamlit_UI SHALL render the Health_Probe's result as a readiness indicator (reachable / unreachable-backend / invalid-credentials / checking) immediately above the "Run Audit" control, calling the same `core.health.check_backend_health()` function the Orchestrator uses rather than a separate implementation, so the UI indicator and the actual gate can never disagree.

### Requirement 2: Configurable Subprocess and LLM Timeouts

**User Story:** As an operator running Cloud Janitor against real AWS resources, I want Terraform and LLM subprocess/HTTP timeouts to be configurable, so that a large real-world plan is not killed mid-apply by a timeout sized for LocalStack's near-instant fixture backend.

**Scope note:** the codebase also contains a hardcoded 60-second total-validation timeout inside `Orchestrator._run_pre_remediation_hook_full()`. That method is never called by `execute_audit()` in production — `execute_audit()` calls the differently-named `_run_pre_remediation_hook()`, and `_run_pre_remediation_hook_full()` is exercised only by its own unit tests today. Making that timeout configurable would add a public environment variable with no effect on any real execution path. This requirement therefore excludes it from scope; wiring `_run_pre_remediation_hook_full()` into the real pipeline (if ever desired) is a separate, larger change than "make timeouts configurable" and is not part of this spec.

#### Acceptance Criteria

1. THE Orchestrator SHALL read the following timeout values from environment variables at the point of use, each defaulting to its current hardcoded literal: `JANITOR_TF_INIT_TIMEOUT` (default 120, currently `orchestrator.py:924,1551`), `JANITOR_TF_APPLY_TIMEOUT` (default 120, currently `orchestrator.py:945,1574`), `JANITOR_TF_VALIDATE_TIMEOUT` (default 180, currently `orchestrator.py:1123`, inside the real `_run_pre_remediation_hook()` call path), `JANITOR_HOOK_TIMEOUT` (default 30, currently `orchestrator.py:1168`, inside `_run_post_remediation_hook()`, which is called from `approve()`'s and `rollback()`'s success paths).
2. THE LLM client module (`core/llm_client.py`) SHALL read `JANITOR_LLM_TIMEOUT` (default 30) in place of the hardcoded `_TIMEOUT = 30` constant at `core/llm_client.py:39`.
3. IF an environment variable listed in criterion 1 or 2 is set to a value that cannot be parsed as a positive integer, THEN THE reading component SHALL log a WARNING identifying the invalid value and the variable name, and SHALL fall back to that variable's default.
4. IF an environment variable listed in criterion 1 is set to a value exceeding a ceiling of 1800 seconds (30 minutes), THEN THE reading component SHALL clamp the effective timeout to 1800 seconds and log a WARNING identifying the clamp, the requested value, and the variable name.
5. WHEN a `terraform init`, `terraform apply`, `terraform validate` (via `_run_pre_remediation_hook()`), or post-remediation hook subprocess call exceeds its configured timeout (raises `subprocess.TimeoutExpired`), THE Orchestrator SHALL catch the exception, log a WARNING identifying the operation name, the configured timeout value, and the resource_id, write a structured error record (`error_category="terraform_failure"` for Terraform calls, `"validation_failure"` for hook calls), and return a failure result — matching the existing non-zero-exit-code failure path rather than propagating an unhandled `TimeoutExpired`.
6. THE Orchestrator's `approve()` and `_handle_confirm_rollback()` methods SHALL read `JANITOR_TF_INIT_TIMEOUT`/`JANITOR_TF_APPLY_TIMEOUT` from the same single configuration source, so the approval path and the rollback path cannot silently diverge in configured timeout value.
