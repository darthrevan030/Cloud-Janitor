"""Bundled pipeline hook scripts (pre/post-remediation).

These shell scripts are shipped as package data so they are available
regardless of how cloud-janitor is installed (pip, uv, wheel, etc.).

Resolve the hooks directory at runtime::

    from cloud_janitor.hooks import HOOKS_DIR
    hook_path = HOOKS_DIR / "pre-remediation.sh"
"""

from pathlib import Path

HOOKS_DIR = Path(__file__).parent
