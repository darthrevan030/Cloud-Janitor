# Changelog

All notable changes to Cloud Janitor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-04

### Added

- Docker Compose Pro override with docker-socket-proxy for secure Docker API access
- Comprehensive security hardening guide and sensitive data redaction in subprocess calls
- AWS accounts configuration example for multi-account setups
- LocalStack provider overrides and Pro demo targets in Makefile
- Python-based seed script replacing bash for LocalStack seeding
- Hypothesis testing profile configuration
- EC2 support in schema validator resource types
- Comprehensive code audit and findings/remediation report

### Changed

- Refactored all AI agents (explainer, tagger, anomaly detector, drift detector,
  incident policy generator, policy suggester, query interpreter) to extract LLM call
  logic into reusable functions
- Refactored orchestrator with lazy-loaded Terraform binary and improved remediation isolation
- Added subprocess environment isolation to orchestrator
- Multi-account orchestrator now uses public `findings_store_path` property
- Dashboard bound to localhost for security
- Upgraded setup-uv CI action to v6 and consolidated uv commands
- Skip existing packages on PyPI publish

### Fixed

- Savings tracker now handles missing or invalid findings store gracefully
- Project root path resolution in post-remediation hook
- ElastiCache tagging in remediation architect
- Terraform provider caching for faster plan/apply cycles

## [0.1.1] - 2026-07-04

### Changed

- Updated demo command and improved error handling
- Updated LocalStack health check configuration
- Updated prerequisites documentation and improved markdown formatting

### Fixed

- Version bump to 0.1.1 with regenerated spec compliance report

## [0.1.0] - 2026-07-03

### Added

- Multi-agent architecture with FinOps Auditor, SecOps Guard, and Remediation Architect
- Human-in-the-loop approval gate with strict command parsing
- Orchestrator coordinating agent sequencing and Terraform execution
- MCP server with fixture-backed cloud provider tools
- Phase B AI agents: RemediationExplainer, PolicySuggester, QueryInterpreter,
  ResourceTagger, AnomalyDetector
- Phase C agents: DriftDetector, IncidentPolicyGenerator, MultiAccountOrchestrator,
  JanitorScheduler
- Pluggable cloud provider backend (AWS, GCP stub, Azure stub, Fixture)
- Shared LLM client module routing all calls through OpenRouter
- SavingsTracker ledger for remediation cost tracking
- ReasoningLogger for structured JSON agent audit traces
- Streamlit dashboard with 4-panel layout, live agent feed, side-by-side diffs,
  approval workflow, and savings counter
- Append-only audit logger (JSON-lines format)
- ApprovalGateStore persistence layer
- RollbackGate two-step validation
- Pre/post-remediation lifecycle hooks
- `bin/tflocal` wrapper with dry-run support
- Centralized path configuration and error telemetry modules
- Click-based CLI with subcommands
- LocalStack integration via docker-compose for local AWS simulation
- Comprehensive property-based test suite (Hypothesis) across all agents and modules
- GitHub Actions CI pipeline (lint, type-check, test, publish)
- Dependabot for pip and Terraform dependency updates
- Spec-driven development with compliance report generator

### Infrastructure

- Terraform remediation HCL generation with mandatory tagging
- Rollback HCL generation and `terraform validate` gating
- TF_CMD environment variable for binary selection
- Docker Compose configuration for LocalStack (ec2/s3)

[0.2.0]: https://github.com/darthrevan030/Cloud-Janitor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/darthrevan030/Cloud-Janitor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/darthrevan030/Cloud-Janitor/releases/tag/v0.1.0
