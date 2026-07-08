# Implementation Plan: Secret Scanning (DX-1)

## Overview

This plan implements Requirement 1 (secret scanning in pre-commit and CI), derived from backlog issue DX-1. This is a standalone, zero-dependency spec — it should be implemented and shipped first among the six specs split out of the original `phase3-ops-hardening` bundle, ahead of any other work, since it closes a live credential-leak risk and has no coupling to any other in-flight spec.

## Tasks

- [ ] 1. Implement secret scanning
  - [ ] 1.1 Add `.pre-commit-config.yaml` with the `gitleaks` hook
    - _Requirements: 1.1_

  - [ ] 1.2 Add the `secret-scan` job to `.github/workflows/ci.yml`
    - `fetch-depth: 0` checkout, `gitleaks/gitleaks-action@v2`; add `secret-scan` to the `build` job's `needs` list
    - _Requirements: 1.2, 1.3_

  - [ ] 1.3 Run `gitleaks detect` against the full repository history and triage findings
    - If any confirmed false positive is found (e.g. `accounts.example.json` placeholder values), commit a `.gitleaksignore` documenting each by fingerprint
    - This is a one-time, implementation-time investigation step — its outcome cannot be predicted from this design document
    - _Requirements: 1.4_

  - [ ] 1.4 Document `pre-commit` setup and baseline regeneration in `README.md`
    - _Requirements: 1.5_

  - [ ] 1.5 Write a CI smoke test that a fixture commit containing a fake high-confidence secret pattern is rejected (`tests/test_secret_scan_config.py`)
    - Assert `.pre-commit-config.yaml` and the CI workflow reference `gitleaks`; assert the CI checkout step uses `fetch-depth: 0`; assert `build`'s `needs` list includes `secret-scan`; assert `README.md` documents `pre-commit install`
    - Optionally shell out to a local `gitleaks detect` against a throwaway fixture file containing a synthetic AWS-key-shaped string, if `gitleaks` is available in the test environment (skip, do not fail, otherwise)
    - _Requirements: 1.1, 1.2, 1.3, 1.5_

- [ ] 2. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests are not applicable here (see design.md), but unit/config tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- `.gitleaksignore` (task 1.3) can only be produced by actually running `gitleaks detect` against the repository — its contents are not predictable from this design document and must be generated at implementation time
- This spec has no dependency on phase1-trust-hardening, phase2-persistent-state, or any of the other five phase3 splits — it can be implemented and merged independently, in any order relative to them

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3"] },
    { "id": 3, "tasks": ["1.4"] },
    { "id": 4, "tasks": ["1.5"] }
  ]
}
```
