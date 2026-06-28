# hooks/

Runtime pipeline hooks executed by the orchestrator during audit and remediation flows. These are **not** dev tools — they gate infrastructure changes in production.

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

Hook failures in `pre-remediation.sh` **block** the pipeline. Failures in `post-remediation.sh` are **non-blocking** (logged but don't halt execution).
