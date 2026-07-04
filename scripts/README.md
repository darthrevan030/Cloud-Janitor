# scripts/

Developer tooling and Git hook sources. None of these run automatically during the pipeline — they're invoked manually or by Git.

## `seed-localstack.sh`

Pre-seeds LocalStack with AWS resources for demo and testing. Reads from modular seed files in `scripts/seeds/`.

**Usage:**

```bash
# Default ghost cluster scenario
bash scripts/seed-localstack.sh

# Custom scenario
bash scripts/seed-localstack.sh scripts/seeds/my-scenario.sh
```

Called automatically by `make demo`, `make demo-pro`, and `make demo-live` after LocalStack is healthy. Can also be run standalone via `make seed`.

## `seeds/`

Modular seed scenario files. Each file creates AWS resources in LocalStack for a specific test case.

| File | Scenario | Description |
|------|----------|-------------|
| `ghost-cluster.sh` | Ghost Cluster (default) | Idle ElastiCache + orphaned EBS + open Redis SG |
| `example-custom.sh` | Template | Copy this to create your own scenarios |

### Creating a Custom Seed

1. Copy `scripts/seeds/example-custom.sh` to `scripts/seeds/my-scenario.sh`
2. Add `awslocal` commands to create your resources
3. Run: `bash scripts/seed-localstack.sh scripts/seeds/my-scenario.sh`
4. Use `make demo-live` to scan against your seeded resources

Resources created in LocalStack are ephemeral — they disappear when the container stops. No cleanup needed.

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
