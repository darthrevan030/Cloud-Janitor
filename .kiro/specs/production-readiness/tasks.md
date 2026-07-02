# Implementation Plan: Production Readiness

## Overview

Transform Cloud Janitor from a development-time project into a pip-installable, distributable Python package. Implementation follows the three-batch approach defined in the design: Batch 1 adds CLI, logging, retry, providers, and pyproject.toml in the existing flat layout; Batch 2 updates README; Batch 3 performs the src-layout migration and CI pipeline setup.

## Tasks

- [ ] 1. Batch 1 — Core infrastructure (flat layout)
  - [x] 1.1 Create `pyproject.toml` with build system, dependencies, and scripts
    - Declare `[build-system]` with hatchling backend
    - Add `[project]` metadata (name, version 0.1.0, description, requires-python >=3.12)
    - Add `[project.dependencies]` with click, boto3, openai, python-dotenv, pyyaml, apscheduler, filelock, mcp, packaging, terraform-local
    - Add `[project.optional-dependencies]` dashboard group with streamlit
    - Add `[dependency-groups]` dev group with hypothesis, moto, mypy, pytest, ruff
    - Add `[project.scripts]` entry point: `cloud-janitor = "cli:main"` (flat layout for Batch 1)
    - Add `[tool.pytest.ini_options]` and `[tool.ruff]` sections
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4, 4.7, 4.8_

  - [x] 1.2 Create `logging_config.py` at project root
    - Implement `configure_logging()` function
    - Read `JANITOR_LOG_LEVEL` env var (case-insensitive matching)
    - Valid levels: DEBUG, INFO, WARNING, ERROR; default to INFO if missing or invalid
    - Emit WARNING log if invalid value provided
    - Format: `%(asctime)s %(levelname)s %(name)s %(message)s` with ISO 8601 timestamps
    - Output to stderr via `logging.StreamHandler(sys.stderr)`
    - _Requirements: 7.2, 7.3, 7.4, 7.6_

  - [ ] 1.3 Add retry logic to `core/llm_client.py`
    - Add `LLMRetryExhausted` and `LLMRateLimitExceeded` exception classes
    - Implement `call_llm()` function with manual retry loop (no tenacity)
    - Retry on HTTP 429, 500, 502, 503, 504 and network timeouts
    - Max 3 retries (4 total attempts), exponential backoff: 1s, 2s, 4s
    - Respect Retry-After header for 429 (up to 60s max)
    - Raise `LLMRateLimitExceeded` if Retry-After > 60s
    - Log each retry attempt at WARNING level with attempt number, delay, error reason
    - Replace any `print()` calls with `logging.getLogger(__name__)` calls
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 7.1_

  - [ ] 1.4 Create `cli.py` at project root with Click CLI
    - Implement `main()` Click group with `--version` option (version read inline via `importlib.metadata.version("cloud-janitor")` with `PackageNotFoundError` fallback to `"0.0.0-dev"` — do NOT import from `cloud_janitor`)
    - Implement `scan` command with `--finops` and `--secops` flags
    - Implement `approve <resource_id>` command
    - Implement `rollback <resource_id>` command
    - Implement `dashboard` command with `try/except ImportError` guard for streamlit
    - Implement `mcp` command for stdio transport
    - Call `configure_logging()` in the main group
    - Delegate to existing `Orchestrator` class for all operations
    - Handle errors: print to stderr and `sys.exit(1)`
    - No top-level `import streamlit` anywhere in the module
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 9.1, 9.2, 9.3, 9.4, 12.2, 12.4_

  - [ ] 1.5 Update stub providers with warning pattern
    - Modify `mcp_server/backends/gcp_provider.py`: add WARNING log on `__init__`, raise `NotImplementedError` with provider+method name in each stub method
    - Modify `mcp_server/backends/azure_provider.py`: same pattern as GCP
    - Ensure providers remain instantiable after warning (no exception on init)
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [ ] 2. Checkpoint — Verify Batch 1
  - Ensure all tests pass, ask the user if questions arise.
  - Verify `pip install -e .` works and `cloud-janitor --help` exits 0
  - Verify retry logic unit tests pass
  - Verify logging configuration tests pass

- [ ] 3. Batch 1 — Tests for core infrastructure
  - [ ] 3.1 Write unit tests for CLI (`tests/test_cli.py`)
    - Use Click's `CliRunner` to test all subcommands
    - Test `scan` calls `execute_audit()` and prints finding count
    - Test `scan --finops` and `scan --secops` flags
    - Test `approve <id>` passes correct command string
    - Test `rollback <id>` passes correct command string
    - Test `dashboard` without streamlit prints install instructions + exits 1
    - Test `--version` prints version string
    - Test unknown subcommand exits non-zero
    - Test agent exception during scan prints error to stderr + exits 1
    - Mock Orchestrator (external I/O), never mock the CLI handler itself
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 1.10, 1.11, 12.2_

  - [ ] 3.2 Write unit tests for logging config (`tests/test_logging_config.py`)
    - Test valid levels (DEBUG, INFO, WARNING, ERROR) configure correctly
    - Test case-insensitive matching (e.g., "debug", "Debug")
    - Test missing env var defaults to INFO
    - Test invalid value falls back to INFO and emits WARNING
    - Test output goes to stderr
    - Test log format includes timestamp, level, name, message
    - _Requirements: 7.2, 7.3, 7.4, 7.6_

  - [ ] 3.3 Write unit tests for LLM retry logic (`tests/test_llm_retry.py`)
    - Test successful call on first attempt (no retry)
    - Test retry on 429, 500, 502, 503, 504 — verify correct attempt count
    - Test retry on network timeout
    - Test `LLMRetryExhausted` raised after 4 total attempts with correct fields
    - Test `LLMRateLimitExceeded` raised when Retry-After > 60s
    - Test Retry-After header respected (delay equals header value)
    - Test non-retriable errors (400, 401, 403) raise immediately
    - Test exponential backoff delays: 1s, 2s, 4s
    - Mock only the OpenAI client (external I/O), not `call_llm` itself
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ] 3.4 Write unit tests for stub providers (`tests/test_stub_providers.py`)
    - Test GCPProvider instantiation emits WARNING log
    - Test AzureProvider instantiation emits WARNING log
    - Test each stub method raises NotImplementedError with provider+method name
    - Test providers remain instantiable after warning
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [ ] 3.5 Write unit tests for version logic in `cli.py` (`tests/test_version.py`)
    - Test the inline `importlib.metadata.version("cloud-janitor")` call returns a string used by `--version`
    - Test fallback to "0.0.0-dev" when `importlib.metadata.version` raises `PackageNotFoundError` (mock `importlib.metadata.version`)
    - Test version string conforms to PEP 440 via `packaging.version.Version()`
    - Do NOT import from `cloud_janitor` — that module doesn't exist until Batch 3
    - Test via Click's `CliRunner` invoking `--version` with mocked metadata
    - The full `cloud_janitor.__version__` test (Req 9.1, 9.5) is deferred to task 7.6 after src-layout migration
    - _Requirements: 9.2, 9.3, 9.4_

  - [ ]* 3.6 Write property test for log level configuration mapping
    - **Property 1: Log Level Configuration Mapping**
    - Generator: random strings (mix of valid levels in random casing + invalid strings)
    - Assertion: root logger level matches expected mapping; invalid values emit WARNING
    - **Validates: Requirements 7.2, 7.6**

  - [ ]* 3.7 Write property test for retry on retriable errors
    - **Property 2: Retry on Retriable Errors**
    - Generator: random retriable error type × random failure count (1–3) × random success/fail on final
    - Assertion: correct total attempts made, correct WARNING log records per retry
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.5**

  - [ ]* 3.8 Write property test for retry exhaustion exception content
    - **Property 3: Retry Exhaustion Exception Content**
    - Generator: random retriable error type that persists for all 4 attempts
    - Assertion: exception has status_or_error string, attempts == 4, mocked sleep calls sum to ~7 seconds (1+2+4) matching the exponential backoff formula
    - **Validates: Requirements 8.4**

  - [ ]* 3.9 Write property test for backoff delay calculation
    - **Property 4: Backoff Delay Calculation**
    - Generator: random attempt number (0–2) × random Retry-After value (None, or float 0.1–120)
    - Assertion: delay follows formula; values > 60 cause immediate LLMRateLimitExceeded raise
    - **Validates: Requirements 8.6, 8.7**

  - [ ]* 3.10 Write property test for stub provider NotImplementedError content
    - **Property 5: Stub Provider NotImplementedError Content**
    - Generator: random choice of (GCPProvider, AzureProvider) × random method name
    - Assertion: NotImplementedError message contains both provider class name and method name
    - **Validates: Requirements 11.3**

- [ ] 4. Checkpoint — Verify Batch 1 tests
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Batch 2 — README accuracy
  - [ ] 5.1 Update README.md with accurate documentation
    - Update Quick Start: replace `pip install -r requirements.txt` with `pip install cloud-janitor`
    - Document all CLI commands (scan, approve, rollback, dashboard, mcp) with invocation syntax
    - Document optional dashboard dependency: `pip install cloud-janitor[dashboard]`
    - Update AWS provider status to "Complete" (not "Stub") in Provider backends table
    - Describe AWS provider as complete implementation querying live AWS via boto3
    - Remove any references to `requirements.txt` as user-facing installation
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 6. Checkpoint — Verify Batch 2
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Batch 3 — Package structure migration and CI
  - [ ] 7.1 Create `src/cloud_janitor/` directory structure and move modules
    - Create `src/cloud_janitor/` with `__init__.py` (importlib.metadata version)
    - Create `src/cloud_janitor/py.typed` marker file (0 bytes)
    - Move `agents/` → `src/cloud_janitor/agents/` (add `__init__.py`)
    - Move `core/` → `src/cloud_janitor/core/` (add `__init__.py`)
    - Move `mcp_server/` → `src/cloud_janitor/mcp_server/` (add `__init__.py`)
    - Move `orchestrator.py` → `src/cloud_janitor/orchestrator/orchestrator.py` (add `__init__.py` with re-exports)
    - Move `cli.py` → `src/cloud_janitor/cli.py`
    - Move `logging_config.py` → `src/cloud_janitor/logging_config.py`
    - Move `app.py` → `src/cloud_janitor/app.py`
    - _Requirements: 3.1, 3.2, 3.4, 10.1_

  - [ ] 7.2 Update all source imports to `cloud_janitor.*` paths
    - Rewrite imports in all moved source modules to use `cloud_janitor.` prefix
    - Update `cli.py` imports: `from cloud_janitor.orchestrator import Orchestrator`, etc.
    - Update agent imports to reference `cloud_janitor.core.llm_client`
    - Update MCP server imports to reference `cloud_janitor.mcp_server.backends`
    - _Requirements: 3.1, 3.2_

  - [ ] 7.3 Update all test imports to `cloud_janitor.*` paths
    - Rewrite every test file in `tests/` to use `from cloud_janitor.` import paths
    - Verify all tests still pass after import rewrite
    - _Requirements: 3.1, 3.2_

  - [ ] 7.4 Update `pyproject.toml` for src-layout
    - Change `[project.scripts]` to `cloud-janitor = "cloud_janitor.cli:main"`
    - Add `[tool.hatch.build.targets.wheel] packages = ["src/cloud_janitor"]`
    - Add `[tool.mypy]` section with `packages = ["cloud_janitor"]` and `mypy_path = "src"`
    - Add `[tool.ruff] src = ["src"]`
    - _Requirements: 3.1, 3.5, 10.2_

  - [ ] 7.5 Create GitHub Actions CI pipeline (`.github/workflows/ci.yml`)
    - Add `lint` job: ruff check on entire codebase
    - Add `type-check` job: mypy on src/
    - Add `test` job: pytest matrix for Python 3.12 and 3.13
    - Add `build` job (depends on lint, type-check, test): build wheel, twine check, install, verify import + CLI help
    - Add `publish` job (on v* tag, depends on build): PyPI trusted publishing
    - Ensure failed steps prevent dependent steps from executing
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ] 7.6 Verify package installability and type annotation marker
    - Run `pip install -e .` and verify `import cloud_janitor` succeeds
    - Verify `cloud-janitor --help` exits 0
    - Verify `py.typed` is included in installed package
    - Verify `from cloud_janitor import __version__` returns PEP 440 string
    - Verify all subpackages importable: `cloud_janitor.agents`, `cloud_janitor.core`, `cloud_janitor.mcp_server`, `cloud_janitor.orchestrator`
    - Verify `import cloud_janitor.nonexistent` raises `ModuleNotFoundError`
    - _Requirements: 3.2, 3.3, 3.5, 3.6, 9.1, 9.5, 10.1, 10.3_

- [ ] 8. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Verify full test suite passes on editable install
  - Verify `pip wheel .` + `twine check` passes
  - Verify `ruff check .` and `mypy src/` pass

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation between batches
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Batch 1 preserves the flat layout — no `src/` directory until Batch 3
- All existing tests will break temporarily during Batch 3 migration (expected per design)
- The implementation language is Python throughout (Click CLI, pytest, hypothesis)
- Session isolation (Audit Remediation Req 14) is explicitly out of scope for this implementation pass. Tracked for a future post-hackathon spec. Do not implement output/<uuid> session directories, SessionManager, or related tests as part of this task list.
- Batch 3 (task 7, src-layout migration) should not begin until the Audit Remediation spec's fixes are merged, to avoid import-path conflicts in files touched by both specs (orchestrator.py, core/paths.py, core/error_telemetry.py, agents/approval_gate.py, bin/tflocal).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.5"] },
    { "id": 1, "tasks": ["1.4"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3", "3.4", "3.5"] },
    { "id": 3, "tasks": ["3.6", "3.7", "3.8", "3.9", "3.10"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["7.1"] },
    { "id": 6, "tasks": ["7.2", "7.3", "7.4"] },
    { "id": 7, "tasks": ["7.5", "7.6"] }
  ]
}
```
