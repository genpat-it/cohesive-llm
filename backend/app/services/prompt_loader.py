"""
Prompt Loader - Clean way to manage and assemble prompts from files.

Usage:
    from app.services.prompt_loader import load_consultant_prompt, load_architect_prompt

    prompt = load_consultant_prompt()  # Returns assembled prompt string
"""

from pathlib import Path
from functools import lru_cache

# Base directories
PROJECT_ROOT = Path(__file__).parent.parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
CATALOG_DIR = PROJECT_ROOT / "data" / "catalog"


def _escape_braces(text: str) -> str:
    """Escape curly braces for ChatPromptTemplate compatibility."""
    return text.replace("{", "{{").replace("}", "}}")


def _load_file(path: Path, escape: bool = True) -> str:
    """Load a file and optionally escape braces."""
    if not path.exists():
        return ""
    content = path.read_text()
    return _escape_braces(content) if escape else content


@lru_cache(maxsize=1)
def load_tool_whitelist() -> str:
    """Load tool whitelist JSON and format as text for the LLM prompt."""
    import json
    wl_path = CATALOG_DIR / "tool_whitelist.json"
    if not wl_path.exists():
        return ""

    with open(wl_path) as f:
        wl = json.load(f)

    lines = [
        "# COMPLETE TOOL WHITELIST",
        "",
        "This is the EXHAUSTIVE list of available tools.",
        "If a tool is NOT in this list, it DOES NOT EXIST.",
        "",
        "## STEPS (Individual Tools)",
        "",
    ]

    # Group by domain
    by_domain = {}
    for s in wl.get("steps", []):
        domain = s.get("domain", "Other")
        by_domain.setdefault(domain, []).append(s)

    for domain, steps in sorted(by_domain.items()):
        lines.append(f"### {domain}")
        lines.append("")
        for s in steps:
            inputs = s.get("inputs", [])
            outputs = s.get("outputs", [])
            out_str = f" → emits: {', '.join(outputs)}" if outputs else " → VOID (no emit)"
            in_str = f" | takes: {', '.join(inputs)}" if len(inputs) > 1 else ""
            lines.append(f"- `{s['id']}` ({s.get('tool', '')}){in_str}{out_str}")
        lines.append("")

    lines.append("## MODULES (Pipeline Templates)")
    lines.append("")
    for m in wl.get("modules", []):
        lines.append(f"- `{m['id']}`")
        if m.get("steps_used"):
            lines.append(f"  - Uses: {', '.join(m['steps_used'])}")
    lines.append("")

    lines.append("## WHAT DOES NOT EXIST")
    lines.append("")
    for na in wl.get("not_available", []):
        alt = f" (use {na['alternative']} instead)" if na.get("alternative") else " (not available)"
        lines.append(f"- {na['tool']}{alt}")

    text = "\n".join(lines)
    return _escape_braces(text)


@lru_cache(maxsize=1)
def load_consultant_prompt() -> str:
    """
    Assemble the complete consultant prompt from:
    1. Base prompt (consultant_base.md)
    2. Tool whitelist (auto-generated from catalog)
    3. Rejection rules (rejection_rules.md)
    """
    parts = [
        _load_file(PROMPTS_DIR / "consultant_base.md"),
        "\n\n# 6. FRAMEWORK TOOL CONSTRAINTS (CRITICAL - READ THIS ENTIRE SECTION)\n\n",
        load_tool_whitelist(),
        "\n\n",
        _load_file(PROMPTS_DIR / "rejection_rules.md"),
    ]
    return "".join(parts)


@lru_cache(maxsize=1)
def load_architect_prompt() -> str:
    """Load the architect prompt from file."""
    return _load_file(PROMPTS_DIR / "architect.md")


@lru_cache(maxsize=1)
def load_diagram_prompt() -> str:
    """Load the diagram prompt from file."""
    return _load_file(PROMPTS_DIR / "diagram.md")


def reload_prompts():
    """Clear the cache to force reload of all prompts."""
    load_tool_whitelist.cache_clear()
    load_consultant_prompt.cache_clear()
    load_architect_prompt.cache_clear()
    load_diagram_prompt.cache_clear()


# For debugging
if __name__ == "__main__":
    print("=" * 60)
    print("CONSULTANT PROMPT")
    print("=" * 60)
    prompt = load_consultant_prompt()
    print(f"Length: {len(prompt)} chars")
    print(prompt[:500] + "...")
