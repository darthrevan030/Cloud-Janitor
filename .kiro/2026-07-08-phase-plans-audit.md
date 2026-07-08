# Principal Engineer Audit: Phase 1-5 Implementation Specs

**Date:** 2026-07-08
**Scope:** `.kiro/specs/phase1-trust-hardening/`, `phase2-persistent-state/`, `phase3-ops-hardening/`, `phase4-aws-lockdown/`, `phase5-future-enhancements/` — the requirements/design/tasks specs derived from `.kiro/2026-07-08-product-backlog.md`.
**Method:** One independent skeptical review per phase, each verifying every non-trivial design claim against the actual current source in this repo (not against the spec's own assertions), plus a cross-phase consistency pass. Nothing described below has been implemented — this is a pre-implementation gate.

## Verdict: none of the 5 phases are ready to hand to an implementer as written

Every phase has at least one CRITICAL finding — a concrete, verifiable defect in the design's own reference code or reasoning, not a stylistic nitpick or a hypothetical edge case. Several are self-contradictions: a spec's own test would fail against its own reference implementation, or two halves of the same design disagree with each other. The good news: none require re-architecting. Every finding below traces to a specific line in a specific file and has a concrete fix. This is exactly what a design-review gate is for — better to find these now than three tasks into implementation.

**Total findings: 8 CRITICAL, 12 HIGH, 20 MEDIUM, 9 LOW** across the 5 phases, plus 1 cross-phase inconsistency (found and fixed during this audit — see below).

---

## Cross-phase finding (found and fixed during this audit)

**`self.current_run_id` assignment point conflicted between Phase 2 and Phase 3.** Phase 2's original design assigned `self.current_run_id = uuid.uuid4().hex` at the point plans are persisted (after the Remediation Architect step, late in `execute_audit()`). Phase 3's OBS-1 (run-scoped reasoning log) needs the run ID available *before* the first agent runs, so the reasoning log can be scoped from the very start of the scan. Phase 2's design incorrectly claimed "no dependency ordering problem" for whichever phase lands first — that was wrong; the assignment *point*, not just the generator function, needed to move.

**Fix applied:** `.kiro/specs/phase2-persistent-state/design.md` and `tasks.md` now assign `self.current_run_id` as the first line of `execute_audit()`, before Step 1. The later `replace_plans()` call reuses that value instead of generating a second one. This makes Phase 2 self-sufficient regardless of implementation order, and Phase 3 now only needs to swap the generator function (`uuid.uuid4().hex` → `generate_run_id()`), not relocate the call site.

---

## Phase 1: Trust Hardening (SEC-3, BUG-1, SEC-1, SEC-2, SEC-4)

**2 CRITICAL, 3 HIGH, 5 MEDIUM, 2 LOW.**

### CRITICAL
1. **The SEC-4 scope-check allowlist only covers the *remediation* direction of each Terraform template, not rollback — and rollback is wired to use the same check.** Rolling back a security-group remediation restores `cidr_blocks = ["0.0.0.0/0"]` by design (`remediation_architect.py:378-398`), but the scope check's own rule explicitly rejects any plan widening ingress back to `0.0.0.0/0`. Rollback for security groups — one of the most common finding types — would be permanently blocked by the very control meant to make rollback safer. The EBS/ElastiCache rollback templates (`aws_ebs_volume.restore_*`, `aws_elasticache_cluster.restore_*`) also don't match either direction's address-prefix allowlist. **Fix:** add a remediate/rollback axis to the allowlist, with the security-group rollback rule explicitly permitting the `0.0.0.0/0` restore.
2. **BUG-1's savings-reversal logic re-derives the refund from the live findings store instead of the immutable ledger entry, and can permanently block reversal for a resource remediated more than once.** `record_rollback()` recomputes cost via `_compute_monthly_savings()`, which reads the *current* `findings_store.json` — overwritten by every subsequent scan — so a delayed rollback can silently refund `$0`. Separately, `already_reversed` is tracked by bare `resource_id` (not `run_id`), so if the same resource is remediated in two different audit cycles, reversing the first poisons reversal for the second forever. **Fix:** negate the stored `monthly_savings_added` from the matched run directly (don't re-derive from live data); track reversal state by `run_id`, not `resource_id`.

### HIGH (summary — see `.kiro/specs/phase1-trust-hardening/` review notes for full detail)
- The resolved actor from `_resolve_actor_or_block()` has no defined path into `_log_action()`/`_run_post_remediation_hook()` other than mutating shared `self.approver` state — a concurrency hazard under any concurrent use of one Orchestrator instance. Thread `actor` explicitly as a parameter instead.
- The STS call in `resolve_actor()` has no timeout hardening (unlike `aws_provider.py`'s own precedent of a 5s/10s `Config` for non-critical AWS calls) and runs even in fixture mode — every approve/rollback could stall for boto3's default timeout with no path to AWS.
- Real-vs-sandbox classification keys off *any* non-empty `AWS_ENDPOINT_URL`, not specifically LocalStack (unlike `aws_provider.py`'s own `"localhost" in endpoint_url` check) — a real deployment using a custom AWS endpoint (FIPS, PrivateLink) would be silently downgraded to lenient sandbox trust, defeating the ticket's purpose.

### Notable MEDIUM
- `ActorResolution.verified` is computed but never persisted — the audit trail can't distinguish an STS-verified actor from a sandbox fallback, exactly where a reviewer would want to know.
- SEC-2's placeholder-based redaction has no defined behavior if the LLM never echoes a placeholder back verbatim (common with cheap/small models) — rehydration silently becomes a no-op with no error surfaced.
- SEC-1's attestation is explicitly unenforceable (the design says so itself) — call it a compliance checkbox, not a technical control, in the requirements language too.

---

## Phase 2: Persistent State (INF-1)

**1 CRITICAL, 3 HIGH, 5 MEDIUM, 5 LOW.**

### CRITICAL
1. **The corruption guard will misclassify ordinary lock contention as fatal corruption, bricking the Orchestrator.** `StateStore.__init__` catches `sqlite3.DatabaseError` and raises `StateStoreCorruptedError` on any hit — but `sqlite3.OperationalError` (e.g. "database is locked", exactly the transient condition WAL mode + `busy_timeout` were added to tolerate) is a subclass of `DatabaseError`. A transient lock during startup would be treated as unrecoverable corruption and refuse to start — undermining the concurrency-safety feature this same design just added. **Fix:** narrow the except clause to exclude `OperationalError` (or match on genuine corruption message text like "malformed"/"not a database"); add a test that opens a valid-but-momentarily-locked file and asserts construction succeeds.

### HIGH
- Requirement 5.5 ("surface a non-fatal warning on plan-persistence failure") is only gestured at in a code comment — no field like `AuditResult.warnings` actually exists in the design's own snippets. An implementer following the design literally would fail this acceptance criterion.
- Requirement 6.5 (document that WAL mode needs POSIX-style locking, isn't guaranteed on network filesystems) is simply absent from `design.md` — not under-explained, just missing.
- Requirement 6.3 ("no write shall be silently lost") is weaker than stated: under contention exceeding `busy_timeout` (5000ms), writes to the audit trail *do* get dropped with only a logged warning — for the specific table framed as compliance-critical. Either add retry/backoff for audit-trail writes specifically, or narrow the requirement's wording to match the documented (and accepted) degraded behavior.

### Notable MEDIUM
- The connection is never closed on a failed construction (leaks a handle every time a broken `state.db` is hit — compounds the lock-contention problem in finding #1).
- `get_audit_trail()` fails to an empty list indistinguishable from "no history exists," ignoring the in-memory `_audit_trail` cache the design deliberately keeps around as a fallback.
- The documented recovery runbook ("delete the file, restart") omits the WAL sidecar files (`state.db-wal`, `state.db-shm`) — an operator following it literally could reintroduce the problem.
- `PRAGMA user_version` forward-compatibility tolerance contradicts the phase's own explicit "no migration path" stance — dead complexity that should either be removed or made to fail closed.

What checked out cleanly: the `plans` table's INSERT columns match its DDL exactly and in order; `get_plan()`'s round trip is field-for-field correct including nested `DependencyReport` data; the task dependency graph has no forward references; the corruption test actually corrupts a real file rather than mocking one.

---

## Phase 3: Ops Hardening (BUG-3, OBS-1, TECH-1, DX-1, BUG-2, OBS-2, FEAT-1, FEAT-2)

**2 CRITICAL, 5 HIGH, 4 MEDIUM, 2 LOW.** Also: **this phase should be split.**

### CRITICAL
1. **The scheduler history/notification wiring will crash with `UnboundLocalError` on the exact exception path it's meant to make robust.** Current `scheduler.py` only binds `result` inside its `try:` block, precisely so the `except`/`finally` path never touches it. The design's new snapshot/diff logic references `result` unconditionally — if `execute_audit()` itself raises (the case the `except` clause exists to catch), the new code crashes instead of recording history. **Fix:** pre-initialize a safe default (e.g. `findings = []`) alongside the existing `total_findings`/`total_waste` defaults.
2. **The health-preflight's own reference implementation doesn't apply the 5-second timeout its own requirement mandates.** `_check_sts()` reuses `aws_provider._make_client()`, which only ever sets `region_name`/`endpoint_url` — no `Config`/timeout parameter exists to pass. Against a genuinely unreachable STS endpoint, the "fail fast" preflight would instead hang for boto3's default (much longer) timeout — the opposite of BUG-3's purpose. **Fix:** add explicit `botocore.config.Config(connect_timeout=5, read_timeout=5)`, either via a new parameter on `_make_client()` or a dedicated client constructor for the health check.

### HIGH
- A genuine positional conflict with Phase 2's `current_run_id` (see "Cross-phase finding" above — now fixed).
- The requirements' "BUG-2 has no cross-phase dependency" framing contradicts the same document's own task list, which anchors BUG-2's findings-store wiring to OBS-1's `run_id`-generation task landing first. This is same-phase coupling that should be stated plainly, not implied away.
- FEAT-1's plan-preview cache has a 10-minute TTL, but the design's own justification for skipping a second `plan` call says "the plan is deterministic between calls seconds apart" — a 10-minute-old cached plan can be evaluated by the scope check (explicitly "the actual security control," per the requirement) against infrastructure state that's since changed. Either shrink the TTL to match the stated rationale, or add a cheap freshness re-check before trusting a stale result.
- The health-check failure classification collapses "backend unreachable" and "credentials invalid" into the same `error_category="io_failure"` — nothing downstream can distinguish "start LocalStack" from "fix your AWS credentials."
- Verified via grep: `_run_pre_remediation_hook_full()` — the method TECH-1's proposed 60-second-timeout config variable belongs to — is never actually called from any production code path (`execute_audit()` calls a *different* method). Making its timeout configurable adds a public env var with no effect on any real path unless wiring that method in is added as a prerequisite.

### Scope/bundling finding
DX-1 (gitleaks) and FEAT-2 (scheduler history/Slack) have zero technical coupling to the run-scoping work (OBS-1/BUG-2) or to OBS-2 (hard-gated on Phase 2, which doesn't exist yet). Bundling all 8 tickets into one 14-task plan means DX-1 — trivial and time-pressured, since it closes a credential-leak risk on a live AWS provider — ships no faster than the largest, most speculative item in the phase. **Recommend re-cutting into at least: (a) DX-1 alone, ship first; (b) BUG-3 + TECH-1, independent hardening; (c) OBS-1 + BUG-2, the genuinely coupled run-scoping pair; (d) FEAT-1, blocked on Phase 1; (e) FEAT-2, fully independent; (f) OBS-2, correctly gated on Phase 2.**

What checked out cleanly: most TECH-1 and BUG-2 file/line citations are exact; OBS-2's SQL filter construction is genuinely parameterized (no injection risk); the `core/run_context.py` module itself is cleanly reusable.

---

## Phase 4: AWS Lockdown (SEC-5, INF-2, DOC-1)

**2 CRITICAL, 4 HIGH, 3 MEDIUM, 2 LOW.** This audit fetched AWS's live Service Authorization Reference to fact-check IAM claims.

### CRITICAL
1. **The remediation IAM policy is very likely missing a required resource type, and would break real (non-LocalStack) security-group remediation** — the one template most people will exercise first. AWS's own IAM documentation requires both `security-group` and `security-group-rule` ARN patterns for `Authorize/RevokeSecurityGroupIngress`; the design's JSON only includes `security-group`. Since LocalStack doesn't enforce IAM, this would pass every test in this repo and only fail the first time it runs against a real account. **Fix:** add `arn:aws:ec2:*:*:security-group-rule/*` to that statement before implementation, and add a real-AWS (not LocalStack) validation step.
2. **The design's own derivation table contradicts its own JSON policy artifact — and the test built to check them would fail against its own design.** The table says the security-group template needs `ec2:CreateTags`; the JSON grants it to EBS resources only, not security groups. Digging into why it was listed at all surfaced a second, independent bug: `_rollback_security_group`'s generated HCL includes a `tags` block on an `aws_security_group_rule` resource, but that classic resource type doesn't support tags in the Terraform AWS provider (only the newer split ingress/egress rule resources do) — meaning the rollback HCL itself may fail Terraform schema validation, unrelated to IAM. **Fix:** reconcile the table vs. JSON; separately, fix or flag the `tags` block bug in `remediation_architect.py`.

### HIGH
- INF-2's central "verified against AWS, not assumed" claim is factually wrong for ElastiCache: AWS's IAM docs *do* support resource-level scoping for `DescribeCacheClusters`/`DescribeReplicationGroups`/`DescribeSnapshots`/`ListTagsForResource`, contradicting the design's blanket "unscopable" claim (correct for EC2 Describe*/CloudWatch/STS, wrong for ElastiCache). Using `Resource: "*"` may still be the right call operationally, but the stated justification needs correcting — a security reviewer will fact-check this claim first.
- `ec2:DeleteSnapshot` is granted based on a `terraform destroy` code path that doesn't exist anywhere in this codebase (grep confirms zero matches) — scope creep against the requirement's own "derive exclusively from emitted HCL" mandate.
- The credential-injection wiring instructions only explicitly mention replacing the `init` subprocess's environment, not `apply`'s (each has its own separate inline `env=` construction today) — an implementer following the prose literally could leave the actual mutating call on ambient credentials, silently defeating SEC-5's read/write split. The design needs to show a diff for all four call sites, not describe a new helper function in isolation.
- DOC-1's "designed, pending implementation" wording for Phase 1 features has no follow-up task anywhere to fix it once Phase 1 actually ships — confirmed Phase 1 is currently 0/37 tasks complete, so this is a live, not hypothetical, stale-docs trap.

### Notable MEDIUM
- The properties meant to catch table/JSON mismatches were authored by the same process that produced the mismatch — they cross-check two hand-written artifacts against each other, not against real HCL output or AWS docs. Recommend a test that actually renders `remediation_architect.py`'s templates and parses the real resource types out, plus a real-account `iam:SimulateCustomPolicy` gate before merge (LocalStack can't validate IAM).

What checked out cleanly: the *read*-side policy (Requirement 3) is fully accurate against `aws_provider.py`; the EC2/CloudWatch/STS "unscopable" claims are correct.

---

## Phase 5: Future Enhancements (FEAT-3, FEAT-4)

**1 CRITICAL, 4 HIGH, 4 MEDIUM, 2 LOW.**

### CRITICAL
**FEAT-3's Cost Explorer date range doesn't align to calendar-month boundaries, which AWS's API requires for `Granularity="MONTHLY"` — on all but one day per month, every real-cost lookup would raise a validation error and silently fall through to the same pricing-estimate heuristic the feature exists to replace.** Nothing surfaces this as a failure — `cost_data_source` would just read `"estimated"` almost every time, looking like a working feature while providing zero actual improvement. **Fix:** align query start/end to the 1st of consecutive months.

### HIGH (this phase's two headline "zero code change" claims are both false)
- **`SecOpsGuard._determine_resource_type()` string-matches AWS-shaped resource IDs (`vol-*`, `cache-*`) — GCP/Azure disk and cache IDs won't match, so `check_encryption()` silently drops 100% of GCP/Azure encryption findings** before they ever become findings, directly contradicting the design's claim that SecOpsGuard needs no changes.
- **`RemediationArchitect` generates AWS-only Terraform (`aws_ebs_snapshot`, `aws_security_group_rule`, hardcoded `aws ec2 delete-volume` shell commands) for the same `resource_type` strings GCP/Azure findings would reuse** — feeding a GCP/Azure finding through the unmodified pipeline produces non-functional or actively wrong HCL. Neither `design.md` nor `tasks.md` mentions `remediation_architect.py` anywhere, despite the design's own resource-mapping table asserting cross-cloud compatibility "with zero code changes." **This phase only designed GCP/Azure audit/detection, not remediation — it should either scope remediation explicitly (new HCL generators, new CLI verbs, provider-block wiring — comparable to building the AWS remediation module twice more) or say plainly that GCP/Azure findings will be report-only for the foreseeable future.** "Effort: L" understates the true scope if remediation parity is ever expected.
- The tag-based Cost Explorer tier queries a `"ResourceId"` cost-allocation tag that nothing in this codebase creates or activates — in practice this tier is unreachable for virtually every real deployment and should be documented as opportunistic-best-effort, not a normally-hit fallback.
- FEAT-4's tests are 100% SDK-mocked with no LocalStack-equivalent and no live-account smoke test proposed — a real risk given GCP's SDK returns typed protobuf objects, not boto3-style dicts, and a mock built around the wrong shape would pass tests while failing on the first real call.

### Notable MEDIUM
- The Cost Explorer cache key embeds "today's date," so the documented 24-hour TTL config knob is decorative across day boundaries — the cache only ever helps same-day repeat scans.
- `SecOpsGuard.SENSITIVE_PORTS` is missing 3389/8080/8443 that a separate port table (which this phase explicitly asks GCP/Azure to share "for cross-provider consistency") already includes — a pre-existing AWS-path bug this phase would inherit and compound across two more providers.
- The GCP-first sequencing call is justified solely by credential/SDK ergonomics, with no explicit flag that customer/market fit (unknowable from this document) might be the more consequential factor a product stakeholder should weigh in on.

What checked out cleanly: FinOpsAuditor's metadata pass-through for the new `cost_data_source` field genuinely requires no changes; credential-failure paths (ImportError/RuntimeError) are sound; the LocalStack bypass logic is correct.

---

## Recommended path forward

1. **Fix the 8 CRITICAL findings first** — every one is a concrete code-level defect with a stated fix, not a design philosophy disagreement. None require re-architecting a phase.
2. **Re-cut Phase 3** into the independently-shippable pieces identified above before writing any code against it — as a single 8-issue bundle it obscures that DX-1 and FEAT-2 have zero coupling to the rest, while OBS-1/BUG-2 are genuinely inseparable.
3. **Re-scope Phase 5's FEAT-4** explicitly as audit/detection-only unless remediation-side work (new HCL templates per cloud) is added to its plan and effort estimate — the current "zero code change" framing is the single most consequential inaccuracy across all 5 phases, since it would ship a feature that silently drops the majority of its own findings.
4. **Re-derive Phase 4's remediation IAM policy** by actually exercising `RemediationArchitect`'s template methods (or running a real/simulated AWS policy check) rather than hand-transcribing a table — this is the one place where a security-focused ticket's own deliverable would fail against a real AWS account.
5. Phases 1 and 2 need targeted fixes (2-3 CRITICAL/HIGH items each) but are structurally closer to implementation-ready than 3-5.
