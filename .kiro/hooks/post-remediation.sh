#!/usr/bin/env bash
# Trigger: after approved remediation completes
# Action: append structured entry to audit.log

RESOURCE_ID="$1"
ACTION="$2"        # "remediate" | "rollback"
RESULT="$3"        # "success" | "failed"
APPROVER="$4"

AUDIT_LOG="./audit.log"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "${TIMESTAMP} | ${ACTION} | ${RESOURCE_ID} | ${RESULT} | approver=${APPROVER}" >> "$AUDIT_LOG"
echo "[post-remediation] Audit entry written."