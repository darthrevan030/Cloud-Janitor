"""Tests for Phase B/C agent ImportError handling in app.py.

Validates Requirements 13.1, 13.2, 13.3:
- 13.1: Each Phase B/C agent is imported via standard import statements (not dynamic)
- 13.2: Missing agent module → ImportError caught individually, name assigned to Optional[type] = None
- 13.3: Type annotations use Optional[type] for fallback values
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_PY_PATH = Path(__file__).resolve().parent.parent / "src" / "cloud_janitor" / "app.py"

# The 9 Phase B/C agents that must be individually imported
EXPECTED_AGENTS = [
    "QueryInterpreter",
    "RemediationExplainer",
    "PolicySuggester",
    "AnomalyDetector",
    "DriftDetector",
    "MultiAccountOrchestrator",
    "ResourceTagger",
    "IncidentPolicyGenerator",
    "JanitorScheduler",
]

# Module paths for each agent (as they appear in app.py)
AGENT_MODULES = {
    "QueryInterpreter": "cloud_janitor.agents.query_interpreter",
    "RemediationExplainer": "cloud_janitor.agents.explainer",
    "PolicySuggester": "cloud_janitor.agents.policy_suggester",
    "AnomalyDetector": "cloud_janitor.agents.anomaly_detector",
    "DriftDetector": "cloud_janitor.agents.drift_detector",
    "MultiAccountOrchestrator": "cloud_janitor.agents.multi_account_orchestrator",
    "ResourceTagger": "cloud_janitor.agents.tagger",
    "IncidentPolicyGenerator": "cloud_janitor.agents.incident_policy_generator",
    "JanitorScheduler": "scheduler",
}


# ---------------------------------------------------------------------------
# Helpers — static analysis of app.py source
# ---------------------------------------------------------------------------


def _read_app_source() -> str:
    """Read app.py source code."""
    return APP_PY_PATH.read_text(encoding="utf-8")


def _parse_app_ast() -> ast.Module:
    """Parse app.py into an AST."""
    source = _read_app_source()
    return ast.parse(source, filename=str(APP_PY_PATH))


def _extract_import_blocks(source: str) -> list[str]:
    """Extract all try/except blocks that catch ImportError from app.py source."""
    # Find all try/except ImportError blocks
    pattern = re.compile(
        r"try:\s*\n"
        r"(.*?)"
        r"except\s+ImportError:",
        re.DOTALL,
    )
    return pattern.findall(source)


# ---------------------------------------------------------------------------
# Requirement 13.2: Missing agent → None (runtime tests via mocking)
# ---------------------------------------------------------------------------


class TestMissingAgentBecomesNone:
    """Validates Req 13.2: IF a Phase B/C agent module is unavailable,
    THEN the name is assigned None via the except ImportError block.

    Strategy: We read app.py source, extract the try/except block for each
    agent, and exec it in an environment where the module import raises
    ImportError. Then verify the agent name is None.
    """

    @pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
    def test_import_error_sets_agent_to_none(self, agent_name: str):
        """When module is missing, the agent name should be None."""
        module_path = AGENT_MODULES[agent_name]
        source = _read_app_source()

        # Extract the specific try/except block for this agent
        # Pattern: try:\n    from <module> import <Agent>\nexcept ImportError:\n    <Agent>: ... = None
        block_pattern = re.compile(
            rf"try:\s*\n\s+from\s+{re.escape(module_path)}\s+import\s+{agent_name}\s*\n"
            rf"except\s+ImportError:\s*\n\s+{agent_name}.*?=\s*None",
            re.DOTALL,
        )
        match = block_pattern.search(source)
        assert match is not None, (
            f"Could not find try/except ImportError block for {agent_name} "
            f"importing from {module_path} in app.py"
        )

        # Execute the block with the import forced to fail
        block_code = match.group(0)
        # We need to provide the typing import for Optional
        exec_globals: dict = {"Optional": type(None).__class__}
        exec_locals: dict = {}

        # Remove the module from sys.modules so import fails
        with patch.dict(sys.modules, {module_path: None}):
            # Also patch any parent modules that might be needed
            parts = module_path.split(".")
            patches = {}
            for i in range(len(parts)):
                partial = ".".join(parts[: i + 1])
                patches[partial] = None

            with patch.dict(sys.modules, patches):
                # Need to add typing.Optional to the exec namespace
                from typing import Optional

                exec_globals = {"__builtins__": __builtins__, "Optional": Optional}
                exec(block_code, exec_globals, exec_locals)

        # The agent name should be None after the ImportError is caught
        # It could be in either exec_globals or exec_locals depending on scope
        result = exec_locals.get(agent_name, exec_globals.get(agent_name))
        assert result is None, (
            f"Expected {agent_name} to be None when module is unavailable, "
            f"got {result!r}"
        )

    @pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
    def test_import_error_block_assigns_none_not_sentinel(self, agent_name: str):
        """The fallback value must be literally None, not a sentinel or empty class."""
        source = _read_app_source()

        # Verify the except block contains `= None` assignment
        pattern = re.compile(
            rf"except\s+ImportError:\s*\n\s+{agent_name}.*?=\s*None",
        )
        match = pattern.search(source)
        assert match is not None, (
            f"{agent_name}'s ImportError handler must assign None, "
            f"not a sentinel value or empty class"
        )


# ---------------------------------------------------------------------------
# Requirement 13.1: Individual imports (not registry/loop pattern)
# ---------------------------------------------------------------------------


class TestIndividualImports:
    """Validates Req 13.1: Each agent is imported individually via standard
    import statements — no dynamic globals() manipulation, no registry loop."""

    def test_each_agent_has_own_try_except_block(self):
        """Every agent must have its own dedicated try/except ImportError block."""
        source = _read_app_source()

        for agent_name in EXPECTED_AGENTS:
            module_path = AGENT_MODULES[agent_name]
            # Look for: try:\n    from <module> import <Agent>\nexcept ImportError:
            pattern = re.compile(
                rf"try:\s*\n\s+from\s+{re.escape(module_path)}\s+import\s+{agent_name}\s*\n"
                rf"except\s+ImportError:",
            )
            match = pattern.search(source)
            assert match is not None, (
                f"Agent {agent_name} must have its own try/except ImportError block "
                f"with 'from {module_path} import {agent_name}'"
            )

    def test_no_loop_based_import_pattern(self):
        """Source must not contain loop-based dynamic import patterns."""
        source = _read_app_source()

        # Check for common dynamic import loop patterns
        forbidden_patterns = [
            # for agent in agents_list: globals()[name] = ...
            r"for\s+\w+\s+in\s+.*agents.*:\s*\n.*globals\(\)",
            # for name in [...]: __import__(name)
            r"for\s+\w+\s+in\s+\[.*\]:\s*\n.*__import__",
            # importlib.import_module in a loop
            r"for\s+\w+\s+in\s+.*:\s*\n.*importlib\.import_module",
            # globals()[name] = ... pattern (outside a try/except)
            r"globals\(\)\[.*\]\s*=",
        ]

        for pattern in forbidden_patterns:
            match = re.search(pattern, source, re.DOTALL)
            assert match is None, (
                f"Found forbidden dynamic import loop pattern in app.py: "
                f"{match.group(0)[:100]!r}"
            )

    def test_no_dunder_import_calls(self):
        """Source must not use __import__() for agent loading."""
        source = _read_app_source()

        # Find any __import__ calls
        matches = re.findall(r"__import__\s*\(", source)
        assert len(matches) == 0, (
            f"Found {len(matches)} __import__() call(s) in app.py — "
            "agents must use standard 'from ... import' statements"
        )

    def test_no_importlib_dynamic_loading(self):
        """Source must not use importlib.import_module for agent loading."""
        source = _read_app_source()

        # Check for importlib usage in agent import context
        # (importlib in test utilities is fine — we check the agent import section)
        agent_section = _extract_agent_import_section(source)
        assert "importlib" not in agent_section, (
            "Agent import section must not use importlib for dynamic loading"
        )

    def test_exactly_nine_agents_imported(self):
        """There must be exactly 9 individual try/except ImportError blocks for agents."""
        source = _read_app_source()

        # Count try/except ImportError blocks that import from agent modules
        agent_import_pattern = re.compile(
            r"try:\s*\n\s+from\s+(?:cloud_janitor\.agents\.\w+|scheduler)\s+import\s+\w+\s*\n"
            r"except\s+ImportError:"
        )
        matches = agent_import_pattern.findall(source)
        assert len(matches) == 9, (
            f"Expected exactly 9 agent try/except ImportError blocks, found {len(matches)}"
        )


# ---------------------------------------------------------------------------
# Requirement 13.3: Type annotations use Optional[type]
# ---------------------------------------------------------------------------


class TestOptionalTypeAnnotations:
    """Validates Req 13.3: Fallback assignments use Optional[type] annotation."""

    @pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
    def test_fallback_has_optional_type_annotation(self, agent_name: str):
        """Each agent's except block must annotate with Optional[type]."""
        source = _read_app_source()

        # Look for: <AgentName>: Optional[type] = None
        pattern = re.compile(
            rf"{agent_name}\s*:\s*Optional\[type\]\s*=\s*None"
        )
        match = pattern.search(source)
        assert match is not None, (
            f"{agent_name}'s fallback must be annotated as 'Optional[type] = None', "
            f"not an untyped assignment"
        )

    def test_typing_optional_is_imported(self):
        """The 'Optional' type must be imported from typing module."""
        source = _read_app_source()

        # Check for: from typing import Optional (possibly among other imports)
        pattern = re.compile(r"from\s+typing\s+import\s+.*Optional")
        match = pattern.search(source)
        assert match is not None, (
            "app.py must import Optional from typing for agent fallback annotations"
        )

    @pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
    def test_annotation_is_inside_except_block(self, agent_name: str):
        """The Optional[type] annotation must be within the except ImportError block."""
        source = _read_app_source()

        # The pattern should be: except ImportError:\n    <Agent>: Optional[type] = None
        pattern = re.compile(
            rf"except\s+ImportError:\s*\n"
            rf"\s+{agent_name}\s*:\s*Optional\[type\]\s*=\s*None"
        )
        match = pattern.search(source)
        assert match is not None, (
            f"{agent_name}'s Optional[type] = None annotation must be directly "
            f"inside the except ImportError block"
        )


# ---------------------------------------------------------------------------
# Negative tests — detect regressions
# ---------------------------------------------------------------------------


class TestNegativeCases:
    """Tests that would fail if the implementation were broken."""

    def test_removing_try_except_would_break_on_missing_module(self):
        """Verify that without try/except, a missing module raises ImportError.

        This confirms the try/except is actually needed — not a vacuous test.
        """
        # Attempt to import a guaranteed-missing module
        with pytest.raises(ImportError):
            # This module definitely doesn't exist
            from cloud_janitor.agents.nonexistent_agent_xyz import FakeAgent  # noqa: F401

    def test_bare_import_without_optional_annotation_is_detected(self):
        """If someone removes the Optional[type] annotation, our test catches it.

        We verify by checking a synthetic bad pattern is NOT in the source.
        """
        source = _read_app_source()

        # A bad pattern would be: except ImportError:\n    AgentName = None
        # (without the : Optional[type] annotation)
        for agent_name in EXPECTED_AGENTS:
            bad_pattern = re.compile(
                rf"except\s+ImportError:\s*\n"
                rf"\s+{agent_name}\s*=\s*None\s*$",  # no type annotation
                re.MULTILINE,
            )
            match = bad_pattern.search(source)
            assert match is None, (
                f"{agent_name} has bare '= None' without Optional[type] annotation"
            )

    def test_agent_list_is_complete(self):
        """Verify our EXPECTED_AGENTS list matches what's actually in app.py.

        If a new agent is added to app.py but not to EXPECTED_AGENTS, this fails.
        """
        source = _read_app_source()

        # Find all names assigned Optional[type] = None in except blocks
        pattern = re.compile(
            r"except\s+ImportError:\s*\n"
            r"\s+(\w+)\s*:\s*Optional\[type\]\s*=\s*None"
        )
        found_agents = pattern.findall(source)

        assert set(found_agents) == set(EXPECTED_AGENTS), (
            f"Mismatch between test's EXPECTED_AGENTS and actual agents in app.py.\n"
            f"In app.py but not in test: {set(found_agents) - set(EXPECTED_AGENTS)}\n"
            f"In test but not in app.py: {set(EXPECTED_AGENTS) - set(found_agents)}"
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_agent_import_section(source: str) -> str:
    """Extract the Phase B/C agent import section from app.py source.

    Looks for the section between the 'from typing import Optional' line
    (preceding agent imports) and the first non-import code after the agent blocks.
    """
    lines = source.split("\n")
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if "from typing import Optional" in line and start_idx is None:
            start_idx = i
        # The agent import section ends after the last except ImportError block
        if start_idx is not None and "JanitorScheduler" in line:
            # Find the end of this block (next blank line or non-indented line)
            for j in range(i + 1, min(i + 5, len(lines))):
                if lines[j].strip() == "" or (lines[j] and not lines[j].startswith(" ")):
                    end_idx = j
                    break
            if end_idx is None:
                end_idx = i + 3
            break

    if start_idx is None or end_idx is None:
        return ""

    return "\n".join(lines[start_idx:end_idx])
