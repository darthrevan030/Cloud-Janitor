# Spec Compliance Report

Generated: 2026-07-01T18:54:11Z

| # | Task | Status | Artifact Verified |
|---|------|--------|-------------------|
| 1 | 1. Create foundational modules and path configuration | ✅ Done | no mapping |
| 2 | 1.1 Create `core/paths.py` centralized path configuration with directory creation helper | ✅ Done | no mapping |
| 3 | 1.2 Create `core/error_telemetry.py` structured error module | ✅ Done | no mapping |
| 4 | 1.3 Create `bin/tflocal` wrapper script | ✅ Done | no mapping |
| 5 | 1.4 Write smoke tests for `bin/tflocal` wrapper (`tests/test_bin_tflocal.py`) | ✅ Done | no mapping |
| 6 | 1.5 Write property tests for `core/error_telemetry.py` | ✅ Done | no mapping |
| 7 | 1.6 Write unit tests for path configuration and directory creation (`tests/test_path_config.py`) | ✅ Done | no mapping |
| 8 | 2. Implement security validation layer | ✅ Done | no mapping |
| 9 | 2.1 Implement TF_CMD validation in Orchestrator | ✅ Done | no mapping |
| 10 | 2.2 Write property test for TF_CMD validation | ✅ Done | no mapping |
| 11 | 2.3 Write unit tests for TF_CMD PATH resolution (`tests/test_tf_cmd_validation.py`) | ✅ Done | no mapping |
| 12 | 2.4 Implement resource ID extraction with allowlist validation | ✅ Done | no mapping |
| 13 | 2.5 Write property test for resource ID extraction | ✅ Done | no mapping |
| 14 | 3. Implement persistent approval gates | ✅ Done | "APPROVE" found in codebase |
| 15 | 3.1 Implement `ApprovalGateStore` class | ✅ Done | no mapping |
| 16 | 3.2 Integrate `ApprovalGateStore` into Orchestrator approval/rollback flow | ✅ Done | output/rollbacks/ exists |
| 17 | 3.3 Write property tests for approval gate persistence | ✅ Done | "APPROVE" found in codebase |
| 18 | 3.4 Write property test for gate lockout invariant | ✅ Done | no mapping |
| 19 | 3.5 Write unit tests for atomic write failure (`tests/test_gate_persistence.py`) | ✅ Done | no mapping |
| 20 | 4. Checkpoint - Ensure all tests pass | ✅ Done | no mapping |
| 21 | 5. Implement rollback path with Terraform execution | ❌ Pending | — |
| 22 | 5.1 Implement Terraform validate + apply rollback flow | ❌ Pending | — |
| 23 | 5.2 Write property test for rollback failure propagation | ❌ Pending | — |
| 24 | 5.3 Write unit tests for rollback Terraform sequence (`tests/test_rollback_flow.py`) | ❌ Pending | — |
| 25 | 6. Implement pre-remediation hook full validation | ❌ Pending | — |
| 26 | 6.1 Implement `_run_pre_remediation_hook_full()` method | ❌ Pending | — |
| 27 | 6.2 Write property tests for pre-remediation hook | ❌ Pending | — |
| 28 | 6.3 Write unit tests for hook timeout (`tests/test_pre_hook.py`) | ❌ Pending | — |
| 29 | 7. Implement data integrity features | ❌ Pending | — |
| 30 | 7.1 Implement findings store schema versioning | ❌ Pending | — |
| 31 | 7.2 Implement reasoning log append mode with separator | ❌ Pending | — |
| 32 | 7.3 Write property test for schema version validation | ❌ Pending | — |
| 33 | 7.4 Write property test for reasoning log append preservation | ❌ Pending | — |
| 34 | 7.5 Write unit tests for schema version WARNING (`tests/test_schema_version.py`) | ❌ Pending | — |
| 35 | 7.6 Write unit tests for reasoning log rotation (`tests/test_reasoning_log.py`) | ❌ Pending | — |
| 36 | 8. Implement savings tracker broad exception handling | ❌ Pending | — |
| 37 | 8.1 Wrap `savings_tracker.record_run()` with broad exception handling | ❌ Pending | — |
| 38 | 8.2 Write property test for savings tracker exception swallowing | ❌ Pending | — |
| 39 | 9. Checkpoint - Ensure all tests pass | ❌ Pending | — |
| 40 | 10. Implement UI–Orchestrator contract alignment | ❌ Pending | — |
| 41 | 10.1 Refactor `app.py` audit delegation to use public Orchestrator API only | ❌ Pending | — |
| 42 | 10.2 Write unit tests for UI delegation (`tests/test_ui_delegation.py`) | ❌ Pending | — |
| 43 | 10.3 Implement NL audit delegation with feature detection | ❌ Pending | — |
| 44 | 10.4 Write unit tests for NL audit feature detection (`tests/test_nl_audit.py`) | ❌ Pending | — |
| 45 | 10.5 Implement explicit Phase B/C agent imports | ❌ Pending | — |
| 46 | 10.6 Write unit tests for agent ImportError handling (`tests/test_agent_imports.py`) | ❌ Pending | — |
| 47 | 10.7 Update `app.py` to import paths from `core/paths.py` | ❌ Pending | — |
| 48 | 10.8 Update Orchestrator to use `core/paths.py` and call `ensure_output_dirs()` | ❌ Pending | — |
| 49 | 11. Wire structured error telemetry into Orchestrator | ❌ Pending | — |
| 50 | 11.1 Integrate `core/error_telemetry.py` into Orchestrator error handling | ❌ Pending | — |
| 51 | 11.2 Write unit tests for `_classify_error()` (`tests/test_error_classification.py`) | ❌ Pending | — |
| 52 | 11.3 Surface structured error fields in Streamlit UI | ❌ Pending | — |
| 53 | 12. Update SPEC_COMPLIANCE.md | ❌ Pending | — |
| 54 | 12.1 Update SPEC_COMPLIANCE.md to reflect NL Audit feature status | ❌ Pending | — |
| 55 | 13. Final checkpoint - Ensure all tests pass | ❌ Pending | — |
| 56 | 1. Create .kiro/ directory structure and commit | ✅ Done | no mapping |
| 57 | 2. Write requirements.md with all user stories | ✅ Done | .kiro/specs/audit-remediation/requirements.md exists |
| 58 | 3. Write design.md with architecture + data flow | ✅ Done | .kiro/specs/audit-remediation/design.md exists |
| 59 | 4. Write fixture JSON for Cost Explorer (3 resources, 2 flaggable) | ✅ Done | fixtures/ exists |
| 60 | 5. Write fixture JSON for Config/Inspector (2 security findings) | ✅ Done | fixtures/ exists |
| 61 | 1. Implement aws_janitor_mcp.py with MCP protocol | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 62 | 2. Implement get_cost_data() → reads Cost Explorer fixture | ✅ Done | fixtures/ exists |
| 63 | 3. Implement get_security_data() → reads Inspector fixture | ✅ Done | fixtures/ exists |
| 64 | 4. Implement validate_hcl() → shells to terraform validate | ✅ Done | no mapping |
| 65 | 5. Write mcp_server/README.md | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 66 | 1. FinOps Auditor — calls MCP, produces findings[], writes findings_store.json | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 67 | 2. SecOps Guard — calls MCP, appends to findings_store.json | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 68 | 3. Remediation Architect — reads findings, dependency check, generates HCL | ✅ Done | agents/remediation_architect.py exists |
| 69 | 4. Rollback HCL generation (alongside remediation, not after) | ✅ Done | agents/remediation_architect.py exists |
| 70 | 5. findings_store.json schema validation | ✅ Done | output/findings_store.json exists |
| 71 | 1. pre-remediation.sh — terraform validate gate | ✅ Done | agents/remediation_architect.py exists |
| 72 | 2. post-remediation.sh — audit.log append | ✅ Done | agents/remediation_architect.py exists |
| 73 | 3. Wire hooks into orchestrator call sequence | ✅ Done | no mapping |
| 74 | 1. Approval gate — parse "APPROVE \<id\>", reject malformed input | ✅ Done | no mapping |
| 75 | 2. Rollback gate — parse "ROLLBACK \<id\>" + "CONFIRM ROLLBACK \<id\>" | ✅ Done | no mapping |
| 76 | 3. Audit log writer (append-only) | ✅ Done | no mapping |
| 77 | 4. Error states: dependency found, validate fails, malformed approval | ✅ Done | "APPROVE" found in codebase |
| 78 | 1. Streamlit layout — 4 panels (agent feed, findings, diff, audit log) | ✅ Done | audit log writer found |
| 79 | 2. Agent activity feed with live status dots | ✅ Done | no mapping |
| 80 | 3. Side-by-side diff view (remediation HCL vs rollback HCL) | ✅ Done | agents/remediation_architect.py exists |
| 81 | 4. Approval input field + confirmation display | ✅ Done | no mapping |
| 82 | 5. Savings counter | ✅ Done | no mapping |
| 83 | 1. End-to-end Ghost Cluster scenario run (no errors) | ❌ Pending | — |
| 84 | 2. Rollback flow run (no errors) | ❌ Pending | — |
| 85 | 3. Error state test: approval typo rejected gracefully | ❌ Pending | — |
| 86 | 4. Rehearse 6-min demo script 3x | ❌ Pending | — |
| 87 | 5. Record demo video for Devpost submission | ❌ Pending | — |
| 88 | 6. Write Devpost submission copy | ❌ Pending | — |
| 89 | 1. Set up shared LLM infrastructure and project dependencies | ❌ Pending | — |
| 90 | 1.1 Create `core/llm_client.py` | ✅ Done | no mapping |
| 91 | 1.2 Update `requirements.txt` with new dependencies | ✅ Done | .kiro/specs/audit-remediation/requirements.md exists |
| 92 | 1.3 Update `.gitignore` with sensitive data files | ✅ Done | no mapping |
| 93 | 1.4 Write unit tests for `core/llm_client.py` | ✅ Done | no mapping |
| 94 | 2. Implement Phase B AI agents (QueryInterpreter, RemediationExplainer, PolicySuggester) | ✅ Done | agents/remediation_architect.py exists |
| 95 | 2.1 Implement `agents/query_interpreter.py` | ✅ Done | no mapping |
| 96 | 2.2 Write property test for QueryInterpreter output validity | ✅ Done | no mapping |
| 97 | 2.3 Implement `agents/explainer.py` | ✅ Done | no mapping |
| 98 | 2.4 Write property test for RemediationExplainer schema completeness | ✅ Done | agents/remediation_architect.py exists |
| 99 | 2.5 Implement `agents/policy_suggester.py` | ✅ Done | no mapping |
| 100 | 2.6 Write property test for PolicySuggester output bounds and exclusion | ✅ Done | no mapping |
| 101 | 3. Implement Phase B AI agents (ResourceTagger, AnomalyDetector) | ✅ Done | no mapping |
| 102 | 3.1 Implement `agents/tagger.py` | ✅ Done | no mapping |
| 103 | 3.2 Write property tests for ResourceTagger | ✅ Done | no mapping |
| 104 | 3.3 Implement `agents/anomaly_detector.py` | ✅ Done | no mapping |
| 105 | 3.4 Write property tests for AnomalyDetector | ✅ Done | no mapping |
| 106 | 4. Checkpoint - Ensure all Phase B agent tests pass | ✅ Done | no mapping |
| 107 | 5. Implement Phase C platform agents (IncidentPolicyGenerator, DriftDetector) | ❌ Pending | — |
| 108 | 5.1 Implement `agents/incident_policy_generator.py` | ✅ Done | no mapping |
| 109 | 5.2 Write property tests for IncidentPolicyGenerator | ✅ Done | no mapping |
| 110 | 5.3 Implement `agents/drift_detector.py` | ✅ Done | no mapping |
| 111 | 5.4 Write property tests for DriftDetector | ✅ Done | no mapping |
| 112 | 6. Implement Phase C platform agents (MultiAccountOrchestrator, JanitorScheduler) | ✅ Done | no mapping |
| 113 | 6.1 Implement `agents/multi_account_orchestrator.py` | ✅ Done | no mapping |
| 114 | 6.2 Write property tests for MultiAccountOrchestrator | ✅ Done | no mapping |
| 115 | 6.3 Implement `scheduler.py` at project root | ✅ Done | no mapping |
| 116 | 6.4 Write property tests for JanitorScheduler | ✅ Done | no mapping |
| 117 | 7. Checkpoint - Ensure all Phase C agent tests pass | ✅ Done | no mapping |
| 118 | 8. Wire MCP tools and orchestrator integration | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 119 | 8.1 Add MCP tool `interpret_query` to `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 120 | 8.2 Add MCP tool `explain_remediation` to `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 121 | 8.3 Add MCP tool `suggest_policies` to `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 122 | 8.4 Add MCP tool `infer_resource_context` to `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 123 | 8.5 Add MCP tool `detect_anomalies` to `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 124 | 8.6 Add MCP tool `policy_from_incident` to `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 125 | 8.7 Integrate AI agents into `orchestrator.py` | ✅ Done | no mapping |
| 126 | 8.8 Write unit tests for MCP tools (Phase B+C) | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 127 | 9. Implement fixture mode compatibility | ✅ Done | fixtures/ exists |
| 128 | 9.1 Update fixture provider for Phase B+C features | ✅ Done | fixtures/ exists |
| 129 | 9.2 Create `accounts.json` fixture for multi-account testing | ✅ Done | fixtures/ exists |
| 130 | 9.3 Write integration tests for fixture mode | ✅ Done | fixtures/ exists |
| 131 | 10. Implement Streamlit UI integration | ✅ Done | app.py exists |
| 132 | 10.1 Add NL query input and AI panels to `app.py` | ✅ Done | app.py exists |
| 133 | 11. Final checkpoint - Ensure all tests pass | ✅ Done | no mapping |
| 134 | 12. Never-raise guarantee validation | ✅ Done | no mapping |
| 135 | 12.1 Write property test for never-raise guarantee across all agents | ✅ Done | no mapping |
| 136 | 1. Batch 1 — Core infrastructure (flat layout) | ❌ Pending | — |
| 137 | 1.1 Create `pyproject.toml` with build system, dependencies, and scripts | ❌ Pending | — |
| 138 | 1.2 Create `logging_config.py` at project root | ❌ Pending | — |
| 139 | 1.3 Add retry logic to `core/llm_client.py` | ❌ Pending | — |
| 140 | 1.4 Create `cli.py` at project root with Click CLI | ❌ Pending | — |
| 141 | 1.5 Update stub providers with warning pattern | ❌ Pending | — |
| 142 | 2. Checkpoint — Verify Batch 1 | ❌ Pending | — |
| 143 | 3. Batch 1 — Tests for core infrastructure | ❌ Pending | — |
| 144 | 3.1 Write unit tests for CLI (`tests/test_cli.py`) | ❌ Pending | — |
| 145 | 3.2 Write unit tests for logging config (`tests/test_logging_config.py`) | ❌ Pending | — |
| 146 | 3.3 Write unit tests for LLM retry logic (`tests/test_llm_retry.py`) | ❌ Pending | — |
| 147 | 3.4 Write unit tests for stub providers (`tests/test_stub_providers.py`) | ❌ Pending | — |
| 148 | 3.5 Write unit tests for version logic in `cli.py` (`tests/test_version.py`) | ❌ Pending | — |
| 149 | 4. Checkpoint — Verify Batch 1 tests | ❌ Pending | — |
| 150 | 5. Batch 2 — README accuracy | ❌ Pending | — |
| 151 | 5.1 Update README.md with accurate documentation | ❌ Pending | — |
| 152 | 6. Checkpoint — Verify Batch 2 | ❌ Pending | — |
| 153 | 7. Batch 3 — Package structure migration and CI | ❌ Pending | — |
| 154 | 7.1 Create `src/cloud_janitor/` directory structure and move modules | ❌ Pending | — |
| 155 | 7.2 Update all source imports to `cloud_janitor.*` paths | ❌ Pending | — |
| 156 | 7.3 Update all test imports to `cloud_janitor.*` paths | ❌ Pending | — |
| 157 | 7.4 Update `pyproject.toml` for src-layout | ❌ Pending | — |
| 158 | 7.5 Create GitHub Actions CI pipeline (`.github/workflows/ci.yml`) | ❌ Pending | — |
| 159 | 7.6 Verify package installability and type annotation marker | ❌ Pending | — |
| 160 | 8. Final checkpoint — Ensure all tests pass | ❌ Pending | — |
| 161 | 1. Create the backends module with CloudProvider ABC | ✅ Done | no mapping |
| 162 | 1.1 Create `mcp_server/backends/__init__.py` with CloudProvider abstract base class | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 163 | 1.2 Implement FixtureProvider in `mcp_server/backends/fixture_provider.py` | ✅ Done | fixtures/ exists |
| 164 | 1.3 Write property tests for FixtureProvider | ✅ Done | no mapping |
| 165 | 2. Implement stub providers | ✅ Done | no mapping |
| 166 | 2.1 Implement AWSProvider in `mcp_server/backends/aws_provider.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 167 | 2.2 Implement GCPProvider and AzureProvider in `mcp_server/backends/gcp_provider.py` and `mcp_server/backends/azure_provider.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 168 | 2.3 Update `mcp_server/backends/__init__.py` to export all providers | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 169 | 3. Wire provider selection into MCP server | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 170 | 3.1 Add PROVIDER_REGISTRY and `_load_provider()` to `aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 171 | 3.2 Refactor MCP tool functions to delegate to provider instance | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 172 | 3.3 Write property tests for provider selection | ✅ Done | no mapping |
| 173 | 4. Checkpoint - Verify backward compatibility | ✅ Done | no mapping |
| 174 | 5. Write backward compatibility property test | ✅ Done | no mapping |
| 175 | 6. Update dependencies and documentation | ✅ Done | no mapping |
| 176 | 6.1 Add new dependencies to `requirements.txt` | ✅ Done | .kiro/specs/audit-remediation/requirements.md exists |
| 177 | 6.2 Rewrite `README.md` at project root as a product README | ✅ Done | no mapping |
| 178 | 6.3 Update `mcp_server/README.md` with provider architecture documentation | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 179 | 6.4 Create `agents/README.md` | ✅ Done | no mapping |
| 180 | 6.5 Create `fixtures/README.md` | ✅ Done | fixtures/ exists |
| 181 | 6.6 Create `tests/README.md` | ✅ Done | no mapping |
| 182 | 6.7 Create `output/README.md` and `rollbacks/README.md` | ✅ Done | output/rollbacks/ exists |
| 183 | 7. Final checkpoint - Ensure all tests pass | ✅ Done | no mapping |
| 184 | 1. Implement Savings Tracker core module | ✅ Done | no mapping |
| 185 | 1.1 Create `agents/savings_tracker.py` with SavingsTracker class | ✅ Done | agents/savings_tracker.py exists |
| 186 | 1.2 Write property test: RunEntry schema and field correctness | ✅ Done | no mapping |
| 187 | 1.3 Write property test: Monthly savings computation | ✅ Done | agents/savings_tracker.py exists |
| 188 | 1.4 Write property test: Recalculate-from-source invariant | ✅ Done | no mapping |
| 189 | 1.5 Write property test: Duplicate run idempotency | ✅ Done | no mapping |
| 190 | 1.6 Write property test: Savings summary correctness | ✅ Done | no mapping |
| 191 | 2. Implement Reasoning Logger and agent integration | ✅ Done | no mapping |
| 192 | 2.1 Create `agents/reasoning_logger.py` with ReasoningLogger class | ✅ Done | no mapping |
| 193 | 2.2 Integrate ReasoningLogger into FinOps Auditor | ✅ Done | agents/finops_auditor.py exists |
| 194 | 2.3 Integrate ReasoningLogger into SecOps Guard | ✅ Done | agents/secops_guard.py exists |
| 195 | 2.4 Integrate ReasoningLogger into Remediation Architect | ✅ Done | agents/remediation_architect.py exists |
| 196 | 2.5 Write property test: Reasoning logger emits valid structured JSON | ✅ Done | no mapping |
| 197 | 2.6 Write property test: Reasoning logger sequential append | ✅ Done | no mapping |
| 198 | 3. Checkpoint | ✅ Done | no mapping |
| 199 | 4. LocalStack wiring and demo infrastructure | ✅ Done | no mapping |
| 200 | 4.1 Replace `terraform` with `tflocal` in `mcp_server/aws_janitor_mcp.py` | ✅ Done | mcp_server/aws_janitor_mcp.py exists |
| 201 | 4.2 Replace `terraform` with `tflocal` in `hooks/pre-remediation.sh` | ✅ Done | agents/remediation_architect.py exists |
| 202 | 4.3 Create `docker-compose.yml` at project root | ✅ Done | no mapping |
| 203 | 4.4 Create `Makefile` at project root with `demo` target | ✅ Done | no mapping |
| 204 | 4.5 Wire `tflocal apply -auto-approve` into orchestrator approval flow | ✅ Done | "APPROVE" found in codebase |
| 205 | 4.6 Update `requirements.txt` to add `terraform-local` | ✅ Done | .kiro/specs/audit-remediation/requirements.md exists |
| 206 | 5. Orchestrator integration with SavingsTracker | ✅ Done | no mapping |
| 207 | 5.1 Wire SavingsTracker into Orchestrator | ✅ Done | no mapping |
| 208 | 5.2 Add ReasoningLogger truncation at audit start in Orchestrator | ✅ Done | no mapping |
| 209 | 5.3 Write unit tests for Orchestrator → SavingsTracker wiring | ✅ Done | no mapping |
| 210 | 6. Update .gitignore and project configuration | ✅ Done | no mapping |
| 211 | 6.1 Add runtime files to `.gitignore` | ✅ Done | no mapping |
| 212 | 7. Checkpoint | ✅ Done | no mapping |
| 213 | 8. Implement SPEC_COMPLIANCE.md generator | ✅ Done | no mapping |
| 214 | 8.1 Create `scripts/generate_spec_compliance.py` | ✅ Done | no mapping |
| 215 | 8.2 Create Git post-commit hook | ✅ Done | no mapping |
| 216 | 8.3 Write property test: Compliance generator parsing and mapping | ✅ Done | no mapping |
| 217 | 8.4 Write property test: Compliance generator output format | ✅ Done | no mapping |
| 218 | 9. Implement Streamlit Reasoning Panel | ✅ Done | app.py exists |
| 219 | 9.1 Add reasoning log panel to `app.py` | ✅ Done | app.py exists |
| 220 | 9.2 Write property test: Agent section header transitions | ✅ Done | no mapping |
| 221 | 9.3 Write property test: Malformed line resilience | ✅ Done | no mapping |
| 222 | 10. Final checkpoint — test quality audit | ✅ Done | no mapping |
| 223 | 10.1 Run full test suite and confirm all tests pass | ✅ Done | no mapping |
| 224 | 10.2 Run test quality audit on all test files | ✅ Done | no mapping |
| 225 | 10.3 Verify no hardcoded `terraform` or `tflocal` binary calls remain | ✅ Done | no mapping |
| 226 | 10.4 Verify runtime files excluded from git | ✅ Done | no mapping |
| 227 | 10.5 Run scripts/generate_spec_compliance.py and commit output | ⏳ Partial | no mapping |
