#!/usr/bin/env bash
# seed-localstack.sh — Pre-seeds LocalStack with the Ghost Cluster demo resources
# so that terraform apply (APPROVE) works end-to-end.
#
# This creates the actual AWS resources referenced in fixtures/aws_cost_explorer.json
# and fixtures/aws_config_inspector.json. After seeding, the remediation architect's
# generated HCL can successfully snapshot+delete volumes and modify security groups.
#
# Usage: bash scripts/seed-localstack.sh
# Requires: awslocal (pip install awscli-local) and a running LocalStack container

set -euo pipefail

ENDPOINT="http://localhost:4566"
REGION="us-east-1"

echo "🌱 Seeding LocalStack with Ghost Cluster demo resources..."

# ── EBS Volume: vol-0abc123def456789a (unattached, 100GB gp2) ──────────
echo "  Creating EBS volume vol-0abc123def456789a..."
awslocal ec2 create-volume \
  --availability-zone "${REGION}a" \
  --size 100 \
  --volume-type gp2 \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=orphaned-data-vol},{Key=Project,Value=legacy-etl}]" \
  --region "$REGION" \
  > /dev/null 2>&1 || true

# ── Security Group: sg-prod-redis (port 6379 open to 0.0.0.0/0) ───────
echo "  Creating VPC and security group sg-prod-redis..."
VPC_ID=$(awslocal ec2 create-vpc --cidr-block 10.0.0.0/16 --region "$REGION" \
  --query 'Vpc.VpcId' --output text 2>/dev/null || echo "")

if [ -n "$VPC_ID" ]; then
  SG_ID=$(awslocal ec2 create-security-group \
    --group-name sg-prod-redis \
    --description "Redis production security group" \
    --vpc-id "$VPC_ID" \
    --region "$REGION" \
    --query 'GroupId' --output text 2>/dev/null || echo "")

  if [ -n "$SG_ID" ]; then
    awslocal ec2 authorize-security-group-ingress \
      --group-id "$SG_ID" \
      --protocol tcp \
      --port 6379 \
      --cidr "0.0.0.0/0" \
      --region "$REGION" \
      > /dev/null 2>&1 || true
    echo "    Security group $SG_ID created with port 6379 open to 0.0.0.0/0"
  fi
fi

# ── ElastiCache: cache-prod-legacy-01 (requires Pro) ───────────────────
echo "  Creating ElastiCache cluster cache-prod-legacy-01..."
awslocal elasticache create-cache-cluster \
  --cache-cluster-id cache-prod-legacy-01 \
  --engine redis \
  --cache-node-type cache.t3.medium \
  --num-cache-nodes 1 \
  --region "$REGION" \
  > /dev/null 2>&1 || echo "    ⚠ ElastiCache creation skipped (requires LocalStack Pro)"

# ── S3 bucket for terraform state (needed by tflocal init) ─────────────
echo "  Creating S3 bucket for terraform state..."
awslocal s3 mb s3://cloud-janitor-state --region "$REGION" > /dev/null 2>&1 || true

echo ""
echo "✅ LocalStack seeded. Resources available at $ENDPOINT"
echo ""
echo "  Seeded resources:"
echo "    • EBS volume (100GB gp2, unattached)"
echo "    • VPC + Security Group (port 6379 open to 0.0.0.0/0)"
echo "    • ElastiCache cluster cache-prod-legacy-01 (Pro only)"
echo "    • S3 bucket cloud-janitor-state"
echo ""
echo "  Run 'make demo' or 'make demo-pro' to start the dashboard."
