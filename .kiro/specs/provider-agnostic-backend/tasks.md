# Implementation Plan: Provider-Agnostic Backend

## Overview

Refactor the MCP server to use a pluggable provider architecture. Extract existing fixture-reading logic into a `FixtureProvider` class behind a `CloudProvider` ABC, add stub providers for AWS/GCP/Azure, and wire provider selection through the `JANITOR_BACKEND` environment variable. All existing tool signatures and test behavior remain unchanged.

## Tasks

- [x] 1. Create the backends module with CloudProvider ABC
  - [x] 1.1 Create `mcp_server/backends/__init__.py` with CloudProvider abstract base class
    - Define `CloudProvider(ABC)` with abstract methods: `get_cost_data`, `get_security_data`, `check_dependencies`
    - Include full type hints and docstrings matching design document signatures
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 1.2 Implement FixtureProvider in `mcp_server/backends/fixture_provider.py`
    - Move existing fixture-reading logic from `aws_janitor_mcp.py` into the class verbatim
    - Constructor accepts optional `fixtures_dir` parameter defaulting to project `fixtures/` directory
    - Implement `get_cost_data` with resource_type and min_idle_days filtering
    - Implement `get_security_data` with check_type filtering and critical_count computation
    - Implement `check_dependencies` with dependencies map lookup and has_dependencies boolean
    - Handle missing fixture files gracefully with error dict
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 1.3 Write property tests for FixtureProvider
    - **Property 2: Cost data structural invariants** — verify total_monthly_waste == round(sum of costs, 2) and filtering correctness for any resource_type/min_idle_days
    - **Property 3: Security data critical count consistency** — verify critical_count matches CRITICAL findings count and check_type filtering
    - **Property 4: Dependency response boolean consistency** — verify has_dependencies == (len(dependents) > 0)
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.6, 2.7, 2.8, 2.9**

- [ ] 2. Implement stub providers
  - [ ] 2.1 Implement AWSProvider in `mcp_server/backends/aws_provider.py`
    - Lazy import of boto3 in `__init__` with ImportError handling and helpful install message
    - All methods raise `NotImplementedError` with descriptive messages
    - Include IAM permission documentation in method docstrings
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ] 2.2 Implement GCPProvider and AzureProvider in `mcp_server/backends/gcp_provider.py` and `mcp_server/backends/azure_provider.py`
    - Minimal stub classes inheriting from CloudProvider
    - All methods raise `NotImplementedError` with descriptive messages
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ] 2.3 Update `mcp_server/backends/__init__.py` to export all providers
    - Export CloudProvider, FixtureProvider, AWSProvider, GCPProvider, AzureProvider
    - _Requirements: 5.4_

- [ ] 3. Wire provider selection into MCP server
  - [ ] 3.1 Add PROVIDER_REGISTRY and `_load_provider()` to `aws_janitor_mcp.py`
    - Define registry mapping backend names to provider classes
    - Implement `_load_provider()` reading `JANITOR_BACKEND` env var (default: `"fixture"`)
    - Raise `ValueError` for invalid backend names with helpful message listing valid options
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 3.2 Refactor MCP tool functions to delegate to provider instance
    - Replace inline fixture-reading logic in `get_cost_data`, `get_security_data`, `check_dependencies` with delegation to `_provider`
    - Keep `validate_hcl` unchanged and directly in `aws_janitor_mcp.py`
    - Remove the now-unused `FIXTURES_DIR` constant (FixtureProvider handles its own path)
    - **IMPORTANT: Do NOT remove imports that `validate_hcl` still needs** (`tempfile`, `os`, `subprocess`). After refactoring, verify with: `python -c "from mcp_server.aws_janitor_mcp import validate_hcl; print('ok')"`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3_

  - [ ] 3.3 Write property tests for provider selection
    - **Property 5: Provider registry completeness** — for any valid backend name, _load_provider() returns a CloudProvider instance
    - **Property 6: Invalid backend rejection** — for any string not in registry, _load_provider() raises ValueError with the invalid name and valid options
    - **Validates: Requirements 5.3, 5.4, 5.5**

- [ ] 4. Checkpoint - Verify backward compatibility
  - Ensure all existing tests pass without modification when `JANITOR_BACKEND` is unset
  - Run `pytest` and verify no regressions
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Write backward compatibility property test
  - **Property 1: Fixture backend behavioral equivalence** — for any valid resource_type and min_idle_days, FixtureProvider output matches the original inline implementation output
  - Compare FixtureProvider results against a reference implementation using the same fixture data
  - **Validates: Requirements 8.1, 8.3**

- [ ] 6. Update dependencies and documentation
  - [ ] 6.1 Add boto3 to requirements.txt
    - Add `boto3>=1.34.0` as an optional dependency line
    - _Requirements: 10.1_

  - [ ] 6.2 Update `mcp_server/README.md` with provider architecture documentation
    - Document all running modes (fixture, aws, gcp, azure)
    - Document `JANITOR_BACKEND` env var and its default value
    - Document implementation status of each provider
    - Include instructions for adding a new provider
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [ ] 7. Final checkpoint - Ensure all tests pass
  - Run full test suite with `pytest`
  - Verify no import errors when boto3 is not installed and backend is fixture
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "name": "Wave 1: Foundation",
      "tasks": ["1.1"],
      "description": "Create CloudProvider ABC"
    },
    {
      "name": "Wave 2: Provider Implementations",
      "tasks": ["1.2", "2.1", "2.2"],
      "description": "Implement all providers (FixtureProvider + stubs)",
      "dependsOn": ["1.1"]
    },
    {
      "name": "Wave 3: Module Wiring",
      "tasks": ["2.3", "3.1"],
      "description": "Export providers and implement registry/selection",
      "dependsOn": ["1.2", "2.1", "2.2"]
    },
    {
      "name": "Wave 4: MCP Refactor",
      "tasks": ["3.2"],
      "description": "Refactor MCP tools to delegate to provider",
      "dependsOn": ["2.3", "3.1"]
    },
    {
      "name": "Wave 5: Verification",
      "tasks": ["4"],
      "description": "Checkpoint - verify backward compatibility",
      "dependsOn": ["3.2"]
    },
    {
      "name": "Wave 6: Documentation & Finalization",
      "tasks": ["6.1", "6.2", "7"],
      "description": "Update dependencies, documentation, and final checkpoint",
      "dependsOn": ["4"]
    }
  ],
  "optionalTasks": ["1.3", "3.3", "5"]
}
```

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The FixtureProvider implementation must be a verbatim extraction of existing logic (no behavior changes)
- `validate_hcl` stays exactly where it is — it is NOT part of the provider interface
- Property tests use `hypothesis` which is already in requirements.txt
- boto3 is optional — the server must work without it when using fixture backend
- **TypedDicts in design.md are illustrative only.** Do NOT use them for runtime validation or as the basis for fixture parsing. The actual fixture schemas are:
  - `aws_cost_explorer.json` resources: `resource_id`, `type`, `name`, `idle_days`, `monthly_cost`, `status` (varies by resource type)
  - `findings_store.json` findings: `id`, `resource_id`, `resource_type`, `agent`, `category`, `severity`, `title`, `description`, `cost_estimate_monthly`, `idle_days`, `metadata`, `detected_at`
  - Parse the **actual fixture fields**, not the TypedDict fields. The provider passes through whatever the JSON contains.
