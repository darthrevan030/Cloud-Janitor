# Implementation Plan: Structured Terraform Plan Preview (FEAT-1)

## Overview

This plan implements Requirement 1 (structured Terraform plan preview), derived from backlog issue FEAT-1. This spec depends on phase1-trust-hardening's `_check_plan_scope` design; if phase1 has not landed yet, implement `preview_plan()`'s own single `init`/`plan -json` call and stub the scope-check portion (see design.md's integration note), then wire in the real `_check_plan_scope` call once phase1 ships — do not implement a second `plan` call to work around the ordering. This spec has no dependency on phase2-persistent-state or on any of the other five phase3 splits.

## Tasks

- [ ] 1. Create `core/plan_diff.py`
  - [ ] 1.1 Implement `PlanChange`, `PlanPreview` dataclasses and `summarize_plan(plan_json)` per the design's action-bucket partition
    - _Requirements: 1.1, 1.2_

  - [ ] 1.2 Write property test for plan summary partition completeness
    - **Property 1: Plan Summary Partition Completeness**
    - **Validates: Requirement 1.1**

  - [ ] 1.3 Write unit tests for `summarize_plan()` (`tests/test_plan_diff.py`)
    - One test per action bucket (create, update, delete, replace via `["create","delete"]` and `["delete","create"]`) using realistic `resource_changes` fixtures
    - `mode == "data"` entries and `actions == ["no-op"]` entries excluded from all buckets
    - Unrecognized action combination is skipped without raising
    - Update/replace entries carry the correct `changed_keys` (attribute-level before/after diff)
    - _Requirements: 1.1, 1.2_

- [ ] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Implement `Orchestrator.preview_plan()` and cache wiring
  - [ ] 3.1 Extract `approve()`'s inline apply-directory construction (HCL write + provider config) into a reusable `_build_apply_dir(plan)` helper
    - Refactor only — no behavior change to the existing `approve()` path
    - _Requirements: 1.3_

  - [ ] 3.2 Implement `Orchestrator.preview_plan(resource_id)` and the `_plan_previews` cache
    - Run `init` + `plan -json` in a `_build_apply_dir()` result; compute `_check_plan_scope(plan_json, resource_id, plan.finding, flow="remediate")` (phase1) and `summarize_plan()` from the same `plan_json`; cache `_CachedPreview` (including a `flow="remediate"` field) keyed by `resource_id` with a **60-second** TTL (`PREVIEW_TTL_SECONDS = 60` — reduced from an originally-proposed 600s during design review; see design.md's "Key Architectural Decisions" for the reasoning); implement `_evict_expired_previews()`
    - If phase1's `_check_plan_scope` does not exist yet in the codebase at implementation time (`NameError`), or exists but rejects the `flow=` argument due to an incompatible signature (`TypeError`), stub `scope_ok=True, scope_reason="scope check pending phase1"` and add a `# TODO(phase1)` marker — catch `(NameError, TypeError)` around the call rather than `NameError` alone, since a signature mismatch against phase1's real, already-shipped `_check_plan_scope(plan_json, resource_id, finding, flow)` (no default for `flow`) raises `TypeError`, not `NameError`. Do not add a second `plan` invocation later to retrofit this
    - Define `PlanPreviewResult` as a `@dataclass` (`success`, `resource_id`, `error`, `preview`, `scope_ok`, `scope_reason`, `flow`) per design.md's Components section — it was previously only implied by call-site usage
    - _Requirements: 1.3, 1.8_

  - [ ] 3.3 Wire cache reuse into `approve()`
    - On a non-expired cache hit for `resource_id`, reuse `apply_dir`/`scope_ok`/`scope_reason` and skip straight to the apply step; on a miss or expired entry, fall back to phase1's inline `init`/`plan -json`/scope-check flow unchanged; discard/clean up an expired cache entry's `apply_dir` before rebuilding
    - _Requirements: 1.4, 1.5, 1.6_

  - [ ] 3.4 Write property tests for preview cache reuse and expiry
    - **Property 2: Preview Cache Reuse Invariant**
    - **Property 3: Preview Cache Expiry Invariant**
    - **Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.8**

  - [ ] 3.5 Write unit tests for preview/approve integration (`tests/test_orchestrator_preview.py`)
    - `preview_plan()` then `approve()` within the 60s TTL results in exactly one `plan -json` subprocess call total (mock and count `subprocess.run` invocations with `"plan"` in argv)
    - `approve()` without a prior `preview_plan()` call behaves identically to phase1's design (no regression)
    - Expired cache entry (> 60s old) is discarded and its `apply_dir` removed; `approve()` falls back to the inline path
    - `preview_plan()` called before phase1's `_check_plan_scope` exists stubs `scope_ok=True` without raising `NameError`/`ImportError`
    - `preview_plan()` called against a `_check_plan_scope` that exists but has an incompatible signature (rejects the `flow=` keyword, simulated `TypeError`) also stubs `scope_ok=True` without raising — regression test for the crash this fix addresses
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.8_

  - [ ] 3.6 Add "Preview Plan" / gated "Approve" controls to the Streamlit UI
    - Render `PlanPreview` as Creates/Updates/Deletes/Replacements tables; only show "Approve" for a resource once `preview_plan()` has succeeded for it in the current session (`st.session_state.previewed_resources`)
    - _Requirements: 1.7_

- [ ] 4. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- This spec assumes phase1-trust-hardening's `_check_plan_scope` exists; see the stub fallback described in task 3.2 if implementation order differs
- This spec has no dependency on phase2-persistent-state or on any of the other five phase3 splits (secret scanning, preflight/timeouts, run-scoped artifacts, scheduled alerting, audit query)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "3.1"] },
    { "id": 2, "tasks": ["3.2"] },
    { "id": 3, "tasks": ["3.3"] },
    { "id": 4, "tasks": ["3.4", "3.5"] },
    { "id": 5, "tasks": ["3.6"] }
  ]
}
```
