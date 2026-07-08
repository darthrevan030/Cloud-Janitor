# Requirements Document

## Introduction

This specification covers Phase 5 of the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`) — the final, P3/"later" pair of issues: **FEAT-3** (real Cost Explorer cost data, replacing the AWS provider's hardcoded pricing constants) and **FEAT-4** (GCP and Azure `CloudProvider` implementations, replacing the two `NotImplementedError` stubs). Both issues are explicitly lower priority than every P1/P2 item in the backlog and are scoped to be picked up only after the trust-hardening (Phase 1) and multi-user/AWS-hardening phases land — this spec does not re-litigate that sequencing, it designs the two features on their own merits so they are ready to implement when their turn comes.

The two issues are unrelated in mechanism but share a foundation: both extend the `CloudProvider` interface established by the `provider-agnostic-backend` spec (`.kiro/specs/provider-agnostic-backend/`), and FEAT-4's own cost-data method inherits the same "Cost Explorer/Cost Management APIs don't expose true per-resource billing" limitation that motivates FEAT-3's design. FEAT-3 is scoped as AWS-only (it does not touch GCP/Azure); FEAT-4 is scoped as GCP-then-Azure infrastructure providers that initially reuse the AWS provider's pre-FEAT-3 cost-estimation approach (see Requirement 9's rationale) rather than standing up their own real-billing integrations — extending FEAT-3's pattern to GCP/Azure is called out as an explicit follow-on, not part of this phase.

**FEAT-4 is scoped as audit/detection-only for GCP and Azure in this phase.** `GCPProvider`/`AzureProvider` make findings visible (cost waste, security violations) exactly as the AWS path does, but generating a working, cloud-correct Terraform remediation for those findings is explicitly OUT OF SCOPE — see Requirement 15. `RemediationArchitect`'s existing HCL generators are hardwired to AWS resource types (`aws_ebs_snapshot`, `aws_security_group_rule`, `aws ec2 delete-volume` CLI calls) and were never designed for GCP/Azure; standing up real GCP/Azure remediation is a comparably-sized follow-on phase, not a byproduct of this one. The effort estimate of "L" for FEAT-4 (see design.md) covers audit/detection only.

Full technical design is in `design.md` in this folder. Grounding references: `src/cloud_janitor/mcp_server/backends/aws_provider.py` (reference implementation pattern and current pricing-constant logic), `src/cloud_janitor/mcp_server/backends/{gcp,azure}_provider.py` (current 3-method stubs), `src/cloud_janitor/mcp_server/backends/fixture_provider.py` (the response shape all providers must match), `src/cloud_janitor/agents/finops_auditor.py` (the `get_cost_data()` consumer), `src/cloud_janitor/agents/savings_tracker.py` (`_compute_monthly_savings()` — the downstream consumer of `cost_estimate_monthly`), and `.kiro/specs/provider-agnostic-backend/` (the `CloudProvider` ABC and `JANITOR_BACKEND` dispatch this phase extends without modifying).

## Glossary

- **CloudProvider**: The abstract base class (`mcp_server/backends/__init__.py`) defining the 3-method contract (`get_cost_data`, `get_security_data`, `check_dependencies`) every backend implements.
- **Cost_Explorer_Client**: A new wrapper module around `boto3.client("ce")` (AWS Cost Explorer) that performs tag-based and service-level cost lookups on behalf of `AWSProvider.get_cost_data()`.
- **Resource_Tagged_Lookup**: A Cost Explorer query filtered by a cost allocation tag matching a specific resource ID, returning cost attributable to exactly one resource.
- **Service_Level_Attribution**: A Cost Explorer query grouped by the `SERVICE` and `USAGE_TYPE` dimensions, used when a resource cannot be isolated by tag — the result is a shared/aggregate figure, not an exact per-resource cost.
- **Cost_Data_Source**: A string tag (`"cost_explorer_tag"`, `"cost_explorer_service"`, or `"estimated"`) attached to each resource dict and propagated into finding metadata, recording which cost lookup strategy produced `monthly_cost`.
- **CE_Cache**: A TTL-based cache (default 24 hours, configurable) of Cost Explorer responses, keyed by `(account_id, time_period, filter)`, avoiding redundant Cost Explorer calls given their per-request latency (seconds) and per-request billing cost.
- **GCPProvider / AzureProvider**: The two `CloudProvider` implementations this phase makes real, currently 3-method `NotImplementedError` stubs in `mcp_server/backends/gcp_provider.py` / `azure_provider.py`.
- **ADC (Application Default Credentials)**: Google Cloud's standard credential resolution chain (`google.auth.default()`), checking `GOOGLE_APPLICATION_CREDENTIALS`, then `gcloud` user credentials, then the GCE/GKE/Cloud Run metadata server, in that order.
- **DefaultAzureCredential**: Azure's standard credential resolution chain (`azure.identity.DefaultAzureCredential`), trying multiple credential sources (environment variables, managed identity, Azure CLI, etc.) in sequence.
- **Persistent_Disk**: GCP's block storage resource, mapped in this spec to AWS EBS volumes.
- **Firewall_Rule**: A GCP VPC firewall rule, mapped in this spec to AWS security groups.
- **Cloud_Memorystore**: GCP's managed Redis/Memcached service, mapped in this spec to AWS ElastiCache.
- **Managed_Disk**: Azure's block storage resource, mapped in this spec to AWS EBS volumes.
- **NSG (Network Security Group)**: Azure's traffic-filtering resource, mapped in this spec to AWS security groups.
- **Azure_Cache_for_Redis**: Azure's managed Redis service, mapped in this spec to AWS ElastiCache.
- **Audit/Detection-Only (FEAT-4 scope boundary)**: This phase makes GCP/Azure findings visible (cost, security) but does NOT stand up working GCP/Azure remediation. `RemediationArchitect` continues to generate AWS-shaped HCL only; GCP/Azure findings degrade to a "manual review required" placeholder instead. See Requirement 15.
- **Calendar-Month-Aligned Period**: A Cost Explorer `TimePeriod` whose `Start` and `End` are both the 1st of a month (e.g. `Start=2026-06-01`, `End=2026-07-01`), required by `Granularity="MONTHLY"` — Cost Explorer raises `ValidationException` for any MONTHLY-granularity period whose boundaries are not both month-starts.

## Requirements

### Requirement 1: Cost Explorer Tag-Based Resource Cost Lookup (Opportunistic Best-Effort Tier)

**User Story:** As a FinOps stakeholder, I want the AWS provider to look up a flagged resource's actual billed cost via AWS Cost Explorer when the resource happens to be tagged for cost allocation, so that savings figures reflect real spend rather than a synthetic pricing formula in the (rare) case this is possible.

> **Scoping note:** This tier is an **opportunistic best-effort path, expected to rarely succeed in practice** — it depends on a cost-allocation tag literally named to match a resource's own ID, which is not created by anything in this codebase today (`ResourceTagger`/`agents/tagger.py` infers `env`/`team`/`owner` context, not a self-referential resource-ID tag) and requires the account owner to have manually activated that tag in AWS Billing preferences, which most accounts never do. Requirement 2 (Service_Level_Attribution), not this requirement, is the PRIMARY expected outcome for a Cost-Explorer-backed figure — see Requirement 2's introduction.

#### Acceptance Criteria

1. WHEN `AWSProvider.get_cost_data()` is running against real AWS (i.e. `AWS_ENDPOINT_URL` is unset) AND a discovered resource (EBS volume, ElastiCache cluster, or stopped EC2 instance) carries a cost allocation tag identifying its resource ID, THE Cost_Explorer_Client SHALL query `ce.get_cost_and_usage()` filtered by that tag key/value over a Calendar-Month-Aligned Period (see Requirement 1a), with `Granularity="MONTHLY"` and `Metrics=["UnblendedCost"]`.
2. WHEN a Resource_Tagged_Lookup returns a non-zero `UnblendedCost` amount for the resource, THE AWSProvider SHALL set that resource's `monthly_cost` field to the returned amount (rounded to 2 decimal places) and SHALL set `cost_data_source` to `"cost_explorer_tag"`.
3. IF the target resource has no cost allocation tag activated in the account, OR the tag-filtered query returns a zero or missing `UnblendedCost` amount, THEN THE AWSProvider SHALL fall back to Requirement 2 (Service_Level_Attribution) rather than reporting a zero cost.
4. THE Cost_Explorer_Client SHALL document, in a module-level docstring, that AWS Cost Explorer does not expose true per-resource-ID cost for most services outside of tag-based cost allocation, and that this tag-based tier is expected to rarely fire in a typical account — this is a documented API/adoption limitation, not a bug in the client.
5. THE Cost_Explorer_Client's module-level docstring SHALL document the required IAM permissions for this feature: `ce:GetCostAndUsage` (Cost Explorer queries, Requirements 1 and 2) and `sts:GetCallerIdentity` (account ID resolution via `AWSProvider._account_id()`, used to key the cache — see Requirement 3.1 and design.md's fallback-wiring example).

### Requirement 1a: Cost Explorer Query Period Must Be Calendar-Month-Aligned

**User Story:** As a FinOps stakeholder, I want every Cost Explorer MONTHLY-granularity query to actually succeed rather than fail validation on all but one day of the month, so that the tag-based and service-level tiers (Requirements 1 and 2) are reachable in practice instead of silently defaulting to the pricing-heuristic tier every single day.

> **Why this matters:** AWS Cost Explorer's `get_cost_and_usage()` with `Granularity="MONTHLY"` requires the requested `TimePeriod` to align to calendar-month boundaries — both `Start` and `End` must be the 1st of a month. A period ending on "today" (true on every day of the month except the 1st) raises `ValidationException`. Because this design's own error handling (Requirement 2.3) treats any Cost Explorer error as a signal to fall through to the pricing-heuristic tier, an unaligned period would mean `cost_data_source` silently resolves to `"estimated"` almost every day, with no error surfaced to the user — defeating FEAT-3's purpose while appearing to work.

#### Acceptance Criteria

1. THE `CostExplorerClient` SHALL compute its query period so that BOTH `Start` and `End` are the 1st of a calendar month — e.g. `Start` = the 1st of the month N months before the current month, `End` = the 1st of the CURRENT month — giving Cost Explorer one or more complete prior calendar month(s) of data, never a partial in-progress month.
2. THE `CostExplorerClient` SHALL NOT construct a query period whose `End` is "today" or any other day-of-month value other than the 1st, for any `Granularity="MONTHLY"` request.
3. THE design SHALL document the following UX tradeoff plainly, not gloss over it: a resource created earlier in the CURRENT month will have no Cost Explorer data available yet under this scheme, because the current partial month is excluded from the query period. For such a resource, both Requirement 1 and Requirement 2 SHALL correctly find no usable cost and fall through to the pricing-heuristic tier (`cost_data_source = "estimated"`) — this is the correct, expected behavior for freshly-created resources, not a bug. This is an inherent limitation of Cost Explorer's monthly granularity, not a defect in this design.

### Requirement 2: Service-Level Cost Attribution Fallback (Primary Expected Tier)

**User Story:** As a FinOps stakeholder, I want a resource that cannot be isolated by cost allocation tag to still get a defensible cost figure grouped by service and usage type, rather than either a zero or a purely synthetic number, so that the savings estimate degrades gracefully instead of failing silently.

> **Scoping note:** Because Requirement 1's tag-based tier is opportunistic and expected to rarely succeed (most accounts never activate a self-referential cost-allocation tag), THIS requirement — not Requirement 1 — is the PRIMARY expected outcome whenever `cost_data_source` is Cost-Explorer-backed at all. Design and documentation prose SHOULD describe the fallback chain as "service-level attribution as the normal Cost-Explorer-backed outcome, with tag-based lookup as an opportunistic upgrade when available," not the reverse.

#### Acceptance Criteria

1. WHEN Resource_Tagged_Lookup (Requirement 1) does not yield a usable cost for a resource, THE Cost_Explorer_Client SHALL query `ce.get_cost_and_usage()` grouped by the `SERVICE` and `USAGE_TYPE` dimensions, filtered to the service matching the resource's type (`AmazonEC2` for EBS/EC2, `AmazonElastiCache` for ElastiCache) over a Calendar-Month-Aligned Period (Requirement 1a).
2. WHEN a Service_Level_Attribution query succeeds, THE AWSProvider SHALL compute the resource's `monthly_cost` as a proportional share of the matching `USAGE_TYPE` group's cost (e.g. EBS: the `EBS:VolumeUsage.*` usage-type group's total cost divided across all unattached volumes of that type currently in scope), set `cost_data_source` to `"cost_explorer_service"`, and SHALL document in code comments that this is an aggregate/shared figure, not an exact per-resource cost.
3. IF both Requirement 1 and Requirement 2 lookups fail (Cost Explorer API error, no matching usage-type group found, or the account has no cost history for the service), THEN THE AWSProvider SHALL fall back to the existing pricing-constant heuristic (`price_per_gb` for EBS, `cost_map` for ElastiCache node types) unchanged from the current implementation, and SHALL set `cost_data_source` to `"estimated"`.
4. WHEN `AWS_ENDPOINT_URL` is set (LocalStack/sandbox mode), THE AWSProvider SHALL skip both Requirement 1 and Requirement 2 entirely and use the pricing-constant heuristic directly with `cost_data_source` set to `"estimated"`, consistent with the existing LocalStack shortcut already used for CloudWatch idle-day detection (`_cw_idle_days`).

### Requirement 3: Cost Explorer Response Caching

**User Story:** As an operator running frequent scheduled scans, I want Cost Explorer responses to be cached, so that repeated audits within a short window don't re-incur Cost Explorer's per-request latency and per-request billing cost for data that hasn't changed.

#### Acceptance Criteria

1. THE Cost_Explorer_Client SHALL cache every successful `get_cost_and_usage()` response keyed by a tuple of `(account_id, time_period_start, time_period_end, filter_hash)`, where `filter_hash` is a stable hash of the request's `Filter`/`GroupBy` parameters.
2. THE CE_Cache SHALL honor a configurable time-to-live read from `JANITOR_CE_CACHE_TTL_SECONDS`, defaulting to `86400` (24 hours) when unset. **Note on TTL semantics post-Requirement-1a:** because the query period (`time_period_start`/`time_period_end`) is now calendar-month-aligned per Requirement 1a rather than ending on "today," the date component of the cache key changes only once per calendar month, not once per day. `JANITOR_CE_CACHE_TTL_SECONDS` therefore governs cache freshness WITHIN a given month (e.g. bounding how long a possibly-still-settling prior-month figure is trusted before Cost Explorer is re-queried for a fresher number), not a day-to-day cache-busting mechanism — this reflects a deliberate design choice (decoupling the cache key from a literal "today" value, per Requirement 1a) rather than a config knob that was accidentally rendered inert. The env var's documentation SHALL state this plainly.
3. WHEN a cache lookup for a given key is a hit AND the cached entry's age is less than the configured TTL, THE Cost_Explorer_Client SHALL return the cached response and SHALL NOT call `ce.get_cost_and_usage()`.
4. WHEN a cache lookup is a miss OR the cached entry has exceeded the TTL, THE Cost_Explorer_Client SHALL call `ce.get_cost_and_usage()`, store the fresh response in the cache keyed as in criterion 1, and return it.
5. THE CE_Cache SHALL be implemented as a single JSON file under the existing `output/` directory tree (`output/cost_explorer_cache.json`, defined in `core/paths.py` alongside the project's other artifact paths) — no new caching infrastructure (Redis, memcached, etc.) SHALL be introduced.
6. IF the CE_Cache file is missing, empty, or fails to parse as JSON, THEN THE Cost_Explorer_Client SHALL treat this as a full cache miss for all keys and SHALL proceed to call Cost Explorer directly, rebuilding the cache file on the next successful write.

### Requirement 4: Cost Data Source Provenance and Savings Tracker Consistency

**User Story:** As an operator reviewing the savings dashboard, I want to know whether a given finding's cost figure came from real billing data or a synthetic estimate, so that I don't conflate a Cost-Explorer-backed figure with a rough approximation when reporting savings externally.

#### Acceptance Criteria

1. THE `cost_data_source` field SHALL be included in every resource dict returned by `AWSProvider.get_cost_data()`, with one of the values `"cost_explorer_tag"`, `"cost_explorer_service"`, or `"estimated"`.
2. WHEN `FinOpsAuditor._build_finding()` builds a finding from a resource dict, THE finding's `metadata` field SHALL include the resource's `cost_data_source` value, relying on the existing metadata pass-through logic (`_build_finding()` already copies every resource key not in its explicit exclusion list into `metadata`) rather than requiring a new code path.
3. THE `SavingsTracker._compute_monthly_savings()` method SHALL require no code change: it SHALL continue to sum each remediated resource's `cost_estimate_monthly` value from the findings store exactly as it does today, regardless of whether that value originated from Cost Explorer or the pricing-constant heuristic.
4. THE `FixtureProvider` (demo/sandbox mode) SHALL be unaffected by this phase: its bundled fixture JSON continues to supply static `monthly_cost` values with no `cost_data_source` field, and `FinOpsAuditor`/`SavingsTracker` SHALL treat a missing `cost_data_source` as equivalent to `"estimated"` for any UI/reporting that surfaces the field.
5. THE documentation (`README.md` and/or `mcp_server/README.md`) SHALL be updated to state plainly that AWS Cost Explorer does not expose exact per-resource billing for most services, and that a `"cost_explorer_service"`-sourced figure is a shared/aggregate estimate, not an exact charge for that one resource.

### Requirement 5: GCP Provider — Persistent Disk Waste Detection

**User Story:** As a FinOps stakeholder running Cloud Janitor against a GCP project, I want unattached Persistent Disks to be detected as idle waste, equivalent to how the AWS provider detects unattached EBS volumes, so that GCP audits surface the same class of finding AWS audits do.

#### Acceptance Criteria

1. WHEN `GCPProvider.get_cost_data()` is called with `resource_type` in (`None`, `"ebs"`), THE GCPProvider SHALL list Persistent Disks in the configured GCP project via the Compute Engine API and identify disks with an empty `users` field (i.e., not attached to any instance) as candidates.
2. THE GCPProvider SHALL compute each unattached disk's `idle_days` as the number of days since its `creationTimestamp` (mirroring the AWS provider's "idle since detach ≈ age since creation for an unattached volume" approximation, since neither API directly exposes a last-detached timestamp).
3. WHEN a disk's `idle_days` is less than the caller's `min_idle_days` parameter, THE GCPProvider SHALL exclude it from the returned `resources` list, consistent with `AWSProvider.get_cost_data()`'s existing filtering behavior.
4. THE GCPProvider SHALL return each qualifying disk using the resource type value `"ebs"` (not a GCP-specific string) so that FinOpsAuditor's existing severity classification (`ebs` unattached > 30 days = MEDIUM) applies unchanged, and SHALL record the disk's actual GCP resource type in the resource dict's metadata (e.g. `"gcp_resource_kind": "persistent_disk"`) for display purposes.
5. THE GCPProvider's returned resource dict SHALL include `id`, `type`, `name`, `idle_days`, `monthly_cost`, `status`, `cost_data_source` (per Requirement 9), and `description`, matching the field set `FixtureProvider`/`AWSProvider` already produce for `"ebs"` resources.

### Requirement 6: GCP Provider — Cloud Memorystore Waste Detection

**User Story:** As a FinOps stakeholder, I want idle Cloud Memorystore (Redis) instances to be detected, equivalent to idle ElastiCache clusters on AWS, so that GCP audits surface the same class of finding.

#### Acceptance Criteria

1. WHEN `GCPProvider.get_cost_data()` is called with `resource_type` in (`None`, `"elasticache"`), THE GCPProvider SHALL list Cloud Memorystore for Redis instances in the configured project via the Cloud Memorystore API.
2. THE GCPProvider SHALL determine each instance's `idle_days` via Cloud Monitoring metrics (`redis.googleapis.com/stats/connections/active` or equivalent), mirroring the AWS provider's CloudWatch-based `_cw_idle_days` approach; if no non-zero datapoint exists in the trailing 90-day window, `idle_days` SHALL be reported as `90`.
3. THE GCPProvider SHALL return each qualifying instance using the resource type value `"elasticache"` (not a GCP-specific string) so FinOpsAuditor's existing severity rule (ElastiCache idle > 30 days = HIGH) applies unchanged, recording the actual GCP resource kind in metadata (e.g. `"gcp_resource_kind": "memorystore_redis"`).
4. THE GCPProvider's returned resource dict SHALL include the same field set as Requirement 5 criterion 5, adapted for a cache resource (matching the fields `AWSProvider` returns for `"elasticache"` resources: `instance_type`, `engine`, `engine_version`, `num_cache_nodes`, etc., populated from the closest equivalent Memorystore instance attributes, or `""`/`0` where no GCP equivalent exists).

### Requirement 7: GCP Provider — Firewall Rule Security Findings

**User Story:** As a security engineer running Cloud Janitor against a GCP project, I want overly permissive VPC firewall rules to be flagged, equivalent to open security groups on AWS, so that GCP audits surface the same class of finding.

#### Acceptance Criteria

1. WHEN `GCPProvider.get_security_data()` is called with `check_type` in (`None`, `"security_group"`), THE GCPProvider SHALL list VPC firewall rules in the configured project via the Compute Engine API and inspect each rule's `sourceRanges` and `allowed` port/protocol entries.
2. WHEN a firewall rule's `direction` is `INGRESS`, its `sourceRanges` includes `"0.0.0.0/0"`, and its `allowed` list includes a sensitive port from the same port table `AWSProvider.get_security_data()` uses (22, 3306, 5432, 6379, 27017, 3389, 8080, 8443), THE GCPProvider SHALL emit a finding with the same `severity` value that table assigns to that port for AWS.
3. THE GCPProvider SHALL emit each finding using the resource type value `"aws_security_group"` for cross-provider consistency with FinOpsAuditor/RemediationArchitect's existing type dispatch, and SHALL additionally set a `"gcp_resource_kind": "firewall_rule"` metadata field so the true resource kind is not lost.
4. THE GCPProvider's `get_security_data()` return value SHALL match the shape `{"findings": [...], "critical_count": int}`, where `critical_count` counts findings with `severity == "CRITICAL"`, identical to `AWSProvider`'s contract.
5. WHEN `check_type` is `"encryption"`, THE GCPProvider SHALL check Persistent Disks for customer-managed or Google-managed encryption-at-rest status and emit a `MEDIUM` finding for any disk with encryption explicitly disabled (GCP disks are encrypted at rest by default; this criterion applies only if a disk has encryption explicitly turned off via a CMEK policy misconfiguration or is flagged by Security Command Center as unencrypted). Per Requirement 16, THE GCPProvider SHALL include an explicit `resource_type: "ebs"` hint field on each such raw finding so `SecOpsGuard.check_encryption()` does not need to re-derive the type from a GCP-shaped `resource_id` via AWS-only substring matching.

### Requirement 8: GCP Provider — Dependency Checks

**User Story:** As an operator approving a remediation, I want a dependency check for a GCP resource before it's deleted, equivalent to the AWS provider's ENI/attachment lookups, so that the same safety gate applies across providers.

#### Acceptance Criteria

1. WHEN `GCPProvider.check_dependencies()` is called with a resource ID that identifies a firewall rule's target network, THE GCPProvider SHALL list instances/subnetworks referencing that network and return them as `dependents`.
2. WHEN called with a Persistent Disk resource ID, THE GCPProvider SHALL check the disk's `users` field for attached instances and return them as `dependents`.
3. WHEN called with a Cloud Memorystore instance resource ID, THE GCPProvider SHALL check for any documented consumer references (e.g. VPC peering connections using the instance) and return them as `dependents`; if the Memorystore API exposes no dependency graph for a given resource, THE GCPProvider SHALL return an empty `dependents` list rather than raising.
4. THE GCPProvider's `check_dependencies()` return value SHALL match the shape `{"has_dependencies": bool, "dependents": [...]}` with `has_dependencies == (len(dependents) > 0)`, identical to `AWSProvider`'s and `FixtureProvider`'s contract.

### Requirement 9: GCP Provider — Credential Resolution, Cost Estimation, and Dependencies

**User Story:** As an operator deploying Cloud Janitor against GCP, I want credential setup to be as close to zero-config as the existing AWS path, and I want honest cost figures rather than an unimplemented Cost Explorer-equivalent, so that the GCP backend is usable on day one without a bespoke billing-export pipeline.

#### Acceptance Criteria

1. THE GCPProvider SHALL resolve credentials via Application Default Credentials (`google.auth.default()`), requiring no code-level credential handling beyond passing the resolved credentials object to each GCP client library constructor — mirroring how `AWSProvider._make_client()` relies on boto3's ambient credential chain today.
2. IF `google.auth.default()` raises `google.auth.exceptions.DefaultCredentialsError` at `GCPProvider.__init__()` time, THEN THE GCPProvider SHALL raise a `RuntimeError` identifying that ADC could not be resolved and pointing to `gcloud auth application-default login` or `GOOGLE_APPLICATION_CREDENTIALS` as remediation, mirroring `AWSProvider`'s `ImportError`-on-missing-boto3 pattern.
3. THE GCPProvider SHALL compute `monthly_cost` for each resource using the same category of approach the AWS provider used **before** FEAT-3 (Requirements 1–4 of this document): a small, explicit price table keyed by disk type / machine tier (e.g. GCP list-price-per-GB-month for `pd-standard`/`pd-ssd`/`pd-balanced`; a `cost_map` for Memorystore tiers), NOT a real Cloud Billing export integration.
4. THE GCPProvider SHALL set every resource's `cost_data_source` field to `"estimated"` (there is no `"cost_explorer_tag"`/`"cost_explorer_service"` equivalent for GCP in this phase).
5. THE project's dependency manifest (`pyproject.toml`) SHALL declare `google-auth`, `google-cloud-compute`, and `google-cloud-redis` as **optional** dependencies (an extras group, e.g. `gcp`), consistent with how `boto3` is a required (not optional) dependency today because the AWS provider is the primary supported backend — GCP dependencies SHALL NOT be required for users running the `fixture` or `aws` backends.
6. IF `JANITOR_BACKEND=gcp` is set and the GCP client libraries are not installed, THEN THE GCPProvider SHALL raise `ImportError` at instantiation with a message naming the missing package(s) and the `pip install` command to add the `gcp` extras group, mirroring `AWSProvider`'s existing boto3 `ImportError` message pattern.

### Requirement 10: Azure Provider — Managed Disk Waste Detection

**User Story:** As a FinOps stakeholder running Cloud Janitor against an Azure subscription, I want unattached Managed Disks to be detected as idle waste, equivalent to unattached EBS volumes, so that Azure audits surface the same class of finding.

#### Acceptance Criteria

1. WHEN `AzureProvider.get_cost_data()` is called with `resource_type` in (`None`, `"ebs"`), THE AzureProvider SHALL list Managed Disks in the configured subscription/resource group via the Azure Compute management API and identify disks with `diskState == "Unattached"` as candidates.
2. THE AzureProvider SHALL compute each unattached disk's `idle_days` as the number of days since its `timeCreated` property, mirroring the same age-since-creation approximation used for GCP (Requirement 5.2) and documented as an approximation, not a true idle-since-detach measurement.
3. WHEN a disk's `idle_days` is less than the caller's `min_idle_days` parameter, THE AzureProvider SHALL exclude it from the returned `resources` list.
4. THE AzureProvider SHALL return each qualifying disk using the resource type value `"ebs"`, matching Requirement 5.4's cross-provider consistency rationale, recording the true Azure resource kind (e.g. `"azure_resource_kind": "managed_disk"`) in metadata.
5. THE AzureProvider's returned resource dict SHALL include the same field set as Requirement 5 criterion 5.

### Requirement 11: Azure Provider — Azure Cache for Redis Waste Detection

**User Story:** As a FinOps stakeholder, I want idle Azure Cache for Redis instances to be detected, equivalent to idle ElastiCache clusters, so that Azure audits surface the same class of finding.

#### Acceptance Criteria

1. WHEN `AzureProvider.get_cost_data()` is called with `resource_type` in (`None`, `"elasticache"`), THE AzureProvider SHALL list Azure Cache for Redis instances in the configured subscription via the Azure Redis management API.
2. THE AzureProvider SHALL determine each instance's `idle_days` via Azure Monitor metrics (`connectedclients` or equivalent), mirroring Requirement 6.2's CloudWatch/Cloud Monitoring pattern; if no non-zero datapoint exists in the trailing 90-day window, `idle_days` SHALL be reported as `90`.
3. THE AzureProvider SHALL return each qualifying instance using the resource type value `"elasticache"`, recording the true Azure resource kind (e.g. `"azure_resource_kind": "cache_for_redis"`) in metadata.
4. THE AzureProvider's returned resource dict SHALL include the same field set as Requirement 6 criterion 4, adapted from the closest equivalent Azure Cache for Redis instance attributes.

### Requirement 12: Azure Provider — Network Security Group Security Findings

**User Story:** As a security engineer running Cloud Janitor against Azure, I want overly permissive NSG rules to be flagged, equivalent to open security groups on AWS, so that Azure audits surface the same class of finding.

#### Acceptance Criteria

1. WHEN `AzureProvider.get_security_data()` is called with `check_type` in (`None`, `"security_group"`), THE AzureProvider SHALL list Network Security Groups and their security rules in the configured subscription via the Azure Network management API.
2. WHEN an NSG rule has `direction == "Inbound"`, `access == "Allow"`, its `sourceAddressPrefix` is `"*"`, `"0.0.0.0/0"`, or `"Internet"`, and its `destinationPortRange` includes a sensitive port from the same port table as Requirement 7.2, THE AzureProvider SHALL emit a finding with the same severity that table assigns to that port.
3. THE AzureProvider SHALL emit each finding using the resource type value `"aws_security_group"`, matching Requirement 7.3's cross-provider consistency rationale, with `"azure_resource_kind": "network_security_group"` in metadata.
4. THE AzureProvider's `get_security_data()` return value SHALL match the shape `{"findings": [...], "critical_count": int}`, identical to the other providers' contract.
5. WHEN `check_type` is `"encryption"`, THE AzureProvider SHALL check Managed Disks for the `encryption.type` property and emit a `MEDIUM` finding for any disk not using at least platform-managed encryption at rest (Azure Managed Disks are encrypted by default with platform-managed keys; this criterion applies to disks with encryption explicitly disabled, which Azure permits only in specific legacy configurations). Per Requirement 16, THE AzureProvider SHALL include an explicit `resource_type: "ebs"` hint field on each such raw finding so `SecOpsGuard.check_encryption()` does not need to re-derive the type from an Azure-shaped `resource_id` via AWS-only substring matching.

### Requirement 13: Azure Provider — Credential Resolution, Cost Estimation, and Dependencies

**User Story:** As an operator deploying Cloud Janitor against Azure, I want a documented, working credential path and honest cost figures, so that the Azure backend is usable without a bespoke Cost Management export pipeline, on the same terms as the GCP backend.

#### Acceptance Criteria

1. THE AzureProvider SHALL resolve credentials via `azure.identity.DefaultAzureCredential()`, requiring the operator to have authenticated via one of the chain's supported mechanisms (`az login` for local development, a service principal via `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` environment variables for automation, or managed identity when running on Azure compute).
2. THE AzureProvider's constructor SHALL require an explicit `subscription_id` parameter (sourced from the `AZURE_SUBSCRIPTION_ID` environment variable when not passed explicitly), since — unlike GCP's ADC, which can infer a default project — every Azure management client requires a subscription ID to be threaded through explicitly.
3. IF neither an explicit `subscription_id` argument nor `AZURE_SUBSCRIPTION_ID` is available at `AzureProvider.__init__()` time, THEN THE AzureProvider SHALL raise a `RuntimeError` identifying the missing subscription ID and how to supply it.
4. IF `DefaultAzureCredential` fails to resolve any credential from its chain when a management client call is first made, THEN THE AzureProvider SHALL surface the underlying `azure.core.exceptions.ClientAuthenticationError` wrapped in a `RuntimeError` that names the credential chain and points to `az login` as the simplest local remediation.
5. THE AzureProvider SHALL compute `monthly_cost` using the same category of approach as Requirement 9.3 (an explicit price table, not a real Cost Management export integration), and SHALL set every resource's `cost_data_source` field to `"estimated"`.
6. THE project's dependency manifest (`pyproject.toml`) SHALL declare `azure-identity`, `azure-mgmt-compute`, `azure-mgmt-network`, and `azure-mgmt-redis` as an optional `azure` extras group, following the same optional-dependency pattern as Requirement 9.5.
7. IF `JANITOR_BACKEND=azure` is set and the Azure client libraries are not installed, THEN THE AzureProvider SHALL raise `ImportError` at instantiation naming the missing package(s) and the `pip install` command for the `azure` extras group.

### Requirement 14: Provider Registry and Backend Selection Unchanged

**User Story:** As a developer, I want the GCP and Azure providers to slot into the existing `JANITOR_BACKEND` dispatch mechanism with zero changes to `aws_janitor_mcp.py`, so that the provider-agnostic architecture the `provider-agnostic-backend` spec established is validated by actual use, not just by stubs.

#### Acceptance Criteria

1. THE `PROVIDER_REGISTRY` dict in `aws_janitor_mcp.py` SHALL require no code change to support the real GCPProvider/AzureProvider implementations — the existing `{"fixture": FixtureProvider, "aws": AWSProvider, "gcp": GCPProvider, "azure": AzureProvider}` mapping already routes `JANITOR_BACKEND=gcp`/`"azure"` to the correct class.
2. WHEN `JANITOR_BACKEND=gcp` or `JANITOR_BACKEND=azure` is set and the corresponding provider is instantiated successfully, THE MCP server's `get_cost_data`, `get_security_data`, and `check_dependencies` tools SHALL delegate to that provider instance exactly as they do for `fixture`/`aws` today, with no tool-signature changes.
3. THE `FinOpsAuditor` agent SHALL require no code changes to consume GCP/Azure findings, since both providers return the same `resource_type` values (`"ebs"`, `"elasticache"`) the agent's existing severity-classification logic already dispatches on (Requirements 5.4, 6.3, 10.4, 11.3). THE `SecOpsGuard` agent's security-group severity dispatch likewise requires no change (Requirements 7.3, 12.3) since it does not re-derive resource type from `resource_id`. **Correction from the original "zero agent changes" claim:** `SecOpsGuard` DOES require exactly one small code change for its encryption-finding classification path — see Requirement 16 — because `_determine_resource_type()`'s existing AWS-only ID-substring-matching would otherwise silently drop every GCP/Azure encryption finding as `"unknown"`.
4. `RemediationArchitect` requires exactly one small code change — see Requirement 15.3 — to avoid generating non-functional, AWS-shaped Terraform HCL against GCP/Azure resource IDs. This is a fail-safe narrowing (route to the existing manual-review placeholder), not new remediation capability, and is the only change required of `RemediationArchitect` in this phase.

### Requirement 15: GCP/Azure Remediation Scope Boundary (Audit/Detection-Only for This Phase)

**User Story:** As an operator running Cloud Janitor against GCP or Azure, I want to be told plainly that a GCP/Azure finding requires manual remediation, rather than have the system silently offer an "Approve" action that generates non-functional AWS-shaped Terraform against a GCP/Azure resource, so that I never mistake a broken remediation for a working one.

#### Acceptance Criteria

1. THE scope of FEAT-4 in this phase SHALL be limited to audit and detection for GCP and Azure — Requirements 5–13 govern `get_cost_data()`, `get_security_data()`, and `check_dependencies()` only. Generating new Terraform remediation templates, cloud-specific remediation CLI verbs, or provider-block/credential wiring for GCP or Azure output from `RemediationArchitect` is explicitly OUT OF SCOPE for this phase.
2. WHEN `RemediationArchitect.generate_remediation()` or `generate_rollback()` is called on a finding whose metadata contains a `gcp_resource_kind` or `azure_resource_kind` key (i.e. the finding originated from `GCPProvider`/`AzureProvider`, not `AWSProvider`), THE RemediationArchitect SHALL degrade to the same "manual review required" behavior it already uses for unhandled AWS resource types (`_remediation_generic`/`_rollback_generic`), rather than emitting AWS-shaped HCL (`aws_ebs_snapshot`, `aws_security_group_rule`, `aws ec2 delete-volume`/`aws elasticache delete-cache-cluster` CLI calls, etc.) against a non-AWS resource ID.
3. THIS requires one small, explicit code change to `agents/remediation_architect.py`: `generate_remediation()`/`generate_rollback()` SHALL check for the presence of a `*_resource_kind` metadata key before dispatching on `resource_type`, and route to the generic manual-review template when found. This is the only `RemediationArchitect` code change introduced by this phase, and it narrows behavior for safety — it does not add new remediation capability.
4. A future follow-on phase — comparable in size to building `remediation_architect.py` itself (i.e., roughly as large again, once for GCP and once for Azure) — is required to add real GCP/Azure remediation: new HCL generators for `google_compute_disk`/`azurerm_managed_disk`-family resources, cloud-specific CLI verbs in place of `aws ec2`/`aws elasticache`, and per-cloud provider-block/credential wiring in generated Terraform. This is explicitly deferred, not silently dropped, and SHALL be documented as such in the Introduction/Glossary rather than implied to already exist.
5. THE `findings_store.json` reporting/UI layer SHALL surface GCP/Azure findings identically to AWS findings for audit purposes (cost, severity, description) — the scope boundary in this requirement affects only the remediation/"Approve" pathway, not detection/reporting.

### Requirement 16: SecOps Guard Cross-Cloud Encryption Classification Fix

**User Story:** As a security engineer, I want GCP Persistent Disk and Azure Managed Disk encryption findings to be correctly classified and surfaced, rather than silently dropped as `"unknown"`, so that FEAT-4's "same class of finding" claim (Requirements 7.5, 12.5) is actually true end-to-end rather than true only for AWS.

#### Acceptance Criteria

1. THE existing `SecOpsGuard._determine_resource_type()` method (`agents/secops_guard.py`) classifies encryption findings by substring-matching `resource_id` against AWS-only naming conventions (`"vol-"`/`"ebs"` → `"ebs"`; `"cache-"`/`"cache"` → `"elasticache"`; else `"unknown"`). GCP Persistent Disk IDs and Azure Managed Disk IDs do not match these patterns, and encryption findings for them SHALL NOT be silently classified as `"unknown"` and discarded by `check_encryption()`'s type filter.
2. Per Requirements 7.5 and 12.5, THE raw encryption finding dict returned by `get_security_data(check_type="encryption")` for GCP/Azure findings SHALL include an explicit `resource_type` hint field (`"ebs"`, matching the same cross-provider normalization Requirements 5.4/10.4 already apply to waste findings) alongside the existing `gcp_resource_kind`/`azure_resource_kind` metadata field.
3. `SecOpsGuard.check_encryption()` SHALL prefer this caller-supplied `resource_type` hint when present over re-deriving the type from `resource_id` string-sniffing, falling back to the existing `_determine_resource_type()` logic only when no hint is supplied — preserving current AWS/fixture behavior unchanged for any finding that lacks the new hint field.
4. THIS is the one necessary `agents/secops_guard.py` code change introduced by this phase (see Requirement 14.3); it SHALL NOT alter classification behavior for any existing AWS-sourced finding.

### Requirement 17: SecOps Guard Sensitive Port Table Alignment (Prerequisite for Cross-Provider Parity)

**User Story:** As a security engineer, I want GCP firewall rules and Azure NSG rules open on RDP (3389) or HTTP/HTTPS-alt (8080/8443) to be flagged with the same severity an equivalent AWS security group would get, so that "same severity handling as AWS" (Requirements 7.2, 12.2) is actually achieved rather than silently narrowed by a stale, AWS-only port list.

#### Acceptance Criteria

1. THE existing `SecOpsGuard.SENSITIVE_PORTS = [22, 3306, 5432, 6379, 27017]` list and `DATABASE_CACHE_PORTS` set (`agents/secops_guard.py`) omit `3389` (RDP), `8080` (HTTP-alt), and `8443` (HTTPS-alt) — ports already present in the full port table `AWSProvider.get_security_data()` uses internally, and the same table Requirements 7.2 and 12.2 already require `GCPProvider`/`AzureProvider` findings to share "for cross-provider consistency."
2. AS A PREREQUISITE for Requirements 7.2 and 12.2's cross-provider-consistency claim, THE `SecOpsGuard.SENSITIVE_PORTS`/`DATABASE_CACHE_PORTS` constants SHALL be expanded to include `3389`, `8080`, and `8443`, with `3389` classified alongside the existing CRITICAL database/cache ports and `8080`/`8443` classified HIGH, matching the severities `AWSProvider.get_security_data()`'s port table already assigns.
3. THIS is a fix to an existing AWS-path file/behavior (`agents/secops_guard.py`), not exclusively new GCP/Azure code: the AWS path itself has under-flagged security groups open on 3389/8080/8443 since before this phase (the raw finding was already returned by `AWSProvider.get_security_data()` but silently filtered out by `SecOpsGuard`'s narrower list). This phase surfaces the gap because leaving it unfixed would compound the same under-flagging across two more clouds.
4. **Cross-spec reconciliation note:** `.kiro/specs/provider-agnostic-backend/requirements.md` Requirement 9, criterion 15 mandates that `agents/README.md` document `SecOpsGuard` severity rules using the pre-this-phase port list (`6379/3306/5432/27017` = CRITICAL, port 22 = HIGH). Once criterion 2 above lands, that earlier requirement's documented content is stale (3389 now joins the CRITICAL tier; 8080/8443 are now HIGH). This phase's Task 6.6 (`tasks.md`) updates `agents/README.md` to match, which is how this reconciliation is carried out in practice — `provider-agnostic-backend`'s own files are not modified by this phase.

### Requirement 18: Real-Account Smoke Test Gate for GCP/Azure Providers

**User Story:** As a developer relying on FEAT-4's test suite, I want at least one test run against a real (free-tier) GCP account and a real Azure account before either provider is declared complete, so that SDK response-shape mismatches invisible to dict-shaped mocks are caught before the first real customer call.

#### Acceptance Criteria

1. THE testing strategy SHALL document, as an explicit and accepted gap for this phase, that FEAT-4's test suite is 100% SDK-mocked (`tests/test_gcp_provider.py`, `tests/test_azure_provider.py`) with no LocalStack-equivalent for GCP/Azure, and that this is materially lower-confidence than the AWS path's LocalStack-based integration tests.
2. THE `google-cloud-compute`/`google-cloud-redis` SDKs return typed protobuf message objects with attribute access (e.g. `disk.users`, `disk.creation_timestamp`), NOT dict-shaped objects like boto3's `describe_volumes()` response. A test suite built entirely around dict-shaped mocks (e.g. `MagicMock(return_value={"users": [...]})`) SHALL NOT be considered sufficient evidence that `GCPProvider` works against the real SDK — mocks SHALL be constructed to mimic the actual protobuf attribute-access shape (e.g. `MagicMock(users=[...], creation_timestamp=...)`), and this distinction SHALL be called out explicitly in code review for `tests/test_gcp_provider.py`.
3. BEFORE `GCPProvider` is considered complete, AT LEAST ONE manual or CI-gated smoke test SHALL be run against a real free-tier GCP project, exercising `get_cost_data()`, `get_security_data()`, and `check_dependencies()` against at least one real Persistent Disk and one real Firewall Rule, to validate the SDK response shape assumption end-to-end.
4. BEFORE `AzureProvider` is considered complete, AT LEAST ONE manual or CI-gated smoke test SHALL be run against a real Azure subscription (free-tier or sandbox), exercising the same three methods against at least one real Managed Disk and one real NSG rule.
5. THESE smoke tests are NOT required to run on every CI build (they require live cloud credentials this repo's standard CI does not have) — they SHALL be run at least once before the corresponding provider's implementation is merged/released, and the fact that they were run (with a date and outcome) SHALL be recorded in the PR/commit description for that provider's implementation.

### Requirement 19: Azure Provider — Dependency Checks

**User Story:** As an operator approving a remediation, I want a dependency check for an Azure resource before it's deleted, equivalent to the AWS provider's ENI/attachment lookups and Requirement 8's GCP equivalent, so that the same safety gate applies across all three providers.

> **Note:** This requirement formalizes what Task 10.2 (`tasks.md`) previously described only as "mirrors Requirement 8, applied to Azure resource kinds" with no requirement ID of its own. Azure's `check_dependencies()` needed the same formal acceptance criteria GCP received in Requirement 8; this requirement supplies them.

#### Acceptance Criteria

1. WHEN `AzureProvider.check_dependencies()` is called with a resource ID that identifies a Network Security Group, THE AzureProvider SHALL list subnets and network interfaces (NICs) associated with that NSG via the Azure Network management API and return them as `dependents`.
2. WHEN called with a Managed Disk resource ID, THE AzureProvider SHALL check the disk's `managedBy` field for an attached virtual machine and return it (if present) as a `dependents` entry.
3. WHEN called with an Azure Cache for Redis instance resource ID, THE AzureProvider SHALL check for any documented consumer references (e.g. VNet/private-endpoint connections using the instance) and return them as `dependents`; if the Azure Redis management API exposes no dependency graph for a given resource, THE AzureProvider SHALL return an empty `dependents` list rather than raising.
4. THE AzureProvider's `check_dependencies()` return value SHALL match the shape `{"has_dependencies": bool, "dependents": [...]}` with `has_dependencies == (len(dependents) > 0)`, identical to `AWSProvider`'s, `FixtureProvider`'s, and `GCPProvider`'s (Requirement 8.4) contract.
