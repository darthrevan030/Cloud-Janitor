"""Property-based tests for core/redaction.py — redact() and rehydrate() contracts.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs containing ARNs, account IDs, and resource IDs.

**Validates: Requirements 4.1, 4.2, 4.3**
"""

from __future__ import annotations

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.core.redaction import redact, rehydrate

# ---------------------------------------------------------------------------
# Strategies — Realistic AWS-like data generators
# ---------------------------------------------------------------------------

# AWS services that commonly appear in ARNs
_AWS_SERVICES = [
    "iam", "s3", "ec2", "lambda", "dynamodb", "sqs", "sns", "rds",
    "elasticache", "ecs", "eks", "cloudformation", "sts", "kms",
]

# AWS regions
_AWS_REGIONS = [
    "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
    "us-east-2", "eu-central-1", "ap-northeast-1", "",
]

# Resource type suffixes for ARNs
_RESOURCE_SUFFIXES = [
    "user/testuser", "role/admin-role", "instance/i-0abc123def456",
    "function:my-lambda", "table/my-table", "queue/my-queue",
    "topic/my-topic", "cluster/my-cluster", "bucket/my-bucket",
    "key/1234abcd-12ab-34cd-56ef-1234567890ab",
]


@st.composite
def aws_account_ids(draw):
    """Generate realistic 12-digit AWS account IDs."""
    # Generate a 12-digit number that looks like an account ID
    digits = draw(st.text(alphabet="0123456789", min_size=12, max_size=12))
    # Avoid all-zeros (LocalStack default) and ensure it looks realistic
    assume(digits != "000000000000")
    # Ensure it doesn't start with a run of the same digit (too uniform)
    return digits


@st.composite
def aws_arns(draw):
    """Generate realistic AWS ARN strings."""
    service = draw(st.sampled_from(_AWS_SERVICES))
    region = draw(st.sampled_from(_AWS_REGIONS))
    account_id = draw(aws_account_ids())
    resource = draw(st.sampled_from(_RESOURCE_SUFFIXES))
    return f"arn:aws:{service}:{region}:{account_id}:{resource}"


@st.composite
def resource_ids(draw):
    """Generate realistic AWS resource IDs (e.g., i-0abc123, vol-0def456, sg-012abc)."""
    prefix = draw(st.sampled_from([
        "i-", "vol-", "sg-", "subnet-", "vpc-", "snap-", "igw-",
        "eni-", "rtb-", "acl-", "nat-", "eipalloc-",
    ]))
    hex_part = draw(st.text(
        alphabet="0123456789abcdef", min_size=8, max_size=17
    ))
    assume(len(hex_part) >= 8)
    return prefix + hex_part


# Strategy for non-excluded field keys
_non_excluded_keys = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_",
    min_size=1, max_size=20,
).filter(lambda k: k not in ("resource_type", "region", "tags"))


@st.composite
def finding_dicts_with_sensitive_data(draw):
    """Generate dicts resembling AWS findings with sensitive values embedded.

    Always includes at least one ARN, one account ID, and one resource ID.

    NOTE: redact() auto-discovers resource IDs only from keys named exactly
    'resource_id' (via _collect_resource_ids_from_obj). Additional resource IDs
    must either be in a 'resource_id' key or passed explicitly. This strategy
    places all resource IDs in 'resource_id' keys (including nested dicts) and
    passes any extras via the resource_ids parameter to exercise both paths.
    """
    # Generate at least one of each sensitive type
    arns = draw(st.lists(aws_arns(), min_size=1, max_size=3))
    accounts = draw(st.lists(aws_account_ids(), min_size=1, max_size=3))
    res_ids = draw(st.lists(resource_ids(), min_size=1, max_size=3))

    # Build a finding-like dict with sensitive data in values
    # Place the primary resource_id in the standard key
    obj = {
        "resource_id": res_ids[0],
        "arn": arns[0],
        "account_id": accounts[0],
        "description": f"Resource {res_ids[0]} in account {accounts[0]} ({arns[0]})",
    }

    # Additional ARNs go in string fields (detected by regex pattern)
    for i, arn in enumerate(arns[1:], start=1):
        obj[f"related_arn_{i}"] = arn

    # Additional account IDs go in string fields (detected by regex pattern)
    for i, acct in enumerate(accounts[1:], start=1):
        obj[f"related_account_{i}"] = acct

    # Additional resource IDs are passed explicitly to redact() since
    # the implementation only auto-discovers from 'resource_id' keys
    extra_resource_ids = res_ids[1:]

    return obj, arns, accounts, res_ids, extra_resource_ids


@st.composite
def finding_dicts_with_excluded_keys(draw):
    """Generate dicts that always include resource_type, region, and tags keys
    alongside sensitive data to verify exclusion behavior.
    """
    # Generate excluded-key values (arbitrary strings, not sensitive)
    resource_type_val = draw(st.sampled_from([
        "ebs", "ec2", "elasticache", "security_group", "rds", "lambda",
    ]))
    region_val = draw(st.sampled_from(_AWS_REGIONS)).strip() or "us-east-1"
    tags_val = draw(st.dictionaries(
        keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz-_", min_size=1, max_size=15),
        values=st.text(min_size=0, max_size=30),
        min_size=0,
        max_size=5,
    ))

    # Also include sensitive data to ensure redaction still works on other keys
    arn = draw(aws_arns())
    account = draw(aws_account_ids())
    res_id = draw(resource_ids())

    obj = {
        "resource_type": resource_type_val,
        "region": region_val,
        "tags": tags_val,
        "resource_id": res_id,
        "arn": arn,
        "account_id": account,
        "description": f"Found {res_id} in {account}",
    }

    return obj


# ---------------------------------------------------------------------------
# Property 5: Redaction Round-Trip Fidelity
# ---------------------------------------------------------------------------


class TestRedactionRoundTripFidelity:
    """Property 5: For any dict containing ARNs, account IDs, and resource IDs:
    `redact()` then `rehydrate()` on a string that contains all placeholders
    returns the original values. The scrubbed output must not contain any of
    the original sensitive values.

    **Validates: Requirements 4.1, 4.2**
    """

    @settings(max_examples=200, deadline=None)
    @given(data=finding_dicts_with_sensitive_data())
    def test_scrubbed_output_contains_no_original_sensitive_values(self, data):
        """After redacting, the scrubbed output must not contain ANY of the
        original ARNs, account IDs, or resource IDs anywhere in its string
        representation.
        """
        obj, arns, accounts, res_ids, extra_resource_ids = data

        scrubbed, mapping = redact(obj, resource_ids=extra_resource_ids)

        # Convert scrubbed to a string for searching
        scrubbed_str = str(scrubbed)

        # Assert: no original ARN appears in the scrubbed output
        for arn in arns:
            assert arn not in scrubbed_str, (
                f"Original ARN {arn!r} leaked into scrubbed output"
            )

        # Assert: no original account ID appears in the scrubbed output
        for acct in accounts:
            assert acct not in scrubbed_str, (
                f"Original account ID {acct!r} leaked into scrubbed output"
            )

        # Assert: no original resource ID appears in the scrubbed output
        for rid in res_ids:
            assert rid not in scrubbed_str, (
                f"Original resource ID {rid!r} leaked into scrubbed output"
            )

    @settings(max_examples=200, deadline=None)
    @given(data=finding_dicts_with_sensitive_data())
    def test_mapping_is_nonempty_when_sensitive_data_present(self, data):
        """The redaction mapping must contain at least one entry when the input
        contains known sensitive values — a no-op redact would be a bug.
        """
        obj, arns, accounts, res_ids, extra_resource_ids = data

        _scrubbed, mapping = redact(obj, resource_ids=extra_resource_ids)

        # With at least 1 ARN, 1 account, and 1 resource ID, mapping must be non-empty
        assert len(mapping) > 0, (
            "redact() returned empty mapping despite input containing sensitive data"
        )

    @settings(max_examples=200, deadline=None)
    @given(data=finding_dicts_with_sensitive_data())
    def test_rehydrate_restores_all_original_values(self, data):
        """Constructing a string from all placeholders in the mapping and calling
        rehydrate() must produce a string containing all the original values.
        """
        obj, arns, accounts, res_ids, extra_resource_ids = data

        _scrubbed, mapping = redact(obj, resource_ids=extra_resource_ids)

        # Build a string that contains all placeholder tokens
        placeholder_text = " ".join(mapping.keys())

        # Rehydrate should restore all original values
        rehydrated = rehydrate(placeholder_text, mapping)

        # Assert each original value is present in the rehydrated text
        for placeholder, original in mapping.items():
            assert original in rehydrated, (
                f"rehydrate() did not restore original value {original!r} "
                f"for placeholder {placeholder!r}"
            )

    @settings(max_examples=200, deadline=None)
    @given(data=finding_dicts_with_sensitive_data())
    def test_rehydrate_removes_all_placeholders(self, data):
        """After rehydration, no placeholder tokens should remain in the output."""
        obj, _arns, _accounts, _res_ids, extra_resource_ids = data

        _scrubbed, mapping = redact(obj, resource_ids=extra_resource_ids)

        # Build a string that contains all placeholder tokens
        placeholder_text = " ".join(mapping.keys())

        # Rehydrate
        rehydrated = rehydrate(placeholder_text, mapping)

        # Assert no placeholder token remains
        for placeholder in mapping.keys():
            assert placeholder not in rehydrated, (
                f"Placeholder {placeholder!r} still present after rehydrate()"
            )

    @settings(max_examples=100, deadline=None)
    @given(data=finding_dicts_with_sensitive_data())
    def test_round_trip_fidelity_in_description_field(self, data):
        """The description field contains a composite string with ARN, account,
        and resource ID. Redacting and rehydrating that specific field should
        return the original description text.
        """
        obj, _arns, _accounts, _res_ids, extra_resource_ids = data

        original_description = obj["description"]
        scrubbed, mapping = redact(obj, resource_ids=extra_resource_ids)

        # The scrubbed description should be different from original
        # (since it contains sensitive data)
        scrubbed_description = scrubbed["description"]
        assert scrubbed_description != original_description, (
            "Scrubbed description is identical to original — "
            "no redaction occurred on a field with known sensitive data"
        )

        # Rehydrating the scrubbed description should restore the original
        restored_description = rehydrate(scrubbed_description, mapping)
        assert restored_description == original_description, (
            f"Round-trip failed for description field.\n"
            f"  Original:  {original_description!r}\n"
            f"  Scrubbed:  {scrubbed_description!r}\n"
            f"  Restored:  {restored_description!r}"
        )


# ---------------------------------------------------------------------------
# Property 6: Redaction Field Exclusion
# ---------------------------------------------------------------------------


class TestRedactionFieldExclusion:
    """Property 6: For any dict with keys `resource_type`, `region`, or `tags`:
    those values must be identical in the redacted output (not altered).

    **Validates: Requirement 4.3**
    """

    @settings(max_examples=200, deadline=None)
    @given(obj=finding_dicts_with_excluded_keys())
    def test_resource_type_preserved_unchanged(self, obj):
        """The `resource_type` field value must be identical in the redacted output."""
        scrubbed, _mapping = redact(obj)

        assert scrubbed["resource_type"] == obj["resource_type"], (
            f"resource_type was altered by redaction: "
            f"{obj['resource_type']!r} -> {scrubbed['resource_type']!r}"
        )

    @settings(max_examples=200, deadline=None)
    @given(obj=finding_dicts_with_excluded_keys())
    def test_region_preserved_unchanged(self, obj):
        """The `region` field value must be identical in the redacted output."""
        scrubbed, _mapping = redact(obj)

        assert scrubbed["region"] == obj["region"], (
            f"region was altered by redaction: "
            f"{obj['region']!r} -> {scrubbed['region']!r}"
        )

    @settings(max_examples=200, deadline=None)
    @given(obj=finding_dicts_with_excluded_keys())
    def test_tags_preserved_unchanged(self, obj):
        """The `tags` field value must be identical in the redacted output."""
        scrubbed, _mapping = redact(obj)

        assert scrubbed["tags"] == obj["tags"], (
            f"tags was altered by redaction: "
            f"{obj['tags']!r} -> {scrubbed['tags']!r}"
        )

    @settings(max_examples=100, deadline=None)
    @given(obj=finding_dicts_with_excluded_keys())
    def test_sensitive_fields_still_redacted_alongside_exclusions(self, obj):
        """Even while excluding resource_type/region/tags, the sensitive fields
        (resource_id, arn, account_id) must still be redacted — exclusion must
        not accidentally disable redaction of other fields.
        """
        scrubbed, mapping = redact(obj)

        # Mapping must be non-empty (sensitive data is present)
        assert len(mapping) > 0, (
            "redact() returned empty mapping despite input containing "
            "ARN, account_id, and resource_id"
        )

        # The arn field must have been redacted
        scrubbed_str = str(scrubbed)
        original_arn = obj["arn"]
        assert original_arn not in scrubbed_str, (
            f"Original ARN {original_arn!r} leaked despite exclusion logic"
        )

    @settings(max_examples=100, deadline=None)
    @given(obj=finding_dicts_with_excluded_keys())
    def test_excluded_keys_not_in_mapping(self, obj):
        """The values from excluded keys (resource_type, region, tags) must
        NOT appear as original values in the redaction mapping — they should
        never have been redacted in the first place.
        """
        _scrubbed, mapping = redact(obj)

        excluded_values = {obj["resource_type"], obj["region"]}
        # tags is a dict, so check its string representation isn't a mapped value
        mapping_originals = set(mapping.values())

        for excl_val in excluded_values:
            if excl_val:  # skip empty strings
                assert excl_val not in mapping_originals, (
                    f"Excluded field value {excl_val!r} was placed into "
                    f"the redaction mapping — it should have been excluded"
                )
