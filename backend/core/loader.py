import json
import os
from pathlib import Path
from typing import Any

# Qwen3 support is handled natively by the current transformers library.
from langchain_huggingface import HuggingFaceEmbeddings

from core.adapters.vector_store import ChromaAdapter, FaissAdapter
from core.config import settings
from core.utils.logger import logger


class DataLoader:
    def __init__(self):
        self.vector_store = None
        self.patterns_vector_store = None
        self.code_db = {}
        self.comp_db = {}
        self.tmpl_db = {}
        self.usage_db = {}
        self.res_list = []

    def load_all(self, store: Any=None) -> None:
        logger.info("loading_resources_start")
        self._load_lookups(store)
        self._load_vector_store()
        logger.info("loading_resources_complete")

    def _resolve_paths(self) -> Any:
        """Resolve data file paths: plugin paths take priority over settings defaults."""
        paths = {
            "code_store": settings.CODE_STORE,
            "catalog_components": settings.CATALOG_COMPONENTS,
            "catalog_templates": settings.CATALOG_TEMPLATES,
            "catalog_resources": settings.CATALOG_RESOURCES,
            "faiss_index": settings.FAISS_INDEX_PATH,
            "chroma_index": settings.CHROMA_INDEX_PATH,
        }
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            # Override with plugin paths if they exist
            if plugin.code_store_path:
                paths["code_store"] = str(plugin.code_store_path)
            if plugin.catalog_components_path:
                paths["catalog_components"] = str(plugin.catalog_components_path)
            if plugin.catalog_templates_path:
                paths["catalog_templates"] = str(plugin.catalog_templates_path)
            if plugin.catalog_resources_path:
                paths["catalog_resources"] = str(plugin.catalog_resources_path)
            # Only override index paths when the directory actually exists on disk.
            # Plugins declare these paths in plugin.yaml, but the index may not have
            # been generated yet — fall back to data/faiss_index in that case.
            if plugin.faiss_index_path and Path(plugin.faiss_index_path).exists():
                paths["faiss_index"] = str(plugin.faiss_index_path)
            elif plugin.faiss_index_path and not Path(plugin.faiss_index_path).exists():
                logger.warning(
                    "plugin_faiss_index_missing_using_default",
                    plugin_path=str(plugin.faiss_index_path),
                    fallback=paths["faiss_index"],
                )
            if hasattr(plugin, "chroma_index_path") and plugin.chroma_index_path and Path(str(plugin.chroma_index_path)).exists():
                paths["chroma_index"] = str(plugin.chroma_index_path)
            else:
                chroma_fallback = str(plugin.faiss_index_path).replace("faiss_index", "chroma_index") if plugin.faiss_index_path else settings.CHROMA_INDEX_PATH
                paths["chroma_index"] = chroma_fallback if Path(chroma_fallback).exists() else settings.CHROMA_INDEX_PATH
            logger.info("plugin_paths_resolved", plugin_name=plugin.name)
        except Exception as e:
            logger.warning("plugin_not_available_using_defaults", error=str(e))
        return paths

    def _resolve_patterns_index_path(self) -> str | None:
        """Resolve the patterns sub-index path from the active plugin config."""
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            if hasattr(plugin, "patterns_index_path") and plugin.patterns_index_path and Path(plugin.patterns_index_path).exists():
                return str(plugin.patterns_index_path)
        except Exception:
            pass
        return None

    def _load_lookups(self, store: Any=None) -> None:  # noqa: C901
        paths = self._resolve_paths()

        # Load Code Store
        code_store_path = paths["code_store"]
        if os.path.exists(code_store_path):
            with open(code_store_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get('id'):
                            self.code_db[entry['id']] = entry['content']
                            if store:
                                store.put(("code",), entry['id'], {"content": entry['content']})
                    except Exception:
                        continue

        # Load Catalogs
        comp_path = paths["catalog_components"]
        if os.path.exists(comp_path):
            with open(comp_path, encoding="utf-8") as f:
                self.comp_db = {c['id']: c for c in (json.load(f).get('components') or [])}
                if store:
                    for k, v in self.comp_db.items():
                        store.put(("components",), k, v)

        tmpl_path = paths["catalog_templates"]
        if os.path.exists(tmpl_path):
            with open(tmpl_path, encoding="utf-8") as f:
                self.tmpl_db = {c['id']: c for c in (json.load(f).get('templates') or [])}
                if store:
                    for k, v in self.tmpl_db.items():
                        store.put(("templates",), k, v)

        res_path = paths["catalog_resources"]
        if os.path.exists(res_path):
            with open(res_path, encoding="utf-8") as f:
                raw_resources = json.load(f).get('resources') or {}
                self.res_list = raw_resources.get('helper_functions') or []
                self.containers_list = raw_resources.get('containers') or []
                if store:
                    store.put(("resources",), "helper_functions", {"list": self.res_list})
                    store.put(("resources",), "containers", {"list": self.containers_list})
                    
        # Load Patterns (from patterns.json alongside components.json if present)
        patterns_path = os.path.join(os.path.dirname(paths["catalog_components"]), "patterns.json")
        if os.path.exists(patterns_path):
            with open(patterns_path, encoding="utf-8") as f:
                patterns_data = json.load(f).get('patterns', [])
                if store:
                    for p in patterns_data:
                        store.put(("patterns",), p["id"], p)

        # Load Tool Graph (deterministic AST-derived DAG from ingestion)
        graph_path = os.path.join(os.path.dirname(paths["catalog_components"]), "tool_graph.json")
        if os.path.exists(graph_path):
            with open(graph_path, encoding="utf-8") as f:
                graph_data = json.load(f)
            if store:
                store.put(("graph",), "adjacency", graph_data)
            logger.info(
                "tool_graph_loaded",
                nodes=len(graph_data.get("nodes", [])),
                edges=len(graph_data.get("edges", [])),
            )
        else:
            logger.info("tool_graph_not_found", path=graph_path)

        # Build reverse index: component_id → list of templates that use it
        if store:
            self._build_usage_index(store)

    def _build_usage_index(self, store: Any) -> None:
        """Build reverse index: for each component, find all templates that
        include it and extract the relevant code snippet showing how it's wired.
        Stored under ("usage", component_id) in the InMemoryStore.
        """
        import re

        # Collect: component_id → [{template_id, snippet}]
        usage_map = {}

        for tmpl_id, tmpl_meta in self.tmpl_db.items():
            tmpl_code = self.code_db.get(tmpl_id, "")
            if not tmpl_code:
                continue

            # Get steps from catalog + parse include statements from code
            catalog_steps = set(tmpl_meta.get("components_used") or [])
            code_includes = set()
            for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", tmpl_code):
                block = match.group(1)
                for item in block.split(';'):
                    name = item.strip().split(' as ')[0].strip() if ' as ' in item else item.strip()
                    if name and re.match(r'^[a-zA-Z0-9_]+$', name):
                        code_includes.add(name)

            all_steps = catalog_steps | code_includes

            for comp_id in all_steps:
                # Extract the snippet showing how this component is called
                snippet = self._extract_usage_snippet(tmpl_code, comp_id)

                if comp_id not in usage_map:
                    usage_map[comp_id] = []
                usage_map[comp_id].append({
                    "template_id": tmpl_id,
                    "template_description": tmpl_meta.get("description", "")[:200],
                    "snippet": snippet,
                })

        # Store in the InMemoryStore and local dictionary
        self.usage_db = usage_map
        for comp_id, usages in usage_map.items():
            store.put(("usage",), comp_id, {"usages": usages})

        logger.info("usage_index_built", components_mapped=len(usage_map))

        # Build the offline knowledge graph once all catalog + usage data is loaded.
        # Graphify-style: nx.DiGraph with EXTRACTED/INFERRED/AMBIGUOUS edge tiers.
        try:
            from core.services.knowledge_graph import kg
            kg.build_nx_graph(store)
            try:
                kg.export_graph_json("graphify-out/catalog_graph.json")
            except Exception as _exp_err:
                logger.debug("catalog_graph_export_skipped", error=str(_exp_err))
        except Exception as _kg_err:
            logger.warning("knowledge_graph_build_skipped", error=str(_kg_err))


    def _extract_usage_snippet(self, template_code: str, component_id: str) -> str:
        """Extract lines around a component's usage in template code.
        Returns the calling context (a few lines before/after) so the agent
        can see how the component is wired (what channels go in/out).
        """
        lines = template_code.split('\n')
        hit_lines = []
        import re
        for i, line in enumerate(lines):
            if re.search(rf'\b{re.escape(component_id)}\b', line) and 'include' not in line.lower():
                hit_lines.append(i)

        if not hit_lines:
            return "(component included but call site not found in code)"

        # Gather context: 2 lines before and 2 lines after each hit
        context_indices = set()
        for h in hit_lines:
            for j in range(max(0, h - 2), min(len(lines), h + 3)):
                context_indices.add(j)

        snippet_lines = [lines[i] for i in sorted(context_indices)]
        return '\n'.join(snippet_lines).strip()

    def _load_vector_store(self) -> None:
        paths = self._resolve_paths()
        faiss_path = paths["faiss_index"]
        chroma_path = paths["chroma_index"]

        # Resolve embedding model: plugin declares it → settings override → error
        embedding_model = None
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            embedding_model = getattr(plugin, "embedding_model", None)
        except Exception:
            pass
        embedding_model = embedding_model or settings.EMBEDDING_MODEL
        if not embedding_model:
            raise ValueError(
                "No embedding model configured. "
                "Set 'model.embedding_model' in plugin.yaml or the EMBEDDING_MODEL environment variable."
            )

        logger.info("loading_embeddings", model=embedding_model, device="cpu")
        embeddings = HuggingFaceEmbeddings(
            model_name=embedding_model,
            model_kwargs={'device': 'cpu', 'trust_remote_code': True},
            encode_kwargs={'normalize_embeddings': True, 'batch_size': 4}
        )

        try:
            if settings.VECTOR_DB_TYPE == "chroma":
                logger.info("loading_vector_store", type="chroma", path=chroma_path)
                self.vector_store = ChromaAdapter(index_path=chroma_path, embeddings=embeddings)
            else:
                logger.info("loading_vector_store", type="faiss", path=faiss_path)
                self.vector_store = FaissAdapter(index_path=faiss_path, embeddings=embeddings)
        except Exception as e:
            logger.error("vector_store_error", error=str(e))

        # Load patterns sub-index (separate FAISS, always FAISS regardless of main db type)
        patterns_path = self._resolve_patterns_index_path()
        if patterns_path:
            try:
                logger.info("loading_patterns_vector_store", path=patterns_path)
                self.patterns_vector_store = FaissAdapter(index_path=patterns_path, embeddings=embeddings)
            except Exception as e:
                logger.warning("patterns_vector_store_error", error=str(e))
        else:
            logger.info("patterns_vector_store_not_found_skipping")

# Global Instance - Note: We keep this for now to prevent breaking existing imports,
# but it should ideally be injected using FastAPI Depends.
data_loader = DataLoader()
