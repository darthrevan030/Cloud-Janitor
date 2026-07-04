# hooks/

Runtime pipeline hooks executed by the orchestrator during audit and remediation flows. These are **not** dev tools — they gate infrastructure changes in production.

> **Note:** The canonical copies of these scripts live in `src/cloud_janitor/hooks/` and are shipped as package data in the wheel. The root `hooks/` directory is a development convenience — edits should be synced to both locations.

## `pre-remediation.sh`

**Trigger:** Before the Approval Gate surfaces a prompt.

**Action:** Validates generated HCL using `tflocal init` + `tflocal validate` in an isolated temp directory.

**Behaviour:**

- Receives two arguments: path to `remediation.tf` and path to the rollback `.tf` file
- Copies each file into a fresh temp dir, runs `tflocal -chdir=<tmp> init -backend=false` then `tflocal -chdir=<tmp> validate`
- Exits `0` if both files pass validation
- Exits `1` (blocks the pipeline) if either file fails

**Environment:**

- `TF_CMD` — override the terraform binary (default: `tflocal`)

## `post-remediation.sh`

**Trigger:** After a successful `APPROVE` or `CONFIRM ROLLBACK`.

**Action:** Appends an entry to the audit log.

**Arguments:** `<resource_id> <action> <result> <approver>`

Where:

- `action` is `remediate` or `rollback`
- `result` is `success` or `failed`
- `approver` is the username from the orchestrator

## How They're Wired

The orchestrator calls these hooks via `subprocess.run(["bash", ...])`. On Windows, paths are converted to Git Bash format (`/d/...`) automatically by the `_to_bash_path()` helper in `orchestrator.py`.

### Environment Isolation

Hooks receive a **minimal environment** containing only `PATH` and essential system variables (`SYSTEMROOT`, `TEMP`, etc. on Windows). They do **not** receive:

- `OPENROUTER_API_KEY` or `JANITOR_LLM_API_KEY`
- `AWS_SECRET_ACCESS_KEY` or `AWS_SESSION_TOKEN`
- `LOCALSTACK_AUTH_TOKEN`

This prevents accidental secret leakage via hook scripts that might log their environment.

### Failure Behavior

- **`pre-remediation.sh` missing**: Pipeline **blocks** (fails closed). Remediation cannot proceed without validation.
- **`pre-remediation.sh` exits non-zero**: Pipeline blocks with the hook's stderr displayed.
- **`post-remediation.sh` missing**: Silently skipped (non-blocking).
- **`post-remediation.sh` exits non-zero**: Logged but does not halt execution.
