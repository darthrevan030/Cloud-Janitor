"""Cloud Janitor CLI — Click-based command-line interface.

Entry point for the cloud-janitor console script. All subcommands delegate
to the Orchestrator class for pipeline execution.
"""

import logging
import sys
from importlib.metadata import PackageNotFoundError, version

import click

from core.logging_config import configure_logging

try:
    _version = version("cloud-janitor")
except PackageNotFoundError:
    _version = "0.0.0-dev"

logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=_version, prog_name="cloud-janitor")
def main() -> None:
    """Cloud Janitor — AI-native infrastructure remediation tool."""
    configure_logging()


@main.command()
@click.option("--finops", is_flag=True, help="Run only FinOps auditor")
@click.option("--secops", is_flag=True, help="Run only SecOps guard")
def scan(finops: bool, secops: bool) -> None:
    """Execute the audit pipeline and print a findings summary."""
    from orchestrator import Orchestrator

    orch = Orchestrator()

    try:
        if finops:
            findings = orch._finops.scan()
            click.echo(f"Scan complete: {len(findings)} finding(s) produced.")
            return
        elif secops:
            findings = orch._secops.scan()
            click.echo(f"Scan complete: {len(findings)} finding(s) produced.")
            return
        else:
            result = orch.execute_audit()
    except Exception as exc:
        logger.error("Agent failed: %s", exc)
        click.echo(f"Error: Agent failed — {exc}", err=True)
        sys.exit(1)

    if not result.success:
        click.echo(f"Error: {result.error}", err=True)
        sys.exit(1)

    click.echo(f"Scan complete: {len(result.findings)} finding(s) produced.")


@main.command()
@click.argument("resource_id")
def approve(resource_id: str) -> None:
    """Approve a remediation plan for a specific resource."""
    from orchestrator import Orchestrator

    orch = Orchestrator()

    try:
        result = orch.approve(command=f"APPROVE {resource_id}")
    except Exception as exc:
        logger.error("Approve failed: %s", exc)
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if result.success:
        click.echo(f"Approved: {resource_id}")
    else:
        click.echo(f"Error: {result.error}", err=True)
        sys.exit(1)


@main.command()
@click.argument("resource_id")
def rollback(resource_id: str) -> None:
    """Rollback a previously applied remediation."""
    from orchestrator import Orchestrator

    orch = Orchestrator()

    try:
        result = orch.rollback(command=f"ROLLBACK {resource_id}")
    except Exception as exc:
        logger.error("Rollback failed: %s", exc)
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if result.success:
        click.echo(f"Rollback complete: {resource_id}")
    elif result.needs_confirmation:
        click.echo(f"Rollback pending confirmation: {resource_id}")
    else:
        click.echo(f"Error: {result.error}", err=True)
        sys.exit(1)


@main.command()
def dashboard() -> None:
    """Launch the Streamlit dashboard."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        click.echo(
            "Error: Dashboard requires the [dashboard] extra.\n"
            "Install with: pip install cloud-janitor[dashboard]",
            err=True,
        )
        sys.exit(1)

    import subprocess
    from pathlib import Path

    app_path = str(Path(__file__).parent / "app.py")
    proc = subprocess.Popen(
        ["streamlit", "run", app_path, "--server.headless", "true"],
    )
    click.echo("Dashboard running at http://localhost:8501")
    proc.wait()


@main.command()
def mcp() -> None:
    """Start the MCP server on stdio transport."""
    from mcp_server.aws_janitor_mcp import mcp as mcp_server

    mcp_server.run(transport="stdio")
