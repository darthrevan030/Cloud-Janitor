"""Property-based tests for the SPEC_COMPLIANCE.md generator.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs.
"""

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from scripts.generate_spec_compliance import parse_tasks, verify_artifact, generate_report, KEYWORD_MAPPING


# --- Strategies ---

# Checkbox markers and their expected status
CHECKBOX_MARKERS = [
    ("x", "done"),
    (" ", "pending"),
    ("-", "partial"),
]

# Extract all keywords from KEYWORD_MAPPING for use in strategies
ALL_KEYWORDS = []
for keywords, target in KEYWORD_MAPPING:
    ALL_KEYWORDS.extend(keywords)

# Strategy for generating a checkbox marker and its expected status
checkbox_marker_strategy = st.sampled_from(CHECKBOX_MARKERS)

# Strategy for generating a keyword from the mapping table
keyword_strategy = st.sampled_from(ALL_KEYWORDS)

# Strategy for random task description text (without keywords)
# Use safe characters that won't accidentally contain keywords
filler_text_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Zs"),
        blacklist_characters=("\x00", "\n", "\r", "|"),
    ),
    min_size=0,
    max_size=30,
)

# Strategy for optional leading whitespace (indented sub-tasks)
indent_strategy = st.sampled_from(["", "  ", "    "])


# --- Property 6: Compliance generator parsing and mapping ---
# Feature: savings-tracker-localstack, Property 6: Compliance generator parsing and mapping


@settings(max_examples=100, deadline=None)
@given(
    marker_status=checkbox_marker_strategy,
    keyword=keyword_strategy,
    prefix_text=filler_text_strategy,
    suffix_text=filler_text_strategy,
    indent=indent_strategy,
)
def test_parse_tasks_correctly_identifies_checkbox_state(
    marker_status, keyword, prefix_text, suffix_text, indent
):
    """
    Property 6 (Part A): Checkbox state parsing

    For any tasks.md file containing lines with `- [x]`, `- [ ]`, or `- [-]`
    checkbox markers and task descriptions containing any of the defined keywords,
    the compliance generator SHALL correctly identify the checkbox state
    (done/pending/partial).

    **Validates: Requirements 8.2, 8.3**
    """
    marker, expected_status = marker_status

    # Build a task line with the keyword embedded in the description
    task_description = f"{prefix_text} {keyword} {suffix_text}".strip()
    # Ensure task description is non-empty
    assume(len(task_description) > 0)

    # Construct the full checkbox line
    line = f"{indent}- [{marker}] {task_description}"
    content = line + "\n"

    # Parse the tasks
    tasks = parse_tasks(content)

    # Should find exactly one task
    assert len(tasks) == 1, (
        f"Expected 1 task, got {len(tasks)} from content: {repr(content)}"
    )

    # Verify the status is correctly identified
    assert tasks[0]["status"] == expected_status, (
        f"Marker '[{marker}]' should produce status '{expected_status}', "
        f"got '{tasks[0]['status']}'"
    )

    # Verify the text was captured (should contain the keyword)
    assert keyword in tasks[0]["text"], (
        f"Keyword '{keyword}' should be in parsed text '{tasks[0]['text']}'"
    )


def _find_first_matching_target(task_text: str) -> str | None:
    """Replicate verify_artifact's first-match logic to find the expected target.

    Because verify_artifact iterates KEYWORD_MAPPING in order and returns on
    the first keyword match found in task_text, we must do the same to predict
    the correct expected target. For example, 'pre-remediation' contains the
    substring 'remediation', and since ["Remediation", "remediation"] appears
    earlier in the mapping than ["pre-remediation"], the function will match
    'remediation' first.
    """
    for keywords, target in KEYWORD_MAPPING:
        for kw in keywords:
            if kw in task_text:
                return target
    return None


@settings(max_examples=100, deadline=None)
@given(
    keyword=keyword_strategy,
    prefix_text=filler_text_strategy,
    suffix_text=filler_text_strategy,
)
def test_verify_artifact_maps_keyword_to_correct_path(
    keyword, prefix_text, suffix_text
):
    """
    Property 6 (Part B): Keyword-to-artifact mapping

    For any task description containing a keyword from the KEYWORD_MAPPING table,
    verify_artifact() SHALL map the task to the correct artifact path according to
    the keyword-to-file mapping table (using first-match semantics — the first
    entry in KEYWORD_MAPPING whose keyword appears in the task text wins).

    **Validates: Requirements 8.2, 8.3**
    """
    # Build a task description containing the keyword
    task_text = f"{prefix_text} {keyword} {suffix_text}".strip()
    assume(len(task_text) > 0)
    assume(keyword in task_text)

    # Determine the ACTUAL first-match target for this task_text,
    # which may differ from the keyword's own entry if another keyword
    # is a substring that appears earlier in the mapping.
    expected_target = _find_first_matching_target(task_text)
    assert expected_target is not None, f"No mapping matched task_text '{task_text}'"

    # Create a temporary project root with the expected artifact
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)

        # Handle special check targets
        if expected_target == "__APPROVE_STRING_CHECK__":
            # Create orchestrator.py with "APPROVE" string
            orchestrator_file = project_root / "orchestrator.py"
            orchestrator_file.write_text('# APPROVE resource here', encoding="utf-8")
            result = verify_artifact(task_text, project_root)
            assert "APPROVE" in result and "found" in result, (
                f"Expected 'APPROVE found' result for keyword '{keyword}', got: {result}"
            )

        elif expected_target == "__AUDIT_LOG_CHECK__":
            # Create audit.log file
            audit_file = project_root / "audit.log"
            audit_file.write_text("audit entry", encoding="utf-8")
            result = verify_artifact(task_text, project_root)
            assert "found" in result, (
                f"Expected 'found' in result for keyword '{keyword}', got: {result}"
            )

        else:
            # Create the target file/directory
            artifact_path = project_root / expected_target
            if expected_target.endswith("/"):
                # It's a directory
                artifact_path.mkdir(parents=True, exist_ok=True)
            else:
                # It's a file
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_text("# placeholder", encoding="utf-8")

            result = verify_artifact(task_text, project_root)
            assert "exists" in result, (
                f"Expected 'exists' in result for keyword '{keyword}' "
                f"(target: {expected_target}), got: {result}"
            )


@settings(max_examples=100, deadline=None)
@given(
    markers_and_statuses=st.lists(
        st.tuples(checkbox_marker_strategy, keyword_strategy, filler_text_strategy),
        min_size=1,
        max_size=10,
    ),
)
def test_parse_tasks_handles_multiple_lines(markers_and_statuses):
    """
    Property 6 (Part C): Multi-line parsing correctness

    For any tasks.md content with multiple checkbox lines, each with different
    markers and keywords, parse_tasks() SHALL correctly identify ALL checkbox
    states and preserve ordering.

    **Validates: Requirements 8.2, 8.3**
    """
    lines = []
    expected_statuses = []

    for (marker, expected_status), keyword, filler in markers_and_statuses:
        task_description = f"{filler} {keyword}".strip()
        assume(len(task_description) > 0)
        line = f"- [{marker}] {task_description}"
        lines.append(line)
        expected_statuses.append(expected_status)

    content = "\n".join(lines) + "\n"
    tasks = parse_tasks(content)

    # Should find the same number of tasks as lines we generated
    assert len(tasks) == len(expected_statuses), (
        f"Expected {len(expected_statuses)} tasks, got {len(tasks)}"
    )

    # Each task should have the correct status in order
    for i, (task, expected_status) in enumerate(zip(tasks, expected_statuses)):
        assert task["status"] == expected_status, (
            f"Task {i}: expected status '{expected_status}', got '{task['status']}'"
        )


# --- Property 7: Compliance generator output format ---
# Feature: savings-tracker-localstack, Property 7: Compliance generator output format

# Strategy for generating a task status
task_status_strategy = st.sampled_from(["done", "pending", "partial"])

# Strategy for generating a task description (safe characters for table cells)
task_description_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Zs"),
        blacklist_characters=("\x00", "\n", "\r", "|"),
    ),
    min_size=1,
    max_size=50,
)

# Strategy for generating a single task dict
task_dict_strategy = st.builds(
    lambda status, text: {"text": text, "status": status},
    status=task_status_strategy,
    description=task_description_strategy,
)


@settings(max_examples=100, deadline=None)
@given(
    tasks=st.lists(
        st.fixed_dictionaries({
            "text": task_description_strategy,
            "status": task_status_strategy,
        }),
        min_size=1,
        max_size=15,
    ),
)
def test_generate_report_produces_valid_markdown_table(tasks):
    """
    Property 7: Compliance generator output format

    For any set of parsed tasks (with varying checkbox states and artifact
    existence results), the compliance generator output SHALL be a valid
    Markdown table containing columns for task number, task description,
    status indicator, and artifact verification result.

    Validates:
    - Output is a valid 4-column Markdown table
    - Headers are exactly: #, Task, Status, Artifact Verified
    - Each row has 4 columns
    - Task numbering is sequential (1, 2, 3, ...)
    - Status column shows correct indicators: ✅ Done, ❌ Pending, ⏳ Partial

    **Validates: Requirements 8.4**
    """
    # Use a temp directory as project_root so verify_artifact can check paths
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)

        # Generate the report — generate_report expects list of (spec_name, tasks) tuples
        report = generate_report([("test-spec", tasks)], project_root)

        # Split into lines
        lines = report.strip().split("\n")

        # --- Validate report structure ---
        # Must have at minimum: title, blank, generated date, blank, header, separator, 1+ data rows
        assert len(lines) >= 6 + len(tasks), (
            f"Report too short: expected at least {6 + len(tasks)} lines, got {len(lines)}"
        )

        # First line should be the title
        assert lines[0] == "# Spec Compliance Report"

        # Find the table header line
        header_line = None
        header_idx = None
        for i, line in enumerate(lines):
            if line.startswith("| # |"):
                header_line = line
                header_idx = i
                break

        assert header_line is not None, "Could not find table header '| # |' in report"

        # --- Validate table header ---
        header_cells = [cell.strip() for cell in header_line.split("|")]
        # Split by | gives empty strings at start and end: ['', '#', 'Task', 'Status', 'Artifact Verified', '']
        header_cells = [c for c in header_cells if c != ""]
        assert header_cells == ["#", "Task", "Status", "Artifact Verified"], (
            f"Expected headers ['#', 'Task', 'Status', 'Artifact Verified'], got {header_cells}"
        )

        # --- Validate separator line ---
        separator_line = lines[header_idx + 1]
        separator_cells = [cell.strip() for cell in separator_line.split("|")]
        separator_cells = [c for c in separator_cells if c != ""]
        assert len(separator_cells) == 4, (
            f"Separator line should have 4 columns, got {len(separator_cells)}"
        )
        # Each cell in separator should be dashes only
        for cell in separator_cells:
            assert all(ch == "-" for ch in cell), (
                f"Separator cell should be all dashes, got: '{cell}'"
            )

        # --- Validate data rows ---
        # Only count rows that are table rows (start with |) and belong to the task table
        # Stop at the first non-table line (section headers, blank lines, or different tables)
        data_rows = []
        for row in lines[header_idx + 2:]:
            stripped = row.strip()
            if not stripped or not stripped.startswith("|"):
                break
            # Verify it has 4 columns (task table format)
            cells = [c.strip() for c in stripped.split("|") if c.strip()]
            if len(cells) == 4:
                data_rows.append(row)
            else:
                break

        assert len(data_rows) == len(tasks), (
            f"Expected {len(tasks)} data rows, got {len(data_rows)}"
        )

        # Valid status indicators
        valid_statuses = {"✅ Done", "❌ Pending", "⏳ Partial"}
        status_for_state = {
            "done": "✅ Done",
            "pending": "❌ Pending",
            "partial": "⏳ Partial",
        }

        for i, row in enumerate(data_rows):
            # Each row should start and end with |
            assert row.startswith("|") and row.endswith("|"), (
                f"Row {i} does not start/end with '|': '{row}'"
            )

            # Split into cells
            cells = [cell.strip() for cell in row.split("|")]
            cells = [c for c in cells if c != ""]

            # Must have exactly 4 columns
            assert len(cells) == 4, (
                f"Row {i} should have 4 columns, got {len(cells)}: {cells}"
            )

            # Column 1: sequential task number
            expected_num = str(i + 1)
            assert cells[0] == expected_num, (
                f"Row {i}: expected task number '{expected_num}', got '{cells[0]}'"
            )

            # Column 3: valid status indicator matching the task's status
            assert cells[2] in valid_statuses, (
                f"Row {i}: status '{cells[2]}' not in valid set {valid_statuses}"
            )

            # Verify the status matches what we expect for this task
            expected_status_display = status_for_state[tasks[i]["status"]]
            assert cells[2] == expected_status_display, (
                f"Row {i}: task has status '{tasks[i]['status']}', "
                f"expected display '{expected_status_display}', got '{cells[2]}'"
            )

            # Column 4: artifact verification (should be a non-empty string)
            assert len(cells[3]) > 0, (
                f"Row {i}: artifact verification column should not be empty"
            )
