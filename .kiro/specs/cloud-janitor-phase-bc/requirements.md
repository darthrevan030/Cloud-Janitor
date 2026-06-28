# Requirements Document

## Introduction

This document specifies the requirements for Phase B (Tier 2 AI Features) and Phase C (Tier 3 Platform Features) of the Cloud Janitor project. Phase B introduces LLM-powered intelligence via OpenRouter's API (OpenAI-compatible, default model: anthropic/claude-haiku-4-5) for natural language querying, remediation explanations, policy suggestions, resource tagging, and anomaly detection. Phase C adds platform capabilities including incident-based policy generation, drift detection with narrative, multi-account orchestration, and scheduled scans. All AI agents follow the safe-default error handling pattern (never raise from AI) and integrate with the existing FinOps → SecOps → Remediation Architect pipeline.

## Glossary

- **QueryInterpreter**: AI agent that translates natural language queries into structured scan parameters
- **RemediationExplainer**: AI agent that generates plain-English explanations of remediation plans
- **PolicySuggester**: AI agent that recommends additional policy checks based on findings patterns
- **ResourceTagger**: AI agent that infers environment, team, and owner context from resource metadata
- **AnomalyDetector**: AI agent that flags suspicious resources not caught by rule-based checks
- **IncidentPolicyGenerator**: AI agent that generates preventive scan policies from incident descriptions
- **DriftDetector**: Agent that compares scan snapshots over time and generates LLM narrative
- **MultiAccountOrchestrator**: Agent that runs concurrent audits across multiple AWS accounts
- **JanitorScheduler**: Agent that provides cron-based automated scans using APScheduler
- **Orchestrator**: The existing pipeline coordinator (FinOps → SecOps → Remediation Architect)
- **ScanParameters**: Structured output from QueryInterpreter containing resource_types, check_types, min_idle_days, intent_summary, and confidence
- **Finding**: A dict representing a detected issue (contains resource_id, severity, category/check_type, cost_estimate_monthly)
- **DriftReport**: Output from DriftDetector comparing two scan snapshots
- **Policy**: A JSON document describing a preventive scan check generated from an incident
- **AccountConfig**: A JSON entry in accounts.json describing an AWS account to audit
- **Snapshot**: A point-in-time record in scan_history.json containing findings, anomalies, and total_waste
- **Safe_Default**: A valid fallback value returned when an AI agent encounters an error (empty lists, zero counts, "unknown" strings)
- **MCP_Tool**: A function decorated with @mcp.tool() exposed through the MCP server interface
- **Fixture_Mode**: Development mode (JANITOR_BACKEND=fixture) where all features work without live AWS credentials
- **LLM_Client**: The shared `llm_client.py` module at project root that wraps the OpenRouter API; all AI agents import from it instead of using the OpenAI SDK directly
- **OpenRouter**: The LLM API gateway used for all AI calls; exposes an OpenAI-compatible endpoint at `https://openrouter.ai/api/v1`

## Requirements

### Requirement 1: AI Agent Error Resilience

**User Story:** As a system operator, I want all AI agents to handle failures gracefully, so that LLM unavailability never crashes the system or blocks the audit pipeline.

#### Acceptance Criteria

1. IF the OpenRouter API is unavailable or returns an error, THEN THE QueryInterpreter SHALL return safe defaults: empty resource_types, empty check_types, min_idle_days=7, confidence=0.0, intent_summary="Could not interpret query."
2. IF the OpenRouter API is unavailable or returns an error, THEN THE RemediationExplainer SHALL return all three explanation keys populated with "Explanation unavailable."
3. IF the OpenRouter API is unavailable or returns an error, THEN THE PolicySuggester SHALL return an empty list
4. IF the OpenRouter API is unavailable or returns an error, THEN THE ResourceTagger SHALL return env="unknown", team=None, owner=None, risk_level="low", confidence=0.0
5. IF the OpenRouter API is unavailable or returns an error, THEN THE AnomalyDetector SHALL return an empty list
6. IF the OpenRouter API is unavailable or returns an error, THEN THE IncidentPolicyGenerator SHALL return an empty list without writing any files
7. IF the OpenRouter API is unavailable or returns an error, THEN THE DriftDetector SHALL return a report with drift=None and reason="error"
8. THE system SHALL ensure that no AI agent raises an unhandled exception to callers regardless of input content or external service state; each agent SHALL catch all exceptions and return its defined safe default within 30 seconds of invocation
9. IF any AI agent returns a safe default due to an error (including transient errors), THEN THE system SHALL always log the failure event including the agent name and error type to stderr so that operators can detect degraded operation; logging SHALL NOT be skipped regardless of error duration or type
10. IF an AI agent returns a safe default due to an error, THEN THE Orchestrator SHALL continue executing subsequent pipeline steps using the safe default values without halting or requiring manual intervention
11. THE system SHALL route all LLM calls through `llm_client.py` at project root; no AI agent SHALL import `openai` or any LLM SDK directly

### Requirement 2: Natural Language Query Interpretation

**User Story:** As a cloud operator, I want to type natural language queries to find specific resources, so that I can quickly filter scans without memorizing parameter syntax.

#### Acceptance Criteria

1. WHEN a natural language query is submitted, THE QueryInterpreter SHALL return a confidence score between 0.0 and 1.0 inclusive
2. WHEN a natural language query is submitted, THE QueryInterpreter SHALL return only resource_types from the valid set: "elasticache", "ebs", "ec2", or an empty list
3. WHEN a natural language query is submitted, THE QueryInterpreter SHALL return only check_types from the valid set: "security_group", "encryption", "public_access", or an empty list
4. WHEN a natural language query is submitted, THE QueryInterpreter SHALL return a min_idle_days value that is a non-negative integer not exceeding 3650
5. IF an empty or whitespace-only query is submitted, THEN THE QueryInterpreter SHALL return safe defaults: resource_types=[], check_types=[], min_idle_days=7, confidence=0.0, intent_summary="Could not interpret query." without calling the LLM
6. IF the LLM returns invalid JSON, THEN THE QueryInterpreter SHALL return safe defaults: resource_types=[], check_types=[], min_idle_days=7, confidence=0.0, intent_summary="Could not interpret query."
7. WHEN a valid query is submitted, THE QueryInterpreter SHALL return an intent_summary as a string of at least 10 characters and at most 200 characters describing the parsed intent
8. WHEN a natural language query is submitted, THE QueryInterpreter SHALL return a dict containing exactly five keys: resource_types, check_types, min_idle_days, intent_summary, and confidence

### Requirement 3: Remediation Explanation

**User Story:** As a cloud operator, I want plain-English explanations of what remediation will do, so that I can make informed approval decisions.

#### Acceptance Criteria

1. WHEN a remediation plan is generated, THE RemediationExplainer SHALL produce a risk_explanation describing why the finding is dangerous in 2-3 sentences with a minimum length of 20 characters
2. WHEN a remediation plan is generated, THE RemediationExplainer SHALL produce a what_terraform_does description of the HCL change in 2-3 sentences with a minimum length of 20 characters
3. WHEN a remediation plan is generated, THE RemediationExplainer SHALL produce a what_rollback_restores description in 1-2 sentences with a minimum length of 20 characters
4. THE RemediationExplainer SHALL return a dict containing exactly three keys: risk_explanation, what_terraform_does, what_rollback_restores, where each value is a non-empty string
5. THE RemediationExplainer SHALL limit LLM output to 400 max_tokens to keep explanations concise for the UI panel
6. IF the remediation_hcl or rollback_hcl input is empty or whitespace-only, THEN THE RemediationExplainer SHALL return all three keys populated with "Explanation unavailable." without calling the LLM, regardless of which specific input is missing

### Requirement 4: Policy Suggestion

**User Story:** As a cloud operator, I want suggestions for additional policy checks after a scan, so that I can discover and enable relevant security and cost checks I may have missed.

#### Acceptance Criteria

1. WHEN scan findings are provided, THE PolicySuggester SHALL return between 0 and 5 suggestion dicts
2. THE PolicySuggester SHALL not return any suggestion whose implied check_type matches an entry in the already_checked list; this SHALL be enforced by post-processing filtering of LLM output in addition to any prompt-level instruction
3. WHEN suggestions are returned, THE PolicySuggester SHALL include for each: suggestion_id (non-empty slug string), title (non-empty string, maximum 80 characters), rationale (non-empty string, maximum 200 characters), query (non-empty string), and priority
4. WHEN suggestions are returned, THE PolicySuggester SHALL set priority to one of: "high", "medium", or "low"
5. WHEN findings is an empty list, THE PolicySuggester SHALL return between 1 and 5 general-purpose suggestions covering common security and cost checks
6. IF already_checked is an empty list, THEN THE PolicySuggester SHALL not apply any exclusion filtering to the suggestions

### Requirement 5: Resource Tagging Inference

**User Story:** As a cloud operator, I want automatic inference of environment, team, and owner from resource metadata, so that untagged resources can be properly categorized.

#### Acceptance Criteria

1. WHEN a resource is analyzed, THE ResourceTagger SHALL return a dict containing exactly five keys: env, team, owner, risk_level, and confidence
2. WHEN a resource is analyzed, THE ResourceTagger SHALL return an env value from the set: "production", "staging", "development", "unknown"
3. WHEN a resource is analyzed, THE ResourceTagger SHALL return a confidence score between 0.0 and 1.0 inclusive
4. IF confidence is strictly below the confidence_threshold (default 0.7), THEN THE ResourceTagger SHALL set team and owner to None; when confidence equals the threshold exactly, inferred values SHALL be preserved
5. IF existing_tags already contains env, team, or owner fields with non-empty, non-null values, THEN THE ResourceTagger SHALL skip inference for those fields and use the existing values unchanged; empty or null values SHALL be treated as absent and inference SHALL proceed normally
6. WHEN batch inference is requested with more than 10 resources, THE ResourceTagger SHALL split the input into chunks of at most 10 and issue one LLM call per chunk, returning results in the same order as the input list
7. WHEN a resource is analyzed, THE ResourceTagger SHALL return a risk_level from the set: "high", "medium", "low"

### Requirement 6: Anomaly Detection

**User Story:** As a cloud operator, I want AI-powered detection of suspicious resources beyond rule-based checks, so that unusual configurations or patterns are surfaced for review.

#### Acceptance Criteria

1. WHEN anomaly detection runs, THE AnomalyDetector SHALL exclude any resource whose resource_id already appears in the findings list passed as input, so that rule-based findings are never duplicated as anomalies
2. WHEN anomalies are detected, THE AnomalyDetector SHALL return a flat list of at most 20 anomaly dicts each containing: anomaly_id (string slug), resource_id (string), anomaly_type (string), description (string of 1-2 sentences), severity (string), and evidence (string describing the specific detail that triggered the anomaly)
3. WHEN anomalies are detected, THE AnomalyDetector SHALL set severity to one of: "high", "medium", "low"
4. WHEN the orchestrator completes FinOps and SecOps scanning, THE AnomalyDetector SHALL be invoked with the combined resource list and findings list before drift detection runs
5. IF the resources input list is empty, THEN THE AnomalyDetector SHALL return an empty list without calling the LLM; IF the list is non-empty but no anomalies are found by the LLM, THEN THE AnomalyDetector SHALL return an empty list
6. IF the LLM returns invalid JSON or an unexpected structure, THEN THE AnomalyDetector SHALL return an empty list without raising an exception

### Requirement 7: Incident-Based Policy Generation

**User Story:** As a security engineer, I want to generate preventive scan policies from incident descriptions, so that past incidents automatically inform future scanning rules.

#### Acceptance Criteria

1. WHEN a non-empty, non-whitespace incident description of at most 2000 characters is provided, THE IncidentPolicyGenerator SHALL generate between 3 and 5 policy dicts inclusive, discarding any excess policies beyond 5 and re-invoking the LLM if fewer than 3 are returned (up to 1 retry, returning whatever policies were generated if the retry also produces fewer than 3)
2. WHEN a policy is generated, THE IncidentPolicyGenerator SHALL write a JSON file to policies/{policy_id}.json, creating the policies/ directory if it does not exist
3. WHEN a policy is generated, THE IncidentPolicyGenerator SHALL set check_type to one of: "security_group", "encryption", "public_access", "idle_resource"
4. WHEN an empty, whitespace-only, or missing incident description is provided, THE IncidentPolicyGenerator SHALL return an empty list without writing any files
5. WHEN an incident description exceeds 2000 characters, THE IncidentPolicyGenerator SHALL truncate the input to the first 2000 characters before the LLM call
6. WHEN a policy with the same incident_hash already exists in the policies/ directory (matched by reading existing policy files and comparing the incident_hash field), THE IncidentPolicyGenerator SHALL return the existing policies without re-generating or writing files
7. WHEN policies are generated, THE IncidentPolicyGenerator SHALL include in each: policy_id (slug string), policy_name, resource_types (non-empty list), check_type, check_logic_description, rationale, query, generated_at (ISO 8601 timestamp), incident_hash (first 8 hex characters of the SHA-256 hash of the original un-truncated incident description), version (integer value 1)
8. IF a file write to policies/ fails due to I/O error, THEN THE IncidentPolicyGenerator SHALL return an empty list and shall not leave partially written files on disk
9. WHEN policies are generated, THE IncidentPolicyGenerator SHALL validate that each policy_id is unique within the returned list and that each resource_types list contains only values from the set: "elasticache", "ebs", "ec2"

### Requirement 8: Drift Detection

**User Story:** As a cloud operator, I want to see what changed between scans with a plain-English narrative, so that I can understand trends and track progress over time.

#### Acceptance Criteria

1. WHEN fewer than 2 snapshots exist in scan_history.json, THE DriftDetector SHALL return drift=None with reason="insufficient history"
2. WHEN a snapshot is saved, THE DriftDetector SHALL write atomically using a temporary file and rename strategy
3. WHEN a snapshot is saved, THE DriftDetector SHALL maintain at most 30 snapshots by rotating oldest entries when the count exceeds 30
4. WHEN a snapshot is saved, THE DriftDetector SHALL acquire a file lock with a timeout of 10 seconds and release it after writing completes, even if an error occurs
5. IF save_snapshot encounters any I/O or other error, THEN THE DriftDetector SHALL log the error to stderr and return without raising an exception, ensuring the caller can continue operating
6. WHEN detect is called with at least 2 snapshots in history, THE DriftDetector SHALL calculate waste_delta as current snapshot total_waste minus previous snapshot total_waste expressed as a float
7. WHEN detect is called with at least 2 snapshots in history, THE DriftDetector SHALL match findings across the two most recent snapshots by the (resource_id, check_type) pair to identify new_findings (present in current but not previous) and resolved_findings (present in previous but not current)
8. WHEN detect is called with at least 2 snapshots in history, THE DriftDetector SHALL calculate critical_delta as the count of findings with severity "CRITICAL" in the current snapshot minus the count in the previous snapshot
9. WHEN detect is called with at least 2 snapshots in history, THE DriftDetector SHALL generate an LLM narrative of 2-3 sentences describing the changes in plain English
10. WHEN detect is called with at least 2 snapshots in history, THE DriftDetector SHALL return a dict containing new_findings (list), resolved_findings (list), waste_delta (float), critical_delta (int), narrative (string), and compared_scans (list of two scan_id strings ordered [previous, current])

### Requirement 9: Multi-Account Orchestration

**User Story:** As an infrastructure team lead, I want to run concurrent audits across all our AWS accounts, so that I get a unified view of findings and waste across the organization.

#### Acceptance Criteria

1. WHEN multi-account audit runs, THE MultiAccountOrchestrator SHALL execute audits concurrently using ThreadPoolExecutor with a configurable max_workers (default 5) and a per-account timeout of 300 seconds
2. IF one account's audit fails or times out, THEN THE MultiAccountOrchestrator SHALL continue auditing remaining accounts and record the failure in that account's by_account entry with status="failed" and error containing the exception message
3. WHEN results are aggregated, THE MultiAccountOrchestrator SHALL inject account_id into each finding before aggregation
4. WHEN results are returned, THE MultiAccountOrchestrator SHALL sort by_account by priority: high first, then medium, then low, with accounts of equal priority ordered alphabetically by account_name
5. IF accounts.json is missing, fails JSON parsing, or contains entries missing any required field (account_id, account_name, role_arn, region, priority), THEN THE MultiAccountOrchestrator SHALL return an empty result dict with accounts_scanned=0 and empty by_account and aggregate_findings lists
6. WHEN multi-account audit completes, THE MultiAccountOrchestrator SHALL report cross_account_duplicates as the total count of findings that share the same (resource_type, check_type) pair with at least one finding in a different account
7. THE MultiAccountOrchestrator SHALL use an isolated findings store per account (findings_store_{account_id}.json) to ensure thread safety
8. WHEN results are returned, THE MultiAccountOrchestrator SHALL include in the result dict: accounts_scanned (int), total_findings (int), total_waste (float), critical_count (int), by_account (list), aggregate_findings (list), and cross_account_duplicates (int)

### Requirement 10: Scheduled Scans

**User Story:** As an operations team, I want automated cron-based scans, so that audits run on schedule without manual intervention.

#### Acceptance Criteria

1. WHEN the scheduler starts, THE JanitorScheduler SHALL read the cron schedule from the JANITOR_SCHEDULE environment variable (default "0 6 ** *"); IF the value is not a valid 5-field cron expression, THEN THE JanitorScheduler SHALL fall back to the default schedule and log a warning to stderr
2. WHEN no scan has run today and the scheduler starts, THE JanitorScheduler SHALL execute one scan immediately
3. WHEN a scheduled scan completes, THE JanitorScheduler SHALL append a log entry to scheduler.log at the project root containing the scan_id, timestamp, total_findings count, total_waste, and status (success or failed)
4. WHEN get_status is called, THE JanitorScheduler SHALL return a dict with keys: running (bool), schedule (string cron expression), next_run (ISO timestamp string or None), last_run (ISO timestamp string or None), runs_completed (int)
5. WHEN start is called multiple times, THE JanitorScheduler SHALL be idempotent by stopping any previous scheduler before starting a new one
6. WHEN stop is called, THE JanitorScheduler SHALL shut down cleanly within 5 seconds with no background threads remaining active
7. THE JanitorScheduler SHALL run as a daemon thread that always exits with the main process regardless of its internal state
8. IF a scheduled scan is still running when the next trigger fires, THEN THE JanitorScheduler SHALL skip the overlapping trigger and log a warning indicating the previous scan is still in progress

### Requirement 11: MCP Tool Integration

**User Story:** As a developer, I want all new features exposed as MCP tools, so that the Streamlit UI and other clients can invoke them through a consistent interface.

#### Acceptance Criteria

1. THE system SHALL expose interpret_query as an MCP tool that accepts user_query (string, required) and returns a ScanParameters dict
2. THE system SHALL expose explain_remediation as an MCP tool that accepts resource_id (string, required), finding (dict, required), remediation_hcl (string, required), rollback_hcl (string, required) and returns an explanation dict
3. THE system SHALL expose suggest_policies as an MCP tool that accepts findings (list, required) and already_checked (list, required) and returns a list of suggestion dicts
4. THE system SHALL expose infer_resource_context as an MCP tool that accepts resource_id (string, required), resource_name (string, required), existing_tags (dict, optional, defaults to empty dict) and returns an inference dict
5. THE system SHALL expose detect_anomalies as an MCP tool that accepts resources (list, required) and findings (list, required) and returns a list of anomaly dicts
6. THE system SHALL expose policy_from_incident as an MCP tool that accepts incident_description (string, required) and returns a list of policy dicts
7. THE system SHALL use direct import (no network transport) for all MCP tool implementations regardless of error conditions or backend mode
8. IF a required parameter is missing or of incorrect type, THEN THE MCP tool SHALL catch all parameter validation errors (including those from underlying libraries) and return an error response indicating the invalid parameter without crashing the server
9. IF any MCP tool invocation triggers an internal agent failure, THEN THE tool SHALL return the agent's safe default response rather than raising an unhandled exception
10. THE system SHALL provide a `llm_client.py` module at project root exposing `get_client() -> openai.OpenAI` and `DEFAULT_MODEL: str`; all AI agents SHALL use `get_client()` for LLM calls and `DEFAULT_MODEL` as the model string

### Requirement 12: Fixture Mode Compatibility

**User Story:** As a developer, I want all Phase B and C features to work with JANITOR_BACKEND=fixture, so that I can develop and test without live AWS credentials.

#### Acceptance Criteria

1. WHEN JANITOR_BACKEND is set to "fixture", THE FixtureProvider SHALL return fixture data containing at least one flaggable resource for each resource_type ("elasticache", "ebs", "ec2") and at least one finding for each check_type ("security_group", "encryption", "public_access") so that all Phase B and C agents can exercise their detection logic
2. WHEN JANITOR_BACKEND is set to "fixture", THE system SHALL return responses from all MCP tools (interpret_query, explain_remediation, suggest_policies, infer_resource_context, detect_anomalies, policy_from_incident) that conform to the same output schema (required keys and value types) as when running against a live backend
3. WHEN JANITOR_BACKEND is set to "fixture", THE system SHALL allow end-to-end execution of the full pipeline (NL query interpretation, FinOps scan, SecOps scan, anomaly detection, drift detection, multi-account orchestration) with all operations completing without raising unhandled exceptions and producing non-empty findings
4. THE system SHALL ensure no Phase B or C feature imports or invokes AWS SDK calls (boto3) at runtime when JANITOR_BACKEND is set to "fixture"; OpenRouter API calls for AI agents SHALL be mockable at the test level via patching `llm_client.get_client` in all backend modes
5. IF JANITOR_BACKEND is set to "fixture" and an AI agent's LLM dependency is also mocked, THEN THE system SHALL produce deterministic output for a given fixture input, enabling repeatable automated test assertions

### Requirement 13: LLM Client Configuration

**User Story:** As a developer or operator, I want a single configurable LLM client module, so that the API provider and model can be swapped without touching agent code.

#### Acceptance Criteria

1. THE system SHALL provide `llm_client.py` at the project root exposing `get_client() -> openai.OpenAI` and `DEFAULT_MODEL: str`
2. `get_client()` SHALL return an `openai.OpenAI` instance configured with `base_url="https://openrouter.ai/api/v1"` and `api_key` read from the `OPENROUTER_API_KEY` environment variable
3. `DEFAULT_MODEL` SHALL be set to the value of the `JANITOR_LLM_MODEL` environment variable, defaulting to `"anthropic/claude-haiku-4-5"` if the variable is unset
4. IF `OPENROUTER_API_KEY` is not set, THEN `get_client()` SHALL raise `EnvironmentError` with a message indicating the missing variable; AI agents SHALL catch this and return their safe defaults
5. THE `openai>=1.0.0` package SHALL be listed in `requirements.txt`; the `anthropic` package SHALL NOT appear in `requirements.txt`
6. WHEN tests mock LLM calls, they SHALL patch `llm_client.get_client` — not any individual agent's import — so that all agents are covered by a single mock target

### Requirement 14: Security

**User Story:** As a security-conscious operator, I want the system to handle sensitive data and user input safely, so that credentials are not leaked and untrusted input cannot compromise the system.

#### Acceptance Criteria

1. THE system SHALL never log, store, or include any sensitive data including `OPENROUTER_API_KEY` or `JANITOR_LLM_MODEL` values in any output file, log entry, error message, Streamlit session state, or API response; any partial exposure of sensitive data SHALL be treated as a complete violation
2. THE IncidentPolicyGenerator SHALL validate that each LLM-returned policy_id matches the pattern `^[a-z0-9\-]+$` before constructing the file path; policies with non-conforming IDs SHALL be skipped and logged to stderr
3. THE system SHALL ensure that `findings_store.json`, `scan_history.json`, `savings_ledger.json`, `scheduler.log`, and `policies/*.json` are listed in `.gitignore` to prevent accidental credential or infrastructure data commits
4. THE MultiAccountOrchestrator SHALL validate each role_arn in accounts.json matches `arn:aws:iam::\d{12}:role/.+` before use; entries failing validation SHALL be skipped with an error logged to stderr and the system SHALL continue processing remaining valid entries from the same file
5. THE JanitorScheduler SHALL rotate `scheduler.log` when it exceeds 10MB using `logging.handlers.RotatingFileHandler` with a maximum of 3 backup files
6. THE DriftDetector SHALL delete any stale `scan_history.json.tmp` file older than 60 seconds at the start of each `save_snapshot` call, before acquiring the file lock
7. THE MultiAccountOrchestrator SHALL catch `(Exception, concurrent.futures.TimeoutError, concurrent.futures.CancelledError)` in the futures completion loop to ensure all account failures are handled regardless of exception type
8. THE system SHALL never store `OPENROUTER_API_KEY` in `st.session_state` or render it in any Streamlit UI element
