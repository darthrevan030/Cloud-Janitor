# Testing Rules

- NEVER run tests via `python -c "..."` inline commands
- ALWAYS write test logic to a `.py` file first (e.g. `tests/test_<feature>.py`)
- ALWAYS invoke tests using the venv interpreter: `".venv/Scripts/python.exe" -m pytest tests/ -n auto`
- NEVER use bare `python` — always use `".venv/Scripts/python.exe"`
- Run tests with: `".venv/Scripts/python.exe" -m pytest -n auto`
- Prefer `pytest` over `unittest` runner