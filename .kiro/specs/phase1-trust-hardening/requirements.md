# Requirements Document

## Introduction

This specification covers Phase 1 of the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`) — the "close the trust gaps" phase: SEC-3, BUG-1, SEC-1, SEC-2, and SEC-4. These five issues share a common theme — the approval/rollback pipeline currently trusts inputs (approver identity, LLM endpoint policy, finding metadata, generated HCL) that it has no way to verify, undermining the tool's core promise as an auditable, safe remediation gate. Full technical design is in `design.md` in this folder; the approved brainstorming spec this was derived from is at `docs/superpowers/specs/2026-07-08-phase1-trust-hardening-design.md`.

## Glossary

- **Orchestrator**: The central coordination module (`src/cloud_janitor/orchestrator/orchestrator.py`) that sequences agent execution, manages approval gates, and invokes Terraform operations.
- **Actor**: The identity (IAM ARN, or a fallback string) attributed to an approval or rollback action in the audit trail.
- **Real_AWS_Mode**: The runtime condition where `JANITOR_BACKEND=aws` and `AWS_ENDPOINT_URL` is unset (i.e., not pointed at LocalStack).
- **Sandbox_Mode**: Any runtime condition that is not Real_AWS_Mode (fixture backend, or `aws` backend pointed at LocalStack via `AWS_ENDPOINT_URL`).
- **Savings_Ledger**: The JSON file (`savings_ledger.json`) managed by `SavingsTracker`, recording cumulative monthly savings from remediations.
- **Retention_Policy**: An operator-declared attestation (`JANITOR_LLM_RETENTION_POLICY`) about whether the configured LLM endpoint retains or trains on submitted data.
- **Finding**: A dict describing a flagged cloud resource, produced by FinOps Auditor or SecOps Guard, consumed by the Remediation Architect and the LLM-calling agents.
- **Redaction_Map**: A dict produced by `redact()` mapping placeholder tokens (e.g. `RESOURCE_1`) back to the original sensitive values they replaced.
- **Plan_JSON**: The parsed output of `terraform plan -json`, containing a `resource_changes` array describing each resource Terraform would create, update, or delete.
- **Scope_Check**: Validation that a Plan_JSON only touches the resource address(es) and action class(es) permitted for the finding being remediated.

## Requirements

### Requirement 1: Fail-Closed IAM Identity Verification for Approvals and Rollbacks

**User Story:** As a security engineer, I want every approval and rollback to be attributed to a real, IAM-verified identity when running against real AWS, and to hard-stop rather than silently default to a placeholder identity if that verification fails, so that the audit trail can always answer "who authorized this" for production changes.

#### Acceptance Criteria

1. WHEN `Orchestrator.approve()`, `Orchestrator.rollback()`, or `Orchestrator._handle_confirm_rollback()` is called AND the Orchestrator was constructed without an explicit `approver` argument, THE Orchestrator SHALL attempt to resolve the current Actor via AWS STS `GetCallerIdentity` before performing any Terraform `init`, `plan`, or `apply` subprocess call.
2. WHEN STS `GetCallerIdentity` succeeds, THE Orchestrator SHALL use the returned `Arn` as the Actor for that action's audit stamps.
3. IF the runtime is in Real_AWS_Mode AND STS `GetCallerIdentity` raises any exception (missing credentials, expired session, network failure, `AccessDenied`), THEN THE Orchestrator SHALL NOT execute any Terraform subprocess call, SHALL return a failure result (`ApprovalResult(success=False, ...)` or `RollbackResult(success=False, ...)`) whose message identifies that identity could not be confirmed, and SHALL write an audit entry with action `"identity_verification_failed"`.
4. IF the runtime is in Real_AWS_Mode AND STS `GetCallerIdentity` succeeds but returns Account `"000000000000"` (LocalStack's default account), THEN THE Orchestrator SHALL treat this identically to an STS failure per criterion 3 (refusing to proceed), since a LocalStack-shaped identity appearing while targeting real AWS indicates misconfiguration.
5. IF the runtime is in Sandbox_Mode AND STS `GetCallerIdentity` raises any exception, THEN THE Orchestrator SHALL fall back to the `JANITOR_ACTOR` environment variable if set, otherwise the Orchestrator's configured default (`"system"`), and SHALL proceed with the action using that fallback Actor.
6. WHEN the Orchestrator is constructed with an explicit `approver` argument (i.e. `approver` is not `None`), THE Orchestrator SHALL use that value as the Actor for all actions on that instance and SHALL NOT call STS `GetCallerIdentity`. The constructor SHALL distinguish "no approver supplied" from "caller explicitly supplied an approver" by whether `approver is not None`, not by comparing the value against the literal string `"system"` (a caller who explicitly passes `approver="system"` must be treated as an explicit approver, not as an omitted one).
7. THE Orchestrator SHALL use the resolved Actor (or fallback) in place of the literal string `"system"` at every existing audit-stamp site (approval log entry, post-remediation hook invocation, rollback log entry, `AuditEntry.actor`).
8. THE Orchestrator SHALL resolve the Actor into a local variable within each of `approve()`, `rollback()`, and `_handle_confirm_rollback()`, and SHALL thread that value explicitly as an `actor` parameter into `_log_action()` and `_run_post_remediation_hook()`, rather than mutating a shared instance attribute (e.g. `self.approver`). This SHALL hold even when a single Orchestrator instance is invoked concurrently (e.g. from multiple Streamlit session threads, or from `multi_account_orchestrator.py`/`scheduler.py` call patterns), so that one call's resolved Actor can never be attributed to a different, concurrent call's audit entry.

### Requirement 2: Savings Ledger Reversal on Rollback

**User Story:** As a FinOps stakeholder, I want the savings ledger to reverse a remediation's recorded savings when that remediation is rolled back, so that the reported cumulative savings reflects the current state of the infrastructure rather than every remediation ever attempted.

#### Acceptance Criteria

1. THE SavingsTracker SHALL expose a `record_rollback(resource_id: str) -> bool` method in addition to its existing `record_run()` and `get_savings_summary()` methods.
2. WHEN `record_rollback(resource_id)` is called AND a prior `record_run()` entry exists that included `resource_id` in its `resources_remediated` list AND no rollback entry already targets that specific (`resource_id`, `run_id`) pair, THE SavingsTracker SHALL select the earliest such un-reversed matching entry, append a new ledger entry with a negative `monthly_savings_added` equal to the negation of that matched entry's own already-stored `monthly_savings_added` value — NOT a value recomputed from the live findings store (`findings_store.json` is overwritten by every subsequent `execute_audit()` scan and may no longer contain `resource_id` by the time a rollback happens, which would otherwise silently produce a `$0` reversal) — record which `run_id` the new entry reverses, recalculate `total_lifetime_savings` across all entries (including the new negative one), and return `True`.
3. IF `record_rollback(resource_id)` is called AND every `record_run()` entry containing `resource_id` in its `resources_remediated` list has already been reversed (or none exists), THEN THE SavingsTracker SHALL make no change to the ledger and return `False`. Reversal state SHALL be tracked per (`resource_id`, `run_id`) pair, not per bare `resource_id` — if `resource_id` is remediated again in a later, distinct `run_id` (e.g. the resource was found again after being reintroduced), that later remediation SHALL remain independently reversible even after an earlier run's remediation of the same `resource_id` has already been reversed.
4. WHEN `Orchestrator._handle_confirm_rollback()` completes a rollback successfully, THE Orchestrator SHALL call `SavingsTracker.record_rollback(resource_id)`, wrapped in a try/except that catches `Exception`, logs a WARNING on failure, and does not alter the `RollbackResult.success` value returned to the caller.

### Requirement 3: Explicit LLM Privacy Posture Attestation

**User Story:** As a compliance-conscious enterprise operator, I want strict privacy mode to require an explicit, operator-declared attestation that the configured LLM endpoint does not retain or train on submitted data, so that the "no training" posture is at minimum a checked, auditable operator self-report rather than an unverifiable guess inferred from the model name. This attestation is a format-checked configuration gate, not a technical enforcement mechanism: the LLM client is a generic OpenAI-compatible adapter with no code-level way to verify what an arbitrary third-party or BYO endpoint actually does with submitted data — it can only require the operator to explicitly assert a retention policy before the client is constructed, and refuse to proceed without that assertion.

#### Acceptance Criteria

1. THE LLM client module SHALL read a `JANITOR_LLM_RETENTION_POLICY` environment variable with permitted values `"none"` or `"unknown"`, defaulting to `"unknown"` when unset.
2. WHEN `JANITOR_PRIVACY_MODE=strict` AND `JANITOR_LLM_RETENTION_POLICY` is not exactly `"none"`, THE LLM client module's `get_client()` SHALL raise a `RuntimeError` identifying that strict mode requires an explicit `none` retention-policy attestation, and SHALL NOT construct a client.
3. WHEN `JANITOR_PRIVACY_MODE=strict` AND `JANITOR_LLM_RETENTION_POLICY=none`, THE LLM client module SHALL still apply the existing free-tier router pattern check (`_FREE_ROUTER_PATTERNS`) and raise `RuntimeError` if the configured model matches a free-tier pattern.
4. THE LLM client module SHALL expose a `get_privacy_posture() -> dict` function returning at minimum the keys `mode`, `retention_policy`, `model`, and `base_url`, reflecting the currently configured environment.

### Requirement 4: Input-Side Redaction of Finding Data Before LLM Prompts

**User Story:** As a security engineer, I want resource identifiers, ARNs, and account IDs stripped from finding data before it is sent to any LLM, so that cloud account details are not unnecessarily exposed to a third-party or BYO LLM endpoint.

#### Acceptance Criteria

1. THE codebase SHALL provide a `redact(obj) -> tuple[Any, dict[str, str]]` function that, given a dict, string, or nested structure, replaces every occurrence of a resource ID, AWS ARN (pattern `arn:aws:...`), and 12-digit AWS account ID with a stable placeholder token (`RESOURCE_n`, `ARN_n`, `ACCOUNT_n`), and returns the scrubbed copy along with a Redaction_Map from placeholder to original value.
2. THE codebase SHALL provide a `rehydrate(text: str, mapping: dict[str, str]) -> str` function that replaces every placeholder token in `text` with its original value from the given Redaction_Map.
3. THE `redact()` function SHALL NOT alter resource type, region, or tag key/value fields.
4. WHEN `explainer.py`, `anomaly_detector.py`, `tagger.py`, `policy_suggester.py`, `incident_policy_generator.py`, or `drift_detector.py` constructs a prompt from finding data or generated HCL, THE agent SHALL first pass that data through `redact()` and SHALL use only the scrubbed copy and its Redaction_Map for prompt construction.
5. WHEN one of the six agents listed in criterion 4 receives a response from the LLM, THE agent SHALL apply `rehydrate()` to the response text using the Redaction_Map produced for that call before using or displaying the response.

### Requirement 5: Prompt-Injection Hardening for LLM-Facing Finding Data

**User Story:** As a security engineer, I want cloud-controlled text (such as resource tags) that reaches an LLM prompt to be explicitly delimited as untrusted data, so that a malicious tag value on a flagged resource cannot be interpreted by the model as an instruction.

#### Acceptance Criteria

1. THE prompt templates used by `explainer.py`, `anomaly_detector.py`, `tagger.py`, `policy_suggester.py`, `incident_policy_generator.py`, and `drift_detector.py` SHALL wrap all finding-derived and HCL-derived content in an explicit data delimiter (e.g. `<untrusted_finding_data>...</untrusted_finding_data>`) accompanied by an instruction that the delimited content is data to analyze, never instructions to follow.
2. THE delimiter wrapping in criterion 1 SHALL be present regardless of whether the delimited content has been passed through `redact()` (Requirement 4), since tag values remain unredacted and are attacker-influenceable.

### Requirement 6: tfsec Policy Check in the Pre-Remediation Hook

**User Story:** As a security engineer, I want generated remediation and rollback HCL to pass a security-posture policy check, not just a syntax check, before an operator is shown the approval prompt.

#### Acceptance Criteria

1. WHEN `hooks/pre-remediation.sh`'s `validate_hcl()` completes its existing `terraform validate`/`fmt` check for a given HCL file AND the `tfsec` binary is present on PATH, THE hook SHALL run `tfsec` against the temporary directory containing that file and SHALL treat any HIGH or CRITICAL severity finding as a validation failure for that file.
2. IF `tfsec` is not present on PATH AND `JANITOR_REQUIRE_TFSEC` is not set to `"1"`, THEN THE hook SHALL print a warning that the policy check was skipped and SHALL proceed as if the check passed.
3. IF `tfsec` is not present on PATH AND `JANITOR_REQUIRE_TFSEC=1`, THEN THE hook SHALL treat this as a validation failure for that file and SHALL block remediation.

### Requirement 7: Terraform Plan Scope Check Before Apply

**User Story:** As a security engineer, I want the orchestrator to verify that a Terraform plan only touches the resource being approved, within an allowed action class for that finding type, before it is applied — for both a remediation apply and a rollback apply — so that untrusted AWS-side metadata cannot cause an out-of-scope or blast-radius-widening change to be silently applied in either direction.

#### Acceptance Criteria

1. WHEN `Orchestrator.approve()` completes a successful `terraform init` for a resource's isolated apply directory, THE Orchestrator SHALL run `terraform plan -json` in that directory before running `terraform apply`, and SHALL evaluate the plan against the scope check for the **remediation** flow. WHEN `Orchestrator._handle_confirm_rollback()` completes a successful `terraform init` for a resource's isolated apply directory, THE Orchestrator SHALL likewise run `terraform plan -json` before `terraform apply`, and SHALL evaluate the plan against the scope check for the **rollback** flow.
2. THE Orchestrator SHALL provide a `_check_plan_scope(plan_json, resource_id, finding, flow) -> tuple[bool, str]` function, where `flow` is either `"remediate"` (called from `approve()`) or `"rollback"` (called from `_handle_confirm_rollback()`), that inspects only entries in `plan_json["resource_changes"]` where `mode == "managed"` and `change.actions` is not `["no-op"]`, ignoring `mode == "data"` entries. The allowlist SHALL be keyed on (resource_type, category, flow) — not resource_type/category alone — because the remediation and rollback templates for the same finding type generate structurally different HCL (different resource addresses and, for security groups, an intentionally opposite CIDR outcome).
3. WHEN `flow` is `"remediate"` AND `finding`'s normalized resource type and category is `(ebs, waste)` or `(elasticache, waste)`, THE scope check SHALL pass if and only if every counted resource_change's action set is exactly `["create"]`, its address contains the sanitized resource ID as a substring, and its address is prefixed with one of the remediation-direction resource addresses (`aws_ebs_snapshot.pre_remediation_`/`null_resource.destroy_` for ebs; `null_resource.snapshot_`/`null_resource.destroy_` for elasticache).
4. WHEN `flow` is `"rollback"` AND `finding`'s normalized resource type and category is `(ebs, waste)`, THE scope check SHALL pass if and only if every counted resource_change's action set is exactly `["create"]`, its address contains the sanitized resource ID as a substring, and its address is prefixed with `aws_ebs_volume.restore_`.
5. WHEN `flow` is `"rollback"` AND `finding`'s normalized resource type and category is `(elasticache, waste)`, THE scope check SHALL pass if and only if every counted resource_change's action set is exactly `["create"]`, its address contains the sanitized resource ID as a substring, and its address is prefixed with `aws_elasticache_cluster.restore_`.
6. WHEN `finding`'s normalized resource type and category is `(ebs, security)` or `(elasticache, security)` — regardless of `flow` — OR `finding` does not match any known remediation template, THE scope check SHALL pass if and only if there are zero counted resource_changes. (Encryption findings have no live remediation or rollback HCL in either direction, so this rule is identical for both flows.)
7. WHEN `flow` is `"remediate"` AND `finding`'s normalized resource type and category is `(security_group, security)`, THE scope check SHALL pass if and only if there is exactly one counted resource_change, its address contains the sanitized resource ID as a substring, its resource type is `aws_security_group_rule`, its address is prefixed with `aws_security_group_rule.remediate_` (the remediation-direction resource address), its action set is `["create"]` or `["update"]`, and its `change.after.cidr_blocks` does **not** contain `"0.0.0.0/0"`.
8. WHEN `flow` is `"rollback"` AND `finding`'s normalized resource type and category is `(security_group, security)`, THE scope check SHALL pass if and only if there is exactly one counted resource_change, its address contains the sanitized resource ID as a substring, its resource type is `aws_security_group_rule`, its address is prefixed with `aws_security_group_rule.restore_` (the rollback-direction resource address), and its action set is `["create"]` or `["update"]` — and, distinctly from criterion 7, its `change.after.cidr_blocks` **MAY** contain `"0.0.0.0/0"` without failing the check. This is the correct, intended behavior: the rollback template's entire purpose is restoring the resource's original open rule, and only the remediation-direction check (criterion 7) exists to reject widening to `0.0.0.0/0`. The `addr_prefix` check in this criterion and criterion 7 exists so that a plan whose one change has the wrong-direction address name (e.g. a `remediate_`-prefixed address presented during a rollback flow, or vice versa) but otherwise "correct" type/action/CIDR shape is still rejected — resource type, action set, and CIDR direction alone are not sufficient to distinguish the two flows.
9. IF the scope check fails for any reason, THEN THE Orchestrator SHALL NOT execute `terraform apply`, SHALL log the reason via an audit entry with action `"scope_check_failed"`, and SHALL return a failure result identifying the violation.
10. THE Orchestrator's scope check documentation SHALL note that `null_resource` + `local-exec` based remediations and rollbacks (`ebs`/`elasticache` waste, both flows) are only checked at the Terraform-resource-address level — the check does not and cannot inspect the shell command embedded in the `local-exec` provisioner — and SHALL explicitly state that the allowlist covers both the remediation and rollback flows for every (resource_type, category) combination that has a live template, not only the remediation direction.
11. FOR the `(ebs, waste)` and `(elasticache, waste)` scope-check rules, in both `flow` values, THE scope check SHALL require that the full REQUIRED set of address-prefixes for that (resource_type, category, flow) — both `aws_ebs_snapshot.pre_remediation_*` **and** `null_resource.destroy_*` for `(ebs, waste, remediate)`; both `null_resource.snapshot_*` **and** `null_resource.destroy_*` for `(elasticache, waste, remediate)`; the single restore-resource prefix for each rollback rule — is present among the counted resource_changes, not merely that no out-of-allowlist entry is present. A plan containing only the destroy/delete resource without its paired snapshot/backup resource (or any other proper non-empty subset of the required set) SHALL fail the scope check.
