# Implementation Plan: Phase 1 Trust Hardening

## Overview

This plan implements 7 requirements derived from 5 backlog issues (SEC-3, BUG-1, SEC-1, SEC-2, SEC-4). Work flows from foundational modules (`core/identity.py`, `core/redaction.py`) through Orchestrator wiring, ledger correctness, LLM trust-boundary hardening, and the apply-time policy gate.

## Tasks

- [ ] 1. Create foundational modules
  - [x] 1.1 Create `core/identity.py`
    - Implement `ActorResolution` dataclass, `IdentityResolutionError` exception, `resolve_actor(fallback: str) -> ActorResolution`
    - Extend `aws_provider._make_client(service, region, config=None)` with an optional `config: botocore.config.Config | None` keyword (default `None`, backward compatible) and reuse it for the STS client so LocalStack (`AWS_ENDPOINT_URL`) works unmodified
    - Pass a short-timeout `Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1})` on the STS call, matching `aws_provider.py`'s own `_dep_client` precedent (aws_provider.py:556) for non-critical-path calls
    - LocalStack detection: match `aws_provider.py`'s own substring check (`"localhost" in endpoint or "127.0.0.1" in endpoint`, aws_provider.py:104-105) — NOT `bool(os.environ.get("AWS_ENDPOINT_URL"))`, which would also misclassify FIPS/VPC/PrivateLink endpoints as LocalStack
    - Real-AWS detection: `JANITOR_BACKEND == "aws" and not is_localstack` (per the corrected `is_localstack` check above)
    - Short-circuit: when not in real-AWS mode, return the fallback/`JANITOR_ACTOR` actor immediately WITHOUT calling STS at all — do not rely on the exception path to eventually time out
    - LocalStack-account defense-in-depth check (`account == "000000000000"` while in real-AWS mode raises)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ] 1.2 Write property tests for identity resolution
    - **Property 1: `resolve_actor()` Real-AWS Raising Contract**
    - **Property 2: Sandbox Identity Fallback Invariant**
    - **Validates: Requirements 1.3, 1.4, 1.5**

  - [ ] 1.3 Write unit tests for `core/identity.py` (`tests/test_identity.py`)
    - Mocked STS success in real-AWS mode → verified ARN returned
    - Mocked STS failure (NoCredentialsError, ClientError, timeout) in real-AWS mode → `IdentityResolutionError` raised
    - Mocked STS returning account `000000000000` in real-AWS mode → `IdentityResolutionError` raised
    - Mocked STS failure in sandbox mode (fixture backend, or `aws` + `AWS_ENDPOINT_URL` set) → fallback actor returned, no exception
    - Sandbox mode (fixture backend, or `aws` + localhost/127.0.0.1 `AWS_ENDPOINT_URL`) → STS client is never constructed/called at all (mock and assert not called), not merely "exception caught"
    - `AWS_ENDPOINT_URL` set to a non-localhost custom endpoint (e.g. a FIPS or PrivateLink-shaped URL) with `JANITOR_BACKEND=aws` → treated as real-AWS mode, STS IS called (not misclassified as LocalStack)
    - `JANITOR_ACTOR` env var respected as fallback when set
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 1.4 Create `core/redaction.py`
    - Implement `redact(obj) -> tuple[Any, dict[str, str]]`: recursive walk of dict/list/str, placeholder substitution for ARNs (`arn:aws:...`), 12-digit account IDs, and known resource IDs; excludes `resource_type`, `region`, `tags` keys
    - Implement `rehydrate(text: str, mapping: dict[str, str]) -> str`
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ] 1.5 Write property tests for redaction
    - **Property 5: Redaction Round-Trip Fidelity**
    - **Property 6: Redaction Field Exclusion**
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [ ] 1.6 Write unit tests for `core/redaction.py` (`tests/test_redaction.py`)
    - Round-trip test: finding with resource_id, ARN, account_id → scrubbed text contains no original values → rehydrated response matches original
    - Field exclusion test: `resource_type`, `region`, `tags` unchanged after `redact()`
    - Nested structure test (dict containing list containing dict)
    - _Requirements: 4.1, 4.2, 4.3_

- [ ] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Wire fail-closed identity into the Orchestrator
  - [ ] 3.1 Add explicit-approver tracking and `_resolve_actor_or_block()` to `Orchestrator`
    - Change the constructor signature to `approver: str | None = None`; store `self._explicit_approver = approver` and track explicit-vs-default by `is not None`, NOT by comparing the value against the literal string `"system"` (that sentinel is ambiguous: a caller who explicitly passes `approver="system"` is indistinguishable from a caller who omitted it)
    - Implement `_resolve_actor_or_block(resource_id) -> tuple[str, bool] | None` returning `(actor, actor_verified)`, calling `resolve_actor()` unless `self._explicit_approver is not None`, logging `"identity_verification_failed"` and returning `None` on `IdentityResolutionError`
    - _Requirements: 1.1, 1.6_

  - [ ] 3.2 Wire `_resolve_actor_or_block()` into `approve()`, `rollback()`, `_handle_confirm_rollback()`
    - Each method calls it first, before any `init`/`plan`/`apply` subprocess invocation; returns its own failure result type immediately on `None`
    - Capture the resolved `(actor, actor_verified)` into LOCAL variables in each method — do NOT assign the result back onto `self.approver` or any other shared instance attribute. This must hold even when a single Orchestrator instance is invoked concurrently (Streamlit session threads, `multi_account_orchestrator.py`, `scheduler.py`), so one call's resolved actor can never leak into a different, concurrent call's audit stamp
    - Give `_log_action()` and `_run_post_remediation_hook()` an **optional, defaulted** `actor: str | None = None` parameter (plus `actor_verified: bool = False`) — NOT a required one. `orchestrator.py` has roughly 28 other `_log_action()` call sites (scan-start, plan-generation, hook-error logging, gate lockouts, terraform init/apply failure logging, etc.) with no resolved actor available; making the parameter required would break all of them. Each method's body SHALL fall back to `self.approver` (the unchanged historical default) whenever `actor is None`. Only the four existing stamp sites that currently read `self.approver` (the approval log entry, the post-remediation hook invocation — called from the dry-run remediation path, the real remediation path, and the rollback path — the rollback log entry, and `_log_action`'s own internal `AuditEntry.actor` assignment) pass an explicit, freshly-resolved `actor`
    - `_run_post_remediation_hook()`'s existing positional signature is `(resource_id, action, result)`, where `action` is the literal string `"remediate"`/`"rollback"` — NOT the actor. Append `actor`/`actor_verified` as new trailing parameters and call this method with **keyword arguments** at every updated call site (e.g. `self._run_post_remediation_hook(resource_id, "remediate", "success", actor=actor, actor_verified=actor_verified)`) so `actor` can never be mispositioned into the `action` slot
    - Add an `actor_verified: bool` field to the `AuditEntry` dataclass (and its `to_dict()`) so the audit trail can distinguish an STS-verified actor from a Sandbox_Mode fallback or explicit-approver bypass
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.7, 1.8_

  - [ ] 3.3 Write unit tests for Orchestrator identity wiring (`tests/test_orchestrator_identity.py`)
    - Real-AWS mode + STS failure → `approve()`/`rollback()` return `success=False`, assert zero `subprocess.run` calls (mock and assert not called)
    - Sandbox mode + STS failure → action proceeds with fallback actor stamped in audit log, `actor_verified=False`
    - Explicit `approver=` at construction → STS never called (mock and assert not called), value used verbatim
    - Concurrency-safety test: two sequential/interleaved calls on the same Orchestrator instance with different resolved actors (e.g. mock `resolve_actor` to return different values per call) each produce an audit entry stamped with their OWN actor — assert no cross-contamination via shared instance state
    - **Property 8: Orchestrator Real-AWS Identity Fail-Closed Invariant** (moved here from Wave 1's task 1.2 — this property depends on `_resolve_actor_or_block()` being wired into the Orchestrator, which doesn't exist until this task)
    - **Validates: Requirements 1.3, 1.4**
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8_

- [ ] 4. Implement savings ledger reversal
  - [ ] 4.1 Add `SavingsTracker.record_rollback()`
    - Track "already reversed" state keyed by the (`resource_id`, `run_id`) PAIR — not bare `resource_id` — by recording a `rolled_back_run_id` field on each rollback ledger entry
    - Find the earliest matching non-reversed `record_run()` entry containing `resource_id` whose (`resource_id`, `run_id`) pair is not already in the reversed set; no-op + return `False` if none found
    - Negate the matched entry's OWN already-stored `monthly_savings_added` value directly — do NOT recompute via `_compute_monthly_savings()`/the live `findings_store.json`, which is overwritten by every subsequent `execute_audit()` scan and may no longer contain `resource_id`
    - Append the negative entry with `type: "rollback"` and `rolled_back_run_id` set to the matched entry's `run_id`; recalculate `total_lifetime_savings`
    - _Requirements: 2.1, 2.2, 2.3_

  - [ ] 4.2 Write property test for savings reversal
    - **Property 3: Savings Reversal Idempotence Per (resource_id, run_id)**
    - **Validates: Requirements 2.2, 2.3**

  - [ ] 4.3 Wire `record_rollback()` into `_handle_confirm_rollback()`
    - Call on success path, wrapped in broad try/except matching the existing `record_run()` pattern; never alters `RollbackResult.success`
    - Scheduled in a later wave than task 3.2 (see the wave graph below) even though both edit `_handle_confirm_rollback()` — task 3.2 wires actor resolution into that method first, and this task's edit lands on top of that already-updated method body, avoiding an avoidable same-method merge/ordering hazard between the two tasks
    - _Requirements: 2.4_

  - [ ] 4.4 Write unit tests for savings reversal (`tests/test_savings_tracker.py`)
    - Rollback after a recorded run reverses the ledger total by the correct amount, using the run's own stored `monthly_savings_added` (assert this holds even when the resource has since been removed from/changed in the live findings store, to rule out any accidental recompute-from-findings-store regression)
    - Rollback with no prior `record_run()` for that resource is a no-op returning `False`
    - Double-rollback does not double-reverse
    - Same `resource_id` remediated in two separate runs (distinct `run_id`s) → reversing the first run's remediation returns `True` and leaves the second run's remediation still reversible; reversing the second afterward also returns `True`; a third call then returns `False`
    - Exception during `record_rollback()` (mocked corrupted ledger) does not propagate and does not flip `RollbackResult.success`
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [ ] 5. Implement explicit LLM privacy attestation
  - [ ] 5.1 Add `JANITOR_LLM_RETENTION_POLICY` check to `core/llm_client.py`
    - Read env var, default `"unknown"`
    - In `get_client()`, strict mode raises `RuntimeError` unless value is exactly `"none"`, before the existing free-router check
    - Implement `get_privacy_posture() -> dict`
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 5.2 Write property test for privacy attestation
    - **Property 4: Strict Mode Attestation Gate**
    - **Validates: Requirement 3.2**

  - [ ] 5.3 Write unit tests for privacy attestation (`tests/test_llm_client_privacy.py`)
    - Strict mode + `JANITOR_LLM_RETENTION_POLICY` unset → `RuntimeError`
    - Strict mode + `="unknown"` → `RuntimeError`
    - Strict mode + `="none"` + non-free model → client constructed successfully
    - Strict mode + `="none"` + free-tier model → existing `RuntimeError` still fires
    - `get_privacy_posture()` returns all 4 required keys with correct values
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 6. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Wire redaction and injection hardening into LLM-calling agents
  - [ ] 7.1 Wire `redact()`/`rehydrate()` and delimited prompt template into `explainer.py`
    - Wrap finding + HCL data through `redact()` before prompt construction; wrap in `<untrusted_finding_data>`/`<untrusted_hcl>` delimiters; `rehydrate()` the response
    - _Requirements: 4.4, 4.5, 5.1, 5.2_

  - [ ] 7.2 Wire `redact()`/`rehydrate()` and delimited prompt template into `anomaly_detector.py`
    - Same pattern applied to the up-to-30 raw resource dicts serialized into its prompt
    - _Requirements: 4.4, 4.5, 5.1, 5.2_

  - [ ] 7.3 Wire `redact()`/`rehydrate()` and delimited prompt template into `tagger.py`, `policy_suggester.py`, `incident_policy_generator.py`, `drift_detector.py`
    - Same pattern applied at each agent's prompt-construction site
    - _Requirements: 4.4, 4.5, 5.1, 5.2_

  - [ ] 7.4 Write unit tests for per-agent redaction + delimiting (one test file per agent, or a shared parametrized module)
    - For each of the 6 agents: assert the constructed prompt contains no raw resource ID/ARN/account ID and does contain the `<untrusted_...>` delimiter wrapper
    - Injection test: place a prompt-injection-style payload in a finding's tag value, assert it appears only inside the delimited block in the constructed prompt
    - Rehydration test: mocked LLM response containing a placeholder token is correctly rehydrated to the original value before being returned by the agent
    - Negative rehydration test: mocked LLM response that never echoes a placeholder token verbatim (e.g. paraphrases or drops it — common with small/cheap models) → `rehydrate()` no-ops for that token without raising, the rest of the response is returned unchanged, and (if a debug log call was added per the design's observability note) a debug log line is emitted for the unmatched placeholder
    - _Requirements: 4.4, 4.5, 5.1, 5.2_

- [ ] 8. Implement tfsec policy check in the pre-remediation hook
  - [ ] 8.1 Add tfsec step to `hooks/pre-remediation.sh`'s `validate_hcl()`
    - Run after existing validate/fmt check; fail on HIGH/CRITICAL when tfsec present
    - Fail-open with warning when absent and `JANITOR_REQUIRE_TFSEC` unset/`0`; fail-closed when `JANITOR_REQUIRE_TFSEC=1`
    - _Requirements: 6.1, 6.2, 6.3_

  - [ ] 8.2 Write hook tests for tfsec integration (`tests/test_pre_remediation_hook.py` or existing hook test harness)
    - Mocked/stubbed `tfsec` binary returning non-zero on a HIGH finding → hook blocks
    - No `tfsec` on PATH, `JANITOR_REQUIRE_TFSEC` unset → hook proceeds with warning
    - No `tfsec` on PATH, `JANITOR_REQUIRE_TFSEC=1` → hook blocks
    - _Requirements: 6.1, 6.2, 6.3_

- [ ] 9. Implement Terraform plan scope check
  - [ ] 9.1 Implement `_check_plan_scope()` in `orchestrator/orchestrator.py`
    - Filter `plan_json["resource_changes"]` to `mode == "managed"` and non-`no-op` actions
    - Add a `flow: Literal["remediate", "rollback"]` parameter and key `_SCOPE_ALLOWLIST` on `(resource_type, category, flow)`, NOT `(resource_type, category)` alone — the remediation and rollback templates for the same finding type generate structurally different HCL (`remediation_architect.py`'s `_remediation_*` vs `_rollback_*` methods)
    - Implement the full per-(resource_type, category, flow) allowlist table from the design, covering every combination that has a live template: `(ebs, waste, remediate)`, `(ebs, waste, rollback)` — `aws_ebs_volume.restore_*`, `(elasticache, waste, remediate)`, `(elasticache, waste, rollback)` — `aws_elasticache_cluster.restore_*`, `(ebs, security, remediate)` and `(ebs, security, rollback)` — both zero-change, `(elasticache, security, remediate)` and `(elasticache, security, rollback)` — both zero-change, `(security_group, security, remediate)` — exactly one `create`/`update` on `aws_security_group_rule.remediate_*` and reject `cidr_blocks` containing `0.0.0.0/0`, `(security_group, security, rollback)` — exactly one `create`/`update` on `aws_security_group_rule.restore_*` and explicitly PERMIT `cidr_blocks` containing `0.0.0.0/0` (this is the correct rollback behavior, not a gap), plus the zero-changes default for anything unmatched
    - The security-group rule MUST check the resource-change's address-prefix direction (`aws_security_group_rule.remediate_` for `flow="remediate"`, `aws_security_group_rule.restore_` for `flow="rollback"`) in addition to resource type, action set, and CIDR direction — without this, a plan whose one change carries the wrong-direction address name but otherwise "correct" type/action/CIDR would incorrectly pass
    - For the `(ebs, waste)` and `(elasticache, waste)` rules (both flows), the check MUST be exhaustive, not merely an allowlist: the set of matched address-prefixes across all counted resource_changes must exactly equal the rule's full required address-prefix set (e.g. both `aws_ebs_snapshot.pre_remediation_*` AND `null_resource.destroy_*` must both be present for `(ebs, waste, remediate)`) — a plan containing only the destroy/delete resource without its paired snapshot/backup resource must be rejected as missing a required resource, not passed because nothing in it is disallowed
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.11_

  - [ ] 9.2 Wire `terraform plan -json` + `_check_plan_scope()` into `approve()` and `_handle_confirm_rollback()`
    - Insert between `init` and `apply`; `approve()` calls with `flow="remediate"`, `_handle_confirm_rollback()` calls with `flow="rollback"`
    - On scope-check failure, log `"scope_check_failed"` and return a failure result without calling apply
    - _Requirements: 7.1, 7.9_

  - [ ] 9.3 Write property test for scope check
    - **Property 7: Scope Check Allowlist Partition (Both Flows)**
    - **Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.7, 7.8**

  - [ ] 9.4 Write unit tests for scope check (`tests/test_scope_check.py`)
    - One in-scope-plan test per (resource_type, category, flow) combination — both `"remediate"` and `"rollback"` where a rollback template exists — built from `remediation_architect.py`'s real templates: `(ebs, waste, remediate)`, `(ebs, waste, rollback)`, `(elasticache, waste, remediate)`, `(elasticache, waste, rollback)`, `(ebs, security, remediate)`, `(ebs, security, rollback)`, `(elasticache, security, remediate)`, `(elasticache, security, rollback)`, `(security_group, security, remediate)`, `(security_group, security, rollback)`
    - Out-of-scope address rejection (a plan touching a resource address unrelated to the approved resource), tested for at least one remediate and one rollback case
    - Security-group widen-to-`0.0.0.0/0` REJECTION in the `"remediate"` flow
    - Security-group widen-to-`0.0.0.0/0` ACCEPTANCE in the `"rollback"` flow (regression test for the defect where the remediation-direction rule was incorrectly applied to rollback plans, which would make every security-group rollback fail its own scope check)
    - EBS/ElastiCache waste rollback address-prefix rejection: a rollback-flow plan using the remediation-direction addresses (or vice versa) is rejected as out-of-scope
    - Security-group wrong-direction address rejection: a plan whose one `aws_security_group_rule` change has the wrong-direction address prefix (e.g. `remediate_*` presented during a `flow="rollback"` call, or `restore_*` during `flow="remediate"`) is rejected even though type, action set, and CIDR direction all otherwise look correct
    - EBS/ElastiCache waste exhaustiveness rejection: a plan containing only the destroy/delete resource (e.g. only `null_resource.destroy_*`) without its paired snapshot/backup resource is rejected as missing a required resource, for both `(ebs, waste, remediate)` and `(elasticache, waste, remediate)`
    - Data-source-only plan (e.g. `data.aws_vpc.current` read with no managed changes) does not count toward the change threshold
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.11_

  - [ ] 9.5 Document the local-exec limitation
    - Add the known-limitation note (Requirement 7.10) as a code comment on `_check_plan_scope()` and in this phase's design doc (already present in `design.md`), and explicitly state that the allowlist covers both the remediation and rollback flows for every (resource_type, category) combination that has a live template
    - _Requirements: 7.10_

- [ ] 10. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 11. Cross-reference follow-up (execute once this phase ships): update `docs/deployment.md`'s Phase-1 wording
  - `docs/deployment.md` (written by `phase4-aws-lockdown`'s DOC-1 requirement) describes this phase's SEC-1 (`JANITOR_LLM_RETENTION_POLICY`/privacy posture) and SEC-3 (fail-closed identity) as "designed, pending implementation" until this phase actually ships in code. Once tasks 1-10 above are complete and merged, update `docs/deployment.md`'s Identity & Auth and BYO-Endpoint & Privacy Posture sections to describe actual shipped behavior instead
  - This task exists because nothing else in either phase's plan revisits that wording once this phase merges — left untracked, the guide would go stale the day this phase ships, unflagged. Tracked as Follow-Up task F.2 in `.kiro/specs/phase4-aws-lockdown/tasks.md`; this entry is the matching cross-reference from this phase's side, so the dependency is visible from both directions
  - _Requirements: phase4-aws-lockdown Requirement 5.7_

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- Task 8 (tfsec) requires `tfsec` to be installable in the CI environment for full coverage of the fail-closed branch; the fail-open branch can be tested without it by ensuring it is absent from PATH in the test environment
- Task 4.3 is deliberately scheduled a wave later than task 3.2 even though both edit `_handle_confirm_rollback()`: task 3.2 wires actor resolution into that method (Wave 3), and task 4.3 wires `record_rollback()` into the same method on top of that already-updated body (Wave 4) — putting both in the same wave would be an avoidable same-method merge/ordering hazard
- Task 11 (the cross-reference follow-up to update `docs/deployment.md`'s Phase-1 wording once this phase ships) is intentionally excluded from the wave graph below — it is a tracked follow-up, not part of this phase's own implementation wave-schedule. It cannot run until tasks 1-10 are complete and merged, which is inherently outside the parallel-implementation wave graph's scope (matching the pattern used for excluded follow-up tasks in `.kiro/specs/phase4-aws-lockdown/tasks.md`)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.5", "1.6"] },
    { "id": 2, "tasks": ["3.1", "4.1", "5.1", "8.1"] },
    { "id": 3, "tasks": ["3.2", "4.2", "5.2", "9.1"] },
    { "id": 4, "tasks": ["3.3", "4.3", "4.4", "5.3", "7.1", "7.2", "7.3", "8.2", "9.2"] },
    { "id": 5, "tasks": ["7.4", "9.3", "9.4", "9.5"] }
  ]
}
```
