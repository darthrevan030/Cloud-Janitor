"""Unit tests for core/redaction.py — round-trip, field exclusion, nested structures."""


from cloud_janitor.core.redaction import redact, rehydrate


# ---------------------------------------------------------------------------
# Test 1: Round-trip — redact() scrubs, rehydrate() restores
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Verify that redact → rehydrate is lossless for LLM response text."""

    def setup_method(self):
        self.finding = {
            "resource_id": "vol-0abc123",
            "arn": "arn:aws:ec2:us-east-1:123456789012:volume/vol-0abc123",
            "account_id": "123456789012",
            "resource_type": "ebs",
            "region": "us-east-1",
        }

    def test_redact_removes_sensitive_values(self):
        scrubbed, mapping = redact(self.finding)

        scrubbed_str = str(scrubbed)
        # Original resource ID must not appear anywhere in the scrubbed output
        assert "vol-0abc123" not in scrubbed_str
        # Original account ID must not appear anywhere in the scrubbed output
        assert "123456789012" not in scrubbed_str
        # Full ARN must not appear
        assert "arn:aws:ec2:us-east-1:123456789012:volume/vol-0abc123" not in scrubbed_str

    def test_redact_produces_placeholder_tokens(self):
        scrubbed, mapping = redact(self.finding)

        # The mapping must contain entries — not empty
        assert len(mapping) >= 1
        # Placeholders follow the expected naming convention
        placeholder_prefixes = {k.rsplit("_", 1)[0] for k in mapping}
        assert placeholder_prefixes & {"ARN", "ACCOUNT", "RESOURCE"}

    def test_rehydrate_restores_originals(self):
        scrubbed, mapping = redact(self.finding)

        # Simulate an LLM response that references the placeholder tokens
        # Build a response containing all placeholders from the mapping
        placeholder_tokens = list(mapping.keys())
        mock_llm_response = (
            f"The resource {placeholder_tokens[0]} should be deleted. "
            f"See all placeholders: {', '.join(placeholder_tokens)}"
        )

        restored = rehydrate(mock_llm_response, mapping)

        # All original values must be present in the restored text
        for placeholder, original in mapping.items():
            assert original in restored, (
                f"Expected '{original}' (from placeholder '{placeholder}') in restored text"
            )
            # Placeholder should no longer appear
            assert placeholder not in restored

    def test_full_round_trip_restores_resource_id(self):
        scrubbed, mapping = redact(self.finding)

        # Build a mock response using the placeholder that maps to vol-0abc123
        resource_placeholder = None
        for ph, orig in mapping.items():
            if orig == "vol-0abc123":
                resource_placeholder = ph
                break

        assert resource_placeholder is not None, "No placeholder maps to vol-0abc123"

        mock_response = f"Delete {resource_placeholder} immediately."
        restored = rehydrate(mock_response, mapping)
        assert restored == "Delete vol-0abc123 immediately."


# ---------------------------------------------------------------------------
# Test 2: Field exclusion — resource_type, region, tags unchanged
# ---------------------------------------------------------------------------


class TestFieldExclusion:
    """Verify that excluded keys are never redacted."""

    def test_resource_type_unchanged(self):
        finding = {
            "resource_id": "vol-0abc123",
            "arn": "arn:aws:ec2:us-east-1:123456789012:volume/vol-0abc123",
            "account_id": "123456789012",
            "resource_type": "ebs",
            "region": "us-east-1",
        }
        scrubbed, _ = redact(finding)
        assert scrubbed["resource_type"] == "ebs"

    def test_region_unchanged(self):
        finding = {
            "resource_id": "vol-0abc123",
            "arn": "arn:aws:ec2:us-east-1:123456789012:volume/vol-0abc123",
            "account_id": "123456789012",
            "resource_type": "ebs",
            "region": "us-east-1",
        }
        scrubbed, _ = redact(finding)
        assert scrubbed["region"] == "us-east-1"

    def test_tags_unchanged(self):
        finding = {
            "resource_id": "i-0deadbeef",
            "arn": "arn:aws:ec2:us-west-2:999888777666:instance/i-0deadbeef",
            "account_id": "999888777666",
            "resource_type": "ec2",
            "region": "us-west-2",
            "tags": {"Name": "prod-web-01", "Environment": "production"},
        }
        scrubbed, _ = redact(finding)
        assert scrubbed["tags"] == {"Name": "prod-web-01", "Environment": "production"}

    def test_tags_with_sensitive_looking_values_preserved(self):
        """Tags may contain ARN-like or account-ID-like strings — they must still be preserved."""
        finding = {
            "resource_id": "vol-abc999",
            "resource_type": "ebs",
            "region": "eu-west-1",
            "tags": {
                "Owner": "arn:aws:iam::111222333444:user/dev",
                "CostCenter": "111222333444",
            },
        }
        scrubbed, _ = redact(finding)
        # Tags must be completely unchanged regardless of content
        assert scrubbed["tags"]["Owner"] == "arn:aws:iam::111222333444:user/dev"
        assert scrubbed["tags"]["CostCenter"] == "111222333444"


# ---------------------------------------------------------------------------
# Test 3: Nested structure — all levels redacted
# ---------------------------------------------------------------------------


class TestNestedStructure:
    """Verify that nested dicts and lists are redacted at all levels."""

    def test_nested_dict_in_list(self):
        nested = {
            "findings": [
                {
                    "resource_id": "i-abc123",
                    "details": {
                        "arn": "arn:aws:ec2:us-east-1:444555666777:instance/i-abc123",
                        "account_id": "444555666777",
                    },
                }
            ]
        }
        scrubbed, mapping = redact(nested)

        scrubbed_str = str(scrubbed)
        # Resource ID at top level of nested list item
        assert "i-abc123" not in scrubbed_str
        # ARN nested two levels deep
        assert "arn:aws:ec2:us-east-1:444555666777:instance/i-abc123" not in scrubbed_str
        # Account ID nested two levels deep
        assert "444555666777" not in scrubbed_str

    def test_multiple_findings_all_redacted(self):
        nested = {
            "findings": [
                {
                    "resource_id": "sg-111aaa",
                    "arn": "arn:aws:ec2:us-east-1:111222333444:security-group/sg-111aaa",
                },
                {
                    "resource_id": "sg-222bbb",
                    "arn": "arn:aws:ec2:us-west-2:555666777888:security-group/sg-222bbb",
                },
            ]
        }
        scrubbed, mapping = redact(nested)

        scrubbed_str = str(scrubbed)
        assert "sg-111aaa" not in scrubbed_str
        assert "sg-222bbb" not in scrubbed_str
        assert "111222333444" not in scrubbed_str
        assert "555666777888" not in scrubbed_str

    def test_deeply_nested_structure(self):
        """Three levels of nesting — dict → list → dict → dict."""
        deep = {
            "scan": {
                "results": [
                    {
                        "resource_id": "cache-deep-001",
                        "metadata": {
                            "arn": "arn:aws:elasticache:eu-west-1:888999000111:cluster:cache-deep-001",
                            "owner_account": "888999000111",
                        },
                    }
                ]
            }
        }
        scrubbed, mapping = redact(deep)

        scrubbed_str = str(scrubbed)
        assert "cache-deep-001" not in scrubbed_str
        assert "888999000111" not in scrubbed_str
        assert "arn:aws:elasticache:eu-west-1:888999000111:cluster:cache-deep-001" not in scrubbed_str

    def test_mapping_has_entries_for_all_sensitive_values(self):
        nested = {
            "findings": [
                {
                    "resource_id": "i-abc123",
                    "details": {
                        "arn": "arn:aws:ec2:us-east-1:444555666777:instance/i-abc123",
                    },
                }
            ]
        }
        _, mapping = redact(nested)

        # Mapping values must contain the originals
        originals_in_mapping = set(mapping.values())
        assert "i-abc123" in originals_in_mapping
        assert any("arn:aws:ec2:us-east-1:444555666777:instance/i-abc123" in v for v in originals_in_mapping)


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


class TestNegativeCases:
    """Negative and edge-case tests for robustness."""

    def test_rehydrate_with_unknown_placeholder_no_error(self):
        """If a placeholder in the mapping is not present in text, no error and text is unchanged."""
        mapping = {"RESOURCE_99": "vol-ghost", "ARN_99": "arn:aws:ec2:us-east-1:000000000000:volume/vol-ghost"}
        original_text = "This text has no placeholders at all."
        result = rehydrate(original_text, mapping)
        assert result == "This text has no placeholders at all."

    def test_redact_empty_dict(self):
        """An empty dict produces an empty dict and an empty mapping."""
        scrubbed, mapping = redact({})
        assert scrubbed == {}
        assert mapping == {}

    def test_redact_non_sensitive_string(self):
        """A plain string with no ARNs, account IDs, or resource IDs passes through."""
        scrubbed, mapping = redact("hello world, no secrets here")
        assert scrubbed == "hello world, no secrets here"
        assert mapping == {}

    def test_rehydrate_empty_mapping(self):
        """rehydrate with empty mapping returns text unchanged."""
        result = rehydrate("some response text", {})
        assert result == "some response text"
