#!/usr/bin/env bash
# example-custom.sh — Template for creating your own seed scenario
#
# Copy this file and modify it to test Cloud Janitor against your own resources.
# Run with: bash scripts/seed-localstack.sh scripts/seeds/my-scenario.sh
#
# Available variables (exported by seed-localstack.sh):
#   $ENDPOINT — LocalStack endpoint (default: http://localhost:4566)
#   $REGION   — AWS region (default: us-east-1)
#
# Tips:
#   - Use 'awslocal' (awscli-local) for commands — it auto-targets LocalStack
#   - Resources don't need real tags/metadata — the scan uses CloudWatch
#     idle_days (which defaults to 90 in LocalStack since no metrics exist)
#   - Create unattached EBS volumes to trigger the FinOps EBS check
#   - Create security groups with 0.0.0.0/0 ingress to trigger the SecOps check
#   - Create unencrypted volumes/clusters to trigger encryption checks

echo "  Scenario: Custom Example"
echo ""

# ── Example: Create multiple unattached volumes ────────────────────────
echo "  Creating 3 orphaned EBS volumes..."
for i in 1 2 3; do
  awslocal ec2 create-volume \
    --availability-zone "${REGION}a" \
    --size $((50 * i)) \
    --volume-type gp3 \
    --no-encrypted \
    --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=test-vol-${i}},{Key=Team,Value=testing}]" \
    --region "$REGION" \
    > /dev/null 2>&1 && echo "    ✓ Volume test-vol-${i} ($(( 50 * i ))GB)" || true
done

# ── Example: Create a security group with SSH open ─────────────────────
echo "  Creating security group with SSH open to world..."
VPC_ID=$(awslocal ec2 describe-vpcs --region "$REGION" --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "")
if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
  VPC_ID=$(awslocal ec2 create-vpc --cidr-block 172.16.0.0/16 --region "$REGION" --query 'Vpc.VpcId' --output text 2>/dev/null)
fi

SG_ID=$(awslocal ec2 create-security-group \
  --group-name sg-test-ssh \
  --description "Test SG with SSH open" \
  --vpc-id "$VPC_ID" \
  --region "$REGION" \
  --query 'GroupId' --output text 2>/dev/null || echo "")

if [ -n "$SG_ID" ]; then
  awslocal ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp \
    --port 22 \
    --cidr "0.0.0.0/0" \
    --region "$REGION" > /dev/null 2>&1 || true
  echo "    ✓ Security group $SG_ID (port 22 → 0.0.0.0/0)"
fi

echo ""
echo "  Done! Run 'make demo-live' to scan these resources."
