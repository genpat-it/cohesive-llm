import re
from collections import defaultdict
from typing import Any

from langgraph.store.base import BaseStore

from core.config import settings
from core.loader import data_loader
from core.services.query_normalizer import (
    IGNORE_WORDS,
    build_semantic_query,
    is_discovery_query,
    normalize_query,
)
from core.utils.logger import logger

# ──────────────────────────────────────────────────────────────────────────────
# INJECTION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _inject_component(comp_id: str, found_ids: set, context_blocks: list, store: BaseStore, embed_code: bool = True) -> None:
    """we put a component block in the context. it has metadata and maybe the source code too."""
    if comp_id in found_ids:
        return

    comp_item = store.get(("components",), comp_id)
    if not comp_item:
        return
    comp_data = comp_item.value or {}

    found_ids.add(comp_id)

    code_item = store.get(("code",), comp_id)
    code_snippet = code_item.value.get("content", "// Code not found") if (code_item and code_item.value) else "// Code not found in repository"

    inputs = comp_data.get('input_channels') or comp_data.get('input_types') or []
    outputs = comp_data.get('output_channels') or comp_data.get('out') or []

    block = f"""
--- COMPONENT: {comp_id} ---
TOOL: {comp_data.get('tool', 'Unknown')}
DOMAIN: {comp_data.get('domain', 'Unknown')}
DESCRIPTION: {comp_data.get('description', 'No description')}
CONTAINER: {comp_data.get('container', 'None')}
INPUTS ({len(inputs)} channels): {', '.join(inputs)}
OUTPUTS: {', '.join(outputs)}
"""

    if embed_code:
        block += f"\n**SOURCE CODE ({comp_id}.nf):**\n```groovy\n{code_snippet}\n```\n"

    context_blocks.append(block)

def _inject_template(template_id: str, found_ids: set, context_blocks: list, store: BaseStore, embed_code: bool = True) -> None:
    """we put a template block in the context. it has metadata and maybe the source code too."""
    if template_id in found_ids:
        return

    tmpl_item = store.get(("templates",), template_id)
    if not tmpl_item:
        return

    found_ids.add(template_id)
    tmpl_data = tmpl_item.value or {}

    inputs = tmpl_data.get('input_channels') or []
    outputs = tmpl_data.get('output_channels') or []

    block = f"""
--- TEMPLATE: {template_id} ---
ID: {tmpl_data.get('id', template_id)}
DESCRIPTION: {tmpl_data.get('description', 'No description')}
INPUTS: {', '.join(inputs)}
OUTPUTS: {', '.join(outputs)}
"""

    if embed_code:
        code_item = store.get(("code",), template_id)
        code_snippet = code_item.value.get("content", "// Code not found") if (code_item and code_item.value) else "// Code not found in repository"
        block += f"\n**SOURCE CODE ({template_id}.nf):**\n```groovy\n{code_snippet}\n```\n"

    context_blocks.append(block)

# Merge default + plugin-specific excluded templates
EXCLUDED_TEMPLATES = set(settings.RAG_EXCLUDED_TEMPLATES)
try:
    from core.plugin_loader import get_active_plugin
    EXCLUDED_TEMPLATES |= get_active_plugin().rag_excluded_templates
except Exception:
    pass

def retrieve_rag_context(user_query: Any, store: BaseStore, embed_code: Any=False) -> Any:  # noqa: C901
    """
    Retrieves context using a Hybrid Approach:
      0. GraphRAG: Graph-guided pre-fill — expand matched components via data-flow edges
      1. Keyword & metadata matching across the component/template catalog
      2. FAISS semantic vector search for latent similarity
    Both keyword/FAISS paths share a preprocessed, synonym-expanded token set.
    """
    if not data_loader.vector_store:
        return "Vector Store not loaded."

    found_ids = set()
    context_blocks = []

    query_info = normalize_query(user_query)
    query_lower = query_info["query_lower"]
    clean_query = query_info["clean_query"]
    query_tokens = query_info["query_tokens"]

    if not clean_query:
        return "Query too broad. Provide more details to search the catalog."

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 0 — GRAPHRAG PRE-FILL
    # Loads the deterministic AST tool graph and pre-populates adjacent
    # components so the architect gets a connected subgraph, not isolated hits.
    # ══════════════════════════════════════════════════════════════════════════
    graph_adjacency: dict[str, list[dict]] = {}  # src_id → [{"target": str, "via": [...]}]
    graph_reverse: dict[str, list[dict]] = {}    # tgt_id → [{"source": str, "via": [...]}]
    try:
        graph_item = store.get(("graph",), "adjacency")
        if graph_item and graph_item.value:
            for edge in graph_item.value.get("edges", []):
                src = edge["source"]
                tgt = edge["target"]
                via = edge.get("via", [])
                if edge.get("edge_type") == "data_flow":
                    graph_adjacency.setdefault(src, []).append({"target": tgt, "via": via})
                    graph_reverse.setdefault(tgt, []).append({"source": src, "via": via})
    except Exception as e:
        logger.warning(f"graph_rag_load_failed: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 1 — DISCOVERY / SUGGESTION INTENT DETECTION
    # ══════════════════════════════════════════════════════════════════════════
    is_discovery = is_discovery_query(clean_query)

    # Inject capability map when the user is exploring the catalog
    if is_discovery:
        try:
            suggestion_block = "### SYSTEM CATALOG OVERVIEW (For user suggestions)\n"
            suggestion_block += "The user's query implies they want to know what is available. Use this capabilities map.\n\n"
            suggestion_block += "**Available Pipelines (Templates):**\n"

            for tmpl in store.search(("templates",), limit=settings.SEARCH_SCAN_LIMIT):
                t_data = tmpl.value or {}
                t_name = t_data.get('id', tmpl.key)
                outputs = t_data.get('output_channels') or []
                out_str = f" *(Generates: {', '.join(outputs)})*" if outputs else ""
                suggestion_block += f"- **{t_name}**{out_str}\n"

            domain_groups = defaultdict(list)
            for comp in store.search(("components",), limit=settings.SEARCH_SCAN_LIMIT):
                c_data = comp.value or {}
                comp_domain = c_data.get("domain", "Other")
                tool_name = c_data.get("tool", "")

                if tool_name and str(tool_name).strip() and str(tool_name).lower() != "none":
                    domain_groups[comp_domain].append(str(tool_name).strip())

            if domain_groups:
                suggestion_block += "\n**Supported Individual Tools (Grouped by Domain):**\n"
                for domain_name, tools_list in sorted(domain_groups.items()):
                    unique_tools = sorted(list(set(tools_list)))
                    suggestion_block += f"- *{domain_name}*: {', '.join(unique_tools)}\n"

            template_total = 0
            template_with_code = 0
            for tmpl in store.search(("templates",), limit=settings.SEARCH_SCAN_LIMIT):
                template_total += 1
                if store.get(("code",), tmpl.key):
                    template_with_code += 1

            component_total = 0
            component_with_code = 0
            for comp in store.search(("components",), limit=settings.SEARCH_SCAN_LIMIT):
                component_total += 1
                if store.get(("code",), comp.key):
                    component_with_code += 1

            if template_total or component_total:
                tmpl_pct = (template_with_code / template_total * 100) if template_total else 0
                comp_pct = (component_with_code / component_total * 100) if component_total else 0
                suggestion_block += (
                    "\n**Code Store Coverage:**\n"
                    f"- Templates: {template_with_code}/{template_total} ({tmpl_pct:.1f}%)\n"
                    f"- Components: {component_with_code}/{component_total} ({comp_pct:.1f}%)\n"
                )

            context_blocks.append(suggestion_block)
        except Exception as e:
            logger.error(f"--- [NODE] TOOL ERROR catalog suggestion error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 2 — HYBRID KEYWORD & METADATA SEARCH
    # ══════════════════════════════════════════════════════════════════════════

    # Low-value tokens that should not drive scoring on their own
    ignore_words = IGNORE_WORDS

    # ── Template Scan ──────────────────────────────────────────────────────
    try:
        template_scores = {}
        for tmpl in store.search(("templates",), limit=settings.SEARCH_SCAN_LIMIT):
            tmpl_id = tmpl.key.lower()
            tmpl_data = tmpl.value or {}
            score = 0

            clean_id_words = tmpl_id.replace("_", " ").split()
            for id_word in clean_id_words:
                if len(id_word) > 3 and id_word in query_tokens and id_word not in ignore_words:
                    score += 8

            for kw in (tmpl_data.get('keywords') or []):
                if str(kw).lower() in query_tokens:
                    score += 5

            for st in (tmpl_data.get('compatible_seq_types') or []):
                if str(st).lower().replace('_', ' ') in query_lower:
                    score += 3

            if score > 0:
                template_scores[tmpl.key] = score

        sorted_tmpls = [k for k, v in sorted(template_scores.items(), key=lambda x: x[1], reverse=True) if v >= settings.RAG_KEYWORD_TEMPLATE_MIN_SCORE and k not in EXCLUDED_TEMPLATES][:settings.RAG_MAX_KEYWORD_TEMPLATES]
        for tmpl_key in sorted_tmpls:
            if tmpl_key not in found_ids:
                context_blocks.append(f"### PIPELINE BLUEPRINT: {tmpl_key}")
                _inject_template(tmpl_key, found_ids, context_blocks, store, embed_code=True)

                tmpl_item = store.get(("templates",), tmpl_key)
                if tmpl_item and tmpl_item.value:
                    for comp_id in (tmpl_item.value.get('components_used') or []):
                        _inject_component(comp_id, found_ids, context_blocks, store, embed_code)
    except Exception as e:
        logger.error(f"--- [NODE] TOOL ERROR template search error: {e}")

    # ── Component Scan ─────────────────────────────────────────────────────
    try:
        component_scores = {}
        for comp in store.search(("components",), limit=settings.SEARCH_SCAN_LIMIT):
            comp_id = comp.key.lower()
            comp_data = comp.value or {}
            score = 0

            tool_name = str(comp_data.get('tool', '')).lower()
            domain_name = str(comp_data.get('domain', '')).lower()

            # High weight for exact tool-name or ID-suffix matches
            if '__' in comp_id:
                suffix = comp_id.split('__')[-1]
                for sw in suffix.split('_'):
                    if sw and len(sw) > 3 and sw in query_tokens and sw not in ignore_words:
                        score += 50

            if tool_name:
                for word in re.split(r'[^a-z0-9]', tool_name):
                    if len(word) > 3 and word in query_tokens and word not in ignore_words:
                        score += 50

            if domain_name:
                for part in re.split(r'[^a-z0-9]', domain_name):
                    if len(part) > 3 and part in query_tokens and part not in ignore_words:
                        score += 5

            for st in (comp_data.get('compatible_seq_types') or []):
                for st_word in str(st).lower().replace('_', ' ').split():
                    if st_word and len(st_word) > 3 and st_word in query_tokens:
                        score += 5

            # Lower weight for I/O types to avoid flooding
            io_combined = (comp_data.get('input_channels') or []) + (comp_data.get('output_channels') or [])
            for io_val in io_combined:
                for io_word in str(io_val).lower().replace('_', ' ').split():
                    if len(io_word) > 3 and io_word in query_tokens and io_word not in ignore_words:
                        score += 2

            for param in (comp_data.get('params') or []):
                clean_param = str(param).lower().replace('-', '')
                if len(clean_param) > 3 and clean_param in query_tokens and clean_param not in ignore_words:
                    score += 1

            # Structural keyword boosting from plugin config
            try:
                from core.plugin_loader import get_active_plugin
                structural_keywords = get_active_plugin().search_keywords
            except Exception:
                structural_keywords = []
            for kw in structural_keywords:
                if kw in query_tokens and kw in comp_id:
                    score += 15

            if score > 0:
                component_scores[comp.key] = score

        # Dynamic thresholding: keep only components scoring ≥20% of top match
        if component_scores:
            max_score = max(component_scores.values())
            threshold = max_score * settings.RAG_KEYWORD_COMPONENT_THRESHOLD
            smart_comps = [k for k, v in sorted(component_scores.items(), key=lambda x: x[1], reverse=True) if v >= threshold][:settings.RAG_MAX_KEYWORD_COMPONENTS]
            for comp_key in smart_comps:
                if comp_key not in found_ids:
                    _inject_component(comp_key, found_ids, context_blocks, store, embed_code)

            # ── GRAPHRAG EXPANSION ───────────────────────────────────────────
            # For every keyword-matched component, pull in its direct graph
            # neighbours (upstream prerequisites + downstream consumers).
            # This ensures the architect always sees a valid connected subgraph.
            if graph_adjacency or graph_reverse:
                graph_neighbors_to_inject = []
                for comp_key in smart_comps:
                    # Downstream: what this component feeds into
                    for edge in graph_adjacency.get(comp_key, []):
                        neighbor = edge["target"]
                        if neighbor not in found_ids and neighbor not in graph_neighbors_to_inject:
                            graph_neighbors_to_inject.append(neighbor)
                    # Upstream: what feeds into this component
                    for edge in graph_reverse.get(comp_key, []):
                        neighbor = edge["source"]
                        if neighbor not in found_ids and neighbor not in graph_neighbors_to_inject:
                            graph_neighbors_to_inject.append(neighbor)

                # Inject up to settings.RAG_MAX_KEYWORD_COMPONENTS neighbors (no code, metadata only)
                max_graph_neighbors = getattr(settings, "RAG_MAX_GRAPH_NEIGHBORS", 4)
                for neighbor_id in graph_neighbors_to_inject[:max_graph_neighbors]:
                    if neighbor_id not in found_ids:
                        _inject_component(neighbor_id, found_ids, context_blocks, store, embed_code=False)
                if graph_neighbors_to_inject:
                    logger.info("graphrag_expansion", injected=min(len(graph_neighbors_to_inject), max_graph_neighbors))

    except Exception as e:
        logger.error(f"--- [NODE] TOOL ERROR component search error: {e}")

    # ── Resource (Helper Function) Scan ────────────────────────────────────

    try:
        res_item = store.get(("resources",), "helper_functions")
        if res_item and isinstance(res_item.value, dict):
            helpers = res_item.value.get("list", [])
            resource_scores = {}
            for i, helper in enumerate(helpers):
                h_name = str(helper.get("name", "")).lower()
                h_score = 0

                if h_name and h_name in query_tokens:
                    h_score += 10

                h_words = set(re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', helper.get("name", "")))
                h_words = {w.lower() for w in h_words if len(w) > 3 and w.lower() not in ignore_words}
                for w in h_words:
                    if w in query_tokens:
                        h_score += 5

                if h_score > 0:
                    resource_scores[i] = (h_score, helper)

            top_resources = sorted(resource_scores.values(), key=lambda x: x[0], reverse=True)[:settings.RAG_MAX_HELPER_FUNCTIONS]
            for _, helper in top_resources:
                if helper.get('name') not in found_ids:
                    context_blocks.append(
                        f"### GROOVY HELPER FUNCTION: {helper.get('name')}\n"
                        f"DESCRIPTION: {helper.get('description')}\n"
                        f"USAGE: `{helper.get('usage')}`\n"
                        f"DEFINED IN: {helper.get('path')}\n"
                    )
                    found_ids.add(helper.get('name'))
    except Exception as e:
        logger.error(f"--- [NODE] TOOL ERROR resource search error: {e}")

    # ── Container Scan ─────────────────────────────────────────────────────
    try:
        containers_item = store.get(("resources",), "containers")
        if containers_item and isinstance(containers_item.value, dict):
            containers = containers_item.value.get("list", [])
            matched_containers = []
            for container in containers:
                c_name = str(container.get("name", "")).lower()
                if c_name and len(c_name) > 2 and c_name in query_tokens:
                    matched_containers.append(container)

            if matched_containers:
                container_block = "### DOCKER CONTAINER REGISTRY LOOKUP\n"
                for c in matched_containers:
                    container_block += f"- **{c.get('name')}**: `{c.get('url')}`\n"
                context_blocks.append(container_block)
    except Exception as e:
        logger.error(f"--- [NODE] TOOL ERROR container search error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 3 — SEMANTIC SEARCH (FAISS)
    # ══════════════════════════════════════════════════════════════════════════
    try:
        # Strip conversational filler that dilutes the vector embedding
        semantic_query = build_semantic_query(clean_query, query_tokens)
        if not semantic_query:
            return "Semantic search skipped because the query was empty after normalization."

        docs_and_scores = data_loader.vector_store.similarity_search_with_score(semantic_query, k=settings.RAG_FAISS_K)

        # Relative distance cutoffs: drop results that are too far from the best match
        if docs_and_scores:
            best_l2 = docs_and_scores[0][1]

            for doc, l2_dist in docs_and_scores:
                if l2_dist > settings.RAG_FAISS_MAX_L2_DISTANCE or l2_dist > (best_l2 + settings.RAG_FAISS_RELATIVE_MARGIN):
                    continue

                meta = doc.metadata
                item_id = meta.get('id')
                item_type = meta.get('type')

                if item_id in found_ids or item_id in EXCLUDED_TEMPLATES:
                    continue

                if item_type == 'template':
                    tmpl_item = store.get(("templates",), item_id)
                    if tmpl_item:
                        tmpl = tmpl_item.value or {}
                        context_blocks.append(f"### PIPELINE BLUEPRINT (Semantic Match): {item_id}\n{doc.page_content}")
                        _inject_template(item_id, found_ids, context_blocks, store, embed_code=True)

                        for comp_id in (tmpl.get('components_used') or []):
                            _inject_component(comp_id, found_ids, context_blocks, store, embed_code)

                elif item_type == 'component':
                    _inject_component(item_id, found_ids, context_blocks, store, embed_code)

    except Exception as e:
        logger.error(f"--- [NODE] TOOL ERROR faiss search error: {e}")

    return "\n".join(context_blocks) + "\n\n"
