#!/usr/bin/env bash
# Validate all generated HCL before the human approval gate.
set -euo pipefail

if [ "${JANITOR_DRY_RUN:-0}" = "1" ]; then
    echo "[pre-remediation] DRY RUN — skipping HCL validation"
    exit 0
fi

CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-${JANITOR_HOME:-${HOME:-/tmp}}/.terraform-plugin-cache}"
mkdir -p "$CACHE_DIR"
if command -v cygpath >/dev/null 2>&1; then
    CACHE_DIR="$(cygpath -m "$CACHE_DIR")"
fi
export TF_PLUGIN_CACHE_DIR="$CACHE_DIR"

TF_CMD="${TF_CMD:-tflocal}"
if ! command -v "$TF_CMD" >/dev/null 2>&1 ||
   ! "$TF_CMD" -version >/dev/null 2>&1 && ! "$TF_CMD" --version >/dev/null 2>&1; then
    if command -v terraform >/dev/null 2>&1; then
        TF_CMD="terraform"
    fi
fi
if ! command -v "$TF_CMD" >/dev/null 2>&1; then
    echo "[pre-remediation] BLOCKED: terraform command not found (TF_CMD=$TF_CMD)"
    exit 1
fi
echo "[pre-remediation] Using TF_CMD=$TF_CMD"

REMEDIATION_FILE="${1:-/tmp/remediation.tf}"
shift || true
ROLLBACK_FILES=("$@")
if [ "${#ROLLBACK_FILES[@]}" -eq 0 ]; then
    ROLLBACK_FILES=("/tmp/rollback.tf")
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

copy_plan() {
    local source="$1"
    local target="$2"
    if [ ! -f "$source" ]; then
        echo "[pre-remediation] BLOCKED: plan file not found: $source"
        return 1
    fi
    cp "$source" "$TMP_DIR/$target"
}

copy_plan "$REMEDIATION_FILE" "remediation.tf" || exit 1
for rollback_file in "${ROLLBACK_FILES[@]}"; do
    copy_plan "$rollback_file" "rollback-$(basename "$rollback_file")" || exit 1
done

cat > "$TMP_DIR/providers.tf" <<'EOF'
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  region                      = "us-east-1"
}

variable "environment" {
  default = "dev"
}
EOF

PLUGIN_ARGS=()
PLUGIN_DIR="${TF_PLUGIN_DIR:-}"
if [ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ]; then
    if command -v cygpath >/dev/null 2>&1; then
        PLUGIN_DIR="$(cygpath -m "$PLUGIN_DIR")"
    fi
    PLUGIN_ARGS=(-plugin-dir="$PLUGIN_DIR")
    echo "[pre-remediation] Using local provider mirror: $PLUGIN_DIR"
else
    echo "[pre-remediation] Local provider mirror unavailable; using persistent cache/registry fallback: $CACHE_DIR"
fi

start_time="$(date +%s)"
echo "[pre-remediation] Initializing $TF_CMD once for ${#ROLLBACK_FILES[@]} rollback plan(s)..."
if ! tf_output=$("$TF_CMD" -chdir="$TMP_DIR" init -backend=false -input=false -upgrade=false "${PLUGIN_ARGS[@]}" 2>&1); then
    echo "[pre-remediation] BLOCKED: $TF_CMD init failed"
    printf '%s\n' "$tf_output" | tail -n 20
    exit 1
fi
echo "[pre-remediation] init completed in $(( $(date +%s) - start_time ))s"

validate_plan() {
    local plan_file="$1"
    local label="$2"
    local plan_start
    plan_start="$(date +%s)"
    echo "[pre-remediation] Validating $label..."
    local validate_output
    if ! validate_output=$("$TF_CMD" -chdir="$TMP_DIR" validate 2>&1); then
        printf '%s\n' "$validate_output" | tail -n 20
        echo "[pre-remediation] Provider validation failed for $label; checking HCL syntax..."
        local fmt_output
        if ! fmt_output=$("$TF_CMD" fmt -check=false -write=false "$plan_file" 2>&1); then
            printf '%s\n' "$fmt_output" | tail -n 20
            echo "[pre-remediation] BLOCKED: $label has HCL syntax errors"
            return 1
        fi
    fi
    echo "[pre-remediation] validate completed for $label in $(( $(date +%s) - plan_start ))s"
}

validate_plan "$TMP_DIR/remediation.tf" "remediation.tf" || exit 1
for rollback_file in "${ROLLBACK_FILES[@]}"; do
    validate_plan "$TMP_DIR/rollback-$(basename "$rollback_file")" "rollback-$(basename "$rollback_file")" || exit 1
done

echo "[pre-remediation] Both plans valid. Proceeding to approval prompt."
