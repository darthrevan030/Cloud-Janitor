"""Script to update all test imports from flat-layout to cloud_janitor.* paths."""

import re
from pathlib import Path

TESTS_DIR = Path(__file__).parent.parent / "tests"


def update_file(filepath: Path) -> bool:
    """Update imports in a single test file. Returns True if changes were made."""
    content = filepath.read_text(encoding="utf-8")
    original = content

    # --- Special case: remove sys.path manipulation ---
    # test_tagger_validate.py: sys.path.insert(0, '.')
    content = re.sub(r"import sys\nsys\.path\.insert\(0, '\.'?\)\n", "", content)
    # test_secops_guard.py: sys.path.insert(0, str(Path(__file__).parent.parent))
    content = re.sub(
        r"import sys\nsys\.path\.insert\(0, str\(Path\(__file__\)\.parent\.parent\)\)\n",
        "",
        content,
    )
    # Also handle standalone sys.path.insert lines (without the import sys before it)
    content = re.sub(r"sys\.path\.insert\(0, '\.'?\)\n", "", content)
    content = re.sub(
        r"sys\.path\.insert\(0, str\(Path\(__file__\)\.parent\.parent\)\)\n",
        "",
        content,
    )

    # --- Module-level imports ---

    # from cli import ... -> from cloud_janitor.cli import ...
    content = re.sub(
        r"^from cli import ",
        "from cloud_janitor.cli import ",
        content,
        flags=re.MULTILINE,
    )

    # from logging_config import ... -> from cloud_janitor.logging_config import ...
    content = re.sub(
        r"^from logging_config import ",
        "from cloud_janitor.logging_config import ",
        content,
        flags=re.MULTILINE,
    )

    # import core.llm_client as llm_client -> import cloud_janitor.core.llm_client as llm_client
    content = re.sub(
        r"^import core\.llm_client as llm_client",
        "import cloud_janitor.core.llm_client as llm_client",
        content,
        flags=re.MULTILINE,
    )

    # import agents.tagger -> import cloud_janitor.agents.tagger
    content = re.sub(
        r"^import agents\.tagger",
        "import cloud_janitor.agents.tagger",
        content,
        flags=re.MULTILINE,
    )

    # from core.X import Y -> from cloud_janitor.core.X import Y
    content = re.sub(
        r"^from core\.",
        "from cloud_janitor.core.",
        content,
        flags=re.MULTILINE,
    )

    # from agents.X import Y -> from cloud_janitor.agents.X import Y
    content = re.sub(
        r"^from agents\.",
        "from cloud_janitor.agents.",
        content,
        flags=re.MULTILINE,
    )

    # from mcp_server.X import Y -> from cloud_janitor.mcp_server.X import Y
    content = re.sub(
        r"^from mcp_server\.",
        "from cloud_janitor.mcp_server.",
        content,
        flags=re.MULTILINE,
    )

    # from orchestrator import <public symbols that ARE re-exported>
    # These are: AuditEntry, AuditResult, ApprovalResult, Orchestrator, RollbackResult
    # Private symbols must come from cloud_janitor.orchestrator.orchestrator
    PUBLIC_SYMBOLS = {"AuditEntry", "AuditResult", "ApprovalResult", "Orchestrator", "RollbackResult"}
    PRIVATE_SYMBOLS = {"_validate_tf_cmd", "TF_CMD_ALLOWLIST", "SCHEMA_VERSION", "_RESOURCE_ID_PATTERN", "FINDINGS_STORE_SCHEMA_VERSION"}

    def replace_orchestrator_import(match):
        """Handle from orchestrator import ... lines."""
        imported_text = match.group(1)
        # Parse the imported names (handle multi-line imports with parens)
        names = [n.strip().rstrip(",") for n in re.split(r"[,\n]", imported_text) if n.strip().rstrip(",")]

        public_names = [n for n in names if n in PUBLIC_SYMBOLS]
        private_names = [n for n in names if n in PRIVATE_SYMBOLS]
        # Any other names - treat as public (from orchestrator.__init__)
        other_names = [n for n in names if n not in PUBLIC_SYMBOLS and n not in PRIVATE_SYMBOLS]

        lines = []
        if public_names or other_names:
            all_public = public_names + other_names
            lines.append(f"from cloud_janitor.orchestrator import {', '.join(all_public)}")
        if private_names:
            lines.append(f"from cloud_janitor.orchestrator.orchestrator import {', '.join(private_names)}")

        return "\n".join(lines)

    # Handle single-line: from orchestrator import X, Y, Z
    content = re.sub(
        r"^from orchestrator import (.+)$",
        replace_orchestrator_import,
        content,
        flags=re.MULTILINE,
    )

    # Handle multi-line: from orchestrator import (\n    X,\n    Y,\n)
    def replace_orchestrator_import_multiline(match):
        """Handle from orchestrator import (...) multi-line."""
        imported_text = match.group(1)
        names = [n.strip().rstrip(",") for n in re.split(r"[,\n]", imported_text) if n.strip().rstrip(",")]

        public_names = [n for n in names if n in PUBLIC_SYMBOLS]
        private_names = [n for n in names if n in PRIVATE_SYMBOLS]
        other_names = [n for n in names if n not in PUBLIC_SYMBOLS and n not in PRIVATE_SYMBOLS]

        lines = []
        if public_names or other_names:
            all_public = public_names + other_names
            if len(all_public) > 3:
                inner = ",\n    ".join(all_public)
                lines.append(f"from cloud_janitor.orchestrator import (\n    {inner},\n)")
            else:
                lines.append(f"from cloud_janitor.orchestrator import {', '.join(all_public)}")
        if private_names:
            lines.append(f"from cloud_janitor.orchestrator.orchestrator import {', '.join(private_names)}")

        return "\n".join(lines)

    content = re.sub(
        r"^from orchestrator import \(\n(.*?)\)",
        replace_orchestrator_import_multiline,
        content,
        flags=re.MULTILINE | re.DOTALL,
    )

    # --- Inline imports inside functions/methods ---
    # Match indented: from orchestrator import X
    content = re.sub(
        r"(\s+)from orchestrator import (Orchestrator|AuditResult|ApprovalResult|AuditEntry|RollbackResult)",
        r"\1from cloud_janitor.orchestrator import \2",
        content,
    )
    # Match indented: from agents.X import Y
    content = re.sub(
        r"(\s+)from agents\.",
        r"\1from cloud_janitor.agents.",
        content,
    )
    # Match indented: from mcp_server.X import Y
    content = re.sub(
        r"(\s+)from mcp_server\.",
        r"\1from cloud_janitor.mcp_server.",
        content,
    )
    # Match indented: from core.X import Y
    content = re.sub(
        r"(\s+)from core\.",
        r"\1from cloud_janitor.core.",
        content,
    )

    # --- patch() targets ---
    # patch("orchestrator. -> patch("cloud_janitor.orchestrator.orchestrator.
    # But be careful: patch("orchestrator.Orchestrator") should go to cloud_janitor.orchestrator.orchestrator
    # since that's where the class lives, and patch needs the actual module path

    # patch("orchestrator._validate_tf_cmd" -> patch("cloud_janitor.orchestrator.orchestrator._validate_tf_cmd"
    # patch("orchestrator.subprocess -> patch("cloud_janitor.orchestrator.orchestrator.subprocess
    # patch("orchestrator.get_cost_data -> patch("cloud_janitor.orchestrator.orchestrator.get_cost_data
    # patch("orchestrator.get_security_data -> patch("cloud_janitor.orchestrator.orchestrator.get_security_data
    # patch("orchestrator.logging -> patch("cloud_janitor.orchestrator.orchestrator.logging
    # patch("orchestrator.Orchestrator._run_pre_remediation_hook -> patch("cloud_janitor.orchestrator.orchestrator.Orchestrator._run_pre_remediation_hook
    content = re.sub(
        r'patch\("orchestrator\.',
        'patch("cloud_janitor.orchestrator.orchestrator.',
        content,
    )

    # patch("agents.X -> patch("cloud_janitor.agents.X
    content = re.sub(
        r'patch\("agents\.',
        'patch("cloud_janitor.agents.',
        content,
    )

    # patch("core.X -> patch("cloud_janitor.core.X
    content = re.sub(
        r'patch\("core\.',
        'patch("cloud_janitor.core.',
        content,
    )

    # patch("mcp_server.X -> patch("cloud_janitor.mcp_server.X
    content = re.sub(
        r'patch\("mcp_server\.',
        'patch("cloud_janitor.mcp_server.',
        content,
    )

    # --- Logger name references ---
    # logging.getLogger("core.llm_client") -> logging.getLogger("cloud_janitor.core.llm_client")
    content = content.replace(
        'logger="core.llm_client"',
        'logger="cloud_janitor.core.llm_client"',
    )

    # --- inspect.getsource references ---
    # inspect.getsource(agents.tagger) stays (the variable name doesn't change)
    # but the import was already changed above

    if content != original:
        filepath.write_text(content, encoding="utf-8")
        return True
    return False


def main():
    """Process all test files."""
    changed = []
    for py_file in sorted(TESTS_DIR.glob("*.py")):
        if update_file(py_file):
            changed.append(py_file.name)
            print(f"  Updated: {py_file.name}")

    print(f"\n{len(changed)} files updated.")


if __name__ == "__main__":
    main()
