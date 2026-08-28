from core.utils.logger import logger

"""
Prompt Loader - Plugin-aware prompt assembly system.

Loads base prompts from core/prompts/ and merges domain-specific overlays
from the active plugin's prompts/ directory.

Usage:
    from core.services.prompt_loader import load_consultant_prompt, load_architect_prompt
    prompt = load_consultant_prompt()  # Returns assembled prompt string
"""

import json
from functools import lru_cache
from pathlib import Path

# Base directories
PROJECT_ROOT = Path(__file__).parent.parent.parent
CORE_PROMPTS_DIR = PROJECT_ROOT / "core" / "prompts"


def _get_plugin_prompts_dir() -> Path:
    """Get the active plugin's prompts directory."""
    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        prompts_dir = plugin.plugin_dir / "prompts"
        if prompts_dir.exists():
            return prompts_dir
    except Exception:
        pass
    # Fallback: check if core/prompts has the files (backward compat)
    return CORE_PROMPTS_DIR


def _get_plugin_catalog_dir() -> Path:
    """Get the active plugin's catalog directory."""
    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        catalog_dir = plugin.plugin_dir / "catalog"
        if catalog_dir.exists():
            return catalog_dir
    except Exception:
        pass
    return None


def _escape_braces(text: str) -> str:
    """Escape curly braces for ChatPromptTemplate compatibility."""
    return text.replace("{", "{{").replace("}", "}}")


def _load_file(path: Path, escape: bool = True) -> str:
    """Load a file and optionally escape braces."""
    if not path or not path.exists():
        return ""
    content = path.read_text()
    return _escape_braces(content) if escape else content


@lru_cache(maxsize=1)
def load_consultant_prompt() -> str:
    """
    Assemble the complete consultant prompt from:
    1. Base prompt (core/prompts/consultant_base.md)
    2. Plugin domain context (plugins/<name>/prompts/domain_context.md)
    3. Plugin rejection rules (plugins/<name>/prompts/rejection_rules.md)

    Placeholder injection happens BEFORE brace escaping to avoid conflicts.
    Placeholders use %%name%% syntax (not {{}} which conflicts with escaping).
    """
    plugin_prompts = _get_plugin_prompts_dir()

    # Load raw files WITHOUT escaping first (escaping happens at the end)
    base = _load_file(CORE_PROMPTS_DIR / "consultant_base.md", escape=False)
    domain_context = _load_file(plugin_prompts / "domain_context.md", escape=False)
    rejection_rules = _load_file(plugin_prompts / "rejection_rules.md", escape=False)

    # Merge via placeholders if present, otherwise append
    if "%%domain_context%%" in base:
        base = base.replace("%%domain_context%%", domain_context)
    elif domain_context:
        base = base + "\n\n" + domain_context

    if "%%rejection_rules%%" in base:
        base = base.replace("%%rejection_rules%%", rejection_rules)
    elif rejection_rules:
        base = base + "\n\n" + rejection_rules

    # Escape braces AFTER all placeholder injection is done
    return _escape_braces(base)


def _generate_tool_tables() -> dict[str, str]:
    """Generate VOID Tools list and Emitting Tools table from catalog."""
    catalog_dir = _get_plugin_catalog_dir()

    if not catalog_dir:
        return {"void_tools": "", "emitting_tools_table": ""}

    catalog_path = catalog_dir / "components.json"
    if not catalog_path.exists():
        return {"void_tools": "", "emitting_tools_table": ""}

    with open(catalog_path) as f:
        catalog = json.load(f)

    void_tools = []
    emitting_rows = []

    for comp in catalog.get("components", []):
        cid = comp["id"]
        outputs = comp.get("output_channels", [])

        if not outputs:
            void_tools.append(f"`{cid}`")
        else:
            formatted = []
            for out in outputs:
                if "." in out:
                    formatted.append("direct (unnamed)")
                else:
                    formatted.append(f".{out}")

            # Deduplicate and join (e.g. if multiple tool_x.out.* entries exist)
            out_str = ", ".join(sorted(set(formatted)))
            emitting_rows.append(f"| `{cid}` | {out_str} |")

    return {
        "void_tools": ", ".join(void_tools),
        "emitting_tools_table": "\n".join(emitting_rows)
    }


@lru_cache(maxsize=1)
def load_architect_prompt() -> str:
    """Load the architect prompt from file, inject dynamic tables and plugin idioms."""
    plugin_prompts = _get_plugin_prompts_dir()

    content = _load_file(CORE_PROMPTS_DIR / "architect.md", escape=False)
    tables = _generate_tool_tables()

    # Inject tool tables (using %% delimiters)
    content = content.replace("%%void_tools%%", tables["void_tools"])
    content = content.replace("%%emitting_tools_table%%", tables["emitting_tools_table"])

    return _escape_braces(content)


@lru_cache(maxsize=1)
def load_diagram_prompt() -> str:
    """Load the diagram prompt from file."""
    return _load_file(CORE_PROMPTS_DIR / "diagram.md")


@lru_cache(maxsize=1)
def load_extractor_prompt() -> str:
    """Load the consultant extraction prompt from file."""
    return _load_file(CORE_PROMPTS_DIR / "extractor.md")


def reload_prompts() -> None:
    """Clear the cache to force reload of all prompts."""
    load_consultant_prompt.cache_clear()
    load_architect_prompt.cache_clear()
    load_diagram_prompt.cache_clear()
    load_extractor_prompt.cache_clear()


# For debugging
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("CONSULTANT PROMPT")
    logger.info("=" * 60)
    prompt = load_consultant_prompt()
    logger.info(f"Length: {len(prompt)} chars")
    logger.info(prompt[:500] + "...")
