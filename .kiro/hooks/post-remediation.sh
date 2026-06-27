#!/usr/bin/env bash
# Trigger: after approved remediation completes
# Action: append structured JSON entry to audit.log

set -euo pipefail

# Validate required arguments
if [ $# -lt 4 ]; then
    echo "[post-remediation] ERROR: Missing required arguments." >&2
    echo "Usage: post-remediation.sh <resource_id> <action> <result> <approver>" >&2
    echo "  action:   remediate | rollback" >&2
    echo "  result:   success | failed" >&2
    exit 1
fi

RESOURCE_ID="$1"
ACTION="$2"        # "remediate" | "rollback"
RESULT="$3"        # "success" | "failed"
APPROVER="$4"

# Resolve project root from script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AUDIT_LOG="$PROJECT_ROOT/audit.log"

# Generate ISO-8601 UTC timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Write JSON-formatted audit entry (append-only)
printf '{"timestamp": "%s", "action": "%s", "resource_id": "%s", "actor": "%s", "result": "%s", "details": "Post-remediation hook entry"}\n' \
    "$TIMESTAMP" "$ACTION" "$RESOURCE_ID" "$APPROVER" "$RESULT" >> "$AUDIT_LOG"

echo "[post-remediation] Audit entry written for resource: $RESOURCE_ID"
