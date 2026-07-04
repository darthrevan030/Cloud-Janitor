# scripts/

Developer tooling and Git hook sources. None of these run automatically during the pipeline — they're invoked manually or by Git.

## `seed-localstack.sh`

Pre-seeds LocalStack with the Ghost Cluster demo resources so that `APPROVE` actually works end-to-end. Creates:

- EBS volume (100GB gp2, unattached) — for the snapshot-then-delete flow
- VPC + Security Group with port 6379 open to `0.0.0.0/0` — for the CIDR narrowing flow
- ElastiCache cluster `cache-prod-legacy-01` (Pro only) — for the snapshot-then-delete flow
- S3 bucket for terraform state

**Usage:**

```bash
bash scripts/seed-localstack.sh
```

Called automatically by `make demo` and `make demo-pro` after LocalStack is healthy. Can also be run standalone via `make seed`.

## `generate_spec_compliance.py`

Reads `.kiro/specs/**/tasks.md`, parses task checkboxes, verifies file artifacts exist, and outputs `SPEC_COMPLIANCE.md` at the project root.

**Usage:**

```bash
python3 scripts/generate_spec_compliance.py
```

The script resolves the project root as its own parent directory (`scripts/` → project root).

## `setup-hooks.sh`

Installs the Git post-commit hook into `.git/hooks/`.

```bash
bash scripts/setup-hooks.sh
```

## `git-hooks/`

Source files for Git hooks that get copied into `.git/hooks/` by `setup-hooks.sh`.

### `git-hooks/post-commit`

Runs after every `git commit`. Regenerates `SPEC_COMPLIANCE.md` and stages it:

```bash
python3 scripts/generate_spec_compliance.py && git add SPEC_COMPLIANCE.md
```

## Adding a New Script

Put it here if it's:

- A one-off or periodic dev task (not part of the runtime pipeline)
- A Git hook source
- A CI/CD helper

If it runs during the audit/remediation flow, it belongs in `hooks/` instead.
