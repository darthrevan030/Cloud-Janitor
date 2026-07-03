"""Fix remaining logger name references in test files."""
from pathlib import Path

tests_dir = Path(__file__).parent.parent / "tests"
fixes = {
    'logging.getLogger("core.logging_config")': 'logging.getLogger("cloud_janitor.core.logging_config")',
    'logging.getLogger("core.llm_client")': 'logging.getLogger("cloud_janitor.core.llm_client")',
}

for py_file in sorted(tests_dir.glob("*.py")):
    content = py_file.read_text(encoding="utf-8")
    original = content
    for old, new in fixes.items():
        content = content.replace(old, new)
    if content != original:
        py_file.write_text(content, encoding="utf-8")
        print(f"  Fixed: {py_file.name}")

print("Done")
