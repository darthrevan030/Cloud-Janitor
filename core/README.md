# core/

Shared infrastructure modules used across all agents and the orchestrator.

## `paths.py`

Centralised path configuration — every module that reads or writes runtime artifacts imports paths from here.

### Exports

| Symbol | Type | Description |
|--------|------|-------------|
| `PROJECT_ROOT` | `Path` | Resolved project root directory |
| `OUTPUT_DIR` | `Path` | Base output directory (`output/`) |
| `ROLLBACKS_DIR` | `Path` | Rollback files (`output/rollbacks/`) |
| `LOGS_DIR` | `Path` | Log files (`output/logs/`) |
| `POLICIES_DIR` | `Path` | Policy artifacts (`output/policies/`) |
| `FINDINGS_STORE_PATH` | `Path` | Findings JSON (`output/findings_store.json`) |
| `AUDIT_LOG_PATH` | `Path` | Audit log (`output/logs/audit.log`) |
| `REASONING_LOG_PATH` | `Path` | Reasoning log (`output/logs/agent_reasoning.log`) |
| `APPROVAL_GATES_PATH` | `Path` | Gate store (`output/approval_gates.json`) |
| `SAVINGS_LEDGER_PATH` | `Path` | Savings ledger (`output/savings_ledger.json`) |
| `HOOKS_DIR` | `Path` | Hooks directory (`hooks/`) |
| `REQUIRED_DIRS` | `list[Path]` | Directories created at startup |
| `ensure_output_dirs()` | `function` | Creates all `REQUIRED_DIRS`, raises `RuntimeError` on failure |

## `error_telemetry.py`

Structured error telemetry — captures agent exceptions as JSONL records for operational diagnosis.

### Exports

| Symbol | Type | Description |
|--------|------|-------------|
| `ERROR_CATEGORIES` | `set[str]` | Valid error categories: `agent_failure`, `terraform_failure`, `validation_failure`, `io_failure` |
| `build_error_record(exc, agent_name, error_category)` | `function` | Builds a structured error dict from an exception |
| `write_error_record(record, log_path)` | `function` | Appends one JSONL line to the target log path |

### Usage

```python
from core.error_telemetry import build_error_record, write_error_record
from core.paths import AUDIT_LOG_PATH

try:
    agent.run()
except Exception as exc:
    record = build_error_record(exc, "finops_auditor", "agent_failure")
    write_error_record(record, AUDIT_LOG_PATH)
```

## `logging_config.py`

Centralized logging configuration — call `configure_logging()` once at application startup to set up structured logging across all modules.

### Exports

| Symbol | Type | Description |
|--------|------|-------------|
| `configure_logging(level)` | `function` | Configures the root logger with timestamped format to stderr |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JANITOR_LOG_LEVEL` | No | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |

### Usage

```python
from core.logging_config import configure_logging

configure_logging()  # Call once at startup
```

## `llm_client.py`

Centralised LLM client — every AI agent imports from here instead of using the OpenAI SDK directly.

### Exports

| Symbol | Type | Description |
|--------|------|-------------|
| `get_client()` | `openai.OpenAI` | Returns an OpenAI client configured for OpenRouter |
| `DEFAULT_MODEL` | `str` | Model string from `JANITOR_LLM_MODEL` env var (default: `anthropic/claude-haiku-4-5`) |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | — | API key for OpenRouter. Raises `EnvironmentError` if missing. |
| `JANITOR_LLM_MODEL` | No | `anthropic/claude-haiku-4-5` | Which model to use for all LLM calls |

### Usage

```python
from core.llm_client import get_client, DEFAULT_MODEL

client = get_client()
response = client.chat.completions.create(
    model=DEFAULT_MODEL,
    messages=[{"role": "user", "content": "..."}],
)
```

### Design Decisions

- **Single import point** — swapping LLM providers means changing one file, not six agents.
- **No anthropic SDK** — uses the OpenAI-compatible endpoint from OpenRouter so only one SDK is needed.
- **Fails loud** — raises `EnvironmentError` immediately if the API key is missing, rather than failing silently mid-pipeline.
