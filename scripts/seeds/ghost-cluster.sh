#!/usr/bin/env bash
# ghost-cluster.sh — Seeds the Ghost Cluster demo scenario (multi-account)
#
# Creates AWS resources in LocalStack simulating 3 accounts:
#   - Production (111222333444): idle ElastiCache + open Redis SG
#   - Staging (555666777888): orphaned EBS volumes
#   - Development (999000111222): unencrypted storage + open SSH
#
# This file is sourced by scripts/seed-localstack.sh — $ENDPOINT and $REGION are available.

echo "  Scenario: Ghost Cluster (multi-account)"
echo ""

# ── Production account resources ───────────────────────────────────────
echo "  🏭 Production (111222333444):"

echo "    Creating VPC and security group (port 6379 open to world)..."
VPC_ID=$(awslocal ec2 create-vpc --cidr-block 10.0.0.0/16 --region "$REGION" \
  --query 'Vpc.VpcId' --output text 2>/dev/null || echo "")

if [ -n "$VPC_ID" ]; then
  SG_ID=$(awslocal ec2 create-security-group \
    --group-name sg-prod-redis \
    --description "Redis production SG - INTENTIONALLY INSECURE for demo" \
    --vpc-id "$VPC_ID" \
    --region "$REGION" \
    --query 'GroupId' --output text 2>/dev/null || echo "")

  if [ -n "$SG_ID" ]; then
    awslocal ec2 authorize-security-group-ingress \
      --group-id "$SG_ID" \
      --protocol tcp \
      --port 6379 \
      --cidr "0.0.0.0/0" \
      --region "$REGION" > /dev/null 2>&1 || true
    echo "    ✓ Security group $SG_ID (port 6379 → 0.0.0.0/0)"
  fi
fi

echo "    Creating ElastiCache cluster cache-prod-legacy-01..."
awslocal elasticache create-cache-cluster \
  --cache-cluster-id cache-prod-legacy-01 \
  --engine redis \
  --engine-version "7.0" \
  --cache-node-type cache.t3.medium \
  --num-cache-nodes 1 \
  --region "$REGION" \
  > /dev/null 2>&1 && echo "    ✓ ElastiCache cluster created" \
  || echo "    ⚠ ElastiCache skipped (requires LocalStack Pro)"

# ── Staging account resources ──────────────────────────────────────────
echo ""
echo "  🧪 Staging (555666777888):"

echo "    Creating orphaned EBS volumes..."
awslocal ec2 create-volume \
  --availability-zone "${REGION}a" \
  --size 100 \
  --volume-type gp2 \
  --no-encrypted \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=orphaned-data-vol},{Key=Account,Value=555666777888},{Key=Project,Value=legacy-etl}]" \
  --region "$REGION" \
  > /dev/null 2>&1 && echo "    ✓ EBS volume (100GB gp2, unattached)" || true

awslocal ec2 create-volume \
  --availability-zone "${REGION}a" \
  --size 50 \
  --volume-type gp3 \
  --no-encrypted \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=staging-logs-vol},{Key=Account,Value=555666777888},{Key=Project,Value=staging-logs}]" \
  --region "$REGION" \
  > /dev/null 2>&1 && echo "    ✓ EBS volume (50GB gp3, unattached)" || true

# ── Development account resources ──────────────────────────────────────
echo ""
echo "  🔧 Development (999000111222):"

echo "    Creating security group with SSH open..."
DEV_VPC_ID=$(awslocal ec2 create-vpc --cidr-block 172.16.0.0/16 --region "$REGION" \
  --query 'Vpc.VpcId' --output text 2>/dev/null || echo "")

if [ -n "$DEV_VPC_ID" ]; then
  DEV_SG_ID=$(awslocal ec2 create-security-group \
    --group-name sg-dev-bastion \
    --description "Dev bastion SG - SSH open to world" \
    --vpc-id "$DEV_VPC_ID" \
    --region "$REGION" \
    --query 'GroupId' --output text 2>/dev/null || echo "")

  if [ -n "$DEV_SG_ID" ]; then
    awslocal ec2 authorize-security-group-ingress \
      --group-id "$DEV_SG_ID" \
      --protocol tcp \
      --port 22 \
      --cidr "0.0.0.0/0" \
      --region "$REGION" > /dev/null 2>&1 || true
    echo "    ✓ Security group $DEV_SG_ID (port 22 → 0.0.0.0/0)"
  fi
fi

echo "    Creating unencrypted EBS volume..."
awslocal ec2 create-volume \
  --availability-zone "${REGION}a" \
  --size 200 \
  --volume-type gp3 \
  --no-encrypted \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=dev-database-vol},{Key=Account,Value=999000111222},{Key=Project,Value=dev-db}]" \
  --region "$REGION" \
  > /dev/null 2>&1 && echo "    ✓ EBS volume (200GB gp3, unencrypted)" || true

# ── Shared infrastructure ──────────────────────────────────────────────
echo ""
echo "  🪣 Shared:"
echo "    Creating S3 bucket for terraform state..."
awslocal s3 mb s3://cloud-janitor-state --region "$REGION" > /dev/null 2>&1 \
  && echo "    ✓ S3 bucket created" || echo "    ✓ S3 bucket already exists"

# ── Generate accounts.json for multi-account mode ──────────────────────
echo ""
echo "  📋 Writing accounts.json for multi-account orchestrator..."
cat > accounts.json << 'EOF'
[
  {
    "account_id": "111222333444",
    "account_name": "Production",
    "role_arn": "arn:aws:iam::111222333444:role/CloudJanitorReadOnly",
    "region": "us-east-1",
    "priority": "high"
  },
  {
    "account_id": "555666777888",
    "account_name": "Staging",
    "role_arn": "arn:aws:iam::555666777888:role/CloudJanitorReadOnly",
    "region": "us-east-1",
    "priority": "medium"
  },
  {
    "account_id": "999000111222",
    "account_name": "Development",
    "role_arn": "arn:aws:iam::999000111222:role/CloudJanitorReadOnly",
    "region": "us-east-1",
    "priority": "low"
  }
]
EOF
echo "    ✓ accounts.json written (3 accounts)"

echo ""
echo "  Summary:"
echo "    Production  → ElastiCache (idle) + Redis SG (open 6379)"
echo "    Staging     → 2 orphaned EBS volumes (unencrypted)"
echo "    Development → SSH SG (open 22) + unencrypted EBS"
echo "    Shared      → S3 state bucket + accounts.json"
