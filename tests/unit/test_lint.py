"""
Lint test — runs ruff against the active codebase.

Catches unused imports, undefined names, style violations, and common
bug patterns. Excludes _app_legacy/ and tests/legacy/.

Run standalone:
    pytest tests/unit/test_lint.py -v

Run only quick lint:
    python -m ruff check
"""

import subprocess
import sys


def test_ruff_lint():
    """Active codebase passes ruff lint rules defined in pyproject.toml."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--output-format", "concise"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Show the violations so the dev can see what to fix
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        count = len(lines)
        # Show first 30 violations + summary
        preview = "\n".join(lines[:30])
        if count > 30:
            preview += f"\n... and {count - 30} more"
        raise AssertionError(
            f"ruff found {count} lint violation(s):\n\n{preview}\n\n"
            f"Auto-fix most with: python -m ruff check --fix"
        )
