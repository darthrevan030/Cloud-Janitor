# Requirements Document

## Introduction

This document specifies the requirements for refactoring the MCP server backend into a provider-agnostic architecture. The refactoring introduces a pluggable `CloudProvider` interface, moves existing fixture-reading logic into a `FixtureProvider` class, and adds stub providers for AWS, GCP, and Azure. Backend selection is controlled via environment variable with full backward compatibility.

## Glossary

- **MCP_Server**: The FastMCP-based server defined in `aws_janitor_mcp.py` that exposes tools to MCP clients
- **CloudProvider**: The abstract base class defining the provider contract
- **FixtureProvider**: A concrete provider that reads data from local JSON fixture files
- **AWSProvider**: A concrete provider stub for live AWS API calls via boto3
- **GCPProvider**: A concrete provider stub for Google Cloud Platform (not yet implemented)
- **AzureProvider**: A concrete provider stub for Microsoft Azure (not yet implemented)
- **Provider_Registry**: A dictionary mapping backend name strings to provider classes
- **JANITOR_BACKEND**: The environment variable controlling which provider is active

## Requirements

### Requirement 1: Provider Interface Definition

**User Story:** As a developer, I want a well-defined abstract provider interface, so that I can implement new cloud backends without modifying MCP tool logic.

#### Acceptance Criteria

1. THE CloudProvider SHALL define an abstract method `get_cost_data(resource_type: Optional[str], min_idle_days: int) -> dict`
2. THE CloudProvider SHALL define an abstract method `get_security_data(check_type: Optional[str]) -> dict`
3. THE CloudProvider SHALL define an abstract method `check_dependencies(resource_id: str) -> dict`
4. THE CloudProvider SHALL inherit from Python's `abc.ABC` and mark all methods with `@abstractmethod`
5. WHEN a class inherits from CloudProvider without implementing all abstract methods, THEN Python SHALL raise `TypeError` at instantiation time

### Requirement 2: FixtureProvider Implementation

**User Story:** As a developer, I want the existing fixture-reading logic encapsulated in a FixtureProvider class, so that the fixture backend continues working identically after the refactor.

#### Acceptance Criteria

1. THE FixtureProvider SHALL implement `get_cost_data` by reading `aws_cost_explorer.json` from the fixtures directory
2. WHEN `resource_type` is provided, THE FixtureProvider SHALL filter resources to only those matching the given type
3. WHEN `min_idle_days` is provided, THE FixtureProvider SHALL filter resources to only those with `idle_days >= min_idle_days`
4. THE FixtureProvider SHALL compute `total_monthly_waste` as `round(sum(r["monthly_cost"] for r in filtered_resources), 2)`
5. THE FixtureProvider SHALL implement `get_security_data` by reading `aws_config_inspector.json` from the fixtures directory
6. WHEN `check_type` is provided, THE FixtureProvider SHALL filter findings to only those matching the given check type
7. THE FixtureProvider SHALL compute `critical_count` as the count of findings where `severity == "CRITICAL"`
8. THE FixtureProvider SHALL implement `check_dependencies` by looking up `resource_id` in the `dependencies` map of `aws_config_inspector.json`
9. THE FixtureProvider SHALL set `has_dependencies` to `True` if and only if `len(dependents) > 0`
10. IF a fixture file does not exist, THEN THE FixtureProvider SHALL return a dict containing an `"error"` key with a descriptive message and appropriate empty defaults

### Requirement 3: AWSProvider Stub

**User Story:** As a developer, I want an AWSProvider class with documented stubs, so that I have a clear starting point for implementing live AWS integration.

#### Acceptance Criteria

1. THE AWSProvider SHALL implement all CloudProvider abstract methods
2. WHEN any method on AWSProvider is called, THEN THE AWSProvider SHALL raise `NotImplementedError` with a message identifying the method and provider
3. THE AWSProvider SHALL document required IAM permissions in method docstrings
4. THE AWSProvider SHALL import boto3 lazily (only when the provider is instantiated)
5. IF boto3 is not installed when AWSProvider is instantiated, THEN THE AWSProvider SHALL raise `ImportError` with a message instructing the user to install boto3

### Requirement 4: GCP and Azure Provider Stubs

**User Story:** As a developer, I want placeholder providers for GCP and Azure, so that the architecture supports multi-cloud expansion.

#### Acceptance Criteria

1. THE GCPProvider SHALL implement all CloudProvider abstract methods
2. WHEN any method on GCPProvider is called, THEN THE GCPProvider SHALL raise `NotImplementedError` with a descriptive message
3. THE AzureProvider SHALL implement all CloudProvider abstract methods
4. WHEN any method on AzureProvider is called, THEN THE AzureProvider SHALL raise `NotImplementedError` with a descriptive message

### Requirement 5: Provider Selection via Environment Variable

**User Story:** As an operator, I want to select the active backend via an environment variable, so that I can switch between fixture and live providers without code changes.

#### Acceptance Criteria

1. THE MCP_Server SHALL read the `JANITOR_BACKEND` environment variable at module load time to determine the active provider
2. WHEN `JANITOR_BACKEND` is not set, THE MCP_Server SHALL default to `"fixture"`
3. WHEN `JANITOR_BACKEND` is set to a valid backend name, THE MCP_Server SHALL instantiate the corresponding provider class
4. THE Provider_Registry SHALL contain mappings for `"fixture"`, `"aws"`, `"gcp"`, and `"azure"`
5. IF `JANITOR_BACKEND` is set to a value not in the Provider_Registry, THEN THE MCP_Server SHALL raise `ValueError` with a message containing the invalid value and listing all valid options

### Requirement 6: MCP Tool Interface Stability

**User Story:** As an MCP client developer, I want the tool signatures to remain unchanged, so that existing integrations continue working.

#### Acceptance Criteria

1. THE MCP_Server SHALL expose a `get_cost_data` tool with parameters `resource_type: Optional[str]` and `min_idle_days: int` with default `7`
2. THE MCP_Server SHALL expose a `get_security_data` tool with parameter `check_type: Optional[str]`
3. THE MCP_Server SHALL expose a `check_dependencies` tool with parameter `resource_id: str`
4. THE MCP_Server SHALL expose a `validate_hcl` tool with parameter `hcl_content: str`
5. THE MCP_Server SHALL delegate `get_cost_data`, `get_security_data`, and `check_dependencies` to the active CloudProvider instance

### Requirement 7: validate_hcl Independence

**User Story:** As a developer, I want validate_hcl to remain a standalone tool, so that it stays provider-agnostic and directly uses the tflocal CLI.

#### Acceptance Criteria

1. THE `validate_hcl` tool SHALL NOT be part of the CloudProvider interface
2. THE `validate_hcl` tool SHALL remain implemented directly in `aws_janitor_mcp.py`
3. THE `validate_hcl` tool SHALL use `tflocal` CLI, consistent with the LocalStack wiring specified in the savings-tracker-localstack spec

### Requirement 8: Backward Compatibility

**User Story:** As a test author, I want all existing tests to pass without modification when the fixture backend is active, so that the refactoring does not introduce regressions.

#### Acceptance Criteria

1. WHEN `JANITOR_BACKEND` is unset or set to `"fixture"`, THE MCP_Server SHALL produce identical output to the pre-refactoring implementation for all tool invocations
2. THE FixtureProvider SHALL read from the same fixture file paths as the original inline implementation
3. THE FixtureProvider SHALL apply the same filtering logic as the original inline implementation

### Requirement 9: Documentation

**User Story:** As a developer, contributor, or judge, I want comprehensive README documentation covering every module, agent, fixture, and configuration option, so that I can understand, run, extend, and evaluate the system without reading source code.

#### Acceptance Criteria

**Project root `README.md`**
1. THE README SHALL contain a one-line product description explaining what Cloud Janitor does and how it differs from static tools like Cloud Custodian
2. THE README SHALL include a Quick Start section with the `make demo` command, prerequisites, and the Streamlit port
3. THE README SHALL describe the 3-agent pipeline (FinOps → SecOps → Remediation Architect → approval gate → apply/rollback) in prose or diagram form
4. THE README SHALL include a complete environment variables table covering `JANITOR_BACKEND`, `TF_CMD`, and `JANITOR_SCHEDULE` with valid values, defaults, and descriptions
5. THE README SHALL include an annotated project structure section covering all top-level files and directories
6. THE README SHALL describe the Ghost Cluster demo scenario (idle ElastiCache + exposed security group) that ships with the fixture data
7. THE README SHALL include a Running Tests section with the `pytest` command

**`mcp_server/README.md`**
8. THE README SHALL document all available provider backends (`fixture`, `aws`, `gcp`, `azure`) in a table with implementation status (complete vs stub) and required env vars per backend
9. THE README SHALL document the `JANITOR_BACKEND` environment variable, its default value (`fixture`), and the consequence of each valid value
10. THE README SHALL document the implementation status of each provider: fixture (complete), aws (stub — raises NotImplementedError), gcp and azure (interface only)
11. THE README SHALL include step-by-step instructions for adding a new provider: create `backends/<name>_provider.py`, inherit `CloudProvider`, implement the 3 abstract methods, add to `PROVIDER_REGISTRY`
12. THE README SHALL list Phase B/C planned MCP tools (`interpret_query`, `explain_remediation`, `suggest_policies`, `infer_resource_context`, `detect_anomalies`, `policy_from_incident`, `aggregate_findings`) with one-line descriptions and `[planned]` status markers

**`agents/README.md`**
13. THE README SHALL document every agent class: FinOpsAuditor, SecOpsGuard, RemediationArchitect, ApprovalGate, ReasoningLogger, AuditLogger — including inputs, outputs, and side effects for each
14. THE README SHALL document FinOpsAuditor severity rules: ElastiCache idle >30d = HIGH, EBS unattached >30d = MEDIUM, all others = LOW
15. THE README SHALL document SecOpsGuard severity rules: ports 6379/3306/5432/27017 open to 0.0.0.0/0 = CRITICAL, port 22 open = HIGH, unencrypted cache/EBS = HIGH
16. THE README SHALL document the agent sequencing requirement: FinOps writes `findings_store.json` fresh, SecOps appends, RemediationArchitect reads both — and explain why order is enforced
17. THE README SHALL document the ApprovalGate command formats (`APPROVE <id>`, `ROLLBACK <id>`, `CONFIRM ROLLBACK <id>`) and the 3-attempt lock behaviour
18. THE README SHALL include the full annotated `findings_store.json` schema

**`fixtures/README.md`**
19. THE README SHALL explain that fixture files provide fake AWS data requiring no credentials, and document their role in the demo pipeline
20. THE README SHALL document the complete `aws_cost_explorer.json` schema with every field, its type, valid values, and which fields are type-specific (elasticache/ebs/ec2)
21. THE README SHALL document the complete `aws_config_inspector.json` schema: findings array, dependencies map, check_type enum, severity enum, and check-type-specific fields
22. THE README SHALL describe the specific resources in the current fixture files and why they were chosen for the Ghost Cluster demo scenario
23. THE README SHALL include instructions for extending fixtures: required vs optional fields, how `check_dependencies` uses the `dependencies` map, and how to add a new resource type

**`tests/README.md`**
24. THE README SHALL list every test file with a one-line description of what it covers
25. THE README SHALL document how to run tests: `pytest`, `pytest -v`, and single-file invocation
26. THE README SHALL document which tests use `hypothesis` for property-based testing and what invariants they verify
27. THE README SHALL document what is intentionally not tested: `app.py` (requires browser context) and LocalStack-dependent paths
28. THE README SHALL include a checklist for adding tests when a new agent is introduced

**`output/README.md` and `rollbacks/README.md`**
29. `output/README.md` SHALL explain that `remediation.tf` is auto-generated by RemediationArchitect, must not be manually edited, is overwritten on each scan, and is the file submitted to `tflocal apply` on approval
30. `rollbacks/README.md` SHALL explain the `<resource_id>.tf` naming convention, that files are generated alongside remediation HCL, one per resource, and are executed on `CONFIRM ROLLBACK <resource_id>`
### Requirement 10: Dependency Management

**User Story:** As a developer, I want boto3 listed as an optional dependency, so that users who only need the fixture backend do not need AWS SDK installed.

#### Acceptance Criteria

1. THE `requirements.txt` SHALL include `boto3>=1.34.0` as a dependency
2. THE MCP_Server SHALL function without boto3 installed when `JANITOR_BACKEND` is not `"aws"`
3. IF `JANITOR_BACKEND` is set to `"aws"` and boto3 is not installed, THEN THE AWSProvider SHALL raise `ImportError` with installation instructions
