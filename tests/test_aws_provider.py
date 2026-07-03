"""Tests for AWSProvider — live AWS backend.

All tests use moto to mock AWS APIs locally. No real credentials or network
calls are made.

Run:
    pytest tests/test_aws_provider.py -v

moto must be installed:
    pip install "moto[ec2,elasticache,cloudwatch]"
"""

from __future__ import annotations

import os
import sys
from typing import Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# moto import — skip entire module if not installed
# ---------------------------------------------------------------------------
try:
    from moto import mock_aws
    import boto3
    MOTO_AVAILABLE = True
except ImportError:
    MOTO_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not MOTO_AVAILABLE, reason="moto not installed — run: pip install 'moto[ec2,elasticache,cloudwatch]'"
)

# ---------------------------------------------------------------------------
# Fake AWS credentials (required by moto)
# ---------------------------------------------------------------------------
AWS_CREDS = {
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_SESSION_TOKEN": "test",
    "AWS_DEFAULT_REGION": "us-east-1",
}

REGION = "us-east-1"


def _provider(region: Optional[str] = REGION):
    """Return a fresh AWSProvider pointed at moto's mocked endpoints."""
    from cloud_janitor.mcp_server.backends.aws_provider import AWSProvider
    return AWSProvider(region=region)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def aws_env():
    """Patch AWS credentials into the environment for the duration of a test."""
    with patch.dict(os.environ, AWS_CREDS):
        yield


# ===========================================================================
# TestAWSProviderInit
# ===========================================================================

class TestAWSProviderInit:
    """AWSProvider instantiation behaviour."""

    def test_instantiates_with_no_region(self, aws_env):
        """AWSProvider() with no region argument does not raise."""
        from cloud_janitor.mcp_server.backends.aws_provider import AWSProvider
        p = AWSProvider()
        assert p is not None

    def test_instantiates_with_explicit_region(self, aws_env):
        """AWSProvider(region='us-west-2') stores the region."""
        from cloud_janitor.mcp_server.backends.aws_provider import AWSProvider
        p = AWSProvider(region="us-west-2")
        assert p._region == "us-west-2"

    def test_import_error_raised_without_boto3(self):
        """ImportError with install hint raised when boto3 is absent."""
        import builtins
        real_import = builtins.__import__

        def _block_boto3(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("no module named boto3")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block_boto3):
            from cloud_janitor.mcp_server.backends.aws_provider import AWSProvider as _AWSProvider
            with pytest.raises(ImportError, match="pip install boto3"):
                _AWSProvider.__new__(_AWSProvider).__init__()

    def test_is_cloud_provider_subclass(self, aws_env):
        """AWSProvider is a CloudProvider subclass (ABC contract satisfied)."""
        from cloud_janitor.mcp_server.backends import CloudProvider
        from cloud_janitor.mcp_server.backends.aws_provider import AWSProvider
        assert issubclass(AWSProvider, CloudProvider)


# ===========================================================================
# TestGetCostData
# ===========================================================================

class TestGetCostData:
    """AWSProvider.get_cost_data() with mocked AWS."""

    # --- Schema ---

    @mock_aws
    def test_returns_required_keys(self, aws_env):
        """get_cost_data() always returns 'resources' and 'total_monthly_waste'."""
        result = _provider().get_cost_data()
        assert "resources" in result
        assert "total_monthly_waste" in result
        assert isinstance(result["resources"], list)
        assert isinstance(result["total_monthly_waste"], (int, float))

    @mock_aws
    def test_empty_account_returns_zero_waste(self, aws_env):
        """Empty account (no resources) → waste == 0.0 and resources == []."""
        result = _provider().get_cost_data()
        assert result["resources"] == []
        assert result["total_monthly_waste"] == 0.0

    # --- EBS volumes ---

    @mock_aws
    def test_unattached_ebs_volume_detected(self, aws_env):
        """Unattached EBS volume with min_idle_days=0 appears in results."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp3")

        result = _provider().get_cost_data(min_idle_days=0)
        ebs = [r for r in result["resources"] if r["type"] == "ebs"]
        assert len(ebs) == 1

    @mock_aws
    def test_ebs_resource_schema(self, aws_env):
        """EBS resource dict contains all required schema fields."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=50, VolumeType="gp2")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        assert result["resources"], "Expected at least one EBS resource"
        r = result["resources"][0]

        required = {"id", "type", "name", "idle_days", "monthly_cost", "status",
                    "attached", "volume_type", "size_gb", "availability_zone",
                    "encrypted", "created_at", "description"}
        missing = required - set(r)
        assert not missing, f"Missing fields: {missing}"

    @mock_aws
    def test_ebs_type_field_is_ebs(self, aws_env):
        """EBS resource 'type' field is always 'ebs'."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=20, VolumeType="gp3")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        for r in result["resources"]:
            assert r["type"] == "ebs"

    @mock_aws
    def test_gp2_cost_higher_than_gp3(self, aws_env):
        """gp2 ($0.10/GB) is more expensive per GB than gp3 ($0.08/GB)."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp2")
        ec2.create_volume(AvailabilityZone=f"{REGION}b", Size=100, VolumeType="gp3")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        by_type = {r["volume_type"]: r["monthly_cost"] for r in result["resources"]}
        assert by_type["gp2"] > by_type["gp3"]

    @mock_aws
    def test_ebs_cost_scales_with_size(self, aws_env):
        """200 GB EBS volume costs exactly twice a 100 GB volume of same type."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp3")
        ec2.create_volume(AvailabilityZone=f"{REGION}b", Size=200, VolumeType="gp3")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        costs = sorted(r["monthly_cost"] for r in result["resources"])
        assert len(costs) == 2
        assert costs[1] == pytest.approx(costs[0] * 2)

    @mock_aws
    def test_attached_ebs_volume_excluded(self, aws_env):
        """Volumes in 'in-use' state (attached) are excluded from results."""
        ec2 = boto3.client("ec2", region_name=REGION)
        # Create and attach a volume
        vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=50, VolumeType="gp3")
        # Run instance to attach to
        instances = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
        iid = instances["Instances"][0]["InstanceId"]
        ec2.attach_volume(VolumeId=vol["VolumeId"], InstanceId=iid, Device="/dev/sdf")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        ids = [r["id"] for r in result["resources"]]
        assert vol["VolumeId"] not in ids, "Attached volume should not appear in waste"

    @mock_aws
    def test_ebs_name_from_tag(self, aws_env):
        """EBS resource 'name' field uses the 'Name' tag when present."""
        ec2 = boto3.client("ec2", region_name=REGION)
        vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
        ec2.create_tags(Resources=[vol["VolumeId"]], Tags=[{"Key": "Name", "Value": "my-test-vol"}])

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        assert result["resources"]
        assert result["resources"][0]["name"] == "my-test-vol"

    @mock_aws
    def test_ebs_name_falls_back_to_volume_id(self, aws_env):
        """EBS resource 'name' falls back to volume ID when no Name tag."""
        ec2 = boto3.client("ec2", region_name=REGION)
        vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        assert result["resources"]
        assert result["resources"][0]["name"] == vol["VolumeId"]

    # --- ElastiCache ---

    @mock_aws
    def test_elasticache_cluster_detected(self, aws_env):
        """Idle ElastiCache cluster (no CW datapoints → 90 idle days) is returned."""
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="idle-redis",
            CacheNodeType="cache.t3.medium",
            Engine="redis",
            NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type="elasticache", min_idle_days=7)
        caches = [r for r in result["resources"] if r["type"] == "elasticache"]
        assert len(caches) == 1
        assert caches[0]["id"] == "idle-redis"
        assert caches[0]["idle_days"] == 90

    @mock_aws
    def test_elasticache_resource_schema(self, aws_env):
        """ElastiCache resource dict contains all required schema fields."""
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="schema-test",
            CacheNodeType="cache.t3.micro",
            Engine="redis",
            NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type="elasticache", min_idle_days=0)
        assert result["resources"]
        r = result["resources"][0]

        required = {"id", "type", "name", "idle_days", "monthly_cost", "status",
                    "connections", "instance_type", "engine", "engine_version",
                    "num_cache_nodes"}
        missing = required - set(r)
        assert not missing, f"Missing fields: {missing}"

    @mock_aws
    def test_elasticache_known_cost_map(self, aws_env):
        """cache.t3.medium cluster has $46 monthly cost from the cost map."""
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="cost-test",
            CacheNodeType="cache.t3.medium",
            Engine="redis",
            NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type="elasticache", min_idle_days=0)
        assert result["resources"]
        assert result["resources"][0]["monthly_cost"] == 46.0

    @mock_aws
    def test_elasticache_unknown_type_gets_default_cost(self, aws_env):
        """Unknown node type falls back to $30.0 default monthly cost."""
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="unknown-type",
            CacheNodeType="cache.r7g.large",
            Engine="redis",
            NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type="elasticache", min_idle_days=0)
        assert result["resources"]
        assert result["resources"][0]["monthly_cost"] == 30.0

    # --- Filtering ---

    @mock_aws
    def test_resource_type_filter_ebs_excludes_elasticache(self, aws_env):
        """resource_type='ebs' returns only EBS resources."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="extra-cluster", CacheNodeType="cache.t3.micro",
            Engine="redis", NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        for r in result["resources"]:
            assert r["type"] == "ebs", f"Non-EBS resource leaked: {r}"

    @mock_aws
    def test_resource_type_filter_elasticache_excludes_ebs(self, aws_env):
        """resource_type='elasticache' returns only ElastiCache resources."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="filter-test", CacheNodeType="cache.t3.micro",
            Engine="redis", NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type="elasticache", min_idle_days=0)
        for r in result["resources"]:
            assert r["type"] == "elasticache", f"Non-ElastiCache resource leaked: {r}"

    @mock_aws
    def test_none_resource_type_returns_all_types(self, aws_env):
        """resource_type=None returns both EBS and ElastiCache resources."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="all-types", CacheNodeType="cache.t3.micro",
            Engine="redis", NumCacheNodes=1,
        )

        result = _provider().get_cost_data(resource_type=None, min_idle_days=0)
        types_seen = {r["type"] for r in result["resources"]}
        assert "ebs" in types_seen
        assert "elasticache" in types_seen

    # --- total_monthly_waste invariant ---

    @mock_aws
    def test_total_waste_equals_sum_of_resource_costs(self, aws_env):
        """total_monthly_waste == round(sum of monthly_cost for returned resources, 2)."""
        ec2 = boto3.client("ec2", region_name=REGION)
        for size in [50, 100, 200]:
            ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=size, VolumeType="gp3")

        result = _provider().get_cost_data(resource_type="ebs", min_idle_days=0)
        expected = round(sum(r["monthly_cost"] for r in result["resources"]), 2)
        assert result["total_monthly_waste"] == expected

    @mock_aws
    def test_total_waste_excludes_filtered_resources(self, aws_env):
        """total_monthly_waste only counts resources that pass all filters."""
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="counted", CacheNodeType="cache.t3.medium",
            Engine="redis", NumCacheNodes=1,
        )

        # With min_idle_days=0 → cluster is included (46.0)
        r0 = _provider().get_cost_data(resource_type="elasticache", min_idle_days=0)
        # With min_idle_days=999 → cluster excluded (0.0)
        r999 = _provider().get_cost_data(resource_type="elasticache", min_idle_days=999)

        assert r0["total_monthly_waste"] == 46.0
        assert r999["total_monthly_waste"] == 0.0


# ===========================================================================
# TestGetSecurityData
# ===========================================================================

class TestGetSecurityData:
    """AWSProvider.get_security_data() with mocked AWS."""

    # --- Schema ---

    @mock_aws
    def test_returns_required_keys(self, aws_env):
        """get_security_data() always returns 'findings' and 'critical_count'."""
        result = _provider().get_security_data()
        assert "findings" in result
        assert "critical_count" in result
        assert isinstance(result["findings"], list)
        assert isinstance(result["critical_count"], int)

    @mock_aws
    def test_empty_account_returns_zero_criticals(self, aws_env):
        """No dangerous resources → critical_count == 0."""
        result = _provider().get_security_data()
        assert result["critical_count"] == 0

    # --- Security group checks ---

    @mock_aws
    def test_redis_port_open_to_world_is_critical(self, aws_env):
        """SG with 6379 open to 0.0.0.0/0 produces a CRITICAL finding."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="redis-public", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 6379,
                "ToPort": 6379,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )

        result = _provider().get_security_data(check_type="security_group")
        assert result["critical_count"] >= 1
        crit = [f for f in result["findings"] if f["severity"] == "CRITICAL"]
        ports = [f["port"] for f in crit]
        assert 6379 in ports

    @mock_aws
    def test_ssh_port_open_to_world_is_critical(self, aws_env):
        """SG with port 22 open to 0.0.0.0/0 produces a CRITICAL finding."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="ssh-public", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )

        result = _provider().get_security_data(check_type="security_group")
        crit_ports = [f["port"] for f in result["findings"] if f["severity"] == "CRITICAL"]
        assert 22 in crit_ports

    @mock_aws
    def test_sg_restricted_to_vpc_cidr_not_flagged(self, aws_env):
        """SG restricted to a private CIDR (not 0.0.0.0/0) produces no findings."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="safe-sg", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 6379,
                "ToPort": 6379,
                "IpRanges": [{"CidrIp": "10.0.0.0/8"}],
            }],
        )

        result = _provider().get_security_data(check_type="security_group")
        assert result["critical_count"] == 0
        assert result["findings"] == []

    @mock_aws
    def test_sg_finding_schema(self, aws_env):
        """Security group finding dict contains all required schema fields."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="schema-sg", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )

        result = _provider().get_security_data(check_type="security_group")
        assert result["findings"]
        f = result["findings"][0]

        required = {"id", "resource_id", "resource_type", "check_type",
                    "severity", "port", "cidr", "current_state",
                    "required_state", "title", "description"}
        missing = required - set(f)
        assert not missing, f"Missing fields: {missing}"

    @mock_aws
    def test_multiple_dangerous_ports_on_one_sg(self, aws_env):
        """SG with two dangerous ports produces two findings."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="multi-bad", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 3306,
                    "ToPort": 3306,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
            ],
        )

        result = _provider().get_security_data(check_type="security_group")
        ports = {f["port"] for f in result["findings"]}
        assert 22 in ports
        assert 3306 in ports

    # --- Encryption checks ---

    @mock_aws
    def test_unencrypted_ebs_produces_medium_finding(self, aws_env):
        """Unencrypted EBS volume produces a MEDIUM severity finding."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(
            AvailabilityZone=f"{REGION}a", Size=50, VolumeType="gp3", Encrypted=False
        )

        result = _provider().get_security_data(check_type="encryption")
        ebs_findings = [
            f for f in result["findings"] if f["resource_type"] == "aws_ebs_volume"
        ]
        assert len(ebs_findings) >= 1
        assert ebs_findings[0]["severity"] == "MEDIUM"
        assert ebs_findings[0]["check_type"] == "encryption"

    @mock_aws
    def test_encrypted_ebs_not_flagged(self, aws_env):
        """Encrypted EBS volume does not produce an encryption finding."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(
            AvailabilityZone=f"{REGION}a", Size=50, VolumeType="gp3", Encrypted=True
        )

        result = _provider().get_security_data(check_type="encryption")
        ebs_findings = [
            f for f in result["findings"] if f["resource_type"] == "aws_ebs_volume"
        ]
        assert ebs_findings == []

    @mock_aws
    def test_unencrypted_elasticache_produces_high_finding(self, aws_env):
        """ElastiCache cluster without encryption at rest is HIGH severity."""
        ec = boto3.client("elasticache", region_name=REGION)
        ec.create_cache_cluster(
            CacheClusterId="unenc-cluster",
            CacheNodeType="cache.t3.micro",
            Engine="redis",
            NumCacheNodes=1,
            # AtRestEncryptionEnabled not set → None in moto → unencrypted
        )

        result = _provider().get_security_data(check_type="encryption")
        cache_findings = [
            f for f in result["findings"] if f["resource_type"] == "aws_elasticache_cluster"
        ]
        assert len(cache_findings) >= 1
        assert cache_findings[0]["severity"] == "HIGH"

    @mock_aws
    def test_encryption_finding_schema(self, aws_env):
        """Encryption finding contains all required schema fields."""
        ec2 = boto3.client("ec2", region_name=REGION)
        ec2.create_volume(
            AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3", Encrypted=False
        )

        result = _provider().get_security_data(check_type="encryption")
        assert result["findings"]
        f = result["findings"][0]

        required = {"id", "resource_id", "resource_type", "check_type",
                    "severity", "encryption_at_rest", "current_state",
                    "required_state", "title", "description"}
        missing = required - set(f)
        assert not missing, f"Missing fields: {missing}"

    # --- check_type filter ---

    @mock_aws
    def test_check_type_filter_security_group_excludes_encryption(self, aws_env):
        """check_type='security_group' returns only security_group findings."""
        ec2 = boto3.client("ec2", region_name=REGION)
        # Create both an open SG and an unencrypted volume
        sg = ec2.create_security_group(GroupName="open-sg", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

        result = _provider().get_security_data(check_type="security_group")
        for f in result["findings"]:
            assert f["check_type"] == "security_group", f"Unexpected finding: {f}"

    @mock_aws
    def test_check_type_filter_encryption_excludes_security_group(self, aws_env):
        """check_type='encryption' returns only encryption findings."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="open-sg2", Description="test")
        ec2.authorize_security_group_ingress(
            GroupId=sg["GroupId"],
            IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )
        ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

        result = _provider().get_security_data(check_type="encryption")
        for f in result["findings"]:
            assert f["check_type"] == "encryption", f"Unexpected finding: {f}"

    # --- critical_count invariant ---

    @mock_aws
    def test_critical_count_equals_critical_finding_count(self, aws_env):
        """critical_count always equals number of CRITICAL findings in the list."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="multi-crit", Description="test")
        for port in [22, 6379, 5432]:
            ec2.authorize_security_group_ingress(
                GroupId=sg["GroupId"],
                IpPermissions=[{
                    "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }],
            )

        result = _provider().get_security_data()
        expected = sum(1 for f in result["findings"] if f["severity"] == "CRITICAL")
        assert result["critical_count"] == expected


# ===========================================================================
# TestCheckDependencies
# ===========================================================================

class TestCheckDependencies:
    """AWSProvider.check_dependencies() with mocked AWS."""

    # --- Schema ---

    @mock_aws
    def test_returns_required_keys(self, aws_env):
        """check_dependencies() always returns 'has_dependencies' and 'dependents'."""
        result = _provider().check_dependencies("vol-doesnotexist")
        assert "has_dependencies" in result
        assert "dependents" in result
        assert isinstance(result["has_dependencies"], bool)
        assert isinstance(result["dependents"], list)

    # --- EBS dependencies ---

    @mock_aws
    def test_unattached_ebs_has_no_dependencies(self, aws_env):
        """Unattached EBS volume has no dependents."""
        ec2 = boto3.client("ec2", region_name=REGION)
        vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

        result = _provider().check_dependencies(vol["VolumeId"])
        assert result["has_dependencies"] is False
        assert result["dependents"] == []

    @mock_aws
    def test_attached_ebs_lists_instance_as_dependent(self, aws_env):
        """EBS volume attached to an instance lists that instance as a dependent."""
        ec2 = boto3.client("ec2", region_name=REGION)
        vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
        instances = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
        iid = instances["Instances"][0]["InstanceId"]
        ec2.attach_volume(VolumeId=vol["VolumeId"], InstanceId=iid, Device="/dev/sdf")

        result = _provider().check_dependencies(vol["VolumeId"])
        assert result["has_dependencies"] is True
        assert iid in result["dependents"]

    # --- Security group dependencies ---

    @mock_aws
    def test_unused_sg_has_no_dependencies(self, aws_env):
        """Security group with no attached ENIs has no dependents."""
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="unused-sg", Description="test")

        result = _provider().check_dependencies(sg["GroupId"])
        assert result["has_dependencies"] is False
        assert result["dependents"] == []

    # --- Boolean consistency invariant ---

    @mock_aws
    def test_has_dependencies_true_iff_dependents_nonempty(self, aws_env):
        """has_dependencies == True iff len(dependents) > 0 (both attached and not)."""
        ec2 = boto3.client("ec2", region_name=REGION)

        # Unattached
        free_vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=5, VolumeType="gp3")
        r_free = _provider().check_dependencies(free_vol["VolumeId"])
        assert r_free["has_dependencies"] == (len(r_free["dependents"]) > 0)

        # Attached
        att_vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=5, VolumeType="gp3")
        instances = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
        iid = instances["Instances"][0]["InstanceId"]
        ec2.attach_volume(VolumeId=att_vol["VolumeId"], InstanceId=iid, Device="/dev/sdg")

        r_att = _provider().check_dependencies(att_vol["VolumeId"])
        assert r_att["has_dependencies"] == (len(r_att["dependents"]) > 0)

    # --- Unknown / wrong-prefix resource IDs ---

    @mock_aws
    def test_unknown_resource_id_returns_empty(self, aws_env):
        """Non-existent resource ID (wrong prefix) returns no dependents."""
        result = _provider().check_dependencies("unknown-resource-xyz")
        assert result["has_dependencies"] is False
        assert result["dependents"] == []

    @mock_aws
    def test_nonexistent_volume_id_returns_empty(self, aws_env):
        """vol- prefix that doesn't exist in account returns no dependents."""
        result = _provider().check_dependencies("vol-00000000000000000")
        assert result["has_dependencies"] is False
        assert result["dependents"] == []