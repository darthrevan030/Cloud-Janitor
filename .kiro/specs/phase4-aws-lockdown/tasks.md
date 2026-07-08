# Implementation Plan: Phase 4 AWS Lockdown

## Overview

This plan implements 5 requirements derived from 3 backlog issues (SEC-5, INF-2, DOC-1). Work flows from the two static IAM policy artifacts and their drift-detection tests (which have no dependency on the credential-boundary code), through the Orchestrator's remediation-role assumption wiring, to the consolidated deployment guide — written last, once the role split and both policy files are real and testable.

## Tasks

- [ ] 1. Create the least-privilege IAM policy artifacts
  - [ ] 1.1 Create `iam/` directory and `iam/janitor-read-policy.json`
    - Write the JSON exactly as specified in design.md Component 3: five statements covering `ec2:Describe*` (4 actions), `elasticache:Describe*` (2 actions), `cloudwatch:GetMetricStatistics`, `sts:GetCallerIdentity`, and `sts:AssumeRole` scoped to a `janitor-remediation-role` ARN placeholder
    - Validate the file is well-formed JSON with `Version: "2012-10-17"` and unique `Sid` values
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 1.2 Create `iam/janitor-remediation-policy.json`
    - Write the JSON exactly as specified in design.md Component 4 (post-review-correction version): eight statements covering EBS snapshot/tag lifecycle, EBS volume lifecycle, security-group-rule lifecycle, EC2 read-for-state-refresh, ElastiCache cluster/snapshot lifecycle, ElastiCache tagging, ElastiCache read-for-state-refresh, and STS provider-init check
    - Resource-scope every action that supports it: EBS volume/snapshot ARNs, **both `arn:aws:ec2:*:*:security-group/*` AND `arn:aws:ec2:*:*:security-group-rule/*`** for `ec2:AuthorizeSecurityGroupIngress`/`ec2:RevokeSecurityGroupIngress` (AWS's IAM docs require both resource types for these actions — granting only `security-group` causes real-AWS `AccessDenied`, a defect LocalStack cannot surface), ElastiCache cluster/snapshot ARNs
    - Do **not** include `ec2:DeleteSnapshot` — it was scope creep justified by a `terraform destroy` cleanup path that does not exist anywhere in `orchestrator.py` (confirmed by grep); re-add only if/when a real `terraform destroy` path is built
    - Use `"*"` for the truly unscopable `ec2:Describe*`/`sts:GetCallerIdentity` actions, and (separately justified as operational simplicity, not an AWS limitation) for `elasticache:DescribeCacheClusters`/`DescribeSnapshots`/`ListTagsForResource`, which AWS does support scoping for
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ] 1.3 Write policy-artifact static validity tests (`tests/test_iam_policies.py`)
    - Valid-JSON parse for both files; `Version` field present and correct
    - No duplicate `Sid` values within either file
    - Every `Resource` value is either `"*"` or a syntactically well-formed ARN pattern (regex check, not a live AWS validation call)
    - _Requirements: 3.1, 4.1_

  - [ ] 1.4 Write Property 4 & 5 tests for the read policy (`tests/test_iam_read_policy.py`)
    - **Property 4: Read Policy Action-Source Completeness** — parse `aws_provider.py` for `_make_client(...)`/`_dep_client(...)` call sites followed by `.get_paginator("<method>")` or `.<method>(...)` calls; map each to its `service:Action` form; assert every mapped action is present in `iam/janitor-read-policy.json`
    - **Property 5: Read Policy Non-Mutation** — assert no action in the read policy has a verb matching `Create|Delete|Modify|Put|Authorize|Revoke|Attach|Detach|Update` for the `ec2`/`elasticache` prefixes
    - **Validates: Requirements 3.1, 3.4, 3.5**

  - [ ] 1.5 Write Property 6 & 7 tests for the remediation policy (`tests/test_iam_remediation_policy.py`)
    - **Property 6: Remediation Policy Resource-Level Scoping Correctness** — hardcode the reference set of AWS actions known to support resource-level permissions (EBS volume/snapshot actions, security-group-rule actions, ElastiCache cluster/snapshot mutation+tagging actions); assert no statement whose actions are all in that set uses `Resource: "*"`
    - **Property 7: Remediation Policy Template Coverage** — hardcode the template→action table from design.md Component 4; assert every action in that table is present in `iam/janitor-remediation-policy.json`
    - Note the known limitation of both properties (design.md, Property 7): they only cross-check two hand-authored artifacts (the table and the JSON) against each other, and structurally could not have caught either CRITICAL finding from the design review (missing `security-group-rule` resource type; erroneous `ec2:CreateTags` table entry) — that is what task 1.6 below is for
    - **Validates: Requirements 4.1, 4.2, 4.4, 4.5**

  - [ ] 1.6 Write Property 8 test — cross-check actual rendered HCL, not just the hand-written table (`tests/test_iam_remediation_policy_hcl.py`)
    - **Property 8: Remediation Policy HCL-Derived Coverage** — call `_remediation_ebs_waste`, `_rollback_ebs_waste`, `_remediation_security_group`, `_rollback_security_group`, `_remediation_elasticache_waste`, and `_rollback_elasticache_waste` on `RemediationArchitect` with representative sample finding dicts; parse each returned HCL string for its actual `resource "<type>" "..."` blocks and `local-exec` AWS CLI subcommands; map each to its expected `service:Action` set and assert every one is covered by `iam/janitor-remediation-policy.json`
    - This test operates on the RETURNED HCL STRING, not the Component 4 table — it is the first test in this phase that would have caught the missing `security-group-rule` resource type independently of a human re-deriving the table correctly
    - **Validates: Requirements 4.1, 4.4**

  - [ ] 1.7 Gate `iam/janitor-remediation-policy.json` on real-AWS (or `iam:SimulateCustomPolicy`) validation before merge
    - Not a pytest task: run the Terraform HCL generated by each of `RemediationArchitect`'s six live-mutation template methods against either (a) a real, minimal, single-account, cost-controlled AWS test account under a role scoped to exactly this policy, or (b) an `iam:SimulateCustomPolicy` dry run covering every action/resource pair in the policy
    - Required because LocalStack does not enforce IAM and structurally cannot detect a missing required resource type (this is precisely how the missing `security-group-rule` ARN pattern went undetected until this design review) — Properties 4–8 passing in CI is necessary but not sufficient for merging this policy
    - Record the outcome (or a link to it) in the PR that adds `iam/janitor-remediation-policy.json`; reference this gate in `docs/deployment.md`'s Operational Notes (task 6.1)
    - _Requirements: 4.6_

- [ ] 2. Checkpoint — Ensure policy artifact tests pass
  - Ensure all tests from task 1 pass (including the real-AWS/`iam:SimulateCustomPolicy` gate in task 1.7), ask the user if questions arise.

- [ ] 3. Implement remediation-role credential assumption
  - [ ] 3.1 Add `_assume_remediation_role()` and `RemediationRoleAssumptionError` to `orchestrator/orchestrator.py`
    - Read `JANITOR_REMEDIATION_ROLE_ARN`; return `None` (with a once-per-process WARNING log) if unset
    - Call `sts.assume_role()` via the existing `_make_client("sts", region=None)` pattern (reusing `aws_provider.py`'s helper, consistent with Phase 1's `core/identity.py` reuse of the same pattern) when set
    - Return `{"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}` from the response's `Credentials`, or raise `RemediationRoleAssumptionError` on any exception
    - _Requirements: 2.1, 2.4_

  - [ ] 3.2 Write property tests for remediation-role assumption
    - **Property 1: Remediation-Role Credential Isolation**
    - **Property 2: Fail-Closed Role Assumption**
    - **Property 3: Unset-Role Fallback Invariant**
    - **Validates: Requirements 2.2, 2.3, 2.4**

  - [ ] 3.3 Write unit tests for `_assume_remediation_role()` (`tests/test_remediation_role.py`)
    - Mocked successful `sts.assume_role` → correct three-key dict returned
    - Mocked `ClientError`/timeout → `RemediationRoleAssumptionError` raised with role ARN in the message
    - `JANITOR_REMEDIATION_ROLE_ARN` unset → `None` returned, `sts.assume_role` never called (mock and assert not called)
    - Warning is logged exactly once across multiple calls within the same process when unset (verify the once-per-process latch)
    - _Requirements: 2.1, 2.3, 2.4_

- [ ] 4. Wire remediation-role credentials into the Orchestrator's approve/rollback flow
  - [ ] 4.1 Add `_terraform_env_for_apply(resource_id)` to `Orchestrator`
    - Calls `_build_subprocess_env("terraform")`, then `_assume_remediation_role()`; merges returned creds over the ambient result on success; returns `None` and logs `_log_action("remediation_role_assumption_failed", ...)` on `RemediationRoleAssumptionError`
    - _Requirements: 2.2, 2.4_

  - [ ] 4.2 Wire `_terraform_env_for_apply()` into `approve()` and `_handle_confirm_rollback()` — **all four `env=_build_subprocess_env("terraform")` call sites**, not just `init`
    - `approve()` currently builds the terraform env inline TWICE — once for its `init` call (`orchestrator.py:921-927`) and once for its `apply` call (`orchestrator.py:942-948`). `_handle_confirm_rollback()` does the same — once for its `init` call (`orchestrator.py:1548-1554`) and once for its `apply` call (`orchestrator.py:1571-1577`). That is four inline `env=_build_subprocess_env("terraform")` sites total across the two methods, not one.
    - Each method calls `_terraform_env_for_apply(resource_id)` **exactly once**, near the top of its terraform sequence, storing the result in a local (e.g. `tf_env`), and passes that **same `tf_env` value to BOTH its `init` call and its `apply` call** — replacing all four inline `_build_subprocess_env("terraform")` call sites listed above, not just the two `init` sites
    - Aborts with its own failure-result type (`ApprovalResult`/`RollbackResult`, `success=False`) on `None`, before any `subprocess.run` invocation
    - Confirm no change to the hook-kind environment (`_build_subprocess_env("hook")`, at `orchestrator.py:1125`, `:1170`, `:1221`) — hooks are untouched by this task
    - _Requirements: 2.2, 2.5, 2.6_

  - [ ] 4.3 Write unit tests for Orchestrator wiring (`tests/test_orchestrator_remediation_role.py`)
    - `JANITOR_REMEDIATION_ROLE_ARN` set + mocked assume-role failure → `approve()`/`_handle_confirm_rollback()` return `success=False`, assert zero `subprocess.run` calls for `init`/`plan`/`apply`
    - `JANITOR_REMEDIATION_ROLE_ARN` set + mocked assume-role success → assert the `env=` kwarg passed to the `init`/`apply` `subprocess.run` calls contains the mocked temporary credentials, not any ambient `AWS_ACCESS_KEY_ID` present in the test's environment
    - **New — same-credential-object test (closes the ambiguity this design review flagged):** for both `approve()` and `_handle_confirm_rollback()`, with the Remediation_Role configured and assumption mocked to succeed, assert the `env=` kwarg captured from the `init` `subprocess.run` call and the `env=` kwarg captured from the `apply` `subprocess.run` call are the SAME credential set (e.g. identical `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`) within that one invocation — this test must fail if an implementation wires the new credentials into `init` only and leaves `apply` on ambient credentials
    - `JANITOR_REMEDIATION_ROLE_ARN` unset → assert `sts.assume_role` never called and the terraform env matches today's `_build_subprocess_env("terraform")` output exactly
    - Hook subprocess calls (pre/post-remediation) are unaffected regardless of Remediation_Role state (env kwarg unchanged from before this task)
    - Two sequential `approve()` calls with the role configured each independently call `_assume_remediation_role()` (no credential caching/reuse across invocations)
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ] 5. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Write the consolidated deployment guide
  - [ ] 6.1 Create `docs/deployment.md`
    - Write the six sections from design.md Component 5: Reference Architecture, EC2 Setup, Identity & Auth, Least-Privilege IAM, BYO-Endpoint & Privacy Posture, Operational Notes
    - Reference Architecture section explicitly states the single-shared-EC2-instance scope decision and the YAGNI rationale (no autoscaling infra exists), citing `.kiro/specs/phase2-persistent-state/requirements.md`'s analogous SQLite-over-Postgres reasoning
    - EC2 Setup section documents instance-profile attachment (Requirement 1) and the minimum EC2-service-principal trust policy for the Read_Role
    - Identity & Auth section states the instance-role requirement and links Phase 1 SEC-3's `core/identity.py` fail-closed design (`.kiro/specs/phase1-trust-hardening/design.md`)
    - Least-Privilege IAM section embeds or links both `iam/janitor-read-policy.json` and `iam/janitor-remediation-policy.json`, documents `JANITOR_REMEDIATION_ROLE_ARN`, and includes the Remediation_Role trust policy JSON from design.md Component 4
    - BYO-Endpoint & Privacy Posture section links (does not restate) Phase 1 SEC-1's `JANITOR_LLM_RETENTION_POLICY`/`get_privacy_posture()` design and README.md's existing "Security Hardening" section (`README.md:392-420`)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 6.2 Write content-presence tests for the deployment guide (`tests/test_deployment_docs.py`)
    - `docs/deployment.md` exists and contains all six required section headers
    - Contains the literal filenames `janitor-read-policy.json` and `janitor-remediation-policy.json`
    - Contains the literal string `JANITOR_REMEDIATION_ROLE_ARN`
    - Does not contain a verbatim duplicate of the README's "Security Hardening" heading text (a substring/line-count check against `README.md:392-420`, not a byte-identical file diff)
    - Contains a reference to `JANITOR_LLM_RETENTION_POLICY` (Phase 1 SEC-1) and to identity/`GetCallerIdentity` (Phase 1 SEC-3)
    - _Requirements: 5.1, 5.3, 5.4, 5.5_

- [ ] 7. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Follow-Up Work (Tracked, Not Part of This Phase's Initial Implementation Waves)

These two items were surfaced by the principal-engineer design review that produced the CRITICAL/HIGH fixes above. Neither blocks this phase's initial implementation — the first is explicitly out of this phase's IAM scope, and the second cannot happen until a different phase ships — but both are tracked here so they are not lost.

- [ ] F.1 Fast-follow: fix (or file separately) the `_rollback_security_group` tags-on-unsupported-resource bug
  - `agents/remediation_architect.py`'s `_rollback_security_group` method (lines 378-398) emits a `tags = {...}` block (via `self._tags_block()`, lines 303-312) inside an `aws_security_group_rule` resource (the block is spliced in at line 396). The classic `aws_security_group_rule` Terraform AWS-provider resource type does not support a `tags` argument — this HCL may fail Terraform schema validation the first time a security-group rollback actually runs against the provider's schema (unbounded `version = ">= 4.0"` constraint in the generated `providers.tf`, per `orchestrator.py`)
  - Out of scope for `phase4-aws-lockdown`'s spec (this phase covers IAM/credentials, not `remediation_architect.py`'s HCL correctness) — flagged here with exact file/line references per design.md Component 4, to be picked up as its own fix or fast-follow ticket during or shortly after this phase's implementation
  - Not required for any task above to be considered complete; do not block Task 1/3/4/6 checkpoints on this

- [ ] F.2 Follow-up (execute only after `phase1-trust-hardening` ships): update `docs/deployment.md`'s "designed, pending implementation" wording
  - Once Phase 1 SEC-1 (`core/llm_client.py` privacy posture) and SEC-3 (`core/identity.py` fail-closed identity) actually ship in code, revisit `docs/deployment.md`'s Identity & Auth and BYO-Endpoint & Privacy Posture sections (written by Task 6.1, possibly using "designed, pending implementation" phrasing per this phase's Notes below) and update them to describe actual shipped behavior instead
  - This task exists because nothing else in either phase's plan revisits that wording once Phase 1 merges — left untracked, the guide would go stale the day Phase 1 ships, unflagged. See the matching cross-reference note added to `.kiro/specs/phase1-trust-hardening/tasks.md`
  - _Requirements: 5.7_

## Notes

- All tasks are mandatory — property tests, unit tests, and static-analysis tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- Task 1 (policy artifacts) has no dependency on Task 3/4 (Orchestrator wiring) and can proceed independently — they are sequenced first here only because Task 6 (the deployment guide) needs both to exist and be tested before it can honestly describe them
- Task 6 is explicitly the last wave, per Requirement 5.6 and this phase's stated sequencing: DOC-1 documents reality established by SEC-5 and INF-2, not aspirational design
- This phase assumes Phase 1 (`phase1-trust-hardening`) SEC-1 (`core/llm_client.py` privacy posture) and SEC-3 (`core/identity.py` fail-closed identity) have landed in code before Task 6.1 is written, since the deployment guide makes concrete claims about both; if Phase 1 has not yet shipped when this phase is picked up, Task 6.1's Identity & Auth and BYO-Endpoint sections should reference the Phase 1 design documents explicitly as "designed, pending implementation" rather than asserting shipped behavior — see Follow-Up task F.2 above for the required revisit once Phase 1 ships
- `iam/` is a new top-level directory (sibling to `src/`, `hooks/`, `docs/`) — confirmed no prior `iam/` directory or IAM policy JSON exists anywhere in the repo
- Task 1.7 (real-AWS-or-`iam:SimulateCustomPolicy` validation gate) and Follow-Up tasks F.1/F.2 are intentionally excluded from the wave graph below: 1.7 is a merge gate/checklist item rather than a coding task with a clean wave slot (it should complete before Task 1's checkpoint is declared done, but does not block Task 3/4's independent progress), and F.1/F.2 are explicitly deferred, non-blocking follow-ups per their own descriptions above

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "1.5", "1.6", "3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "4.1"] },
    { "id": 3, "tasks": ["4.2"] },
    { "id": 4, "tasks": ["4.3"] },
    { "id": 5, "tasks": ["6.1"] },
    { "id": 6, "tasks": ["6.2"] }
  ]
}
```
