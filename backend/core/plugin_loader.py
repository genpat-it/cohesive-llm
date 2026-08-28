from core.utils.logger import logger

"""
Plugin Loader - Loads plugin configuration from plugin.yaml files.

Each plugin defines:
  - catalog paths (components, templates, resources)
  - prompt overlays (domain_context, idioms, rejection_rules)
  - void tool definitions
  - RAG tuning overrides
  - optional modules directory for framework validation

Usage:
    from core.plugin_loader import active_plugin
    components_path = active_plugin.catalog_components_path
"""

import os
from pathlib import Path
from typing import Any

import yaml


class PluginConfig:
    """Represents a loaded plugin configuration."""

    def __init__(self, plugin_dir: Path, config: dict):
        self.plugin_dir = plugin_dir
        self.name = config.get("name", plugin_dir.name)
        self.description = config.get("description", "")
        self.version = config.get("version", "0.0.0")
        self._config = config

        # Catalog paths
        catalog = config.get("catalog") or {}
        self.catalog_components_path = self._resolve(catalog.get("components"))
        self.catalog_templates_path = self._resolve(catalog.get("templates"))
        self.catalog_resources_path = self._resolve(catalog.get("resources"))

        # Code store and FAISS/Chroma (Do not use _resolve so we get target paths even if they don't exist yet)
        self.code_store_path = (self.plugin_dir / config.get("code_store")) if config.get("code_store") else None
        self.faiss_index_path = (self.plugin_dir / config.get("faiss_index")) if config.get("faiss_index") else None
        self.chroma_index_path = (self.plugin_dir / config.get("chroma_index")) if config.get("chroma_index") else None
        self.patterns_index_path = (self.plugin_dir / config.get("patterns_index")) if config.get("patterns_index") else None

        # Modules directory (for framework validation)
        modules_dir = config.get("modules_dir")
        if modules_dir:
            modules_path = Path(modules_dir)
            if not modules_path.is_absolute():
                modules_path = self.plugin_dir / modules_path
            self.modules_dir = modules_path.resolve()
        else:
            # Check environment variable fallback
            env_dir = os.getenv("NF_FRAMEWORK_DIR")
            self.modules_dir = Path(env_dir).resolve() if env_dir else None

        # Prompts
        prompts = config.get("prompts") or {}
        self.prompt_domain_context = self._resolve(prompts.get("domain_context"))
        self.prompt_idioms = self._resolve(prompts.get("idioms"))
        self.prompt_rejection_rules = self._resolve(prompts.get("rejection_rules"))

        # Void tools
        self._void_config = config.get("void_tools") or {}
        self._void_exceptions = config.get("void_tool_exceptions") or []

        # Import prefix (defaults to '../' for relative imports)
        self.import_prefix = config.get("import_prefix", "../")

        # Static helper function imports (e.g. { "isSarsCov2": "../functions/parameters.nf" })
        self.helper_imports = config.get("helper_imports") or {}

        # RAG tuning
        self._rag_config = config.get("rag") or {}

        # Model configuration (plugin-specific embedding model)
        _model_cfg = config.get("model") or {}
        self.embedding_model: str | None = _model_cfg.get("embedding_model")

        # Benchmark data directory (relative to plugin dir)
        _bdata = config.get("benchmark_data")
        self.benchmark_data_path: Path | None = (self.plugin_dir / _bdata).resolve() if _bdata else None

    def _resolve(self, relative_path: str | None) -> Path | None:
        """Resolve a relative path against the plugin directory."""
        if not relative_path:
            return None
        resolved = self.plugin_dir / relative_path
        return resolved if resolved.exists() else None

    # ── Void Tool Detection ──────────────────────────────────────────────────

    @property
    def void_tool_suffixes(self) -> list[str]:
        """Suffixes that identify void tools (e.g., '__report_gen')."""
        if isinstance(self._void_config, dict):
            return self._void_config.get("suffixes", [])
        return []

    @property
    def void_tool_exact_names(self) -> list[str]:
        """Exact names of void tools."""
        if isinstance(self._void_config, dict):
            return self._void_config.get("exact_names", [])
        return []

    def is_void_tool(self, name: str) -> bool:
        """Check if a process/module name is a void tool (no emit channels)."""
        lower = name.lower().strip()
        if lower in [n.lower() for n in self.void_tool_exact_names]:
            return True

        for suffix in self.void_tool_suffixes:
            if lower.endswith(suffix.lower()):
                skip = False
                for exc in self.void_tool_exceptions:
                    if exc.get("suffix", "").lower() == suffix.lower():
                        unless = exc.get("unless_contains", "").lower()
                        if unless and unless in lower:
                            skip = True
                            break
                if not skip:
                    return True
        return False

    # ── RAG Tuning ───────────────────────────────────────────────────────────

    def rag_setting(self, key: str, default: Any = None) -> Any:
        """Get a RAG tuning parameter, falling back to default."""
        return self._rag_config.get(key, default)

    @property
    def rag_excluded_templates(self) -> set[str]:
        """Templates to exclude from RAG results."""
        return set(self._rag_config.get("excluded_templates", []))

    @property
    def search_keywords(self) -> list[str]:
        """Domain-specific keywords that boost search ranking."""
        return self._rag_config.get("search_keywords", [])

    @property
    def query_synonyms(self) -> dict[str, list[str]]:
        """Dynamic synonyms mapping for Graph normalization (e.g. 'assembly': ['contigs', 'spades'])."""
        return self._rag_config.get("query_synonyms", {})

    @property
    def void_tool_exceptions(self) -> list[dict[str, str]]:
        """Void tool suffix exceptions (e.g., vdabricate is NOT void despite __abricate suffix)."""
        return self._void_exceptions

    # ── Framework Components ─────────────────────────────────────────────────

    def load_framework_components(self) -> set[str]:
        """Load valid component names from the modules directory (if configured)."""
        if not self.modules_dir or not self.modules_dir.exists():
            return set()

        components = set()
        components.update(f.stem for f in self.modules_dir.rglob("*.nf"))

        return components

    def __repr__(self) -> str:
        return f"PluginConfig(name='{self.name}', dir='{self.plugin_dir}')"


def load_plugin(plugin_name: str) -> PluginConfig:
    """Load a plugin by name from the plugins/ directory."""
    base_dir = Path(__file__).parent.parent
    plugin_dir = base_dir / "plugins" / plugin_name

    if not plugin_dir.exists():
        plugins_parent = base_dir / 'plugins'
        available = [d.name for d in plugins_parent.iterdir() if d.is_dir() and not d.name.startswith('_')] if plugins_parent.exists() else []
        raise FileNotFoundError(
            f"Plugin '{plugin_name}' not found at {plugin_dir}. "
            f"Available plugins: {available}"
        )

    config_path = plugin_dir / "plugin.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Plugin '{plugin_name}' has no plugin.yaml at {config_path}"
        )

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    return PluginConfig(plugin_dir, config)


# ── Global Active Plugin ────────────────────────────────────────────────────

_active_plugin: PluginConfig | None = None


def get_active_plugin() -> PluginConfig:
    """Get the currently active plugin. Loads on first access."""
    global _active_plugin
    if _active_plugin is None:
        plugin_name = os.getenv("NF_AGENT_PLUGIN", "synthetic")
        _active_plugin = load_plugin(plugin_name)
        logger.info(f"--- [PLUGIN] Loaded plugin: {_active_plugin}")
    return _active_plugin


def set_active_plugin(plugin_name: str) -> PluginConfig:
    """Set the active plugin by name."""
    global _active_plugin
    _active_plugin = load_plugin(plugin_name)
    logger.info(f"--- [PLUGIN] Switched to plugin: {_active_plugin}")
    return _active_plugin

