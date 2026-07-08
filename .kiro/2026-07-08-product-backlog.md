# Cloud Janitor — Product Backlog (GitHub-issue-ready)

_Last scoped 2026-07-08 against the uploaded **v0.3.0** codebase (src-layout under
`src/cloud_janitor/`), not the older GitHub `master`. Every `##` block below is one issue — copy
the block into a GitHub issue as-is. The title line doubles as the issue title; the **metadata line**
(Priority · Effort · Depends on · Type) maps to labels. Sorted by **priority (P0→P3)**, then
**effort (S→S-M→M→M-L→L)** within each priority. Verified 2026-07-08 against current code —
see verification notes inline where a claim needed correction._

**Label taxonomy:**
- `type:gap` — a hole in an existing feature's logic (completing it, not new functionality)
- `type:enh` — net-new functionality
- `type:tech-debt` — debt taken on due to a design decision

Issues can carry more than one type. `priority:P0–P3` and `effort:S|S-M|M|M-L|L` round out the set.

---

## Already resolved in v0.3.0 (verified against the code — not tickets)

An earlier scoping pass against the GitHub `master` flagged these; all are fixed in the uploaded
version, so they are **not** issues. Listed so the closure is on record:

- **Missing NL audit method** — `Orchestrator.execute_natural_language_audit()` exists
  (`orchestrator/orchestrator.py:629`) and `app.py:879/896` feature-detects and calls it. Done.
- **`terraform apply` with no `init`** — `approve()` runs `init` then `apply` in a per-resource temp
  dir (`orchestrator.py:918–947`). Done.
- **Rollback never applied Terraform** — `_handle_confirm_rollback()` now stages the rollback HCL and
  runs `init`+`apply`, only logging success on a zero return code (`orchestrator.py:1528–1600`). Done.
- **Per-resource approval applied the whole plan** — `approve()` writes only the approved resource's
  HCL to an isolated temp dir before applying (`orchestrator.py:853–962`). Done.
- **Rollback had no attempt-throttle** — the approval gate is now enforced on rollback with lockout
  (`orchestrator.py:1013–1021`, `1476–1512`). Done.
- **Approval-gate lock was in-memory / reset on restart** — persisted via `ApprovalGateStore`
  (`agents/approval_gate.py:375`) with a corruption guard, instantiated at `orchestrator.py:442`. Done.
- **No findings schema validation** — `agents/schema_validator.py` performs real structural/enum
  validation of the findings store. Done. Note: there is **no schema-versioning mechanism** —
  the only "version" artifact is a hardcoded, unenforced `"schema_version": "1.0.0"` literal written
  by `finops_auditor.py:208` that nothing reads. Validation is real; versioning is not.
- **No CI** — `.github/workflows/ci.yml` present (lint, type-check, tests across Python 3.12/3.13). Done.
- **AWS backend was a `NotImplementedError` stub** — `mcp_server/backends/aws_provider.py` is a real
  boto3 provider (zero `NotImplementedError`, 614 lines of real calls). Done — but see SEC-5 / INF-2,
  which it now unblocks.
- **BYO LLM endpoint** — configurable via `JANITOR_LLM_BASE_URL` / `_API_KEY` / `_MODEL`
  (`core/llm_client.py:208,211,235`). Partially done — the *no-training guarantee* is still weak; see
  SEC-1.

---

## Sorted index (triage view)

| ID | Title | Priority | Effort | Type | Depends on |
|----|-------|----------|--------|------|------------|
| SEC-3 | Approver identity is hardcoded `"system"` | P1 | S-M | gap | — |
| BUG-1 | Savings ledger is never reversed on rollback | P1 | S-M | gap | — |
| SEC-1 | "No training" guarantee is weaker than it looks | P1 | M | enh | — |
| SEC-2 | Cloud data is sent to the LLM without input-side redaction | P1 | M | gap, enh | — |
| INF-1 | Plans / pending rollbacks / audit trail are still in-memory | P1 | M-L | tech-debt | — |
| SEC-4 | Only `terraform validate` gates LLM-generated HCL before apply | P1 | M-L | enh | — |
| BUG-3 | No backend-reachability preflight before an audit | P2 | S | gap | — |
| OBS-1 | Reasoning log is truncated at the start of every run | P2 | S | gap | — |
| TECH-1 | Subprocess timeouts are hardcoded | P2 | S | tech-debt | — |
| DX-1 | No secret scanning / pre-commit hooks | P2 | S | tech-debt | — |
| DOC-1 | No consolidated enterprise deployment guide | P2 | S | enh | SEC-1, INF-2 |
| BUG-2 | Single-account `findings_store.json` has no write lock | P2 | M | gap, tech-debt | — |
| SEC-5 | AWS-mode credential sourcing / read-write split undefined | P2 | M | enh | — |
| INF-2 | Least-privilege IAM policy for the live AWS provider | P2 | M | enh | — |
| OBS-2 | Queryable / exportable audit trail for compliance | P2 | M | enh | INF-1 |
| FEAT-1 | Surface `terraform plan` preview to the operator | P2 | M | enh | — |
| FEAT-2 | Scheduled-scan run history and alerting | P2 | M | enh | — |
| FEAT-3 | Real Cost Explorer cost data (replace fixture values) | P3 | M | enh | — |
| FEAT-4 | GCP / Azure backend implementations | P3 | L | enh | — |

---

## SEC-3 — Approver identity is hardcoded `"system"`

**Priority:** P1 · **Effort:** S-M · **Depends on:** — · **Type:** GAP

**Goal.** The audit trail should record *who* approved a change, not a constant.

**Current state & gap.** `Orchestrator.__init__` defaults `approver="system"` (`orchestrator.py:343`),
and every call site — `app.py:528`, `cli.py:37,68,90`, `multi_account_orchestrator.py:239–241` —
constructs `Orchestrator()` with no argument. Approvals, executions, and rollbacks are all stamped
`"system"` (`orchestrator.py:840`, `1164`, `1595`, `1608`). Neither `approve()` nor `rollback()`
accepts an actor parameter, and there is no UI widget, CLI flag, or env var that overrides it — it is
currently impossible to attribute an approval/rollback to a real user. In a shared deployment the
audit trail can't answer "who authorized this?" — the whole point of an approval gate in a compliance
tool.

**What's needed.** Thread an authenticated identity from the UI session into each approval/rollback
call (not just orchestrator construction) and stamp *that* onto the audit entry. Until real auth
exists (INF-1 / a login layer), at minimum require the operator to enter an identifier that is
recorded and non-empty.

**Implementation sketch.** Let `approve()`/`rollback()` accept an `actor` that overrides the default;
have `app.py` pass the session identity. Validate non-empty. Longer term this comes from SSO/OIDC on
the shared deployment.

**Open questions.** Identity source for the target deployment — SSO/OIDC on EC2, IAM session, or a
simple login? Shared decision with INF-1.

---

## BUG-1 — Savings ledger is never reversed on rollback

**Priority:** P1 · **Effort:** S-M · **Depends on:** — · **Type:** GAP

**Goal.** Rolling back a remediation should reverse the savings that remediation recorded.

**Current state & gap.** `approve()` records savings via `SavingsTracker.record_run(...)`
(`orchestrator.py:971`), but `SavingsTracker` (`agents/savings_tracker.py`) exposes only
`record_run()` and `get_savings_summary()` — there is **no** reverse or rollback method, and
`_handle_confirm_rollback()`'s success path (`orchestrator.py:1593–1600`) never touches the tracker.
Since rollback now genuinely reverts infrastructure, the savings figure will overstate realized
savings: it credits the deletion but never debits the restore, drifting monotonically wrong.

**What's needed.** Add a compensating entry to the ledger when a rollback succeeds.

**Implementation sketch.** Add `SavingsTracker.record_rollback(resource_id)` that appends a negative /
reversing entry (preserve history rather than mutating the original), and call it from
`_handle_confirm_rollback()` on success, wrapped in the same non-blocking try/except used for
`record_run`.

**Open questions.** Negative line item (audit-friendly) vs. mutating the original entry (simpler
totals)? Prefer the compensating entry.

---

## SEC-1 — "No training" guarantee is weaker than it looks

**Priority:** P1 · **Effort:** M · **Depends on:** — · **Type:** ENH

**Goal.** The enterprise "our data is never retained or trained on" story should be enforceable and
verifiable per backend, not a model-name heuristic.

**Current state & gap.** BYO-endpoint config exists (`JANITOR_LLM_BASE_URL/_API_KEY/_MODEL`) and
`JANITOR_PRIVACY_MODE=strict` is implemented, but it does two things and neither verifies anything:
(1) `get_client()` (`core/llm_client.py:219–226`) blocks only if the configured model name substring-
matches `_FREE_ROUTER_PATTERNS` (`:free`, `/free`, `openrouter/auto`) — trivially bypassed by any
paid-looking model name that still routes to a training-permissive backend; (2) `call_llm()`
(`core/llm_client.py:122–129`) sends an OpenRouter `extra_body["provider"]` hint
(`data_collection: "deny"`) that the code never confirms the provider actually honors. For an
arbitrary BYO endpoint, strict mode does nothing at all. The load-bearing enterprise claim rests on a
name check plus an unverified request hint, which won't survive due diligence.

**What's needed.** Turn "no training" into a real, per-backend posture: for the managed default,
send/verify the provider's no-retention flags; for Bedrock-in-account and Azure OpenAI, document and
assert the data-handling contract; for self-hosted (vLLM/Ollama), assert egress stays in-VPC. Surface
the active posture in the UI and fail closed in strict mode if it can't be established.

**Implementation sketch.** Add a `privacy_posture()` per adapter that returns
`{retention: none|unknown, mechanism: ...}`; strict mode requires `none`. Pin model versions per
backend. Pairs with SEC-2 so even a compliant endpoint receives minimal data.

**Open questions.** Which backend do first target customers require? Bedrock-in-account is often the
fastest defensible "no data leaves our AWS org" story.

---

## SEC-2 — Cloud data is sent to the LLM without input-side redaction

**Priority:** P1 · **Effort:** M · **Depends on:** — · **Type:** GAP, ENH

**Goal.** Findings that flow into LLM prompts should be scrubbed of identifiers that don't need to
leave the environment, and hardened against prompt injection from cloud-controlled text.

**Current state & gap.** There is output-side hygiene — `_redact()` scrubs terraform subprocess
stderr before logging/display (`orchestrator.py:135–136`, called from the init/apply/rollback sites
only) — but no **input-side** redaction of finding data sent *to* the model. The LLM-calling agents
are `explainer.py`, `anomaly_detector.py`, `tagger.py`, `policy_suggester.py`,
`incident_policy_generator.py`, and `drift_detector.py`; none of them call `_redact` or any
sanitizer before building a prompt. For example, `explainer.py:82–87` serializes the raw finding
dict and full Terraform HCL straight into the prompt template, and `anomaly_detector.py:124–127`
does the same with up to 30 raw resource dicts. Resource IDs, ARNs, account IDs, and tag values go to
the LLM as-is, and in `aws` mode a finding's tag values are attacker-influenceable, so a malicious tag
could carry instructions into the prompt. (Note: `finops_auditor.py`, `secops_guard.py`, and
`remediation_architect.py` never call the LLM at all — `remediation_architect.py`'s `_sanitize_id`
only formats resource IDs into valid Terraform identifiers and is unrelated to LLM I/O.)

**What's needed.** A redaction/tokenization layer that replaces identifiers with stable placeholders
before the prompt and re-hydrates after, plus structural prompt-injection defense (delimit all
cloud-sourced text as data, never instructions).

**Implementation sketch.** `redact(findings) -> (scrubbed, mapping)` / `rehydrate(text, mapping)`
around the LLM call in `explainer.py`, `anomaly_detector.py`, `tagger.py`, `policy_suggester.py`,
`incident_policy_generator.py`, and `drift_detector.py`. Wrap finding-derived text in explicit data
delimiters. Add a test with an injection payload in a tag value and assert the model contract holds.

**Open questions.** Which fields are load-bearing for remediation quality (type, region probably must
stay) vs. redactable (raw account IDs)?

---

## INF-1 — Plans / pending rollbacks / audit trail are still in-memory

**Priority:** P1 · **Effort:** M-L · **Depends on:** — · **Type:** TECH-DEBT

**Goal.** All approval-flow state must survive restarts and be shared across workers, not just the
approval gates.

**Current state & gap.** Gate state is persisted (`ApprovalGateStore`, `approval_gate.py:391,418`),
which is good — but `_audit_trail` (`orchestrator.py:449`), `_last_plans` (`:452`), and
`_pending_rollbacks` (`:455`) are plain in-memory `list`/`list`/`set` attributes with no load/save. On
restart, gate decisions survive but a plan generated in one process is invisible to another (so
`approve()` can't find it), pending rollbacks vanish, and the rich in-memory trail is lost (only
`audit.log` persists). This is the remaining slice of the multi-user blocker after the gate-store work.

**What's needed.** Move the rest of the mutable state behind the same persistence approach as the gate
store — a `StateStore` (SQLite single-node, Postgres/Redis multi-node) holding plans, pending
rollbacks, and trail keyed by run/resource.

**Implementation sketch.** Generalize the existing `ApprovalGateStore` pattern into a `StateStore`;
back it with SQLite first; inject into `Orchestrator`. Keystone for OBS-2.

**Open questions.** Single-node-first (SQLite) vs. designing for horizontal scale up front — depends on
whether near-term target is one shared EC2 box or an autoscaled service.

---

## SEC-4 — Only `terraform validate` gates LLM-generated HCL before apply

**Priority:** P1 · **Effort:** M-L · **Depends on:** — · **Type:** ENH

**Goal.** Machine-generated HCL should pass security/policy checks and a scope check before it can be
applied — syntactic validity is not safety.

**Current state & gap.** `hooks/pre-remediation.sh:82–91` runs only `terraform validate` (falling
back to `terraform fmt` on failure) — a pure syntax check. No tfsec/checkov/OPA call exists anywhere
in the file or repo. `approve()` and the rollback path go straight from `init` to `apply
-auto-approve` (`orchestrator.py:920–942`, `1547–1571`) with **no `terraform plan` step at all**, so
there is no scope/address diff against the finding's target resource. A hallucinated or
tag-manipulated plan could widen a security group instead of narrowing it, or act on out-of-scope
resources — an unacceptable blast radius for an auditor that auto-applies.

**What's needed.** A policy gate between generation and apply: `tfsec`/`checkov` for posture, plus a
scope check that diffs planned resource addresses (`terraform plan -json`) against the finding's target
and fails closed on any extra address or disallowed action class (snapshot / delete / CIDR-narrow).

**Implementation sketch.** Add the policy step to the pre-remediation hook chain; parse `plan -json`
(also feeds FEAT-1) and reject out-of-scope addresses. Codify an allowlist of action classes per
finding type alongside the Remediation Architect's templates.

**Open questions.** Per-finding-type action allowlist needs defining — worth doing with the Architect's
template owner.

---

## BUG-3 — No backend-reachability preflight before an audit

**Priority:** P2 · **Effort:** S · **Depends on:** — · **Type:** GAP

**Goal.** The app should confirm the active backend (LocalStack in fixture/demo, STS in `aws`) is
reachable before running an audit/apply that needs it.

**Current state & gap.** The only reachability check anywhere is a shell `curl`-based LocalStack
health loop in `Makefile:7-17,34-44`, used by `make demo`/`make live` before seeding — entirely
outside the application runtime. `execute_audit()` goes straight from `_reasoning_logger.truncate()`
into `self._finops.scan()` with no STS `get_caller_identity` call or health ping. A not-ready
container or bad credentials surfaces as a confusing failure deep inside the scan rather than a clear
preflight error.

**What's needed.** A cheap reachability probe against the active backend, shown as a readiness
indicator that gates the audit control.

**Implementation sketch.** LocalStack health GET for fixture/demo; STS `get-caller-identity` in `aws`
mode. Fold into a single environment-health panel.

**Open questions.** Block the audit on a red probe, or warn-and-allow?

---

## OBS-1 — Reasoning log is truncated at the start of every run

**Priority:** P2 · **Effort:** S · **Depends on:** — · **Type:** GAP

**Goal.** Agent reasoning should be retained across runs, or archived, not discarded.

**Current state & gap.** `execute_audit()` calls `self._reasoning_logger.truncate()` at the start of
each run (`orchestrator.py:496`). `truncate()` (`reasoning_logger.py:62–90`) only backs up the file
via rename if it exceeds a 10 MB rotation threshold — on every normal run (the common case) it opens
the log in `"w"` mode and wipes it with no backup at all. For a tool whose selling point is
explainable AI-driven decisions, losing the reasoning trail on every new audit undercuts auditability.

**What's needed.** Append per-run reasoning under a run ID, or rotate rather than truncate, so a given
audit's reasoning can be retrieved later.

**Implementation sketch.** Replace `truncate()` with run-scoped files or a rotating handler keyed by a
`run_id`. Apply SEC-2 redaction before persisting (reasoning can contain the same sensitive data).

**Open questions.** Retention window?

---

## TECH-1 — Subprocess timeouts are hardcoded

**Priority:** P2 · **Effort:** S · **Depends on:** — · **Type:** TECH-DEBT

**Goal.** Terraform/hook timeouts should be configurable.

**Current state & gap.** All timeouts are literal integers passed directly to `subprocess.run`:
`orchestrator.py:924,945` (init/apply in `approve()`, 120s each), `:1123` (rollback file validation,
180s), `:1168` (pre-remediation hook, 30s), `:1189` (60s wall-clock budget for hook validation),
`:1551,1574` (init/apply in rollback, 120s each), plus `core/llm_client.py:39` (LLM HTTP client,
30s). None are sourced from config or env. Large plans against real AWS will exceed 120s and be
killed mid-apply, which is both a failure and a partial-apply hazard.

**What's needed.** Make timeouts configurable (env/config) with sensible defaults; log when a timeout
fires.

**Implementation sketch.** Read timeouts from config; a killed `apply` should trigger a state-check on
resume.

**Open questions.** Sensible ceiling for real-AWS applies?

---

## DX-1 — No secret scanning / pre-commit hooks

**Priority:** P2 · **Effort:** S · **Depends on:** — · **Type:** TECH-DEBT

**Goal.** Prevent credentials from being committed now that the AWS provider is real.

**Current state & gap.** `.github/dependabot.yml` and `.github/workflows/ci.yml` exist (lint,
type-check, test), but there is no `.pre-commit-config.yaml` and no gitleaks/detect-secrets/
trufflehog step anywhere in CI. With a live boto3 provider and `accounts.example.json`, the risk of a
committed key is real — and this is a cloud-security tool, so a leak would be especially bad optics.

**What's needed.** `pre-commit` with `detect-secrets`/`gitleaks`, plus a CI secret-scan step.

**Implementation sketch.** Add `.pre-commit-config.yaml` and a `gitleaks` job in the existing CI
workflow; baseline the repo first to avoid a wall of historical false positives.

**Open questions.** None blocking.

---

## DOC-1 — No consolidated enterprise deployment guide

**Priority:** P2 · **Effort:** S · **Depends on:** SEC-1, INF-2 · **Type:** ENH

**Goal.** One guide for running Cloud Janitor as a shared service: deployment, identity, IAM scope, and
LLM egress posture.

**Current state & gap.** There is no `docs/` directory at all. A "Security Hardening" section exists
embedded in `README.md:392–420` (subprocess isolation, output redaction, hook validation, dashboard
binding, Docker socket risk), and `docker-compose.pro.yml` provides a docker-socket-proxy setup for
the Pro/ElastiCache image — but the shared-deployment story is scattered across these rather than
consolidated, and doesn't cover identity (SEC-3), remaining state (INF-1), IAM scope (INF-2), or
no-training posture (SEC-1) together.

**What's needed.** A `docs/deployment.md` covering EC2 setup, the auth/identity story, the
least-privilege IAM role, and BYO-endpoint / privacy posture, with a data-handling section.

**Implementation sketch.** Write after the referenced features land so it documents reality, not
intent.

**Open questions.** One reference architecture (single shared EC2) first, or document scaled too?

---

## BUG-2 — Single-account `findings_store.json` has no write lock

**Priority:** P2 · **Effort:** M · **Depends on:** — · **Type:** GAP, TECH-DEBT

**Goal.** Two audits running at once must not corrupt shared findings state in single-account mode.

**Current state & gap.** The multi-account orchestrator isolates findings stores per account
(`multi_account_orchestrator.py:231`, `findings_store_{account_id}.json`), but the single-account
default path (`core/paths.py:22`, `FINDINGS_STORE_PATH`) is one shared file written by
`finops_auditor.py:222–226`, `secops_guard.py:287–329`, and `orchestrator.py:1292–1305` — none of
which take a lock. `drift_detector.py` already uses `filelock.FileLock` for its own snapshot file, so
the pattern exists in the codebase but was never applied here. Two operators clicking Execute Audit
concurrently on a shared deployment can interleave writes and produce a torn store, making
`_validate_findings_store()` pass/fail nondeterministically.

**What's needed.** Per-run isolation (run-scoped store keyed by a run ID, matching the multi-account
pattern) or a file lock around the read-modify-write. Run-scoping is the better long-term shape and
composes with OBS-2.

**Implementation sketch.** Introduce a `run_id`; write `findings_store/<run_id>.json` with a "latest"
pointer for the UI. Short term, reuse the `filelock` pattern already used in `drift_detector.py`
around the store read-modify-write. Test two overlapping audits don't corrupt.

**Open questions.** Does the UI read the store at a fixed path? If so, run-scoping needs the pointer.

---

## SEC-5 — AWS-mode credential sourcing / read-write split undefined

**Priority:** P2 · **Effort:** M · **Depends on:** — · **Type:** ENH

**Goal.** Real-AWS runs should source credentials explicitly and separate read (audit) from write
(remediation).

**Current state & gap.** `aws_provider.py`'s `_make_client` (lines 25–33) builds a plain
`boto3.client()` with only `region_name`/`endpoint_url`, relying entirely on boto3's default/ambient
credential chain — no `profile_name`, `boto3.Session`, or `assume_role` usage exists anywhere in
`src/`. There is no defined posture for *how* credentials are supplied in a shared deployment, and
audit (read) and remediation (write) use the same path — a compromised auditor can mutate.

**What's needed.** Standardize on instance-role / assumed-role credentials, no long-lived keys in
config, and a separate narrowly-scoped role for write vs. read.

**Implementation sketch.** Document in DOC-1; enforce the two-role split in the provider/orchestrator
boundary. Pairs with INF-2.

**Open questions.** Confirm the two-role model is acceptable operationally for target customers.

---

## INF-2 — Least-privilege IAM policy for the live AWS provider

**Priority:** P2 · **Effort:** M · **Depends on:** — · **Type:** ENH

**Goal.** Ship a documented minimal IAM policy covering exactly the read (audit) and write
(remediation) actions the provider performs.

**Current state & gap.** No IAM policy JSON or Terraform IAM resource exists anywhere in the repo.
Required permissions exist only as scattered docstring comments in `aws_provider.py` (e.g. lines
63–66, 333–337, 531–535) — no consolidated least-privilege policy artifact for engineers to attach. A
cloud auditor asking for broad permissions is a hard sell and a real risk — the permission set is a
core enterprise blocker.

**What's needed.** Enumerate the API calls per agent, derive least-privilege policies (read for
FinOps/SecOps, scoped write for the Architect's remediation types), and publish them.

**Implementation sketch.** Derive the action list from the AWS provider's actual calls; emit a
ready-to-attach policy JSON plus a Terraform snippet. Split audit-only vs. remediation (pairs with
SEC-5).

**Open questions.** Which remediation action classes are in scope for the first real-AWS release?

---

## OBS-2 — Queryable / exportable audit trail for compliance

**Priority:** P2 · **Effort:** M · **Depends on:** INF-1 · **Type:** ENH

**Goal.** The audit trail should be queryable and exportable, not just an append-only text file plus a
lost-on-restart in-memory list.

**Current state & gap.** The persistent trail is `audit.log` (append-only via `AuditLogger`); the rich
`_audit_trail` is in-memory and lost on restart (INF-1). A compliance tool needs filter by
resource/actor/result/date and export (CSV/JSON) for evidence.

**What's needed.** Persist entries to the INF-1 store with query APIs and a UI view with filters and
export.

**Implementation sketch.** Write entries to the store in `_log_action`; add `query_audit(...)` and an
export path; build the Streamlit view.

**Open questions.** Tamper-evidence (hash-chained entries) now, or later?

---

## FEAT-1 — Surface `terraform plan` preview to the operator

**Priority:** P2 · **Effort:** M · **Depends on:** — · **Type:** ENH

**Goal.** Before approving, the operator should see the actual `terraform plan` diff, not just the
generated HCL.

**Current state & gap.** The operator only ever sees `RemediationPlan.remediation_hcl` before
approving. `approve()` runs `init` (`orchestrator.py:~921`) directly into `apply -auto-approve`
(`~942`) with no `terraform plan` or `plan -json` call anywhere — the only `"plan"` references are
`_log_action("plan", ...)` calls that just log HCL generation, not an actual plan preview. The
effective change is what Terraform plans, which can differ from a reading of raw HCL — so approval is
on trust.

**What's needed.** Capture `terraform plan` output and render a human-readable diff in the approval
panel; require it be shown before the approve control is active.

**Implementation sketch.** Run `plan -json` in the per-resource temp dir, render add/change/destroy per
resource, block approval until viewed. The `plan -json` output also feeds SEC-4's scope check.

**Open questions.** Raw plan text vs. structured diff — structured is friendlier, more work.

---

## FEAT-2 — Scheduled-scan run history and alerting

**Priority:** P2 · **Effort:** M · **Depends on:** — · **Type:** ENH

**Goal.** Scheduled scans should record run history and notify operators of new high-severity findings.

**Current state & gap.** `scheduler.py`'s `JanitorScheduler` runs APScheduler-based cron scans
(`JANITOR_SCHEDULE`, default `"0 6 * * *"`), but only tracks in-memory counters
(`_runs_completed`, `_last_run`) and writes a rotating log file — there is no persisted structured run
history and no notification path. A scheduled scan that finds a critical exposure sits silently in a
log file.

**What's needed.** Persist each scheduled run (via INF-1 / OBS-2) and add a notification channel
(email, Slack webhook) triggered on new HIGH/CRITICAL findings.

**Implementation sketch.** Hook the scheduler's post-run to write history and evaluate a
severity-threshold notifier that dedupes against the previous run.

**Open questions.** Notify every run or only on new/changed findings (avoid alert fatigue)?

---

## FEAT-3 — Real Cost Explorer cost data (replace fixture values)

**Priority:** P3 · **Effort:** M · **Depends on:** — · **Type:** ENH

**Goal.** Savings and cost figures should come from real Cost Explorer data, not static pricing
constants, now that the AWS provider is live.

**Current state & gap.** `aws_provider.py`'s `get_cost_data()` computes cost from hardcoded pricing
constants applied to resource size/type (e.g. `price_per_gb = 0.10 if vtype == "gp2" else 0.08`, a
flat `cost_map` for node types) — synthetic pricing math over live resource inventory, not actual AWS
billing data. No `ce:GetCostAndUsage` / Cost Explorer client exists anywhere. `fixture_provider.py`
returns bundled static JSON for demo mode, as documented.

**What's needed.** Pull real cost/usage from Cost Explorer for flagged resources and feed the savings
tracker with actuals.

**Implementation sketch.** Extend the AWS provider with a cost lookup; map to the existing findings
cost field. Cache aggressively — Cost Explorer has latency and per-request cost.

**Open questions.** Attribute at resource granularity, or tag/service level where per-resource cost
isn't available?

---

## FEAT-4 — GCP / Azure backend implementations

**Priority:** P3 · **Effort:** L · **Depends on:** — · **Type:** ENH

**Goal.** Make the multi-cloud stubs real so `JANITOR_BACKEND=gcp|azure` audit those clouds.

**Current state & gap.** `gcp_provider.py` and `azure_provider.py` each implement exactly 3 methods
(`get_cost_data`, `get_security_data`, `check_dependencies`), each with a single
`raise NotImplementedError(...)`. Multi-cloud is a future differentiator, not near-term critical.

**What's needed.** Implement the `CloudProvider` interface for GCP and Azure equivalents of the covered
resource types, following the pattern the AWS provider established.

**Implementation sketch.** One provider per cloud under `mcp_server/backends/`, reusing the agent
contract; defer until the AWS path is fully hardened (SEC-5 / INF-2).

**Open questions.** Real demand for multi-cloud from target customers, or is AWS depth the better
investment first?

---

## Execution roadmap (dependency-respecting order)

1. **Close the trust gaps (P1):** SEC-3 (real approver) and BUG-1 (savings on rollback) are quick and
   high-value. Then SEC-2 (input-side redaction) and SEC-1 (real no-training posture) go together —
   they're the enterprise-credibility pair. SEC-4 (policy + scope gate on HCL) removes the biggest
   remaining blast-radius risk.
2. **Finish multi-user (P1→P2):** INF-1 (persist the remaining state) completes what the gate-store
   started and is the keystone for OBS-2.
3. **Harden ops (P2):** BUG-3 health preflight, OBS-1 reasoning retention, TECH-1 timeouts, DX-1 secret
   scanning — all small. Then BUG-2 (store lock), OBS-2, FEAT-1 (plan preview, also feeds SEC-4),
   FEAT-2 (schedule history/alerting).
4. **Lock down real AWS (P2):** SEC-5 (creds / read-write split) and INF-2 (least-priv IAM) now that
   the provider is live, then DOC-1 documents the whole shared deployment.
5. **Later (P3):** FEAT-3 (real cost data), FEAT-4 (multi-cloud).
