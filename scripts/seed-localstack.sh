#!/usr/bin/env bash
# seed-localstack.sh — Pre-seeds LocalStack with AWS resources for demo/testing
#
# Reads resource definitions from a seed file and creates them in LocalStack.
# Default seed file: scripts/seeds/ghost-cluster.sh (the built-in demo scenario)
# Custom seed file: pass as first argument, e.g.:
#   bash scripts/seed-localstack.sh scripts/seeds/my-scenario.sh
#
# Usage:
#   bash scripts/seed-localstack.sh                          # default ghost cluster
#   bash scripts/seed-localstack.sh scripts/seeds/custom.sh  # custom scenario
#
# Requires: awslocal (pip install awscli-local) and a running LocalStack container

set -euo pipefail

ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SEED_FILE="${1:-scripts/seeds/ghost-cluster.sh}"

if [ ! -f "$SEED_FILE" ]; then
  echo "❌ Seed file not found: $SEED_FILE"
  echo ""
  echo "Available seed files:"
  ls scripts/seeds/*.sh 2>/dev/null || echo "  (none)"
  echo ""
  echo "Create your own: copy scripts/seeds/ghost-cluster.sh as a template"
  exit 1
fi

echo "🌱 Seeding LocalStack from: $SEED_FILE"
echo "   Endpoint: $ENDPOINT"
echo "   Region:   $REGION"
echo ""

# Export for use in seed files
export ENDPOINT REGION

# Source the seed file (it defines the resources to create)
source "$SEED_FILE"

echo ""
echo "✅ LocalStack seeded successfully."
echo "   Run 'make demo', 'make demo-pro', or 'make demo-live' to start the dashboard."
