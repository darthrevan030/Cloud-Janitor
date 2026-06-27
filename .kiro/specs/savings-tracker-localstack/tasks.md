# Implementation Plan: Savings Tracker & LocalStack Integration

## Overview

This plan implements four sub-features for the Cloud Janitor project: a persistent savings ledger, LocalStack wiring for demo-mode Terraform execution, a SPEC_COMPLIANCE.md generator script, and a streaming agent reasoning logger with Streamlit panel. Tasks are ordered to build foundational components first (savings tracker, reasoning logger), then infrastructure (LocalStack, Makefile), then integration (orchestrator wiring, Streamlit panel), and finally the compliance generator.

## Tasks

- [x] 1. Implement Savings Tracker core module
  - [x] 1.1 Create `savings.py` with SavingsTracker class
    - Implement `__init__`, `_load_ledger`, `_write_ledger`, `_compute_monthly_savings`, `_recalculate_total` methods
    - Implement `record_run(resources_remediated)` with duplicate detection via `run_id` matching
    - Implement `get_savings_summary()` returning `total_lifetime_monthly`, `total_lifetime_annual`, `total_runs`, `last_run_savings`
    - Handle missing/corrupt ledger file gracefully (return empty structure)
    - Use `findings_store.json` → `scan_id` as `run_id` and `completed_at` as `timestamp`
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4_

  - [x] 1.2 Write property test: RunEntry schema and field correctness
    - **Property 1: RunEntry schema and field correctness**
    - **Validates: Requirements 1.2, 2.1**

  - [x] 1.3 Write property test: Monthly savings computation
    - **Property 2: Monthly savings computation**
    - **Validates: Requirements 2.2**

  - [x] 1.4 Write property test: Recalculate-from-source invariant
    - **Property 3: Recalculate-from-source invariant**
    - **Validates: Requirements 2.3, 2.4**

  - [x] 1.5 Write property test: Duplicate run idempotency
    - **Property 4: Duplicate run idempotency**
    - **Validates: Requirements 3.1, 3.3**

  - [x] 1.6 Write property test: Savings summary correctness
    - **Property 5: Savings summary correctness**
    - **Validates: Requirements 4.1, 4.2, 4.4**

- [x] 2. Implement Reasoning Logger and agent integration
  - [x] 2.1 Create `agents/reasoning_logger.py` with ReasoningLogger class
    - Implement `__init__` with configurable `log_path` defaulting to `agent_reasoning.log`
    - Implement `truncate()` to clear log at audit start
    - Implement `emit(agent, event_type, resource_id, message)` appending one JSON line per call
    - Validate `event_type` against allowed set: check, finding, skip, decision, handoff
    - Truncate `agent` to 64 chars, `message` to 500 chars silently
    - On filesystem errors: print to stderr, do NOT raise
    - _Requirements: 9.4, 9.5, 9.6, 9.7_

  - [x] 2.2 Integrate ReasoningLogger into FinOps Auditor
    - Add `emit("finops_auditor", "check", ...)` at scan start and per-resource check
    - Add `emit("finops_auditor", "finding", ...)` per flagged resource
    - Add `emit("finops_auditor", "skip", ...)` per resource below threshold
    - Add `emit("finops_auditor", "handoff", ...)` at scan complete
    - _Requirements: 9.1_

  - [x] 2.3 Integrate ReasoningLogger into SecOps Guard
    - Add `emit("secops_guard", "check", ...)` at scan start and per-rule
    - Add `emit("secops_guard", "finding", ...)` per violation detected
    - Add `emit("secops_guard", "handoff", ...)` at scan complete
    - _Requirements: 9.2_

  - [x] 2.4 Integrate ReasoningLogger into Remediation Architect
    - Add `emit("remediation_architect", "check", ...)` at start and per-dependency check
    - Add `emit("remediation_architect", "decision", ...)` per result and per HCL generated
    - Add `emit("remediation_architect", "handoff", ...)` at planning complete
    - _Requirements: 9.3_

  - [x] 2.5 Write property test: Reasoning logger emits valid structured JSON
    - **Property 8: Reasoning logger emits valid structured JSON**
    - Use `st.text(alphabet=st.characters(blacklist_categories=('Cs',)))` for message and agent fields — must cover quotes, backslashes, and unicode characters, NOT just default ASCII
    - **Validates: Requirements 9.4, 9.9**

  - [x] 2.6 Write property test: Reasoning logger sequential append
    - **Property 9: Reasoning logger sequential append**
    - **Validates: Requirements 9.6**

- [x] 3. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. LocalStack wiring and demo infrastructure
  - [x] 4.1 Replace `terraform` with `tflocal` in `mcp_server/aws_janitor_mcp.py`
    - Change `["terraform", "init", "-backend=false"]` to `["tflocal", "init", "-backend=false"]`
    - Change `["terraform", "validate"]` to `["tflocal", "validate"]`
    - _Requirements: 6.1_

  - [x] 4.2 Replace `terraform` with `tflocal` in `.kiro/hooks/pre-remediation.sh`
    - Replace all occurrences of `terraform -chdir=` with `tflocal -chdir=`
    - _Requirements: 6.1_

  - [x] 4.3 Create `docker-compose.yml` at project root
    - Define `localstack` service with `localstack/localstack:latest` image
    - Expose port 4566, set SERVICES=ec2,elasticache,s3,ebs, DEFAULT_REGION=us-east-1
    - Mount Docker socket volume
    - _Requirements: 6.3_

  - [x] 4.4 Create `Makefile` at project root with `demo` target
    - Run `docker-compose up -d` to start LocalStack
    - Poll LocalStack health endpoint (`http://localhost:4566/_localstack/health`) every 2s, max 30 attempts with progress dots
    - Launch Streamlit dashboard via `streamlit run app.py` as the final step
    - The Makefile SHALL NOT invoke `tflocal apply` directly — the apply happens inside `orchestrator.py` when the user types `APPROVE <resource-id>` through the Streamlit UI
    - Exit non-zero with error message if health check exceeds 60 seconds
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 4.5 Wire `tflocal apply -auto-approve` into orchestrator approval flow
    - In `orchestrator.py` `approve()` method, insert the `tflocal apply` call AFTER `_run_pre_remediation_hook()` returns None (success) and BEFORE `_run_post_remediation_hook()` is called
    - The execution sequence in `approve()` is: (1) validate input → (2) `_run_pre_remediation_hook()` (tflocal validate) → (3) **INSERT `tflocal apply -auto-approve` HERE** → (4) `_run_post_remediation_hook()` (audit.log) → (5) `_savings_tracker.record_run()`
    - Output directory for `tflocal apply` is `self.project_root / "output"` (where `remediation.tf` lives)
    - Code to insert:

      ```python
      apply_result = subprocess.run(
          ["tflocal", "apply", "-auto-approve"],
          capture_output=True,
          text=True,
          timeout=120,
          cwd=str(self.project_root / "output"),
      )
      if apply_result.returncode != 0:
          error = apply_result.stderr.strip() or apply_result.stdout.strip()
          return ApprovalResult(
              success=False,
              message=f"tflocal apply failed: {error}",
              resource_id=plan.resource_id,
          )
      ```

    - If `tflocal apply` returns non-zero exit code, surface stderr as error message and halt pipeline without proceeding to post-remediation hook or savings tracking
    - This is triggered exclusively by the user typing `APPROVE <resource-id>` in the UI, NOT by the Makefile
    - _Requirements: 6.4, 6.5_

  - [x] 4.6 Update `requirements.txt` to add `terraform-local`
    - Add `terraform-local>=0.18.0` to requirements.txt
    - _Requirements: 6.2_

- [x] 5. Orchestrator integration with SavingsTracker
  - [x] 5.1 Wire SavingsTracker into Orchestrator
    - Import and instantiate `SavingsTracker` in `Orchestrator.__init__`
    - Call `self._savings_tracker.record_run(resources_remediated=[resource_id])` in `approve()` method after successful execution, after `_run_post_remediation_hook`
    - Ensure `record_run` is NOT called from `_run_post_remediation_hook` to avoid double-counting
    - Handle `FileNotFoundError` and `OSError` from savings tracker gracefully (log warning, don't block approval)
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 5.2 Add ReasoningLogger truncation at audit start in Orchestrator
    - Instantiate ReasoningLogger in Orchestrator and call `truncate()` at the beginning of `execute_audit()`
    - Pass the shared logger instance to each agent
    - _Requirements: 9.5_

  - [x] 5.3 Write unit tests for Orchestrator → SavingsTracker wiring
    - Verify `record_run()` is called from `approve()` with correct arguments
    - Verify `record_run()` is NOT called from `_run_post_remediation_hook`
    - Verify savings tracker errors don't block approval
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 6. Update .gitignore and project configuration
  - [x] 6.1 Add runtime files to `.gitignore`
    - Add `savings_ledger.json` to `.gitignore`
    - Add `agent_reasoning.log` to `.gitignore`
    - _Requirements: 1.4, 9.8_

- [x] 7. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement SPEC_COMPLIANCE.md generator
  - [x] 8.1 Create `generate_spec_compliance.py` at project root
    - Read and parse `.kiro/specs/tasks.md` for checkbox lines (`- [x]`, `- [ ]`, `- [-]`)
    - Implement keyword-to-file mapping table per requirements 8.3
    - Verify file existence for done tasks
    - Output `SPEC_COMPLIANCE.md` as a 4-column Markdown table with columns: `#`, `Task`, `Status`, `Artifact Verified`
    - Exit with non-zero code if tasks.md is missing
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 8.2 Create Git post-commit hook
    - Create `.git/hooks/post-commit` that runs `python3 generate_spec_compliance.py && git add SPEC_COMPLIANCE.md`
    - Make the hook executable
    - _Requirements: 8.6_

  - [x] 8.3 Write property test: Compliance generator parsing and mapping
    - **Property 6: Compliance generator parsing and mapping**
    - **Validates: Requirements 8.2, 8.3**

  - [x] 8.4 Write property test: Compliance generator output format
    - **Property 7: Compliance generator output format**
    - Verify output is a valid 4-column Markdown table with headers: `#`, `Task`, `Status`, `Artifact Verified`
    - **Validates: Requirements 8.4**

- [ ] 9. Implement Streamlit Reasoning Panel
  - [x] 9.1 Add reasoning log panel to `app.py`
    - Implement `reasoning_log_panel()` using `@st.fragment(run_every=1)` for Streamlit >= 1.33
    - Implement fallback polling with background thread for older Streamlit
    - Read `agent_reasoning.log`, parse JSONL, skip malformed lines silently
    - Color-code events: check=#9e9e9e, finding=#ff9800, skip=#bdbdbd, decision=#2196f3, handoff=bold
    - Insert section headers when agent name changes between consecutive events
    - Auto-scroll to latest event during audit execution
    - Clear previous reasoning display when new audit starts
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 9.2 Write property test: Agent section header transitions
    - **Property 10: Agent section header transitions**
    - **Validates: Requirements 10.3**

  - [ ] 9.3 Write property test: Malformed line resilience
    - **Property 11: Malformed line resilience**
    - **Validates: Requirements 10.6**

- [ ] 10. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The implementation language is Python, matching the existing codebase
- `hypothesis` is already in requirements.txt for property-based testing
- All agents already exist — tasks 2.2–2.4 modify existing files to add emit calls
- **Key design constraint**: `tflocal apply -auto-approve` is triggered exclusively by `orchestrator.py` when the user types `APPROVE <resource-id>` — the Makefile only starts LocalStack and launches Streamlit
- **Property 8 testing note**: Use `st.text(alphabet=st.characters(blacklist_categories=('Cs',)))` for agent/message fields to cover quotes, backslashes, and unicode — not just default ASCII

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "6.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "1.6", "2.2", "2.3", "2.4", "4.3", "4.6"] },
    { "id": 2, "tasks": ["2.5", "2.6", "4.1", "4.2"] },
    { "id": 3, "tasks": ["4.4", "4.5", "5.1", "5.2"] },
    { "id": 4, "tasks": ["5.3", "8.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "9.1"] },
    { "id": 6, "tasks": ["9.2", "9.3"] }
  ]
}
```
