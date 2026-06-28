#!/usr/bin/env bash
# Trigger: before Remediation Architect surfaces approval prompt
# Action: validate generated HCL — block if invalid
# This hook runs automatically; engineers cannot skip it.

set -e

TF_CMD="${TF_CMD:-tflocal}"

REMEDIATION_FILE="${1:-/tmp/remediation.tf}"
ROLLBACK_FILE="${2:-/tmp/rollback.tf}"

# validate_hcl copies a .tf file to an isolated temp directory,
# runs tflocal init + validate, then cleans up.
# Returns 0 on success, 1 on validation failure.
validate_hcl() {
    local hcl_file="$1"
    local label="$2"
    local tmp_dir

    if [ ! -f "$hcl_file" ]; then
        echo "[pre-remediation] BLOCKED: $label file not found: $hcl_file"
        return 1
    fi

    tmp_dir=$(mktemp -d)
    trap "rm -rf '$tmp_dir'" RETURN

    cp "$hcl_file" "$tmp_dir/main.tf"

    echo "[pre-remediation] Initializing $TF_CMD for $label..."
    if ! $TF_CMD -chdir="$tmp_dir" init -backend=false -input=false >/dev/null 2>&1; then
        echo "[pre-remediation] BLOCKED: $TF_CMD init failed for $label"
        return 1
    fi

    echo "[pre-remediation] Validating $label..."
    if ! $TF_CMD -chdir="$tmp_dir" validate; then
        echo "[pre-remediation] BLOCKED: $label failed validation"
        return 1
    fi

    return 0
}

echo "[pre-remediation] Validating remediation HCL..."
if ! validate_hcl "$REMEDIATION_FILE" "remediation.tf"; then
    exit 1
fi

echo "[pre-remediation] Validating rollback HCL..."
if ! validate_hcl "$ROLLBACK_FILE" "rollback.tf"; then
    exit 1
fi

echo "[pre-remediation] Both plans valid. Proceeding to approval prompt."
exit 0
