# Requirements Document

## Introduction

This specification covers FEAT-1 (structured Terraform plan preview) from the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`). It is one of six independently-shippable specs split out of the original bundled `phase3-ops-hardening` spec, per a principal-engineer design review (`.kiro/2026-07-08-phase-plans-audit.md`).

**Dependency on phase1-trust-hardening:** this spec builds directly on top of phase1-trust-hardening's `_check_plan_scope` design (SEC-4, phase1's Requirement 7), which already inserts a `terraform plan -json` call between `init` and `apply` inside `Orchestrator.approve()` and `_handle_confirm_rollback()`. This spec extends that same call rather than adding a second, redundant `plan` invocation — `preview_plan()`'s cache is consumed directly by `approve()`'s scope-check step. If phase1 has not landed yet when this spec is implemented, `preview_plan()` runs its own single `init`/`plan -json` call and stubs the scope-check portion (see the integration note under Requirement 1), then wires in the real `_check_plan_scope()` call once phase1 ships — it must not add a second `plan` call to retrofit this later.

**This spec has no dependency on phase2-persistent-state**, and no dependency on the other four phase3 splits (secret scanning, preflight/timeouts, run-scoped artifacts, scheduled alerting, audit query).

Full technical design is in `design.md` in this folder.

## Glossary

- **Orchestrator**: The central coordination module (`src/cloud_janitor/orchestrator/orchestrator.py`) that sequences agent execution, manages approval gates, and invokes Terraform operations.
- **Plan_JSON**: The parsed output of `terraform plan -json`, containing a `resource_changes` array. Definition reused from `phase1-trust-hardening/requirements.md`.
- **PlanPreview**: A structured, human-readable summary of a Plan_JSON's resource changes, partitioned into creates/updates/deletes/replacements.
- **Scope_Check**: The `_check_plan_scope` validation designed in phase1-trust-hardening (SEC-4), which this spec's work reuses rather than duplicates.

## Requirements

### Requirement 1: Structured Terraform Plan Preview

**User Story:** As an operator approving a remediation, I want to see a structured, human-readable summary of what `terraform plan` will actually do before I approve it, so that my approval is based on the real effective change rather than a reading of LLM-generated HCL that may not match what Terraform will do.

**Integration note:** phase1-trust-hardening's Requirement 7 (SEC-4) already inserts a `terraform plan -json` call between `init` and `apply` inside `Orchestrator.approve()` and `_handle_confirm_rollback()`, feeding `_check_plan_scope()`. This requirement extends that same `plan -json` invocation to also produce an operator-facing structured summary — it explicitly does not add a second, independent `plan` call. If phase1's scope-check work has not yet landed when this spec is implemented, `preview_plan()` (criterion 3) SHALL still run its own single `init`/`plan -json` call and MAY defer the scope-check portion until phase1 ships, but MUST NOT duplicate a `plan` call once phase1 exists.

#### Acceptance Criteria

1. THE codebase SHALL provide a `summarize_plan(plan_json: dict) -> PlanPreview` function that partitions every entry in `plan_json["resource_changes"]` where `mode == "managed"` and `change.actions != ["no-op"]` into exactly one of `creates`, `updates`, `deletes`, or `replacements`, based on Terraform's action-set semantics: `["create"]` → create, `["update"]` → update, `["delete"]` → delete, `["create","delete"]` or `["delete","create"]` → replace.
2. FOR EACH resource change categorized as `update` or `replace`, `summarize_plan()` SHALL include the resource address, resource type, and the list of top-level attribute keys whose `before`/`after` values differ.
3. THE Orchestrator SHALL provide a `preview_plan(resource_id: str) -> PlanPreviewResult` method that runs `terraform init` and `terraform plan -json` in an isolated temporary directory for the given resource (reusing `approve()`'s existing directory-construction logic — HCL write, provider config), computes both `_check_plan_scope()`'s result (phase1) and `summarize_plan()`'s result from that single `plan -json` output, and caches the temporary directory and both results in memory, keyed by `resource_id`, for up to 60 seconds.
4. WHEN `Orchestrator.approve(resource_id)` is called AND a non-expired cached preview from criterion 3 exists for that `resource_id`, THE Orchestrator SHALL reuse the cached `plan -json` output, the cached temporary directory, and the cached scope-check result rather than re-running `terraform init`/`terraform plan`.
5. WHEN `Orchestrator.approve(resource_id)` is called AND no non-expired cached preview exists for that `resource_id`, THE Orchestrator SHALL run `init`/`plan -json`/scope-check inline exactly as phase1-trust-hardening designed, with no behavior change.
6. THE Orchestrator SHALL delete a cached preview's temporary directory once it has been consumed by `approve()`, once it expires (60 seconds elapsed), or when a new preview is requested for the same `resource_id` (superseding and cleaning up the prior one).
7. THE Streamlit_UI SHALL render a "Preview Plan" control per pending remediation that calls `preview_plan()` and displays the structured creates/updates/deletes/replacements summary, and SHALL only render that resource's "Approve" control after the operator has triggered a preview for it in the current session (tracked in Streamlit session state). This is a UX guardrail against approving without looking, not a security boundary — the scope check inside `approve()` is the actual security control (phase1-trust-hardening Requirement 7) and runs unconditionally regardless of whether a preview occurred.
8. **Cache freshness rationale (design review correction):** the cache TTL is 60 seconds, not the 10 minutes originally proposed. The design's own justification for skipping a second `plan` call is that "the plan is deterministic between calls seconds apart" — a cache that can be trusted by the scope check (criterion 4) for up to 10 minutes contradicts that rationale, since infrastructure state can plausibly change within that window while the scope check — explicitly "the actual security control" per criterion 7 — would still evaluate a stale plan as if it were current. 60 seconds keeps the cache useful for the realistic "operator clicks Preview, reads it, clicks Approve" workflow while bounding the staleness window to something consistent with the "seconds apart" justification.
