# scripts/

Developer tooling and Git hook sources. None of these run automatically during the pipeline — they're invoked manually or by Git.

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
