# Testing Rules

- NEVER run tests via `python -c "..."` inline commands
- ALWAYS write test logic to a `.py` file first (e.g. `tests/test_<feature>.py`)
- ALWAYS invoke tests using the venv interpreter: `".venv/Scripts/python.exe" -m pytest tests/ -n auto`
- NEVER use bare `python` — always use `".venv/Scripts/python.exe"`
- Run tests with: `".venv/Scripts/python.exe" -m pytest -n auto`
- Prefer `pytest` over `unittest` runner

## Hypothesis / Property Tests

- Hypothesis generates strings containing null bytes (`\x00`) by default — Windows rejects these in `os.environ`. Always restrict alphabets: `blacklist_categories=("Cs",), blacklist_characters="\x00"`
- Use `deadline=None` on property tests that involve file I/O or mock setup — Windows tmp_path operations are slow and trip the default 200ms deadline
- For tests that patch `os.environ` with `clear=True`: remember to explicitly `del os.environ["KEY"]` for keys you need ABSENT — `patch.dict` with `clear=False` won't remove pre-existing keys
- When testing module-level variables read at import time (e.g. `_PRIVACY_MODE` in `llm_client.py`), patch with `patch.object(module, "_VAR_NAME", value)` — patching `os.environ` alone won't work because the variable was already captured

## Orchestrator Test Patterns

- The Orchestrator constructor requires a directory tree: `output/`, `output/remediations/`, `output/rollbacks/`, `output/logs/`, `output/policies/`, `hooks/` — use `tmp_path` fixture and create all of these
- For `approve()` tests: inject a `RemediationPlan` into `orch._last_plans` so the method passes the `_find_plan()` gate
- Set `JANITOR_DRY_RUN=1` in env patches to skip terraform execution in approval/rollback tests that only test identity, gating, or audit logic
- Mock `cloud_janitor.orchestrator.orchestrator.resolve_actor` (the import location in the orchestrator module), not `cloud_janitor.core.identity.resolve_actor`
- Subprocess call counts change when new steps are added between init and apply (e.g. plan + show for scope check) — hardcoded `call_count` assertions in existing tests will break; update them when adding new subprocess steps

## Hook / Shell Script Tests

- To test bash hook scripts: create stub scripts in a temp `bin/` directory, set `PATH` to only include that directory + `/usr/bin:/bin`
- Stub scripts need `chmod +x` (use `stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH`)
- Set `TF_CMD=terraform` pointing to a stub, not the real binary — avoids provider downloads
- Use a controlled env dict (not inheriting the full process env) to isolate test conditions like `JANITOR_REQUIRE_TFSEC`
