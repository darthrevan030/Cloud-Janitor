# Implementation Plan: Phase 5 Future Enhancements

## Overview

This plan implements 19 requirements covering two independent backlog issues: FEAT-3 (real Cost Explorer data, Requirements 1, 1a, 2–4) and FEAT-4 (GCP and Azure `CloudProvider` implementations plus two required agent-compatibility fixes, Requirements 5–19). Work is organized into three sequenced tracks: FEAT-3 (self-contained, can proceed independently), GCP Wave 1 (the reference implementation for the second cloud provider pattern), and Azure Wave 2 (reuses GCP's patterns with Azure-specific credential/SDK substitutions). Because FEAT-4 is by far the largest single item in the backlog (effort: L, for audit/detection only — see Requirement 15), its two clouds are deliberately sequenced rather than parallelized — Azure's tasks assume GCP's provider is already implemented and reviewed, since several Azure tasks are direct structural copies of GCP tasks with different SDK calls.

**Scope correction from design review:** FEAT-4 is audit/detection-only in this phase. Two small, deliberate exceptions to the original "zero agent code changes" claim are included below: a `SecOpsGuard` classification fix (Requirement 16, Task 6.4/10.5) and a `RemediationArchitect` scope guard (Requirement 15, Task 12.4) that routes GCP/Azure findings to the existing manual-review placeholder instead of generating non-functional AWS-shaped Terraform. Building real GCP/Azure remediation is explicitly out of scope and deferred to a follow-on phase.

## Tasks

- [ ] 1. FEAT-3: Build the Cost Explorer client and cache
  - [x] 1.1 Add `COST_EXPLORER_CACHE_PATH` to `core/paths.py`
    - Add the single new constant `COST_EXPLORER_CACHE_PATH = OUTPUT_DIR / "cost_explorer_cache.json"` following the existing pattern for `SAVINGS_LEDGER_PATH`/`APPROVAL_GATES_PATH`
    - _Requirements: 3.5_

  - [x] 1.2 Create `mcp_server/backends/cost_explorer.py` with `CostExplorerCache`
    - Implement `get(key)`/`set(key, response)`/`make_key(account_id, start, end, filter_obj)` per the design
    - TTL read from `JANITOR_CE_CACHE_TTL_SECONDS`, default `86400`
    - Corrupted/missing cache file treated as a full miss on `get()`, safely rewritten on next `set()`
    - Atomic write (tmp + rename), matching the existing pattern in `savings_tracker.py`/`finops_auditor.py`
    - Document in the class docstring that once Task 1.4's calendar-month-aligned period lands, the cache key's date component changes only once per calendar month (not daily) — `JANITOR_CE_CACHE_TTL_SECONDS` therefore governs within-month freshness, not day-to-day cache busting (see design.md's `CostExplorerCache` docstring)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ] 1.3 Write property tests for the cache
    - **Property 3: Cache Round-Trip and TTL Expiry**
    - **Property 4: Corrupted Cache File Degrades to Full Miss**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6**

  - [ ] 1.4 Implement `CostExplorerClient` in `cost_explorer.py`, including `_trailing_complete_months()`
    - **CRITICAL — do this before `lookup_by_tag`/`lookup_by_service`:** implement `_trailing_complete_months(months_back=1) -> tuple[str, str]` so `start`/`end` are BOTH the 1st of a calendar month (`end` = 1st of the current month, `start` = 1st of the month `months_back` months earlier). Do NOT use `date.today()` as `end` — Cost Explorer's `Granularity="MONTHLY"` rejects any period whose boundaries aren't both month-starts, which a same-day `end` would violate on every day but the 1st (Requirement 1a)
    - `lookup_by_tag(tag_key, resource_id, account_id) -> Optional[CostLookupResult]` — tag-filtered `ce.get_cost_and_usage()` over `_trailing_complete_months()`'s period, `Granularity="MONTHLY"`. Docstring/comment SHALL note this tier is opportunistic best-effort and expected to rarely fire (Requirement 1)
    - `lookup_by_service(service_name, usage_type_prefix, account_id, divisor) -> Optional[CostLookupResult]` — grouped by `SERVICE`/`USAGE_TYPE`, prorated by `divisor`, over the same calendar-month-aligned period. Docstring/comment SHALL note this is the PRIMARY expected Cost-Explorer-backed tier in practice (Requirement 2)
    - Both methods read-through the cache from task 1.2 before calling `_make_client("ce", region)`
    - Module-level docstring documenting: the per-resource-cost limitation (Req 1.4), the required IAM permissions `ce:GetCostAndUsage` and `sts:GetCallerIdentity` (Req 1.5), and the calendar-month-alignment UX tradeoff — a resource created earlier in the current month has no CE data yet and correctly falls to the pricing heuristic (Req 1a.3)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1a.1, 1a.2, 1a.3, 2.1, 2.2_

  - [ ] 1.5 Write unit tests for `CostExplorerClient` (`tests/test_cost_explorer.py`)
    - Mocked `ce.get_cost_and_usage` returning a non-zero tag match → `cost_data_source="cost_explorer_tag"`
    - Mocked zero/missing tag match → falls through to service-level call
    - Mocked service-level match → `cost_data_source="cost_explorer_service"`, amount correctly prorated by `divisor`
    - Mocked total CE failure (raises `ClientError`) on both tiers → caller's heuristic path is reached (verify via the calling test in task 1.7, this test verifies the client itself surfaces `None` rather than raising)
    - `_trailing_complete_months()` returns a period whose `Start`/`End` are both month-starts, for a range of `date.today()` values fixed via freezegun/monkeypatch, including the December→January rollover
    - _Requirements: 1.1, 1.2, 1.3, 1a.1, 1a.2, 2.1, 2.2, 2.3_

  - [ ] 1.6 Write property tests for the fallback chain and calendar-month alignment
    - **Property 1: Cost Fallback Chain Termination**
    - **Property 2: LocalStack Cost Path Bypass**
    - **Property 10: Cost Explorer Query Period Is Always Calendar-Month-Aligned** — for any `date.today()` across a full year (parametrize or generate via hypothesis strategies), assert `start`/`end` are both `day == 1` and `end` is strictly the 1st of the current month
    - **Validates: Requirements 1.2, 1a.1, 1a.2, 2.2, 2.3, 2.4**

  - [ ] 1.7 Wire the fallback chain into `AWSProvider.get_cost_data()`, and implement `AWSProvider._account_id()`
    - **New method required (MEDIUM defect fix):** implement `_account_id()` — no such method or any STS usage exists anywhere in this codebase today. Call `sts:GetCallerIdentity` via `_make_client("sts", self._region)`, cache the resolved account ID on the instance (it never changes during the provider's lifetime), and add `sts:GetCallerIdentity` to the module's IAM-permission docstring
    - **Restructure the EBS branch into two passes (defect fix — see design.md Section 2):** the current single-pass `describe_volumes` paginator loop (lines ~135-193) costs each unattached volume as it's found, before the total count of same-`volume_type` volumes still to come is known. This made the original draft's `divisor=self._unattached_ebs_count` both undefined (no such attribute exists anywhere) and the wrong shape (a single global count instead of a per-type one). Fix: **Pass 1** enumerates all unattached volumes via the existing paginator and builds `type_counts: dict[str, int]` keyed by `volume_type`, with no cost/idle filtering yet. **Pass 2** iterates those volumes, applies `min_idle_days` filtering (unchanged logic), and resolves cost via `_resolve_ebs_cost(vol, gb, vtype, divisor=type_counts[vtype])` — the count for THAT SPECIFIC volume's type, looked up fresh per volume
    - Replace the EBS `price_per_gb` computation and ElastiCache `cost_map` lookup with calls through `CostExplorerClient.lookup_by_tag()` → `lookup_by_service(..., divisor=type_counts[vtype])` → existing heuristic (now a private `_pricing_heuristic_*` method, logic unchanged)
    - Skip Cost Explorer entirely when `AWS_ENDPOINT_URL` is set (LocalStack), reusing the existing `is_localstack` detection pattern from `_cw_idle_days`
    - Add `cost_data_source` key to every resource dict returned
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 2.1, 2.2, 2.3, 2.4_

  - [ ] 1.8 Write unit tests for `AWSProvider.get_cost_data()` fallback wiring (`tests/test_aws_provider_cost.py`)
    - `_account_id()` calls `sts:GetCallerIdentity` exactly once and caches the result across multiple calls (mocked STS client)
    - Tag-based success path end-to-end (mocked CE + mocked EC2/ElastiCache describe calls)
    - Service-level fallback path end-to-end
    - **Per-type proration regression test (Requirement 2.2 defect fix):** mock `describe_volumes` to return a mix of volume types (e.g. 3 unattached `gp2` volumes and 2 unattached `gp3` volumes), mock `lookup_by_service` to capture its `divisor` argument on every call, and assert each `gp2` volume is costed with `divisor == 3` and each `gp3` volume with `divisor == 2` — i.e. the PER-TYPE count, never a single global count (`5`) shared across both types. This is the explicit test guarding against the original design's `self._unattached_ebs_count` bug
    - Total CE failure falls back to unchanged heuristic values (regression check against pre-FEAT-3 output)
    - `AWS_ENDPOINT_URL` set → zero calls to the CE client (mock and assert not called)
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4_

- [ ] 2. FEAT-3: Propagate cost provenance to findings and document the limitation
  - [ ] 2.1 Verify `FinOpsAuditor._build_finding()` metadata pass-through requires no code change
    - Confirm (via a test, not a code change) that `cost_data_source` on a resource dict flows into the finding's `metadata` field through the existing exclusion-list copy logic
    - _Requirements: 4.2_

  - [ ] 2.2 Write property test for metadata propagation
    - **Property 5: Cost Data Source Propagates to Finding Metadata**
    - **Validates: Requirement 4.2**

  - [ ] 2.3 Write unit test confirming `SavingsTracker` needs no change (`tests/test_finops_cost_source.py`)
    - Full `record_run()` → `_compute_monthly_savings()` cycle using a findings store containing mixed `cost_explorer_tag`/`cost_explorer_service`/`estimated` sourced findings, asserting the summed total is source-agnostic
    - _Requirements: 4.3, 4.4_

  - [ ] 2.4 Update documentation for the Cost Explorer limitation
    - Add a short section to `README.md` and/or `mcp_server/README.md` stating Cost Explorer does not expose exact per-resource billing for most services, and that `cost_explorer_service`-sourced figures are shared/aggregate estimates
    - _Requirements: 4.5_

- [ ] 3. Checkpoint — FEAT-3 complete, all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - FEAT-3 (Requirements 1–4) is now independently shippable regardless of FEAT-4's progress.

- [ ] 4. FEAT-4 Wave 1 (GCP): Dependencies and credential resolution
  - [x] 4.1 Add the `gcp` optional extras group to `pyproject.toml`
    - `[project.optional-dependencies] gcp = ["google-auth>=2.30.0", "google-cloud-compute>=1.19.0", "google-cloud-redis>=2.16.0"]`
    - _Requirements: 9.5_

  - [x] 4.2 Implement `GCPProvider.__init__()` credential resolution
    - Lazy import of `google.auth`/GCP client libraries with `ImportError` on missing SDK, naming the `gcp` extras group and `pip install` command
    - `google.auth.default()` resolution; `DefaultCredentialsError` wrapped in `RuntimeError` naming `gcloud auth application-default login` / `GOOGLE_APPLICATION_CREDENTIALS`
    - Resolve `project_id` from constructor arg, ADC's resolved project, or `GCP_PROJECT_ID` env var, in that order
    - _Requirements: 9.1, 9.2, 9.6_

  - [ ] 4.3 Write unit tests for GCP credential resolution (`tests/test_gcp_provider.py`)
    - Mocked `google.auth.default` success → credentials + project stored
    - Mocked `DefaultCredentialsError` → `RuntimeError` with the two remediation hints in the message
    - GCP SDK not importable (mocked `ImportError` on the google-cloud imports) → `ImportError` naming the `gcp` extras group
    - _Requirements: 9.1, 9.2, 9.6_

  - [ ] 4.4 Write property test for missing-SDK behavior
    - **Property 8: Missing SDK Raises Actionable ImportError** (GCP half)
    - **Validates: Requirement 9.6**

- [ ] 5. FEAT-4 Wave 1 (GCP): Persistent Disk and Cloud Memorystore waste detection
  - [ ] 5.1 Implement `GCPProvider.get_cost_data()` — Persistent Disks
    - List disks via Compute Engine API, filter to `users` empty (unattached), compute `idle_days` from `creationTimestamp`
    - Apply `min_idle_days` filtering; return `type="ebs"`, `gcp_resource_kind="persistent_disk"` in metadata
    - Cost via `_DISK_PRICE_PER_GB` price table; `cost_data_source="estimated"`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 9.3, 9.4_

  - [ ] 5.2 Implement `GCPProvider.get_cost_data()` — Cloud Memorystore
    - List Redis instances via Cloud Memorystore API, compute `idle_days` via Cloud Monitoring (`redis.googleapis.com/stats/connections/active`), default `90` on no datapoints
    - Return `type="elasticache"`, `gcp_resource_kind="memorystore_redis"` in metadata
    - Cost via `_MEMORYSTORE_PRICE` price table; `cost_data_source="estimated"`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 9.3, 9.4_

  - [ ] 5.3 Write unit tests for GCP cost data (`tests/test_gcp_provider.py`)
    - Mocked Compute Engine `DisksClient.list` with a mix of attached/unattached disks, both above and below `min_idle_days`. **Mocks MUST use attribute access matching the real protobuf shape (e.g. `MagicMock(users=[...], creation_timestamp=...)`), NOT dict literals (`{"users": [...]}`) — `google-cloud-compute` returns typed message objects, not dicts, and a dict-shaped mock can pass while the real SDK call fails (Requirement 18)**
    - Mocked Cloud Memorystore `CloudRedisClient.list_instances` with mocked Cloud Monitoring idle-day lookup, including the no-datapoints-in-90-days default (same attribute-access mocking requirement applies)
    - Assert `type`/`gcp_resource_kind`/`cost_data_source` fields match spec on every returned resource
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 18.2_

  - [ ] 5.4 Write property test for GCP resource-type consistency (cost data half)
    - **Property 6: Cross-Provider Resource Type Consistency** (GCP `get_cost_data` half)
    - **Property 7: GCP/Azure Response Shape Parity** (GCP `get_cost_data` half)
    - **Validates: Requirements 5.4, 5.5, 6.3, 6.4**

- [ ] 6. FEAT-4 Wave 1 (GCP): Firewall rule security findings and dependency checks
  - [ ] 6.1 Implement `GCPProvider.get_security_data()` — Firewall Rules
    - List firewall rules via Compute Engine API, check `direction=="INGRESS"`, `sourceRanges` containing `"0.0.0.0/0"`, and `allowed` entries against `_SENSITIVE_PORTS` (already the full 8-port table matching AWS, including 3389/8080/8443)
    - Emit findings with `resource_type="aws_security_group"`, `gcp_resource_kind="firewall_rule"` in metadata, severity from the shared port table
    - Implement the `check_type="encryption"` branch for Persistent Disk encryption status — each raw finding MUST include an explicit `"resource_type": "ebs"` key alongside `"gcp_resource_kind": "persistent_disk"`, so `SecOpsGuard.check_encryption()` (fixed in Task 6.5) doesn't need to re-derive type from a GCP-shaped `resource_id` (Requirement 16.2)
    - Return `{"findings": [...], "critical_count": int}`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 16.2_

  - [ ] 6.2 Implement `GCPProvider.check_dependencies()`
    - Firewall-rule-network dependents, Persistent-Disk `users` dependents, Cloud Memorystore consumer references (empty list if the API exposes none)
    - Return `{"has_dependencies": bool, "dependents": [...]}`
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ] 6.3 Write unit tests for GCP security data and dependencies (`tests/test_gcp_provider.py`)
    - Mocked `FirewallsClient.list` with a rule open to `0.0.0.0/0` on a sensitive port (assert severity matches AWS's table for that port) and a rule that is not a violation (assert excluded)
    - Mocked disk encryption check, both compliant and non-compliant cases
    - Mocked dependency lookups for all three resource kinds, including the "no dependency graph exposed" empty-list case
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4_

  - [ ] 6.4 Write property tests for GCP security/dependency parity
    - **Property 6: Cross-Provider Resource Type Consistency** (GCP `get_security_data` half)
    - **Property 7: GCP/Azure Response Shape Parity** (GCP `get_security_data`/`check_dependencies` halves)
    - **Validates: Requirements 7.3, 7.4, 8.4**

  - [ ] 6.5 SecOps Guard cross-cloud compatibility fix (`agents/secops_guard.py`) — the one necessary change to an existing AWS-path file
    - **Encryption classification (Requirement 16):** update `_determine_resource_type()` to prefer a caller-supplied `resource_type` hint on the raw finding (`"ebs"`/`"elasticache"`) when present, falling back to the existing AWS-only `resource_id` substring-sniffing only when no hint is supplied. Preserves current AWS/fixture behavior unchanged (no hint present) while fixing GCP's Task 6.1 encryption findings, which would otherwise be classified `"unknown"` and silently dropped by `check_encryption()`'s type filter
    - **Sensitive port table alignment (Requirement 17), a pre-existing AWS-path gap surfaced by this phase:** expand `SENSITIVE_PORTS` from `[22, 3306, 5432, 6379, 27017]` to `[22, 3306, 5432, 6379, 27017, 3389, 8080, 8443]`, and `DATABASE_CACHE_PORTS` to add `3389` to the CRITICAL tier — matching the 8-port table `AWSProvider`/`GCPProvider`/`AzureProvider` already use internally. Without this, a GCP firewall rule (or an AWS security group, today) open on RDP/HTTP-alt/HTTPS-alt is silently dropped despite being correctly flagged upstream
    - Write regression tests confirming AWS/fixture-sourced findings are classified identically to before this change
    - Write unit tests confirming a GCP-shaped encryption finding (with `resource_type` hint) is correctly classified and not dropped, and that a firewall/SG finding on 3389/8080/8443 is no longer silently excluded (`tests/test_secops_guard_cross_cloud.py`)
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 17.1, 17.2, 17.3_

  - [ ] 6.6 Update `agents/README.md`'s `SecOpsGuard` severity-rule documentation to match the Task 6.5 port-list fix
    - `agents/README.md` currently documents (per `.kiro/specs/provider-agnostic-backend/requirements.md` Requirement 9.15) `SecOpsGuard` severity rules using the pre-this-phase port list: "ports 6379/3306/5432/27017 open to 0.0.0.0/0 = CRITICAL, port 22 open = HIGH, unencrypted cache/EBS = HIGH." Once Task 6.5 lands, this documented content is stale
    - Update the documented rule to state: ports 6379/3306/5432/27017/**3389** open to 0.0.0.0/0 = CRITICAL, port 22 open = HIGH, ports **8080/8443** open to 0.0.0.0/0 = HIGH, unencrypted cache/EBS = HIGH — matching `SecOpsGuard.SENSITIVE_PORTS`/`DATABASE_CACHE_PORTS` post-fix
    - This task reconciles the `provider-agnostic-backend` spec's Requirement 9.15 (which mandated the now-stale port list be documented) with this phase's port-list fix; `provider-agnostic-backend`'s own spec files are not modified — only `agents/README.md`'s actual content is brought current, per Requirement 17's cross-reference note in `requirements.md`
    - _Requirements: 17.1, 17.2, 17.3_

- [ ] 7. Checkpoint — GCP provider complete, all tests pass
  - Run the full test suite with `JANITOR_BACKEND=gcp` paths mocked end-to-end
  - Ensure all tests pass, ask the user if questions arise.
  - This checkpoint gates the start of Azure (Wave 2) — Azure's tasks below assume this provider is stable and its patterns (price table structure, sensitive-port scan, resource-type normalization, the Task 6.5 SecOps Guard fix) are the template to copy.

- [ ] 8. FEAT-4 Wave 2 (Azure): Dependencies and credential resolution
  - [x] 8.1 Add the `azure` optional extras group to `pyproject.toml`
    - `[project.optional-dependencies] azure = ["azure-identity>=1.17.0", "azure-mgmt-compute>=33.0.0", "azure-mgmt-network>=27.0.0", "azure-mgmt-redis>=14.4.0"]`
    - _Requirements: 13.6_

  - [ ] 8.2 Implement `AzureProvider.__init__()` credential and subscription resolution
    - Lazy import of `azure.identity`/`azure-mgmt-*` with `ImportError` on missing SDK, naming the `azure` extras group
    - Resolve `subscription_id` from constructor arg or `AZURE_SUBSCRIPTION_ID`; `RuntimeError` if neither is present, raised **before** constructing `DefaultAzureCredential()`
    - Construct `DefaultAzureCredential()` (credential resolution itself stays lazy until first management-client call)
    - _Requirements: 13.1, 13.2, 13.3, 13.6_

  - [ ] 8.3 Write unit tests for Azure credential/subscription resolution (`tests/test_azure_provider.py`)
    - Missing `subscription_id` and missing `AZURE_SUBSCRIPTION_ID` → `RuntimeError`, and assert `DefaultAzureCredential` constructor was never called (mock and assert not called) — Property 9
    - Azure SDK not importable → `ImportError` naming the `azure` extras group
    - Mocked `ClientAuthenticationError` on first management-client call → wrapped `RuntimeError` naming `az login`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.6_

  - [ ] 8.4 Write property tests for Azure credential handling
    - **Property 8: Missing SDK Raises Actionable ImportError** (Azure half)
    - **Property 9: Azure Requires an Explicit Subscription ID**
    - **Validates: Requirements 13.3, 13.6**

- [ ] 9. FEAT-4 Wave 2 (Azure): Managed Disk and Azure Cache for Redis waste detection
  - [ ] 9.1 Implement `AzureProvider.get_cost_data()` — Managed Disks
    - List disks via `azure-mgmt-compute`'s `DisksOperations.list`, filter to `disk_state == "Unattached"`, compute `idle_days` from `time_created`
    - Return `type="ebs"`, `azure_resource_kind="managed_disk"` in metadata; cost via `_DISK_PRICE_PER_GB`; `cost_data_source="estimated"`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 13.5_

  - [ ] 9.2 Implement `AzureProvider.get_cost_data()` — Azure Cache for Redis
    - List instances via `azure-mgmt-redis`'s `RedisOperations.list_by_subscription`, compute `idle_days` via Azure Monitor `connectedclients` metric, default `90` on no datapoints
    - Return `type="elasticache"`, `azure_resource_kind="cache_for_redis"` in metadata; cost via `_REDIS_PRICE`; `cost_data_source="estimated"`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 13.5_

  - [ ] 9.3 Write unit tests for Azure cost data (`tests/test_azure_provider.py`)
    - Mocked `DisksOperations.list` with attached/unattached disks above/below `min_idle_days`
    - Mocked `RedisOperations.list_by_subscription` with mocked Azure Monitor idle-day lookup, including the no-datapoints default
    - Assert `type`/`azure_resource_kind`/`cost_data_source` fields on every returned resource
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4_

  - [ ] 9.4 Write property test for Azure resource-type consistency (cost data half)
    - **Property 6: Cross-Provider Resource Type Consistency** (Azure `get_cost_data` half)
    - **Property 7: GCP/Azure Response Shape Parity** (Azure `get_cost_data` half)
    - **Validates: Requirements 10.4, 10.5, 11.3, 11.4**

- [ ] 10. FEAT-4 Wave 2 (Azure): NSG security findings and dependency checks
  - [ ] 10.1 Implement `AzureProvider.get_security_data()` — Network Security Groups
    - List NSGs and security rules via `azure-mgmt-network`, check `direction=="Inbound"`, `access=="Allow"`, `source_address_prefix` in `{"*","0.0.0.0/0","Internet"}`, and `destination_port_range` against `_SENSITIVE_PORTS` (already the full 8-port table, aligned per Task 6.5)
    - Emit findings with `resource_type="aws_security_group"`, `azure_resource_kind="network_security_group"` in metadata, severity from the shared port table
    - Implement the `check_type="encryption"` branch for Managed Disk `encryption.type` — each raw finding MUST include an explicit `"resource_type": "ebs"` key alongside `"azure_resource_kind": "managed_disk"`, reusing the `SecOpsGuard` fix already made in Task 6.5 (Requirement 16.2)
    - Return `{"findings": [...], "critical_count": int}`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 16.2_

  - [ ] 10.2 Implement `AzureProvider.check_dependencies()`
    - NSG-to-subnet/NIC associations, Managed Disk attachment (`managed_by` field), Azure Cache for Redis consumer references (empty list if none exposed)
    - Return `{"has_dependencies": bool, "dependents": [...]}`
    - _Requirements: 19.1, 19.2, 19.3, 19.4_

  - [ ] 10.3 Write unit tests for Azure security data and dependencies (`tests/test_azure_provider.py`)
    - Mocked NSG rule open to `0.0.0.0/0`/`Internet`/`*` on a sensitive port (assert severity matches AWS/GCP's table for that port) and a compliant rule (assert excluded)
    - Mocked disk encryption check, both compliant and non-compliant
    - Mocked dependency lookups for all three resource kinds, including the empty-list case
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [ ] 10.4 Write property tests for Azure security/dependency parity
    - **Property 6: Cross-Provider Resource Type Consistency** (Azure `get_security_data` half)
    - **Property 7: GCP/Azure Response Shape Parity** (Azure `get_security_data`/`check_dependencies` halves)
    - **Validates: Requirements 12.3, 12.4, 19.4**

  - [ ] 10.5 Write Azure-specific regression tests for the Task 6.5 SecOps Guard fix
    - Confirm an Azure-shaped encryption finding (with `resource_type` hint from Task 10.1) is correctly classified as `"ebs"` and not dropped as `"unknown"`
    - Confirm an NSG finding on 3389/8080/8443 is no longer silently excluded by `SecOpsGuard.SENSITIVE_PORTS`
    - No new `secops_guard.py` code — this only extends test coverage for the fix already made in Task 6.5 to Azure's shapes
    - _Requirements: 16.2, 16.3, 17.2_

- [ ] 11. Checkpoint — Azure provider complete, all tests pass
  - Run the full test suite with `JANITOR_BACKEND=azure` paths mocked end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Cross-cutting: registry/agent compatibility verification, RemediationArchitect scope guard, and documentation
  - [ ] 12.1 Write the provider registry parity test (`tests/test_provider_registry_parity.py`)
    - Parametrized test instantiating `fixture`, `aws` (mocked boto3), `gcp` (mocked google-cloud), and `azure` (mocked azure-mgmt) providers through `PROVIDER_REGISTRY`/`_load_provider()` with zero changes to `aws_janitor_mcp.py`
    - Feed each provider's `get_cost_data()`/`get_security_data()` output through `FinOpsAuditor.classify_severity()` (zero code changes) and `SecOpsGuard`'s severity dispatch (with the Task 6.5 fix applied) and assert identical severity classification for equivalent resource shapes across all three real providers
    - Feed a GCP/Azure-origin finding through `RemediationArchitect.generate_remediation()`/`generate_rollback()` (with the Task 12.4 scope guard applied) and assert it returns the generic manual-review template, never AWS-shaped HCL
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [ ] 12.2 Update `mcp_server/README.md` provider status table
    - Change GCP/Azure rows from "interface only" to "complete (audit/detection only — remediation is manual-review-only, see Requirement 15)", list required env vars (`GOOGLE_APPLICATION_CREDENTIALS`/ADC for GCP; `AZURE_SUBSCRIPTION_ID` + one of the `DefaultAzureCredential` chain's mechanisms for Azure) and the new optional extras groups (`pip install 'cloud-janitor[gcp]'` / `[azure]`)
    - _Requirements: 9.1, 9.2, 9.5, 9.6, 13.1, 13.2, 13.6, 13.7, 15.1_

  - [ ] 12.3 Update the resource-type mapping table into `mcp_server/README.md` or `agents/README.md`
    - Reproduce the design document's cross-cloud resource mapping table (EBS↔Persistent Disk↔Managed Disk, ElastiCache↔Memorystore↔Cache for Redis, Security Group↔Firewall Rule↔NSG) so contributors extending a fourth provider have a worked example
    - Note plainly that this table governs `FinOpsAuditor`/`SecOpsGuard` dispatch only — `RemediationArchitect` deliberately does NOT extend this reuse to GCP/Azure findings (Requirement 15)
    - _Requirements: 5.4, 6.3, 7.3, 10.4, 11.3, 12.3_

  - [ ] 12.4 Implement the `RemediationArchitect` scope guard (`agents/remediation_architect.py`) — the one necessary change to this file, narrowing behavior for safety
    - In `generate_remediation()` and `generate_rollback()`, check `finding.get("metadata", {})` for a `gcp_resource_kind` or `azure_resource_kind` key BEFORE dispatching on `resource_type`; if present, return `self._remediation_generic(finding)` / `self._rollback_generic(finding)` instead of the AWS-specific templates
    - Write regression tests confirming AWS-origin findings (no such metadata key) dispatch exactly as before this change
    - Write tests confirming a GCP/Azure-origin finding (`gcp_resource_kind`/`azure_resource_kind` present) always returns the generic "manual review required" placeholder and never `aws_ebs_snapshot`/`aws_security_group_rule`/`aws ec2`/`aws elasticache` HCL, regardless of its `resource_type`/`category` values
    - _Requirements: 15.1, 15.2, 15.3_

  - [ ] 12.5 Document the Requirement 18 real-account smoke-test gate
    - Add a short runbook (module docstring in `tests/test_gcp_provider.py`/`tests/test_azure_provider.py`, or a `tests/manual/README.md`) describing: what to run against a real free-tier GCP project and a real Azure subscription/sandbox before declaring the respective provider complete, which three `CloudProvider` methods to exercise, and that the run's date/outcome must be recorded in the merging PR's description
    - Explicitly document, next to the mocked test suite, that SDK-mock-only coverage is a known, accepted, lower-confidence gap relative to the AWS path's LocalStack-based tests (Requirement 18.1)
    - _Requirements: 18.1, 18.3, 18.4, 18.5_

- [ ] 13. Final checkpoint — all tests pass
  - Run the full test suite (`pytest`) with no live cloud credentials required (all boto3/google-cloud/azure-mgmt calls mocked)
  - Verify the `fixture` and `aws` backends have zero behavioral regressions from FEAT-3's changes (existing tests pass unmodified except where Requirement 4 explicitly adds `cost_data_source` assertions)
  - Verify `pip install cloud-janitor` (no extras) still succeeds and the `fixture`/`aws` backends work without `google-auth`/`azure-identity` installed
  - Confirm the Requirement 18 smoke-test runbook has actually been run at least once for GCP and once for Azure before either provider is declared complete (not merely documented)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- FEAT-3 (Task groups 1–3) and FEAT-4 (Task groups 4–12) are independent workstreams and MAY be implemented in either order or in parallel by different contributors — the backlog lists them as unrelated P3 items bundled into one phase document for planning convenience, not because one depends on the other.
- Within FEAT-4, GCP (Task groups 4–7) MUST precede Azure (Task groups 8–11) per the design's staged-wave decision — Azure's tasks are written as structural copies of GCP's with SDK-specific substitutions, and reviewing GCP first surfaces pattern issues before they're duplicated into the Azure implementation. **Before starting Task group 4, confirm the GCP-first sequencing with a product stakeholder** — the ergonomics analysis in design.md justifies "GCP is not harder to build first," but says nothing about which cloud target customers actually run, which this plan cannot determine from the codebase alone.
- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`), with the sole exception of Task 12.5's live smoke tests (Requirement 18), which are run manually/CI-gated outside the standard `pytest` suite due to requiring live cloud credentials.
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`. No live AWS/GCP/Azure credentials or network access are required for any test in the standard suite — all cloud SDK calls are mocked. The one exception is the Requirement 18 smoke-test runbook (Task 12.5), which is explicitly NOT part of the standard `pytest` run.
- Real GCP/Azure Cost Explorer-equivalent integration (Cloud Billing export to BigQuery, Azure Cost Management export) is explicitly out of scope for this phase — Requirements 9.3–9.4 and 13.5 intentionally scope GCP/Azure cost estimation to a price-table heuristic, matching the AWS provider's pre-FEAT-3 baseline. A future phase could extend FEAT-3's real-billing pattern to GCP/Azure once their export pipelines are considered in scope.
- **GCP/Azure remediation (not just cost estimation) is also explicitly out of scope for this phase (Requirement 15).** FEAT-4 in this phase is audit/detection only — `RemediationArchitect` degrades GCP/Azure findings to its existing manual-review placeholder (Task 12.4) rather than generating AWS-shaped HCL against a non-AWS resource. Building real GCP/Azure remediation (new HCL generators, cloud-specific CLI verbs, provider-block/credential wiring) is a comparably-sized follow-on phase.
- Two small, deliberate changes to existing agent files are included in this plan and are NOT optional: `agents/secops_guard.py` (Task 6.5 — Requirements 16, 17) and `agents/remediation_architect.py` (Task 12.4 — Requirement 15). Both are narrowing/fail-safe changes, not new capability, and both are called out explicitly because the original design claimed "zero agent changes," which design review found to be inaccurate.
- IAM/permission requirements for the new `ce:GetCostAndUsage` and `sts:GetCallerIdentity` calls (FEAT-3, Task 1.4/1.7) should be added to whatever least-privilege policy artifact INF-2 produces, once INF-2 is implemented — this plan does not modify INF-2's deliverable, only notes the new permissions that will need to be added to it.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "4.1", "8.1"] },
    { "id": 2, "tasks": ["1.3", "1.4", "4.2"] },
    { "id": 3, "tasks": ["1.5", "1.6", "4.3", "4.4"] },
    { "id": 4, "tasks": ["1.7", "5.1", "5.2"] },
    { "id": 5, "tasks": ["1.8", "2.1", "5.3", "5.4", "6.1", "6.2"] },
    { "id": 6, "tasks": ["2.2", "2.3", "2.4", "6.3", "6.4"] },
    { "id": 7, "tasks": ["6.5"] },
    { "id": 8, "tasks": ["6.6"] },
    { "id": 9, "tasks": ["3", "7"] },
    { "id": 10, "tasks": ["8.2"] },
    { "id": 11, "tasks": ["8.3", "8.4", "9.1", "9.2"] },
    { "id": 12, "tasks": ["9.3", "9.4", "10.1", "10.2"] },
    { "id": 13, "tasks": ["10.3", "10.4", "10.5"] },
    { "id": 14, "tasks": ["11"] },
    { "id": 15, "tasks": ["12.4"] },
    { "id": 16, "tasks": ["12.1", "12.2", "12.3", "12.5"] },
    { "id": 17, "tasks": ["13"] }
  ]
}
```
