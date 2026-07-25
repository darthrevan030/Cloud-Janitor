"""Property tests for iam/janitor-read-policy.json correctness.

Property 4: Read Policy Action-Source Completeness
    Every boto3 method call following a _make_client(...)/_dep_client(...) in
    aws_provider.py has a corresponding IAM action in the read policy.

Property 5: Read Policy Non-Mutation
    No action in the read policy uses a mutation verb (Create, Delete, Modify,
    Put, Authorize, Revoke, Attach, Detach, Update) for ec2/elasticache prefixes.

**Validates: Requirements 3.1, 3.4, 3.5**
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths (relative to project root)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AWS_PROVIDER_PATH = PROJECT_ROOT / "src" / "cloud_janitor" / "mcp_server" / "backends" / "aws_provider.py"
READ_POLICY_PATH = PROJECT_ROOT / "iam" / "janitor-read-policy.json"

# ---------------------------------------------------------------------------
# Service alias mapping — maps the variable name used in _make_client/dep_client
# calls to the canonical IAM service prefix.
# ---------------------------------------------------------------------------

_SERVICE_ALIAS_MAP = {
    "ec2": "ec2",
    "elasticache": "elasticache",
    "cloudwatch": "cloudwatch",
    "sts": "sts",
    "cw": "cloudwatch",
    "ec": "elasticache",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _boto3_method_to_iam_action(method_name: str) -> str:
    """Convert a snake_case boto3 method to CamelCase IAM action verb.

    Example: describe_volumes -> DescribeVolumes
             get_metric_statistics -> GetMetricStatistics
    """
    return "".join(word.capitalize() for word in method_name.split("_"))


def _load_policy_actions(policy_path: Path) -> set[str]:
    """Load all actions from an IAM policy JSON file as a flat set.

    Returns actions in the form 'service:ActionName'.
    """
    with open(policy_path) as f:
        policy = json.load(f)

    actions: set[str] = set()
    for statement in policy.get("Statement", []):
        raw = statement.get("Action", [])
        if isinstance(raw, str):
            raw = [raw]
        actions.update(raw)
    return actions


def _extract_boto3_calls_from_source(source_path: Path) -> list[tuple[str, str]]:
    """Parse aws_provider.py and extract (service_prefix, method_name) tuples.

    Finds patterns:
      1. Variable assigned from _make_client("service", ...) or _dep_client("service")
         followed by var.get_paginator("method") or var.method(...)
      2. Fails loudly if boto3.client(...) or boto3.resource(...) is found directly.

    Returns a list of (iam_service_prefix, boto3_method_name) pairs.
    """
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))

    # Step 1: Fail if boto3.client() or boto3.resource() is called outside _make_client/_dep_client
    # We check for direct boto3.client/boto3.resource calls NOT inside _make_client or _dep_client
    _check_no_direct_boto3_usage(tree, source)

    # Step 2: Find variable→service mappings from _make_client/_dep_client calls
    var_service_map = _find_client_assignments(tree)

    # Step 3: Find all method calls on those variables
    calls = _find_method_calls(tree, var_service_map)

    return calls


def _check_no_direct_boto3_usage(tree: ast.Module, source: str) -> None:
    """Assert no direct boto3.client()/boto3.resource() outside helper functions.

    The only legitimate boto3.client() call sites are inside _make_client and
    _dep_client function definitions (including when _dep_client is a nested
    function inside another method). Any other use is a policy-bypass.
    """
    allowed_funcs = {"_make_client", "_dep_client"}

    def _is_inside_allowed_nested(node: ast.AST, func_body: list[ast.stmt]) -> bool:
        """Check if node is inside an allowed nested function definition."""
        for stmt in func_body:
            if isinstance(stmt, ast.FunctionDef) and stmt.name in allowed_funcs:
                # Check if node's line number falls within this nested func
                for child in ast.walk(stmt):
                    if child is node:
                        return True
            # Also recurse into nested structures (if/try/for/with blocks)
            for child_node in ast.iter_child_nodes(stmt):
                if isinstance(child_node, ast.FunctionDef) and child_node.name in allowed_funcs:
                    for nested_child in ast.walk(child_node):
                        if nested_child is node:
                            return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name not in allowed_funcs:
            # Collect line ranges of allowed nested functions inside this func
            nested_allowed_lines: set[int] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name in allowed_funcs:
                    # Mark all lines in this nested func as allowed
                    for nested_node in ast.walk(child):
                        if hasattr(nested_node, "lineno"):
                            nested_allowed_lines.add(nested_node.lineno)

            # Now walk for boto3.client/resource calls NOT in allowed nested funcs
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if (
                        isinstance(child.func.value, ast.Name)
                        and child.func.value.id == "boto3"
                        and child.func.attr in ("client", "resource")
                    ):
                        # Skip if this call is inside an allowed nested function
                        if hasattr(child, "lineno") and child.lineno in nested_allowed_lines:
                            continue
                        raise AssertionError(
                            f"Direct boto3.{child.func.attr}() call found in function "
                            f"'{node.name}' at line {child.lineno}. "
                            f"All AWS client creation must go through _make_client or _dep_client."
                        )


def _find_client_assignments(tree: ast.Module) -> dict[str, str]:
    """Find variable assignments from _make_client/dep_client calls.

    Returns a mapping of {variable_name: service_string}.
    Example: {"ec2": "ec2", "ec": "elasticache", "cw": "cloudwatch"}
    """
    var_service: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            # Check if it's _make_client(...) or _dep_client(...)
            func = call.func
            if isinstance(func, ast.Name) and func.id in ("_make_client", "_dep_client"):
                # First positional arg is the service name
                if call.args and isinstance(call.args[0], ast.Constant):
                    service_name = call.args[0].value
                    var_service[target.id] = service_name

    return var_service


def _find_method_calls(
    tree: ast.Module, var_service_map: dict[str, str]
) -> list[tuple[str, str]]:
    """Find all boto3 API method calls on client variables.

    Handles:
      - var.get_paginator("method_name") → extracts method_name
      - var.method_name(...) → extracts method_name (excluding get_paginator itself)

    Returns (iam_service_prefix, boto3_method_name) pairs.
    """
    calls: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        # Must be called on a known client variable
        if not isinstance(func.value, ast.Name):
            continue
        var_name = func.value.id
        if var_name not in var_service_map:
            continue

        service = var_service_map[var_name]
        method_attr = func.attr

        if method_attr == "get_paginator":
            # Extract the paginator method name from the string argument
            if node.args and isinstance(node.args[0], ast.Constant):
                method_name = node.args[0].value
                pair = (service, method_name)
                if pair not in seen:
                    seen.add(pair)
                    calls.append(pair)
        elif not method_attr.startswith("_") and method_attr != "get_paginator":
            # Direct method call like ec.describe_replication_groups()
            # Skip internal/dunder methods
            pair = (service, method_attr)
            if pair not in seen:
                seen.add(pair)
                calls.append(pair)

    return calls


# ---------------------------------------------------------------------------
# Property 4: Read Policy Action-Source Completeness
# ---------------------------------------------------------------------------


class TestReadPolicyActionSourceCompleteness:
    """Property 4: Every boto3 call in aws_provider.py is covered by the read policy.

    **Validates: Requirements 3.1, 3.5**
    """

    def test_source_file_exists(self):
        """Precondition: aws_provider.py must exist."""
        assert AWS_PROVIDER_PATH.exists(), (
            f"Source file not found: {AWS_PROVIDER_PATH}"
        )

    def test_policy_file_exists(self):
        """Precondition: janitor-read-policy.json must exist."""
        assert READ_POLICY_PATH.exists(), (
            f"Policy file not found: {READ_POLICY_PATH}"
        )

    def test_no_direct_boto3_client_usage(self):
        """All AWS client creation goes through _make_client or _dep_client."""
        source = AWS_PROVIDER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Should not raise
        _check_no_direct_boto3_usage(tree, source)

    def test_all_boto3_methods_covered_by_policy(self):
        """Every boto3 method called via _make_client/_dep_client has a policy action."""
        calls = _extract_boto3_calls_from_source(AWS_PROVIDER_PATH)
        policy_actions = _load_policy_actions(READ_POLICY_PATH)

        # calls is a list of (service, method_name) e.g. ("ec2", "describe_volumes")
        # Map to IAM action form: "ec2:DescribeVolumes"
        assert len(calls) > 0, "Parser found zero boto3 calls — sanity check failed"

        missing: list[str] = []
        for service, method_name in calls:
            # Resolve service alias to IAM prefix
            iam_prefix = _SERVICE_ALIAS_MAP.get(service, service)
            iam_action = f"{iam_prefix}:{_boto3_method_to_iam_action(method_name)}"

            if iam_action not in policy_actions:
                missing.append(f"{iam_action} (from {service}.{method_name})")

        assert not missing, (
            f"The following IAM actions derived from aws_provider.py are NOT "
            f"present in {READ_POLICY_PATH.name}:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_parser_finds_known_methods(self):
        """Sanity check: parser detects the known method→action mappings."""
        calls = _extract_boto3_calls_from_source(AWS_PROVIDER_PATH)

        # Convert to set of (service, method) for easier lookup
        call_set = set(calls)

        # These are the known calls from the design doc
        expected_calls = [
            ("ec2", "describe_volumes"),
            ("ec2", "describe_instances"),
            ("ec2", "describe_security_groups"),
            ("ec2", "describe_network_interfaces"),
            ("elasticache", "describe_cache_clusters"),
            ("elasticache", "describe_replication_groups"),
            ("cloudwatch", "get_metric_statistics"),
        ]

        for service, method in expected_calls:
            assert (service, method) in call_set, (
                f"Parser failed to detect known call: {service}.{method}()"
            )

    def test_extracted_actions_are_not_empty(self):
        """The parser must find at least 7 distinct boto3 methods (known minimum)."""
        calls = _extract_boto3_calls_from_source(AWS_PROVIDER_PATH)
        assert len(calls) >= 7, (
            f"Expected at least 7 distinct boto3 method calls, found {len(calls)}: "
            f"{calls}"
        )


# ---------------------------------------------------------------------------
# Property 5: Read Policy Non-Mutation
# ---------------------------------------------------------------------------

# Mutation verb prefixes that MUST NOT appear for ec2/elasticache in the read policy
_MUTATION_VERBS = (
    "Create",
    "Delete",
    "Modify",
    "Put",
    "Authorize",
    "Revoke",
    "Attach",
    "Detach",
    "Update",
)

# Service prefixes to check for mutation verbs
_CHECKED_SERVICES = ("ec2", "elasticache")


class TestReadPolicyNonMutation:
    """Property 5: The read policy contains no mutation actions for ec2/elasticache.

    **Validates: Requirements 3.4**
    """

    def test_no_mutation_verbs_in_read_policy(self):
        """No ec2/elasticache action in the read policy has a mutation verb prefix."""
        policy_actions = _load_policy_actions(READ_POLICY_PATH)

        violations: list[str] = []
        for action in policy_actions:
            if ":" not in action:
                continue
            service_prefix, action_name = action.split(":", 1)

            # Only check ec2 and elasticache prefixes
            if service_prefix.lower() not in _CHECKED_SERVICES:
                continue

            # Check if the action starts with any mutation verb
            for verb in _MUTATION_VERBS:
                if action_name.startswith(verb):
                    violations.append(
                        f"{action} (mutation verb: {verb})"
                    )
                    break

        assert not violations, (
            f"Read policy contains mutation actions for {'/'.join(_CHECKED_SERVICES)} "
            f"services (violates Requirement 3.4):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_policy_has_actions_to_check(self):
        """Sanity: the read policy has at least one ec2 or elasticache action."""
        policy_actions = _load_policy_actions(READ_POLICY_PATH)

        ec2_or_elasticache = [
            a for a in policy_actions
            if any(a.lower().startswith(svc + ":") for svc in _CHECKED_SERVICES)
        ]
        assert len(ec2_or_elasticache) > 0, (
            "Read policy has zero ec2/elasticache actions — nothing to validate"
        )

    def test_sts_actions_not_flagged_as_mutations(self):
        """sts:AssumeRole and sts:GetCallerIdentity are NOT ec2/elasticache.

        They should not trigger the mutation check even though 'AssumeRole'
        doesn't start with 'Describe' or 'Get'.
        """
        policy_actions = _load_policy_actions(READ_POLICY_PATH)

        # Verify these are present (precondition)
        assert "sts:AssumeRole" in policy_actions
        assert "sts:GetCallerIdentity" in policy_actions

        # Verify our check correctly excludes them — they are STS, not ec2/elasticache
        for action in ("sts:AssumeRole", "sts:GetCallerIdentity"):
            service_prefix = action.split(":")[0]
            assert service_prefix.lower() not in _CHECKED_SERVICES, (
                f"{action} should not be checked for mutation verbs"
            )

    def test_cloudwatch_actions_not_flagged(self):
        """cloudwatch:GetMetricStatistics is NOT ec2/elasticache.

        Ensures cloudwatch actions don't trigger mutation verb checks.
        """
        policy_actions = _load_policy_actions(READ_POLICY_PATH)
        assert "cloudwatch:GetMetricStatistics" in policy_actions

        service_prefix = "cloudwatch"
        assert service_prefix not in _CHECKED_SERVICES
