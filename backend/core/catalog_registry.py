from typing import Any

from core.utils.logger import logger

"""
Catalog Registry - Central registry for component metadata used by AST validators.

Replaces hardcoded VOID_TOOL_SUFFIXES, FRAMEWORK_COMPONENTS, and import path
conventions with a dynamic, plugin-driven registry.

Usage:
    from core.catalog_registry import get_registry
    registry = get_registry()
    registry.is_void_tool("process_final_report")  # True
    registry.component_exists("process_data_prep")  # True
"""

import difflib
import json
from pathlib import Path


class CatalogRegistry:
    """Central registry of known components, void tools, and import paths."""

    def __init__(self):
        self._void_suffixes: list[str] = []
        self._void_exact: set[str] = set()
        self._void_exceptions: list[dict] = []
        self._valid_components: set[str] = set()
        self._import_paths: dict[str, str] = {}
        self._component_outputs: dict[str, list[str]] = {}
        self._function_exports: dict[str, str] = {}
        self._initialized = False

    def init_from_plugin(self, plugin: Any) -> None:
        """Initialize the registry from a PluginConfig."""
        # Load void tools from plugin config
        self._void_suffixes = plugin.void_tool_suffixes
        self._void_exact = {n.lower() for n in plugin.void_tool_exact_names}

        # Load void tool exceptions (e.g., vdabricate is NOT void despite __abricate suffix)
        self._void_exceptions = plugin.void_tool_exceptions

        # Load valid components from framework dir (if available)
        self._valid_components = plugin.load_framework_components()

        # Also load component names from catalog
        if plugin.catalog_components_path:
            self._load_catalog_components(plugin.catalog_components_path)

        # Also load templates from catalog
        if plugin.catalog_templates_path:
            self._load_catalog_templates(plugin.catalog_templates_path)

        # Build import path mappings (from catalog or plugin prefix)
        self._build_import_paths(plugin)

        # Dynamic scanning for exported functions
        self._load_function_exports(plugin)

        self._initialized = True
        logger.info(f"--- [REGISTRY] Initialized: {len(self._valid_components)} components, "
              f"{len(self._void_exact)} exact void, {len(self._void_suffixes)} void suffixes, {len(self._function_exports)} exported functions")

    def init_from_catalog(self, catalog_path: Path, templates_path: Path | None = None, modules_dir: Path | None = None) -> None:
        """Initialize directly from a catalog file (for ingestion-based setups)."""
        self._load_catalog_components(catalog_path)
        if templates_path:
            self._load_catalog_templates(templates_path)

        # Load framework components from directory
        if modules_dir and modules_dir.exists():
            self._valid_components.update(f.stem for f in modules_dir.rglob("*.nf"))

        self._initialized = True

    def _load_catalog_components(self, catalog_path: Path) -> None:
        """Load component IDs and their output channels from catalog JSON. Also auto-detects void tools."""
        try:
            with open(catalog_path) as f:
                catalog = json.load(f)
            for comp in catalog.get("components", []):
                comp_id = comp.get("id", "")
                if comp_id:
                    self._valid_components.add(comp_id)
                    outputs = comp.get("output_channels") or comp.get("out") or []
                    self._component_outputs[comp_id] = outputs

                    if not outputs:
                        self._void_exact.add(comp_id.lower())

                    # Store raw relative_path from catalog if available safely
                    if comp.get("relative_path"):
                        self._import_paths[comp_id] = comp["relative_path"]
        except Exception as e:
            logger.warning(f"--- [REGISTRY] Warning: Could not load catalog: {e}")

    def _load_catalog_templates(self, catalog_path: Path) -> None:
        """Load template IDs and their output channels from templates JSON."""
        try:
            with open(catalog_path) as f:
                catalog = json.load(f)
            for tmpl in catalog.get("templates", []):
                tmpl_id = tmpl.get("id", "")
                if tmpl_id:
                    self._valid_components.add(tmpl_id)
                    self._component_outputs[tmpl_id] = tmpl.get("output_channels") or []

                    if tmpl.get("relative_path"):
                        self._import_paths[tmpl_id] = tmpl["relative_path"]
        except Exception as e:
            logger.warning(f"--- [REGISTRY] Warning: Could not load templates catalog: {e}")

    def _build_import_paths(self, plugin: Any) -> None:
        """Build import path mappings based on catalog relative_path and plugin prefix."""
        prefix = getattr(plugin, "import_prefix", "../")

        for comp_name in self._valid_components:
            # If we have a catalog-provided relative path, prepend the prefix
            if comp_name in self._import_paths and not self._import_paths[comp_name].startswith(prefix):
                self._import_paths[comp_name] = f"{prefix}{self._import_paths[comp_name]}"
            elif comp_name not in self._import_paths:
                # Fallback for framework components not in catalog
                if comp_name.startswith('step_'):
                    self._import_paths[comp_name] = f"{prefix}steps/{comp_name}"
                elif comp_name.startswith('multi_'):
                    self._import_paths[comp_name] = f"{prefix}multi/{comp_name}"
                elif comp_name.startswith('module_'):
                    self._import_paths[comp_name] = f"{prefix}modules/{comp_name}"
                else:
                    self._import_paths[comp_name] = f"{prefix}{comp_name}"

    def _load_function_exports(self, plugin: Any) -> None:
        """Scan the code store to find all exported functions and map them to their parent component."""
        import re
        store_paths = [
            plugin.plugin_dir / "code_store_hollow.jsonl",
            plugin.plugin_dir / "code_store.jsonl",
        ]
        
        target_path = next((p for p in store_paths if p.exists()), None)
        if not target_path:
            return

        static_helpers = set(getattr(plugin, "helper_imports", {}).keys())

        try:
            with open(target_path, encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    comp_id = data.get("id")
                    content = data.get("content", "")
                    
                    if not comp_id or not content:
                        continue
                        
                    matches = re.finditer(r'^\s*def\s+([a-zA-Z0-9_]+)\s*\(', content, flags=re.MULTILINE)
                    for m in matches:
                        func_name = m.group(1)
                        if func_name not in static_helpers:
                            self._function_exports[func_name] = comp_id
        except Exception as e:
            logger.warning(f"--- [REGISTRY] Warning: Could not load function exports: {e}")

    # ── Public API ───────────────────────────────────────────────────────────

    def is_void_tool(self, name: str) -> bool:
        """Check if a process/module is a void tool (no emit channels)."""
        lower = name.lower().strip()
        if lower in self._void_exact:
            return True
        for suffix in self._void_suffixes:
            if lower.endswith(suffix.lower()):
                # Check plugin-driven exceptions
                skip = False
                for exc in self._void_exceptions:
                    if exc.get("suffix", "").lower() == suffix.lower():
                        unless = exc.get("unless_contains", "").lower()
                        if unless and unless in lower:
                            skip = True
                            break
                if skip:
                    continue
                return True
        return False

    def get_import_path(self, comp_id: str) -> str | None:
        """Get the import path for a component."""
        return self._import_paths.get(comp_id)

    def get_function_import_path(self, func_name: str) -> str | None:
        """Get the import path for a specific exported function."""
        comp_id = self._function_exports.get(func_name)
        if comp_id:
            return self.get_import_path(comp_id)
        return None



    def resolve_canonical_id(self, query: str) -> str | None:
        """Resolve arbitrary shorthand, tool name, or alias to canonical component ID."""
        if not query or not isinstance(query, str):
            return None
        q_raw = query.strip()
        if q_raw in self._valid_components:
            return q_raw
        try:
            from core.services.knowledge_graph import kg
            if kg.is_built:
                v = kg.project_vertex(q_raw)
                if v:
                    return v
        except Exception:
            pass
        q_lower = q_raw.lower()
        for comp in self._valid_components:
            if comp.lower() == q_lower:
                return comp
            if comp.lower().endswith(f"__{q_lower}") or comp.lower().endswith(f"_{q_lower}"):
                return comp
        matches = difflib.get_close_matches(q_raw, list(self._valid_components), n=1, cutoff=0.7)
        if matches:
            return matches[0]
        return None

    def component_exists(self, name: str) -> bool:
        """Check if a component exists in the registry."""
        return name in self._valid_components

    def get_close_matches(self, name: str, n: int = 3, cutoff: float = 0.5) -> list[str]:
        """Find similar component names (for error messages)."""
        return difflib.get_close_matches(name, self._valid_components, n=n, cutoff=cutoff)

    @property
    def valid_components(self) -> set[str]:
        """All known valid component names."""
        return self._valid_components.copy()

    @property
    def function_exports(self) -> set[str]:
        """All known exported functions."""
        return set(self._function_exports.keys())

    @property
    def is_initialized(self) -> bool:
        return self._initialized


# ── Singleton ────────────────────────────────────────────────────────────────

_registry: CatalogRegistry | None = None


import threading
_registry_lock = threading.Lock()

def get_registry() -> CatalogRegistry:
    """Get the global catalog registry. Initializes from active plugin if needed."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = CatalogRegistry()
                try:
                    from core.plugin_loader import get_active_plugin
                    plugin = get_active_plugin()
                    _registry.init_from_plugin(plugin)
                except Exception as e:
                    logger.warning(f"--- [REGISTRY] Warning: Plugin-based init failed ({e}), using empty registry")
    return _registry


def reset_registry() -> None:
    """Reset the registry (for testing or plugin switching)."""
    global _registry
    _registry = None
