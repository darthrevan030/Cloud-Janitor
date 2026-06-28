# core/

Shared infrastructure modules used across all agents and the orchestrator.

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
