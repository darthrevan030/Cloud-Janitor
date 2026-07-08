# Requirements Document

## Introduction

This specification covers DX-1 from the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`): secret scanning in pre-commit and CI. It is one of six independently-shippable specs split out of the original bundled `phase3-ops-hardening` spec, per a principal-engineer design review (`.kiro/2026-07-08-phase-plans-audit.md`) that found the original 8-issue bundle had little real coupling between its constituent tickets.

**This spec has zero dependencies on any other phase or spec.** It touches only repository configuration (`.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `README.md`) and is the smallest, most time-pressured item in the original bundle — it closes a credential-leak risk on a codebase where the AWS provider is now live and real credentials are realistically in scope during development. It should ship first, ahead of every other split-out spec, rather than being held back by larger, more speculative work in the same original bundle.

Full technical design is in `design.md` in this folder.

## Glossary

- **gitleaks**: An open-source secret-scanning CLI tool, run here as both a pre-commit hook and a CI job.
- **Pre-commit hook**: A local Git hook, installed via the `pre-commit` framework, that runs before a commit is finalized.
- **`.gitleaksignore`**: A gitleaks baseline file that allowlists specific confirmed-false-positive findings by fingerprint, without weakening the scan rule that produced them.

## Requirements

### Requirement 1: Secret Scanning in Pre-Commit and CI

**User Story:** As a developer working in a repo with real AWS credentials in scope now that the AWS provider is live, I want secret scanning enforced both locally and in CI, so that a committed access key or LLM API key is caught before it reaches a shared branch.

#### Acceptance Criteria

1. THE repository SHALL provide a `.pre-commit-config.yaml` that runs the `gitleaks` pre-commit hook (`gitleaks protect --staged` or the equivalent official hook entry) on every commit.
2. THE CI workflow (`.github/workflows/ci.yml`) SHALL include a `secret-scan` job that runs `gitleaks detect` against the full commit history (checkout with `fetch-depth: 0`) on every push and pull request, independent of and in addition to the pre-commit hook (so a push that bypasses local hooks is still caught).
3. IF `gitleaks` reports one or more findings, THEN THE commit SHALL be rejected by the pre-commit hook, and/or THE `secret-scan` CI job SHALL fail and block the `build` job (which SHALL depend on it, alongside the existing `lint`/`type-check`/`test` jobs).
4. IF the first `gitleaks detect` run against the existing repository history reports findings that are confirmed false positives (e.g. placeholder values in `accounts.example.json`), THEN THE repository SHALL commit a `.gitleaksignore` baseline file documenting each allowed finding by fingerprint, rather than disabling or weakening the rule that produced it.
5. THE `README.md` SHALL document how to install `pre-commit` locally (`pre-commit install`) and how to regenerate the gitleaks baseline if a new false positive appears.
