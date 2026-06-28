# Implementation Plan: Cloud Janitor Phase B+C (AI & Platform Features)

## Overview

This plan implements 9 features spanning Phase B (Tier 2 AI Features) and Phase C (Tier 3 Platform Features). The implementation follows a layered approach: shared infrastructure first (llm_client.py), then individual AI agents, then platform agents, then orchestrator integration and MCP tool wiring. Each agent is implemented with its safe-default error handling pattern and tested independently before integration.

## Tasks

- [ ] 1. Set up shared LLM infrastructure and project dependencies
  - [x] 1.1 Create `llm_client.py` at project root
    - Implement `get_client() -> openai.OpenAI` configured with `base_url="https://openrouter.ai/api/v1"` and `api_key` from `OPENROUTER_API_KEY` env var
    - Implement `DEFAULT_MODEL: str` reading from `JANITOR_LLM_MODEL` env var, defaulting to `"anthropic/claude-haiku-4-5"`
    - Raise `EnvironmentError("OPENROUTER_API_KEY is not set")` if env var missing
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

  - [x] 1.2 Update `requirements.txt` with new dependencies
    - Add `openai>=2.44.0` (OpenAI-compatible SDK for OpenRouter)
    - Add `filelock>=3.13.0` (file locking for DriftDetector)
    - Add `APScheduler>=3.10.0` (cron scheduling)
    - Ensure `anthropic` package is NOT present
    - _Requirements: 13.5_

  - [x] 1.3 Update `.gitignore` with sensitive data files
    - Add entries for `findings_store.json`, `scan_history.json`, `savings_ledger.json`, `scheduler.log`, `policies/*.json`
    - _Requirements: 14.3_

  - [x] 1.4 Write unit tests for `llm_client.py`
    - Test `get_client()` returns OpenAI instance with correct base_url
    - Test `DEFAULT_MODEL` reads from env var with correct default
    - Test `EnvironmentError` raised when `OPENROUTER_API_KEY` unset
    - Test that no sensitive values are logged or exposed
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [x] 2. Implement Phase B AI agents (QueryInterpreter, RemediationExplainer, PolicySuggester)
  - [x] 2.1 Implement `agents/query_interpreter.py`
    - Create `QueryInterpreter` class with `interpret(query: str) -> dict`
    - Implement prompt construction for NL-to-structured-params mapping
    - Validate parsed output: resource_types against {"elasticache", "ebs", "ec2"}, check_types against {"security_group", "encryption", "public_access"}
    - Clamp min_idle_days >= 0, confidence in [0.0, 1.0]
    - Return safe defaults on empty/whitespace query without calling LLM
    - Return safe defaults on any exception (LLM failure, invalid JSON)
    - Import LLM via `from llm_client import get_client, DEFAULT_MODEL`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 1.1, 1.8, 1.9, 1.11_

  - [x] 2.2 Write property test for QueryInterpreter output validity
    - **Property 3: QueryInterpreter Output Validity**
    - For any string input, verify: confidence ∈ [0.0, 1.0], resource_types items ∈ valid set, check_types items ∈ valid set, min_idle_days ≥ 0, intent_summary is non-empty string, exactly 5 keys returned
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.7**

  - [x] 2.3 Implement `agents/explainer.py`
    - Create `RemediationExplainer` class with `explain(resource_id, finding, remediation_hcl, rollback_hcl) -> dict`
    - Return dict with exactly 3 keys: risk_explanation, what_terraform_does, what_rollback_restores
    - Set max_tokens=400 on LLM call
    - Return all keys as "Explanation unavailable." when remediation_hcl or rollback_hcl is empty/whitespace (without calling LLM)
    - Return all keys as "Explanation unavailable." on any exception
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 1.2, 1.8, 1.9, 1.11_

  - [x] 2.4 Write property test for RemediationExplainer schema completeness
    - **Property 4: RemediationExplainer Schema Completeness**
    - For any combination of inputs, verify: dict has exactly 3 keys, each value is a non-empty string
    - **Validates: Requirements 3.4, 1.2**

  - [x] 2.5 Implement `agents/policy_suggester.py`
    - Create `PolicySuggester` class with `suggest(findings, already_checked) -> list[dict]`
    - Construct prompt for LLM to analyze finding patterns
    - Post-process filter: remove suggestions whose check_type matches already_checked entries
    - Validate each suggestion has: suggestion_id, title (≤80 chars), rationale (≤200 chars), query, priority ∈ {"high", "medium", "low"}
    - Return 0-5 suggestions; return sensible defaults when findings is empty
    - Return [] on any exception
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 1.3, 1.8, 1.9, 1.11_

  - [x] 2.6 Write property test for PolicySuggester output bounds and exclusion
    - **Property 5: PolicySuggester Output Bounds and Exclusion**
    - For any findings list and already_checked list, verify: 0-5 dicts returned, each has required keys, priority valid, no suggestion references check_type in already_checked
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

- [x] 3. Implement Phase B AI agents (ResourceTagger, AnomalyDetector)
  - [x] 3.1 Implement `agents/tagger.py`
    - Create `ResourceTagger` class with `infer(resource_id, resource_name, existing_tags) -> dict` and `infer_batch(resources) -> list[dict]`
    - Implement confidence_threshold logic: if confidence < threshold → set team/owner to None; if confidence == threshold → preserve inferred values
    - Implement existing_tags passthrough: skip inference for fields with non-empty, non-null string values
    - Treat existing_tags fields as present only when value is a non-empty, non-null string; empty strings and None values trigger inference as if the field were absent
    - Implement batch: split into chunks of 10, single LLM call per chunk, preserve input order
    - Return safe defaults on any exception: env="unknown", team=None, owner=None, risk_level="low", confidence=0.0
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 1.4, 1.8, 1.9, 1.11_

  - [x] 3.2 Write property tests for ResourceTagger
    - **Property 6: ResourceTagger Enum and Confidence Constraints**
    - For any input, verify: env ∈ valid set, risk_level ∈ valid set, confidence ∈ [0.0, 1.0], team/owner None when confidence < threshold
    - **Property 7: ResourceTagger Existing Tags Passthrough**
    - For any existing_tags with env/team/owner populated, output preserves those values
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.6**

  - [x] 3.3 Implement `agents/anomaly_detector.py`
    - Create `AnomalyDetector` class with `detect(resources, findings) -> list[dict]`
    - Filter out resources already in findings by resource_id before LLM call
    - Only call LLM when unflagged resources exist; return [] when resources is empty
    - Always call LLM when unflagged resources list is non-empty, even when no anomalies are expected; never skip the LLM call based on predicted output
    - Validate each anomaly has: anomaly_id, resource_id, anomaly_type, description, severity ∈ {"high", "medium", "low"}, evidence
    - Post-filter: ensure no anomaly resource_id exists in findings
    - Cap at 20 anomalies max
    - Return [] on any exception
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 1.5, 1.8, 1.9, 1.11_

  - [x] 3.4 Write property tests for AnomalyDetector
    - **Property 8: AnomalyDetector Disjoint Resource IDs**
    - For any resources and findings lists, verify anomaly resource_ids are disjoint from finding resource_ids
    - **Property 9: AnomalyDetector Output Schema**
    - For any input, verify: flat list, each element has required keys, severity valid
    - **Validates: Requirements 6.1, 6.2, 6.3**

- [x] 4. Checkpoint - Ensure all Phase B agent tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement Phase C platform agents (IncidentPolicyGenerator, DriftDetector)
  - [x] 5.1 Implement `agents/incident_policy_generator.py`
    - Create `IncidentPolicyGenerator` class with `generate(incident_description) -> list[dict]` and `list_policies() -> list[dict]`
    - Input validation: empty/whitespace → []; >2000 chars → truncate before LLM call
    - Compute incident_hash as sha256[:8] of original un-truncated description
    - Check policies/ directory for existing policies matching incident_hash; return existing if found
    - Validate policy_id against `^[a-z0-9\-]+$` before file path construction; skip and log unsafe IDs
    - Validate check_type ∈ {"security_group", "encryption", "public_access", "idle_resource"}
    - Validate resource_types contains only values from {"elasticache", "ebs", "ec2"} and is non-empty
    - Write each policy as `policies/{policy_id}.json`; create directory if needed
    - If LLM returns < 3 policies, retry once; return whatever was generated if retry also < 3
    - Return [] on any exception; ensure no partial files left on disk on I/O error
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 1.6, 1.8, 1.9, 1.11, 14.2_

  - [x] 5.2 Write property tests for IncidentPolicyGenerator
    - **Property 10: IncidentPolicyGenerator Idempotency**
    - Calling generate() twice with same text returns same result without second LLM call
    - **Property 11: IncidentPolicyGenerator File Consistency**
    - For successful generation, files exist at policies/{policy_id}.json matching returned dicts; on failure, no new files created
    - **Property 12: IncidentPolicyGenerator Schema and Bounds**
    - For valid input, returns 3-5 dicts with correct schema (all required keys, valid enums)
    - **Property 13: IncidentPolicyGenerator Input Validation**
    - Whitespace-only returns []; strings > 2000 chars are truncated
    - **Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6, 7.7**

  - [x] 5.3 Implement `agents/drift_detector.py`
    - Create `DriftDetector` class with `save_snapshot(scan_id, findings, anomalies, total_waste) -> None` and `detect(findings) -> dict`
    - Implement atomic write: write to `.tmp` file then rename
    - Implement file lock with 10-second timeout using filelock library; always release in finally
    - Clean up stale `.tmp` files older than 60 seconds at start of save_snapshot
    - Implement max_snapshots=30 rotation (keep last 30)
    - Implement detect(): match findings by (resource_id, check_type) pair; calculate waste_delta, critical_delta, new/resolved findings
    - Generate LLM narrative for drift (2-3 sentences)
    - save_snapshot logs errors to stderr, never raises
    - detect returns {"drift": None, "reason": "insufficient history"} when < 2 snapshots; returns {"drift": None, "reason": "error"} on failure
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 1.7, 1.8, 1.9, 1.11, 14.6_

  - [x] 5.4 Write property tests for DriftDetector
    - **Property 14: DriftDetector Max Snapshots Invariant**
    - For any sequence of save_snapshot calls, entries in scan_history.json never exceed 30
    - **Property 15: DriftDetector Waste Delta Correctness**
    - For two snapshots with total_waste W_prev and W_curr, waste_delta = W_curr - W_prev
    - **Property 16: DriftDetector Finding Diff Correctness**
    - new_findings contains exactly findings in current but not previous; resolved_findings the inverse
    - **Property 17: DriftDetector Output Schema**
    - For history with ≥2 snapshots, returns dict with all required keys and correct types
    - **Validates: Requirements 8.3, 8.4, 8.5, 8.8**

- [x] 6. Implement Phase C platform agents (MultiAccountOrchestrator, JanitorScheduler)
  - [x] 6.1 Implement `agents/multi_account_orchestrator.py`
    - Create `MultiAccountOrchestrator` class with `run_all() -> dict` and `load_accounts() -> list[dict]`
    - Load accounts from `accounts.json`; return empty result if file missing/invalid/entries missing required fields
    - Validate role_arn matches `arn:aws:iam::\d{12}:role/.+`; skip invalid entries with error logged to stderr
    - Execute concurrent audits via ThreadPoolExecutor(max_workers=5) with 300s per-account timeout
    - Each account uses isolated findings store: `findings_store_{account_id}.json`
    - Inject account_id into every finding before aggregation
    - Sort by_account by priority (high → medium → low), then alphabetically by account_name within same priority
    - Catch `(Exception, concurrent.futures.TimeoutError, concurrent.futures.CancelledError)` per account
    - Calculate cross_account_duplicates by (resource_type, check_type) pairs
    - Return complete result dict with all required fields
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 14.4, 14.7_

  - [x] 6.2 Write property tests for MultiAccountOrchestrator
    - **Property 18: MultiAccountOrchestrator Fault Isolation**
    - When one account raises, remaining accounts succeed unaffected
    - **Property 19: MultiAccountOrchestrator Account ID Injection**
    - Every finding in aggregate_findings has account_id matching its source account
    - **Property 20: MultiAccountOrchestrator Priority Sorting**
    - by_account is sorted high → medium → low
    - **Validates: Requirements 9.2, 9.3, 9.4**

  - [x] 6.3 Implement `scheduler.py` at project root
    - Create `JanitorScheduler` class with `start()`, `stop()`, `get_status() -> dict`
    - Read JANITOR_SCHEDULE from env var (default "0 6 ** *"); validate as 5-field cron; fall back to default with warning on invalid
    - start() is non-blocking, idempotent (stops previous scheduler before starting new one)
    - Run one scan immediately on start if no scan has run today
    - Use APScheduler BackgroundScheduler with CronTrigger
    - Skip overlapping triggers (log warning if previous scan still running)
    - Log each run to scheduler.log using RotatingFileHandler (10MB, 3 backups)
    - Run as daemon thread (exits with main process)
    - Graceful shutdown on stop() within 5 seconds
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 14.5_

  - [x] 6.4 Write property tests for JanitorScheduler
    - **Property 21: JanitorScheduler Status Schema**
    - For any state, get_status() returns dict with keys: running, schedule, next_run, last_run, runs_completed with correct types
    - **Property 22: JanitorScheduler Idempotent Start**
    - Multiple start() calls result in exactly one running scheduler
    - **Validates: Requirements 10.4, 10.5**

- [x] 7. Checkpoint - Ensure all Phase C agent tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Wire MCP tools and orchestrator integration
  - [x] 8.1 Add MCP tool `interpret_query` to `mcp_server/aws_janitor_mcp.py`
    - Decorator `@mcp.tool()`, accepts `user_query: str`, returns ScanParameters dict
    - Import via `from agents.query_interpreter import QueryInterpreter` (direct import, no network transport)
    - Catch parameter validation errors and return error response without crashing server
    - _Requirements: 11.1, 11.7, 11.8, 11.9_

  - [x] 8.2 Add MCP tool `explain_remediation` to `mcp_server/aws_janitor_mcp.py`
    - Decorator `@mcp.tool()`, accepts `resource_id`, `finding`, `remediation_hcl`, `rollback_hcl`
    - Import via `from agents.explainer import RemediationExplainer`
    - Return safe default on internal failure
    - _Requirements: 11.2, 11.7, 11.8, 11.9_

  - [x] 8.3 Add MCP tool `suggest_policies` to `mcp_server/aws_janitor_mcp.py`
    - Decorator `@mcp.tool()`, accepts `findings: list`, `already_checked: list`
    - Import via `from agents.policy_suggester import PolicySuggester`
    - Return safe default on internal failure
    - _Requirements: 11.3, 11.7, 11.8, 11.9_

  - [x] 8.4 Add MCP tool `infer_resource_context` to `mcp_server/aws_janitor_mcp.py`
    - Decorator `@mcp.tool()`, accepts `resource_id`, `resource_name`, `existing_tags` (optional, defaults to {})
    - Import via `from agents.tagger import ResourceTagger`
    - Return safe default on internal failure
    - _Requirements: 11.4, 11.7, 11.8, 11.9_

  - [x] 8.5 Add MCP tool `detect_anomalies` to `mcp_server/aws_janitor_mcp.py`
    - Decorator `@mcp.tool()`, accepts `resources: list`, `findings: list`
    - Import via `from agents.anomaly_detector import AnomalyDetector`
    - Return safe default on internal failure
    - _Requirements: 11.5, 11.7, 11.8, 11.9_

  - [ ] 8.6 Add MCP tool `policy_from_incident` to `mcp_server/aws_janitor_mcp.py`
    - Decorator `@mcp.tool()`, accepts `incident_description: str`
    - Import via `from agents.incident_policy_generator import IncidentPolicyGenerator`
    - Return safe default on internal failure
    - _Requirements: 11.6, 11.7, 11.8, 11.9_

  - [ ] 8.7 Integrate AI agents into `orchestrator.py`
    - Add `execute_natural_language_audit(query: str)` method
    - Integrate AnomalyDetector post-scan (after FinOps + SecOps, before drift)
    - Integrate DriftDetector: save_snapshot after each audit, detect drift
    - On QueryInterpreter failure: fall back to full unfiltered scan
    - Pass safe defaults downstream when any agent fails
    - _Requirements: 1.10, 6.4, 11.10_

  - [ ] 8.8 Write unit tests for MCP tools (Phase B+C)
    - Test each new MCP tool is callable and returns valid schema
    - Test parameter validation error handling (missing/wrong type params)
    - Test safe default responses on internal agent failure
    - Mock `llm_client.get_client` for all tests
    - _Requirements: 11.1-11.9, 12.2_

- [ ] 9. Implement fixture mode compatibility
  - [ ] 9.1 Update fixture provider for Phase B+C features
    - Ensure fixture data contains at least one flaggable resource per resource_type ("elasticache", "ebs", "ec2")
    - Ensure fixture data contains at least one finding per check_type ("security_group", "encryption", "public_access")
    - Ensure all MCP tools produce conforming output schemas in fixture mode
    - _Requirements: 12.1, 12.2_

  - [ ] 9.2 Create `accounts.json` fixture for multi-account testing
    - Include 2-3 sample accounts with valid account_id, account_name, role_arn, region, priority fields
    - Used for development/testing when JANITOR_BACKEND=fixture
    - _Requirements: 12.3_

  - [ ] 9.3 Write integration tests for fixture mode
    - Test full pipeline: NL query → scan → anomaly → drift in fixture mode
    - Test all MCP tools return valid schemas in fixture mode
    - Test multi-account orchestration completes without exceptions
    - Verify no boto3 imports at runtime when JANITOR_BACKEND=fixture
    - Verify deterministic output when LLM is also mocked
    - _Requirements: 12.3, 12.4, 12.5_

- [ ] 10. Implement Streamlit UI integration
  - [ ] 10.1 Add NL query input and AI panels to `app.py`
    - Add natural language query text input with QueryInterpreter integration
    - Add remediation explanation panel (rendered alongside approval gate)
    - Add policy suggestions panel (shown post-scan)
    - Add anomaly detection results panel
    - Add drift report panel with narrative
    - Add scheduler controls (start/stop/status)
    - Add multi-account view
    - Implement session state caching with keys: nl_query_result, explanation_cache, policy_suggestions, resource_tags_cache, anomaly_results, drift_report, scheduler_instance, multi_account_results
    - Ensure `OPENROUTER_API_KEY` is never stored in `st.session_state` or rendered in UI
    - Ensure LLM-generated text rendered without `unsafe_allow_html=True`
    - _Requirements: 14.1, 14.8_

- [ ] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Never-raise guarantee validation
  - [ ] 12.1 Write property test for never-raise guarantee across all agents
    - **Property 1: Never-Raise Guarantee**
    - For any input (empty, malformed, None-like, adversarial), calling each agent's primary method does not raise
    - **Property 2: Safe Defaults on LLM Failure**
    - When LLM raises or returns unparseable output, each agent returns correct safe-default schema
    - **Validates: Requirements 1.1-1.9**

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All tests should mock `llm_client.get_client` (single mock target per Requirement 13.6)
- The implementation language is Python throughout
- File paths for new agents: `agents/query_interpreter.py`, `agents/explainer.py`, `agents/policy_suggester.py`, `agents/tagger.py`, `agents/anomaly_detector.py`, `agents/incident_policy_generator.py`, `agents/drift_detector.py`, `agents/multi_account_orchestrator.py`
- Scheduler lives at project root: `scheduler.py`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "2.1", "2.3", "2.5"] },
    { "id": 2, "tasks": ["2.2", "2.4", "2.6", "3.1", "3.3"] },
    { "id": 3, "tasks": ["3.2", "3.4", "4"] },
    { "id": 4, "tasks": ["5.1", "5.3"] },
    { "id": 5, "tasks": ["5.2", "5.4", "6.1", "6.3"] },
    { "id": 6, "tasks": ["6.2", "6.4", "7"] },
    { "id": 7, "tasks": ["8.1", "8.2", "8.3", "8.4", "8.5", "8.6"] },
    { "id": 8, "tasks": ["8.7", "8.8", "9.1", "9.2"] },
    { "id": 9, "tasks": ["9.3", "10.1"] },
    { "id": 10, "tasks": ["11", "12.1"] }
  ]
}
```
