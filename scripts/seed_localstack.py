"""Seed LocalStack with demo resources using boto3 directly.

No awslocal or AWS CLI required — uses the same boto3 already in the project venv.

Usage:
    .venv/Scripts/python.exe scripts/seed_localstack.py
    .venv/Scripts/python.exe scripts/seed_localstack.py --scenario ghost-cluster
    .venv/Scripts/python.exe scripts/seed_localstack.py --scenario custom

Environment variables:
    AWS_ENDPOINT_URL — LocalStack endpoint (default: http://localhost:4566)
    AWS_DEFAULT_REGION — Region (default: us-east-1)
"""

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# Disable retries for faster feedback against LocalStack
_config = Config(retries={"max_attempts": 1}, connect_timeout=5, read_timeout=5)


def _client(service: str):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=_config,
    )


def seed_ghost_cluster():
    """Seed the Ghost Cluster multi-account demo scenario."""
    ec2 = _client("ec2")
    s3 = _client("s3")

    print("🌱 Seeding LocalStack with Ghost Cluster scenario")
    print(f"   Endpoint: {ENDPOINT}")
    print(f"   Region:   {REGION}")
    print()

    # ── Production: VPC + Security Group (port 6379 open) ──────────────
    print("  🏭 Production (111222333444):")
    try:
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc["Vpc"]["VpcId"]
        sg = ec2.create_security_group(
            GroupName="sg-prod-redis",
            Description="Redis production SG - INTENTIONALLY INSECURE for demo",
            VpcId=vpc_id,
        )
        sg_id = sg["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 6379,
                "ToPort": 6379,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )
        print(f"    ✓ Security group {sg_id} (port 6379 → 0.0.0.0/0)")
    except Exception as e:
        print(f"    ⚠ Security group: {e}")

    # ElastiCache (Pro only)
    try:
        elasticache = _client("elasticache")
        elasticache.create_cache_cluster(
            CacheClusterId="cache-prod-legacy-01",
            Engine="redis",
            EngineVersion="7.0",
            CacheNodeType="cache.t3.medium",
            NumCacheNodes=1,
        )
        print("    ✓ ElastiCache cluster cache-prod-legacy-01")
    except Exception as e:
        print(f"    ⚠ ElastiCache skipped: {type(e).__name__}")

    # ── Staging: Orphaned EBS volumes ──────────────────────────────────
    print()
    print("  🧪 Staging (555666777888):")
    try:
        vol1 = ec2.create_volume(
            AvailabilityZone=f"{REGION}a",
            Size=100,
            VolumeType="gp2",
            Encrypted=False,
            TagSpecifications=[{
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Name", "Value": "orphaned-data-vol"},
                    {"Key": "Account", "Value": "555666777888"},
                    {"Key": "Project", "Value": "legacy-etl"},
                ],
            }],
        )
        print(f"    ✓ EBS volume {vol1['VolumeId']} (100GB gp2, unattached)")
    except Exception as e:
        print(f"    ⚠ EBS volume 1: {e}")

    try:
        vol2 = ec2.create_volume(
            AvailabilityZone=f"{REGION}a",
            Size=50,
            VolumeType="gp3",
            Encrypted=False,
            TagSpecifications=[{
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Name", "Value": "staging-logs-vol"},
                    {"Key": "Account", "Value": "555666777888"},
                    {"Key": "Project", "Value": "staging-logs"},
                ],
            }],
        )
        print(f"    ✓ EBS volume {vol2['VolumeId']} (50GB gp3, unattached)")
    except Exception as e:
        print(f"    ⚠ EBS volume 2: {e}")

    # ── Development: SSH SG + unencrypted EBS ──────────────────────────
    print()
    print("  🔧 Development (999000111222):")
    try:
        dev_vpc = ec2.create_vpc(CidrBlock="172.16.0.0/16")
        dev_vpc_id = dev_vpc["Vpc"]["VpcId"]
        dev_sg = ec2.create_security_group(
            GroupName="sg-dev-bastion",
            Description="Dev bastion SG - SSH open to world",
            VpcId=dev_vpc_id,
        )
        dev_sg_id = dev_sg["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=dev_sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )
        print(f"    ✓ Security group {dev_sg_id} (port 22 → 0.0.0.0/0)")
    except Exception as e:
        print(f"    ⚠ Dev security group: {e}")

    try:
        vol3 = ec2.create_volume(
            AvailabilityZone=f"{REGION}a",
            Size=200,
            VolumeType="gp3",
            Encrypted=False,
            TagSpecifications=[{
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Name", "Value": "dev-database-vol"},
                    {"Key": "Account", "Value": "999000111222"},
                    {"Key": "Project", "Value": "dev-db"},
                ],
            }],
        )
        print(f"    ✓ EBS volume {vol3['VolumeId']} (200GB gp3, unencrypted)")
    except Exception as e:
        print(f"    ⚠ Dev EBS volume: {e}")

    # ── Shared: S3 bucket ──────────────────────────────────────────────
    print()
    print("  🪣 Shared:")
    try:
        s3.create_bucket(Bucket="cloud-janitor-state")
        print("    ✓ S3 bucket cloud-janitor-state")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print("    ✓ S3 bucket already exists")
    except Exception as e:
        print(f"    ⚠ S3 bucket: {e}")

    # ── Write accounts.json ────────────────────────────────────────────
    print()
    print("  📋 Writing accounts.json...")
    accounts = [
        {
            "account_id": "111222333444",
            "account_name": "Production",
            "role_arn": "arn:aws:iam::111222333444:role/CloudJanitorReadOnly",
            "region": "us-east-1",
            "priority": "high",
        },
        {
            "account_id": "555666777888",
            "account_name": "Staging",
            "role_arn": "arn:aws:iam::555666777888:role/CloudJanitorReadOnly",
            "region": "us-east-1",
            "priority": "medium",
        },
        {
            "account_id": "999000111222",
            "account_name": "Development",
            "role_arn": "arn:aws:iam::999000111222:role/CloudJanitorReadOnly",
            "region": "us-east-1",
            "priority": "low",
        },
    ]
    accounts_path = Path.cwd() / "accounts.json"
    accounts_path.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    print("    ✓ accounts.json written (3 accounts)")

    print()
    print("✅ LocalStack seeded successfully.")
    print()
    print("  Summary:")
    print("    Production  → ElastiCache (idle) + Redis SG (open 6379)")
    print("    Staging     → 2 orphaned EBS volumes (unencrypted)")
    print("    Development → SSH SG (open 22) + unencrypted EBS")
    print("    Shared      → S3 state bucket + accounts.json")


if __name__ == "__main__":
    scenario = "ghost-cluster"
    if "--scenario" in sys.argv:
        idx = sys.argv.index("--scenario")
        if idx + 1 < len(sys.argv):
            scenario = sys.argv[idx + 1]

    if scenario == "ghost-cluster":
        seed_ghost_cluster()
    else:
        print(f"Unknown scenario: {scenario}")
        print("Available: ghost-cluster")
        sys.exit(1)
