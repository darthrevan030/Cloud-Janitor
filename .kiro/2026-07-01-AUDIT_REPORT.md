# Cloud Janitor — Audit Report (Verified Pass)
Repo: `darthrevan030/Cloud-Janitor` · Audited: 2026-07-01

## How to read this report

Every finding below is tagged with how it was verified:

- **CONFIRMED** — verified against the actual file content, quoted/paraphrased with line-level accuracy.
- **INFERRED** — consistent with what I could see, but I did not open the underlying file directly. Treat as a lead to confirm yourself, not a settled fact.
- **RETRACTED** — included in the first pass, disproven on recheck. Kept here for transparency.

Files directly retrieved and read in full or near-full: `orchestrator.py` (606 lines, complete), `app.py` (~1000 of 1375 lines), `.gitignore` (complete), `SPEC_COMPLIANCE.md` (complete), `README.md` (complete). Files **not** opened this pass: `agents/finops_auditor.py`, `agents/secops_guard.py`, `agents/approval_gate.py`, `agents/remediation_architect.py`, `savings.py`, `mcp_server/*`. Findings that depend on those are marked INFERRED.

## Corrections from the first pass

1. **RETRACTED — "two branches with different content, needs merging."** I don't have solid evidence for this. On recheck I could only ever retrieve `app.py` labeled `main` and `orchestrator.py` labeled `master` — every attempt to fetch the other combination (`app.py`@master, `orchestrator.py`@main) was blocked by my tool's own access-permission gate, not by GitHub. I have no actual diff proving two divergent versions exist. **Action for you:** run `git ls-remote --heads origin` — this is the one command that tells you, with certainty, exactly which branches exist on the remote right now. If `master` is still there, `git push origin --delete master` removes it.
2. **RETRACTED — "findings_store.json etc. may not be gitignored."** Checked `.gitignore` directly: it explicitly covers both the root-level and `output/`-prefixed paths for `findings_store.json`, `audit.log`, `savings_ledger.json`, `remediation.tf`, and rollback directories, including `.gitkeep` exceptions for both. This is already handled.
3. **Downgraded — "NL Audit button will crash for all users."** It's wrapped in `try/except Exception`, so it fails gracefully with `st.error(...)` rather than crashing the app. It's still a dead, non-functional button (the method it calls doesn't exist), just not as severe as "unhandled crash."
4. **Sharpened — rollback bug.** On rereading `orchestrator.py` in full: it's not just that *confirming* a rollback has no attempt limit — **neither `rollback()` nor `_handle_confirm_rollback()` uses any `ApprovalGate` at all.** The entire rollback path (unlike `approve()`) has zero rate-limiting. And `_handle_confirm_rollback()` truly never calls `subprocess.run(...)` — it logs success and returns `success=True` without touching Terraform. Both are confirmed by direct code read, not inference.
5. **Sharpened — path mismatch severity.** This isn't just a code-hygiene nit. `orchestrator.py`'s `Orchestrator.__init__` wires `self.findings_store_path = self.project_root / "findings_store.json"` (root-level) into the `FinOpsAuditor` and `SecOpsGuard` instances it creates. `app.py`'s `load_findings()` reads from `PROJECT_ROOT / "output" / "findings_store.json"`. That means: **every real audit run writes findings to a path the UI never reads from.** The Findings panel will show 0 findings (or stale seed data, if `output/findings_store.json` is a checked-in fixture) even after a successful audit. Same mismatch pattern applies to the rollbacks directory (root `rollbacks/` vs `output/rollbacks/`), which breaks the Diff View panel's resource dropdown. The audit-trail panel is *not* affected the same way, because it has an in-memory fallback (`st.session_state.orchestrator._audit_trail`) that's populated regardless of file path — so that one degrades gracefully; findings and diff view do not.

## Confirmed findings

### CRITICAL

**1. Rollback path has no approval-gate rate limiting, and never executes Terraform.**
*(CONFIRMED — orchestrator.py, `rollback()`, `_handle_confirm_rollback()`)*
Neither method constructs or checks an `ApprovalGate`. `_handle_confirm_rollback` validates the rollback file exists, discards the pending-rollback flag, logs a success entry, runs the post-remediation hook, and returns `RollbackResult(success=True, ...)` — at no point calling `tflocal apply` or any equivalent. Functionally, "rollback" in this codebase is a logging no-op today. Given the product's entire value proposition is "human approval + safe rollback before touching anything," this is the single most important thing to fix before any real-AWS use.

**2. `TF_CMD` environment variable is used unvalidated in `subprocess.run`.**
*(CONFIRMED — orchestrator.py, module level + `approve()`)*
`TF_CMD = os.environ.get("TF_CMD", "tflocal")`, then `subprocess.run([TF_CMD, "apply", "-auto-approve"], ...)`. `subprocess.run` with a list (not `shell=True`) avoids shell-metacharacter injection, but there's no allowlist — if `TF_CMD` is set to an arbitrary path/binary, that binary runs with whatever privileges the process has. Practically this requires an attacker to already control the process environment (e.g. a compromised CI runner or misconfigured container), so treat this as defense-in-depth hardening rather than a directly exploitable remote vector — but it's a one-line fix, so worth doing regardless.

### HIGH

**3. `app.py`'s "Run Audit" button duplicates `Orchestrator.execute_audit()` instead of calling it.**
*(CONFIRMED — app.py, `if st.button("▶ Run Audit", ...)` block)*
The handler manually calls `orch._log_action(...)`, `orch._finops.scan()`, `orch._secops.scan()`, `orch._validate_findings_store()`, `orch._architect.plan()`, `orch._last_plans = plans`, `orch._run_pre_remediation_hook(...)` — reaching into five private members and reimplementing `execute_audit()`'s exact control flow line-for-line. Any future change to `execute_audit()` (new validation step, new hook, new agent) has to be manually mirrored here or the UI silently diverges from the orchestrator's real behavior.

**4. Path mismatch between `orchestrator.py` and `app.py` breaks the Findings and Diff View panels.**
*(CONFIRMED — see "Corrections" #5 above for full detail)*
`orchestrator.py`: `findings_store.json`, `rollbacks/`, `audit.log`, `agent_reasoning.log` all at project root.
`app.py`: `output/findings_store.json`, `output/rollbacks/`, `output/logs/audit.log`, `output/logs/agent_reasoning.log`.
These need to converge on one convention. Given `.gitignore` and the more feature-complete `app.py` both already assume the `output/`-prefixed convention, that's the more likely "current" direction — but confirm which branch/file set you intend to keep before picking a side.

**5. Pre-remediation hook only validates the first rollback file it finds, not all of them.**
*(CONFIRMED — orchestrator.py, `_run_pre_remediation_hook()`)*
```python
for plan in plans:
    candidate = self.rollbacks_dir / f"{plan.resource_id}.tf"
    if candidate.exists():
        rollback_path = candidate
        break
```
If an audit produces 3 active plans, only one resource's rollback file gets validated against `remediation.tf`, regardless of how many resources the remediation actually touches.

### MEDIUM

**6. Approval gates are held in a plain in-memory dict — lost on process restart.**
*(CONFIRMED — orchestrator.py, `self._approval_gates: dict[str, ApprovalGate] = {}`)*
No persistence. If the Streamlit process restarts (which it does automatically on file save in dev), every gate's attempt count resets — a 3-strikes-locked gate becomes fully unlocked again.

**7. `_validate_findings_store()` checks agent presence but not schema version.**
*(CONFIRMED — orchestrator.py)*
Only checks that both `"finops"` and `"secops"` appear as `agent` values in the findings list. No `schema_version` field exists or is checked. As Phase B/C agents add new finding shapes, there's nothing to catch a stale-format store being read by newer code (or vice versa).

**8. `savings_tracker.record_run()` exception handling is too narrow.**
*(CONFIRMED — orchestrator.py, `approve()`)*
```python
except (FileNotFoundError, OSError) as e:
```
A `json.JSONDecodeError` from a corrupted ledger, or a `ValueError`/`KeyError` from unexpected data, will propagate uncaught — surfacing to the user as an unhandled exception *after* a successful, already-applied remediation. Since this call is explicitly meant to be non-blocking, the catch should be broad.

**9. `_extract_resource_id_from_command()` uses an anti-space check, not an allowlist.**
*(CONFIRMED — orchestrator.py)*
```python
if not resource_id or " " in resource_id:
    return None
```
Works today because AWS resource IDs don't contain spaces, but it's not an explicit format contract — a positive regex allowlist (`re.fullmatch(r'[a-zA-Z0-9_\-:.]+', resource_id)`) documents intent and fails safely on unexpected input.

**10. SPEC_COMPLIANCE.md item #77 is marked Pending, but the feature it tracks already exists (and is broken).**
*(CONFIRMED — SPEC_COMPLIANCE.md + app.py cross-reference)*
Item 77, "Add NL query input and AI panels to `app.py`," is `❌ Pending`. But `app.py` already has the NL query text input, the "🔍 NL Audit" button, and a call to `orch.execute_natural_language_audit(...)` — a method that doesn't exist anywhere in `orchestrator.py`'s public API (confirmed: only `execute_audit`, `approve`, `rollback`, `get_audit_trail` are defined). The compliance generator is checking for file/string existence, not actually parsing feature completeness — it missed that this exact feature is already (non-functionally) merged. The button currently fails gracefully via `except Exception: st.error(...)` rather than crashing, but it will never succeed for any user.

**11. Reasoning log is truncated at the start of every audit run.**
*(CONFIRMED — orchestrator.py, `execute_audit()`)*
`self._reasoning_logger.truncate()` runs as literally the first line of every audit. Fine for a live demo; means all historical agent reasoning is gone the moment the next audit starts, which will hurt when you're trying to debug why a past decision was made.

### LOW

**12. No structured error telemetry — all agent exceptions become raw f-string error text.**
*(CONFIRMED — app.py, multiple `except Exception as e: st.error(f"... failed: {e}")` blocks)*
No traceback capture into the audit log, no error classification. Debugging a production failure means correlating UI timestamps with log timestamps by hand.

**13. Phase B/C agent imports use `globals()[attr] = ...` — mypy-invisible, silent-`None` on failure.**
*(CONFIRMED — app.py, `_PHASE_BC_AGENTS` loop)*
```python
for _mod_name, _attr_name in _PHASE_BC_AGENTS:
    try:
        _mod = __import__(_mod_name, fromlist=[_attr_name])
        globals()[_attr_name] = getattr(_mod, _attr_name)
    except (ImportError, AttributeError):
        globals()[_attr_name] = None
```
Works, but any static type checker loses visibility into these names, and any downstream code using them needs a manual `is not None` guard with no single source of truth for "is this feature available."

**14. Concurrent Streamlit sessions share the same on-disk files.**
*(CONFIRMED at the design level, from `app.py` + `orchestrator.py` path constants)*
Each browser session gets its own `Orchestrator` instance via `st.session_state` (Streamlit's normal per-session isolation works correctly here), but all instances point at the same fixed filesystem paths (`findings_store.json`, `output/remediation.tf`, rollback files). Two people running audits concurrently against the same deployment will overwrite each other's plans on disk. Fine for a solo demo; worth a README callout before anyone else touches it.

## Findings carried forward as INFERRED (not independently verified this pass)

These were in the original report and are plausible given what I *did* verify, but I have not opened the underlying files to confirm them directly. I'd rather flag that honestly than restate them as settled fact a second time.

- **Race condition / no file locking between FinOps and SecOps writes to `findings_store.json`.** Consistent with what's visible in `orchestrator.py` (no locking calls anywhere at the orchestration layer), but the actual write logic lives in `agents/finops_auditor.py` and `agents/secops_guard.py`, which I haven't opened.
- **GCP/Azure MCP provider stubs raise `NotImplementedError` in a way that could propagate unhandled to MCP clients.** Consistent with the README's own description ("all methods raise `NotImplementedError`"), but I haven't opened `mcp_server/aws_janitor_mcp.py` or the provider files to check whether the MCP tool layer catches this.

If you want these fully verified, the fastest path is for you to paste or upload those specific files directly — that sidesteps the GitHub-scraping friction entirely and gets you a ground-truth answer instead of an inference.

## Fix status

I've written a corrected `orchestrator.py` addressing findings #1, #2, #4 (partially — picks the `output/`-prefixed convention), #5, #6 (documented, not fully solved — see note in file), #7, #8, #9 as actual working code, provided alongside this report. `app.py` I only have ~1000 of 1375 lines for, so rather than regenerate the whole file from partial knowledge (risking inventing the missing ~375 lines), I've provided exact, surgical patch instructions below for the two changes it needs.

### Patch 1 — Replace the duplicated "Run Audit" handler

Find this block in `app.py` (starts at `if st.button("▶ Run Audit", ...)`) and replace the entire body with a single call to the orchestrator's real pipeline:

```python
if st.button("▶ Run Audit", type="primary", use_container_width=False):
    orch = st.session_state.orchestrator
    statuses = {"finops": "running", "secops": "running", "remediation": "running"}
    _render_live_feed(statuses)

    result = orch.execute_audit()

    if not result.success:
        statuses = {"finops": "success", "secops": "success", "remediation": "failure"}
        _render_live_feed(statuses)
        st.session_state.agent_status = statuses
        st.session_state.audit_result = result
        st.error(result.hook_error or result.error or "Audit failed.")
        st.stop()

    statuses = {"finops": "success", "secops": "success", "remediation": "success"}
    _render_live_feed(statuses)
    st.session_state.agent_status = statuses
    st.session_state.audit_result = result
    st.success("Audit complete.")
    st.rerun()
```

This loses the three-stage live-status animation (finops → secops → remediation shown running in sequence), since `execute_audit()` runs atomically. If you want to keep the granular live feed, the right fix is to add optional status-callback hooks to `execute_audit()` itself rather than reimplementing its internals in the UI — happy to write that version instead if you'd rather keep the animation.

### Patch 2 — Guard the NL Audit button until the backing method exists

Find:
```python
if nl_submitted and nl_query and nl_query.strip():
    orch = st.session_state.orchestrator
    with st.spinner("Interpreting query and running audit..."):
        try:
            result = orch.execute_natural_language_audit(nl_query.strip())
```

Replace the guard condition:
```python
if nl_submitted and nl_query and nl_query.strip():
    orch = st.session_state.orchestrator
    if not hasattr(orch, "execute_natural_language_audit"):
        st.info("Natural-language audit is coming soon — not yet implemented.")
    else:
        with st.spinner("Interpreting query and running audit..."):
            try:
                result = orch.execute_natural_language_audit(nl_query.strip())
```
(and indent the rest of that block one level further in). This turns a permanently-broken button into an honest "not built yet" message instead of a generic failure toast.

### Patch 3 — Align `app.py`'s path constants if you keep `orchestrator.py`'s current (root-level) convention

Only needed if you decide to keep `orchestrator.py` on root-level paths instead of adopting `output/`-prefixed paths (the fixed `orchestrator.py` I've written goes the other way — it switches to `output/`-prefixed to match `app.py` and `.gitignore`). Pick one direction and apply it in exactly one place; don't patch both files toward each other.
