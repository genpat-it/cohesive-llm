from core.utils.logger import logger

"""
Tool Registry — Discovers and merges core + plugin tools for the LangGraph agents.

Core tools live in core/services/consultant_tools.py and architect_tools.py.
Plugin tools are discovered from plugins/<name>/tools.py (if it exists).

Usage:
    from core.tool_registry import get_consultant_tools, get_architect_tools
    tools = get_consultant_tools()   # Returns merged list of @tool functions
"""

import importlib.util
import sys
from collections.abc import Callable


def _load_plugin_tools(module_attr: str) -> list[Callable]:
    """Try to load tools from the active plugin's tools.py module.

    Args:
        module_attr: Name of the list attribute in the plugin tools module
                     (e.g., 'CONSULTANT_TOOLS' or 'ARCHITECT_TOOLS')

    Returns:
        List of @tool functions from the plugin, or empty list if none found.
    """
    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        tools_file = plugin.plugin_dir / "tools.py"

        if not tools_file.exists():
            return []

        # Dynamically import the plugin's tools module
        module_name = f"plugins.{plugin.plugin_dir.name}.tools"

        if module_name in sys.modules:
            module = sys.modules[module_name]
        else:
            spec = importlib.util.spec_from_file_location(module_name, tools_file)
            if spec is None or spec.loader is None:
                return []

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

        plugin_tools = getattr(module, module_attr, [])
        if plugin_tools:
            logger.info(f"--- [TOOL REGISTRY] Loaded {len(plugin_tools)} plugin {module_attr}")
        return plugin_tools

    except Exception as e:
        logger.warning(f"--- [TOOL REGISTRY] Warning: Could not load plugin tools ({e})")
        return []


def get_consultant_tools() -> list:
    """Get the merged list of consultant tools (core + plugin).

    Plugin tools are appended after core tools. If a plugin tool has the same
    name as a core tool, the plugin version overrides the core version.
    """
    from core.services.consultant_tools import CONSULTANT_TOOLS as core_tools  # noqa: N811
    plugin_tools = _load_plugin_tools("CONSULTANT_TOOLS")

    if not plugin_tools:
        return list(core_tools)

    # Plugin tools override core tools with the same name
    core_by_name = {t.name: t for t in core_tools}
    plugin_by_name = {t.name: t for t in plugin_tools}

    merged_names = {}
    # Core tools first
    for name, t in core_by_name.items():
        merged_names[name] = t
    # Plugin overrides / additions
    for name, t in plugin_by_name.items():
        if name in merged_names:
            logger.info(f"--- [TOOL REGISTRY] Plugin overrides core tool: {name}")
        merged_names[name] = t

    return list(merged_names.values())


def get_architect_tools() -> list:
    """Get the merged list of architect tools (core + plugin)."""
    from core.services.architect_tools import ARCHITECT_TOOLS as core_tools  # noqa: N811
    plugin_tools = _load_plugin_tools("ARCHITECT_TOOLS")

    if not plugin_tools:
        return list(core_tools)

    core_by_name = {t.name: t for t in core_tools}
    plugin_by_name = {t.name: t for t in plugin_tools}

    merged_names = {}
    for name, t in core_by_name.items():
        merged_names[name] = t
    for name, t in plugin_by_name.items():
        if name in merged_names:
            logger.info(f"--- [TOOL REGISTRY] Plugin overrides core tool: {name}")
        merged_names[name] = t

    return list(merged_names.values())
