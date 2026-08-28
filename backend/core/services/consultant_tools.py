from core.utils.logger import logger

"""
Consultant Tools — LangGraph @tool functions for the consultant agent.

These tools are bound to the consultant LLM via bind_tools(), allowing it to
dynamically verify IDs, search the catalog, and inspect template logic
instead of relying solely on bulk RAG context injection.
"""

import re
import json
import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain.tools import tool, ToolRuntime


from core.config import settings
from core.loader import data_loader
from core.services.query_normalizer import (
    IGNORE_WORDS,
    build_semantic_query,
    is_discovery_query,
    normalize_query,
)

# ──────────────────────────────────────────────────────────────────────────────
# TOOL 1: Lookup Catalog Item
# ──────────────────────────────────────────────────────────────────────────────

@tool
def lookup_catalog_item(item_id: str, include_code: bool, runtime: ToolRuntime) -> str:
    """Look up a component or template from the knowledge base to get its exact details.
    Use this to get metadata, inputs, outputs, and optionally the source code or template logic.
    This replaces separate calls for verifying IDs or fetching code.

    Args:
        item_id: The exact ID to look up (e.g. 'process_data_prep' or 'my_pipeline_template')
        include_code: Set to True to also fetch the source code or template logic flow.
    """
    store = runtime.store

    # Check components
    comp_item = store.get(("components",), item_id)
    tmpl_item = store.get(("templates",), item_id) if not comp_item else None

    if not comp_item and not tmpl_item:
        # Check code store dynamically
        code_item = store.get(("code",), item_id)
        if code_item:
            code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
            parsed = _parse_nextflow_channels(code)
            res = {
                "valid": True,
                "id": item_id,
                "namespace": "component",
                "tool": item_id,
                "description": f"Dynamically resolved component '{item_id}' from DSL2 source.",
                "inputs": parsed["takes"],
                "outputs": parsed["emits"],
                "code_available": True,
            }
            if include_code:
                res["source_code"] = code[:settings.MAX_CODE_DISPLAY_LENGTH] + ("\n// ... (truncated)" if len(code) > settings.MAX_CODE_DISPLAY_LENGTH else "")
            return json.dumps(res, indent=2)

        from core.services.knowledge_graph import kg
        if kg.is_built and item_id in kg.nx_graph:
            node_data = kg.nx_graph.nodes[item_id]
            return json.dumps({
                "valid": True,
                "id": item_id,
                "namespace": node_data.get("type", "component"),
                "tool": item_id,
                "description": node_data.get("description", f"Graph vertex '{item_id}'"),
                "inputs": node_data.get("takes", []),
                "outputs": node_data.get("emits", []),
                "code_available": False,
            }, indent=2)

        return json.dumps({"valid": False, "error": f"ID '{item_id}' not found in catalog"}, indent=2)

    result = {"valid": True, "id": item_id}

    if comp_item:
        data = comp_item.value or {}
        result.update({
            "namespace": "component",
            "tool": data.get("tool"),
            "domain": data.get("domain"),
            "description": data.get("description"),
            "inputs": data.get("input_channels") or data.get("input_types") or [],
            "outputs": data.get("output_channels") or data.get("out") or [],
        })
    else:
        data = tmpl_item.value or {}
        result.update({
            "namespace": "template",
            "description": data.get("description"),
            "components_used": data.get("components_used") or [],
            "input_channels": data.get("input_channels") or [],
            "output_channels": data.get("output_channels") or [],
        })
        if include_code:
            result["logic_flow"] = data.get("logic_flow") or []

    if include_code:
        code_item = store.get(("code",), item_id)
        if code_item:
            code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
            result["code_available"] = True
            result["source_code"] = code[:settings.MAX_CODE_DISPLAY_LENGTH] + ("\n// ... (truncated)" if len(code) > settings.MAX_CODE_DISPLAY_LENGTH else "")

            # Parse real channels from code to override potentially stale metadata
            parsed = _parse_nextflow_channels(code)
            if parsed["takes"]:
                result["inputs"] = parsed["takes"]
            if parsed["emits"]:
                result["outputs"] = parsed["emits"]
        else:
            result["code_available"] = False
            result["warning"] = "Source code not found in the code store."

    return json.dumps(result, indent=2)


@tool
def lookup_components_batch(item_ids: list[str], runtime: ToolRuntime, include_code: bool = False) -> str:
    """Batch lookup multiple catalog components or templates in a single tool call to minimize roundtrips.

    Args:
        item_ids: List of catalog item IDs to look up (e.g. ['step_module_a', 'step_module_b']).
        include_code: Whether to include truncated Nextflow DSL2 source code.
    """
    store = runtime.store
    from core.services.knowledge_graph import kg
    if not kg.is_built:
        kg.build_nx_graph(store)

    results = {}
    for item_id in item_ids:
        item_id_clean = str(item_id).strip()
        if not item_id_clean:
            continue
            
        # Extract graph neighbors if available with confidence tiers
        graph_up = []
        graph_down = []
        comm_id = None
        if kg.is_built and item_id_clean in kg.G:
            graph_up = [
                f"{u} ({kg.G[u][item_id_clean].get('confidence', 'AMBIGUOUS')})"
                for u in kg.G.predecessors(item_id_clean)
            ][:5]
            graph_down = [
                f"{v} ({kg.G[item_id_clean][v].get('confidence', 'AMBIGUOUS')})"
                for v in kg.G.successors(item_id_clean)
            ][:5]
            comm_id = kg.G.nodes[item_id_clean].get("community")

        comp_item = store.get(("components",), item_id_clean)
        if comp_item and comp_item.value:
            c_val = comp_item.value
            entry = {
                "valid": True,
                "id": item_id_clean,
                "namespace": "component",
                "tool": c_val.get("tool"),
                "domain": c_val.get("domain"),
                "description": str(c_val.get("description") or "")[:200],
                "inputs": c_val.get("input_channels") or c_val.get("input_types") or [],
                "outputs": c_val.get("output_channels") or c_val.get("out") or [],
                "upstream_tools": graph_up,
                "downstream_tools": graph_down,
            }
            if comm_id is not None:
                entry["community"] = comm_id
            if include_code:
                code_item = store.get(("code",), item_id_clean)
                if code_item and code_item.value:
                    code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
                    entry["source_code"] = code[:settings.MAX_CODE_DISPLAY_LENGTH]
            results[item_id_clean] = entry
            continue

        tmpl_item = store.get(("templates",), item_id_clean)
        if tmpl_item and tmpl_item.value:
            t_val = tmpl_item.value
            entry = {
                "valid": True,
                "id": item_id_clean,
                "namespace": "template",
                "domain": t_val.get("domain"),
                "description": str(t_val.get("description") or "")[:200],
                "inputs": t_val.get("input_channels") or [],
                "outputs": t_val.get("output_channels") or [],
                "components_used": t_val.get("components_used") or [],
                "upstream_tools": graph_up,
                "downstream_tools": graph_down,
            }
            if include_code:
                code_item = store.get(("code",), item_id_clean)
                if code_item and code_item.value:
                    code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
                    entry["source_code"] = code[:settings.MAX_CODE_DISPLAY_LENGTH]
            results[item_id_clean] = entry
            continue

        # Dynamic AST fallback
        code_item = store.get(("code",), item_id_clean)
        if code_item and code_item.value:
            code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
            parsed = _parse_nextflow_channels(code)
            entry = {
                "valid": True,
                "id": item_id_clean,
                "namespace": "component",
                "tool": item_id_clean,
                "description": f"Dynamically resolved component '{item_id_clean}' from DSL2 source.",
                "inputs": parsed["takes"],
                "outputs": parsed["emits"],
                "upstream_tools": graph_up,
                "downstream_tools": graph_down,
            }
            if include_code:
                entry["source_code"] = code[:settings.MAX_CODE_DISPLAY_LENGTH]
            results[item_id_clean] = entry
            continue

        results[item_id_clean] = {"valid": False, "error": f"ID '{item_id_clean}' not found in catalog"}

    return json.dumps(results, indent=2)

# ──────────────────────────────────────────────────────────────────────────────
# TOOL 2: Search components/templates (Full Hybrid: Keyword + FAISS)
# ──────────────────────────────────────────────────────────────────────────────

import threading
_faiss_lock = threading.Lock()

@tool
def search_components(query: str, runtime: ToolRuntime, input_channel: str = None, output_channel: str = None, limit: int = 50, offset: int = 0) -> str:  # noqa: C901
    """Search for catalog components using natural language or semantic descriptions.
    Use this when you don't know the exact ID but know what the component should do.

    Args:
        query: Description of what the component should do (e.g. "trim fastq reads").
    """
    store = runtime.store
    results = []
    warnings = []
    found_ids = set()
    query_info = normalize_query(query)
    query_tokens = query_info["query_tokens"]
    clean_query = query_info["clean_query"]
    query_lower = query_info["query_lower"]

    # Merge default + plugin-specific excluded templates
    excluded_templates = set(settings.RAG_EXCLUDED_TEMPLATES)
    try:
        from core.plugin_loader import get_active_plugin
        excluded_templates |= get_active_plugin().rag_excluded_templates
    except Exception:
        pass

    if is_discovery_query(clean_query) and len(query_tokens) < 2:
        return json.dumps([
            {
                "type": "meta",
                "warning": "Query is too broad for targeted search.",
                "hint": "Add organism, sequencing type, and analysis goal to refine results.",
            }
        ], indent=2)

    # ── Dynamic Negative Constraint Detection ──
    negated_tokens = set()
    neg_matches = re.findall(r'\b(?:without|no|not|exclude|excluding|except|do\s+not\s+use|dont\s+use)\s+([a-zA-Z0-9_\-\s,]+?)(?:\.|$|;|\buse\b|\bwith\b|\band\b)', query_lower)
    for match in neg_matches:
        for word in re.split(r'[^a-z0-9]', match):
            if len(word) > 2 and word not in ("and", "the", "use", "using", "for", "with"):
                negated_tokens.add(word)

    def _is_negated(item_id: str, tool_name: str = "") -> bool:
        if not negated_tokens:
            return False
        combined = f"{item_id.lower()} {tool_name.lower()}"
        return any(neg in combined for neg in negated_tokens)

    # ── Keyword: Template Scan ──
    try:
        for tmpl in store.search(("templates",), limit=settings.SEARCH_SCAN_LIMIT):
            tmpl_id = tmpl.key.lower()
            if _is_negated(tmpl_id):
                continue
            tmpl_data = tmpl.value
            score = 0

            clean_id_words = tmpl_id.replace("_", " ").split()
            for id_word in clean_id_words:
                if len(id_word) > 3 and id_word in query_tokens and id_word not in IGNORE_WORDS:
                    score += 8

            for kw in (tmpl_data.get('keywords') or []):
                if str(kw).lower() in query_tokens:
                    score += 5

            for st in (tmpl_data.get('compatible_seq_types') or []):
                if str(st).lower().replace('_', ' ') in query_lower:
                    score += 3

            if score >= settings.RAG_KEYWORD_TEMPLATE_MIN_SCORE and tmpl.key not in excluded_templates:
                found_ids.add(tmpl.key)
                results.append({
                    "id": tmpl.key,
                    "type": "template",
                    "description": str(tmpl_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_TMPL],
                    "inputs": tmpl_data.get("input_channels") or [],
                    "outputs": tmpl_data.get("output_channels") or [],
                    "components_used": tmpl_data.get("components_used") or [],
                    "_score": score,
                })
    except Exception as e:
        logger.error(f"--- [NODE] CONSULTANT TOOL ERROR template scan error: {e}")

    # ── Keyword: Component Scan ──
    try:
        component_scores = {}
        for comp in store.search(("components",), limit=settings.SEARCH_SCAN_LIMIT):
            comp_id = comp.key.lower()
            comp_data = comp.value
            tool_name = str(comp_data.get('tool', '')).lower()
            if _is_negated(comp_id, tool_name):
                continue
            score = 0
            domain_name = str(comp_data.get('domain', '')).lower()

            if '__' in comp_id:
                suffix = comp_id.split('__')[-1]
                for sw in suffix.split('_'):
                    if sw and len(sw) > 3 and sw in query_tokens and sw not in IGNORE_WORDS:
                        score += 50

            if tool_name:
                for word in re.split(r'[^a-z0-9]', tool_name):
                    if len(word) > 3 and word in query_tokens and word not in IGNORE_WORDS:
                        score += 50

            if domain_name:
                for part in re.split(r'[^a-z0-9]', domain_name):
                    if len(part) > 3 and part in query_tokens and part not in IGNORE_WORDS:
                        score += 5

            for st in (comp_data.get('compatible_seq_types') or []):
                for st_word in str(st).lower().replace('_', ' ').split():
                    if st_word and len(st_word) > 3 and st_word in query_tokens:
                        score += 5

            # Keywords defined in catalog components.json
            for kw in (comp_data.get('keywords') or []):
                for kw_word in str(kw).lower().replace('_', ' ').split():
                    if kw_word and len(kw_word) > 3 and kw_word in query_tokens and kw_word not in IGNORE_WORDS:
                        score += 25

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
                component_scores[comp.key] = (score, comp_data)

        if component_scores:
            max_score = max(s for s, _ in component_scores.values())
            threshold = max_score * settings.RAG_KEYWORD_COMPONENT_THRESHOLD
            for comp_key, (score, comp_data) in sorted(component_scores.items(), key=lambda x: x[1][0], reverse=True):
                if score >= threshold and comp_key not in found_ids:
                    found_ids.add(comp_key)
                    results.append({
                        "id": comp_key,
                        "type": "component",
                        "tool": comp_data.get("tool"),
                        "domain": comp_data.get("domain"),
                        "description": str(comp_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                        "inputs": comp_data.get("input_channels") or comp_data.get("input_types") or [],
                        "outputs": comp_data.get("output_channels") or comp_data.get("out") or [],
                        "_score": score,
                    })

        # ── Dynamic Multi-Intent Conjunction Direct AST Projection ──
        from core.services.knowledge_graph import kg
        if not kg.is_built:
            kg.build_nx_graph(store)

        sub_intents = kg.decompose_conjunction_query(query)
        if len(sub_intents) > 1:
            for sub_q in sub_intents:
                v_proj = kg.project_vertex(sub_q)
                if v_proj and v_proj not in found_ids:
                    comp_item = store.get(("components",), v_proj)
                    if comp_item and comp_item.value:
                        found_ids.add(v_proj)
                        c_val = comp_item.value
                        results.append({
                            "id": v_proj,
                            "type": "component",
                            "tool": c_val.get("tool"),
                            "domain": c_val.get("domain"),
                            "description": str(c_val.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                            "inputs": c_val.get("input_channels") or c_val.get("input_types") or [],
                            "outputs": c_val.get("output_channels") or c_val.get("out") or [],
                            "_score": 90,
                        })
                    else:
                        tmpl_item = store.get(("templates",), v_proj)
                        if tmpl_item and tmpl_item.value:
                            found_ids.add(v_proj)
                            t_val = tmpl_item.value
                            results.append({
                                "id": v_proj,
                                "type": "template",
                                "description": str(t_val.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_TMPL],
                                "components_used": t_val.get("components_used") or [],
                                "_score": 90,
                            })
    except Exception as e:
        logger.error(f"--- [NODE] CONSULTANT TOOL ERROR component scan error: {e}")

    # ── Semantic Search (FAISS) ──
    try:
        if not data_loader.vector_store:
            warnings.append("Vector store not loaded; semantic search skipped.")
        if data_loader.vector_store:
            semantic_query = build_semantic_query(clean_query, query_tokens)
            if not semantic_query:
                warnings.append("Semantic search skipped because the query was empty after normalization.")
                semantic_query = clean_query

            with _faiss_lock:
                docs_and_scores = data_loader.vector_store.similarity_search_with_score(
                semantic_query, k=settings.RAG_FAISS_K
            )

            if docs_and_scores:
                best_l2 = docs_and_scores[0][1]
                for doc, l2_dist in docs_and_scores:
                    if l2_dist > settings.RAG_FAISS_MAX_L2_DISTANCE:
                        continue
                    if l2_dist > (best_l2 + settings.RAG_FAISS_RELATIVE_MARGIN):
                        continue

                    meta = doc.metadata
                    item_id = meta.get('id')
                    item_type = meta.get('type')

                    if item_id in excluded_templates or _is_negated(item_id, meta.get('tool', '')):
                        continue
                    # Allow duplicates in results so RRF can see both lists
                    found_ids.add(item_id)

                    if item_type == 'template':
                        tmpl_item = store.get(("templates",), item_id)
                        if tmpl_item:
                            tmpl_data = tmpl_item.value
                            results.append({
                                "id": item_id,
                                "type": "template",
                                "description": str(tmpl_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_TMPL],
                                "components_used": tmpl_data.get("components_used") or [],
                                "_score": 0,
                                "_semantic": True,
                            })
                    elif item_type == 'component':
                        comp_item = store.get(("components",), item_id)
                        if comp_item:
                            comp_data = comp_item.value
                            results.append({
                                "id": item_id,
                                "type": "component",
                                "tool": comp_data.get("tool"),
                                "domain": comp_data.get("domain"),
                                "description": str(comp_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                                "inputs": comp_data.get("input_channels") or comp_data.get("input_types") or [],
                                "outputs": comp_data.get("output_channels") or comp_data.get("out") or [],
                                "_score": 0,
                                "_semantic": True,
                            })
    except Exception as e:
        warnings.append("Semantic search failed; returning keyword matches only.")
        logger.error(f"--- [NODE] CONSULTANT TOOL ERROR faiss search error: {e}")

    # Sort keyword hits first (by score), then semantic hits
    results.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # Clean internal scoring fields
    for r in results:
        r.pop("_score", None)
        r.pop("_semantic", None)

    # ── RRF Merging ──
    # Split into keyword and semantic lists
    keyword_ranked = []
    semantic_ranked = []
    
    # Sort them by their original metrics
    kw_results = [r for r in results if not r.get("_semantic")]
    kw_results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    keyword_ranked = [r["id"] for r in kw_results]
    
    sem_results = [r for r in results if r.get("_semantic")]
    sem_results.sort(key=lambda x: x.get("_score", 999.0)) # L2 distance, lower is better
    semantic_ranked = [r["id"] for r in sem_results]
    
    RRF_K = 60
    MIN_RRF = 0.008
    all_ids = dict.fromkeys(keyword_ranked + semantic_ranked)
    
    rrf_scores = {}
    for pid in all_ids:
        kw_pos = keyword_ranked.index(pid) if pid in keyword_ranked else len(keyword_ranked)
        sem_pos = semantic_ranked.index(pid) if pid in semantic_ranked else len(semantic_ranked)
        rrf_scores[pid] = 1.0 / (RRF_K + kw_pos) + 1.0 / (RRF_K + sem_pos)
        
    merged_ids = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
    
    # Build final results based on RRF rank
    results_map = {r["id"]: r for r in results}
    final_results = []
    
    from core.services.knowledge_graph import kg
    if input_channel or output_channel:
        if not kg.is_built:
            store = runtime.store
            kg.build_graph(store)

    for pid in merged_ids:
        if rrf_scores[pid] >= MIN_RRF:
            r = results_map[pid]
            # Graph RAG Constraint Filtering
            if input_channel and not kg.find_path(input_channel, pid, runtime.store):
                continue
            if output_channel and not kg.find_path(pid, output_channel, runtime.store):
                continue
            r.pop("_score", None)
            r.pop("_semantic", None)
            final_results.append(r)
            
    # Apply limit and offset
    final_results = final_results[offset:offset+limit]

    # ── Knowledge Graph BFS Depth=1 Discovery ──
    if kg.is_built and final_results:
        existing_ids = {r.get("id") for r in final_results if isinstance(r, dict)}
        discovered = []
        for r in final_results[:5]:
            hit_id = r.get("id")
            if hit_id and hit_id in kg.G:
                for succ in kg.G.successors(hit_id):
                    if succ not in existing_ids and succ not in [d.get("id") for d in discovered]:
                        edata = kg.G[hit_id][succ]
                        conf = edata.get("confidence", "AMBIGUOUS")
                        if conf in ("EXTRACTED", "INFERRED"):
                            comp_item = store.get(("components",), succ) or store.get(("templates",), succ)
                            if comp_item and comp_item.value:
                                c_val = comp_item.value
                                discovered.append({
                                    "id": succ,
                                    "type": "component" if store.get(("components",), succ) else "template",
                                    "tool": c_val.get("tool", succ),
                                    "description": str(c_val.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                                    "inputs": c_val.get("input_channels") or c_val.get("input_types") or [],
                                    "outputs": c_val.get("output_channels") or c_val.get("out") or [],
                                    "discovered_via_graph": f"{hit_id} ({conf})",
                                })
        if discovered:
            final_results.extend(discovered[:3])
                
    if warnings:
        final_results.append({"type": "meta", "warnings": warnings})
    return json.dumps(final_results, indent=2)



# ──────────────────────────────────────────────────────────────────────────────
# TOOL 5: Check channel compatibility between two components
# ──────────────────────────────────────────────────────────────────────────────

def _parse_nextflow_channels(code: str) -> dict:
    """Helper: parses a Nextflow DSL2 source code block for its take/emit channels."""
    takes = []
    emits = []
    
    # First isolate the NAMED workflow block to avoid the anonymous entrypoint
    workflow_match = re.search(r'\bworkflow\s+([_a-zA-Z0-9]+)\s*\{(.*)', code, re.DOTALL)
    workflow_body = workflow_match.group(2) if workflow_match else code

    # Parse take: block — lines after "take:" until "main:" or "emit:" or "\n}"
    take_match = re.search(
        r'\btake\s*:(.*?)(?=\bmain\s*:|\bemit\s*:|\n\s*\}|$)',
        workflow_body, re.DOTALL
    )
    if take_match:
        for line in take_match.group(1).split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                # Extract first token (the channel name)
                name = re.split(r'\s', line)[0]
                takes.append(name)

    # Parse emit: block — lines after "emit:" until "\n}" or next top-level block
    emit_match = re.search(
        r'\bemit\s*:(.*?)(?=\n\s*\}|\n\s*\bworkflow\b|\n\s*\bprocess\b|$)',
        workflow_body, re.DOTALL
    )
    if emit_match:
        for line in emit_match.group(1).split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                name = re.split(r'\s|=', line)[0]
                emits.append(name)

    return {"takes": takes, "emits": emits, "partial": False}


def _parse_include_statements(code: str) -> list:
    """Parse 'include { proc_xxx } from ...' statements from Nextflow code.
    Returns list of included component IDs.
    """
    includes = []
    if not code:
        return includes

    # Match: include { proc_xxx } from '...'  or  include { proc_xxx; proc_yyy } from '...'
    for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", code):
        block = match.group(1)
        for item in block.split(';'):
            item = item.strip()
            # Handle "proc_xxx as alias" patterns
            name = item.split(' as ')[0].strip() if ' as ' in item else item.strip()
            # Only keep identifiers that look like proc_/mod_/multiproc_ IDs or function names
            if name and re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
                includes.append(name)

    return includes



# ──────────────────────────────────────────────────────────────────────────────
# TOOL 6 Validate a complete pipeline plan
# ──────────────────────────────────────────────────────────────────────────────

@tool
def check_plan_logic(component_ids: list, template_id: str, runtime: ToolRuntime) -> str:  # noqa: C901
    """Validate that the components selected exist and that the plan aligns with a reference template.
    Use this as a final sanity check before writing your formal plan.

    Args:
        component_ids: List of component IDs you plan to use.
        template_id: The base template ID you are using for design inspiration.
    """
    store = runtime.store
    issues = []
    warnings = []

    logger.info(f"--- [NODE] CONSULTANT TOOL check plan logic for {len(component_ids)} components with template {template_id}")

    if not component_ids:
        logger.info("--- [NODE] CONSULTANT TOOL no component ids provided")
        return json.dumps({
            "valid": False,
            "issues": ["No component IDs provided"],
            "warnings": [],
        }, indent=2)

    # we check if all ids are real
    valid_ids = []
    invalid_ids = []
    for comp_id in component_ids:
        comp = store.get(("components",), comp_id)
        tmpl = store.get(("templates",), comp_id) if not comp else None
        if comp or tmpl:
            valid_ids.append(comp_id)
        else:
            invalid_ids.append(comp_id)
            issues.append(f"Component '{comp_id}' not found in catalog")

    logger.info(f"--- [NODE] CONSULTANT TOOL found {len(invalid_ids)} invalid ids")

    # ── Topological Knowledge Graph Validation ──
    from core.services.knowledge_graph import kg
    if not kg.is_built:
        kg.build_nx_graph(store)

    topological_order = valid_ids
    if kg.is_built and valid_ids:
        try:
            import networkx as nx
            subgraph_nodes = set(valid_ids)
            sub_g = kg.nx_graph.subgraph(subgraph_nodes)
            if nx.is_directed_acyclic_graph(sub_g):
                topological_order = list(nx.topological_sort(sub_g))
        except Exception:
            pass

    # we look at how the data flows between the steps
    channel_report = []
    for i in range(len(valid_ids) - 1):
        src_id = valid_ids[i]
        tgt_id = valid_ids[i + 1]

        # we get the channel info from the catalog
        src_comp = store.get(("components",), src_id)
        tgt_comp = store.get(("components",), tgt_id)

        src_outputs = []
        tgt_inputs = []

        if src_comp:
            src_data = src_comp.value or {}
            src_outputs = src_data.get("output_channels") or src_data.get("out") or []
        else:
            src_tmpl = store.get(("templates",), src_id)
            if src_tmpl:
                src_outputs = (src_tmpl.value or {}).get("output_channels") or []

        if tgt_comp:
            tgt_data = tgt_comp.value or {}
            tgt_inputs = tgt_data.get("input_channels") or tgt_data.get("input_types") or []
        else:
            tgt_tmpl = store.get(("templates",), tgt_id)
            if tgt_tmpl:
                tgt_inputs = (tgt_tmpl.value or {}).get("input_channels") or []

        # we read the code to see the exact channels
        src_code_item = store.get(("code",), src_id)
        tgt_code_item = store.get(("code",), tgt_id)

        src_code = src_code_item.value.get("content", "") if src_code_item else ""
        tgt_code = tgt_code_item.value.get("content", "") if tgt_code_item else ""

        src_parsed = _parse_nextflow_channels(src_code)
        tgt_parsed = _parse_nextflow_channels(tgt_code)

        effective_outputs = src_parsed["emits"] if src_parsed["emits"] else src_outputs
        effective_inputs = tgt_parsed["takes"] if tgt_parsed["takes"] else tgt_inputs

        if src_parsed["partial"] or tgt_parsed["partial"]:
            warnings.append(f"Code for '{src_id}' to '{tgt_id}' may be partial so channel analysis is approximate")

        pair_info = {
            "source": src_id,
            "target": tgt_id,
            "source_emits": effective_outputs,
            "target_takes": effective_inputs,
        }

        # Knowledge Graph edge confidence
        if kg.is_built and kg.G.has_edge(src_id, tgt_id):
            edata = kg.G[src_id][tgt_id]
            pair_info["graph_edge_confidence"] = edata.get("confidence", "AMBIGUOUS")

        if not effective_outputs:
            warnings.append(f"No output channels detected for '{src_id}' so we cannot verify connection to '{tgt_id}'")
        elif not effective_inputs:
            warnings.append(f"No input channels detected for '{tgt_id}' so we cannot verify connection from '{src_id}'")
        else:
            # we check if the channels overlap
            out_lower = {ch.lower() for ch in effective_outputs}
            in_lower = {ch.lower() for ch in effective_inputs}
            if not (out_lower & in_lower):
                pair_info["mismatch"] = True
                warnings.append(
                    f"Channel mismatch. '{src_id}' emits {effective_outputs} "
                    f"but '{tgt_id}' takes {effective_inputs}. this may need channel adaptation"
                )

        channel_report.append(pair_info)

    # we compare the plan with the template
    template_coverage = None
    if template_id:
        logger.info(f"--- [NODE] CONSULTANT TOOL comparing plan with template {template_id}")
        tmpl_item = store.get(("templates",), template_id)
        if not tmpl_item:
            issues.append(f"Template '{template_id}' not found in catalog")
        else:
            tmpl_data = tmpl_item.value or {}
            tmpl_steps = set(tmpl_data.get("components_used") or [])
            plan_steps = set(component_ids)

            missing_from_plan = tmpl_steps - plan_steps
            extra_in_plan = plan_steps - tmpl_steps

            # we also look at the include lines in the template code
            code_item = store.get(("code",), template_id)
            includes_from_code = []
            if code_item:
                tmpl_code = code_item.value.get("content", "")
                includes_from_code = _parse_include_statements(tmpl_code)

                # we only keep the includes that are recognized components in the catalog
                from core.catalog_registry import get_registry
                registry = get_registry()
                code_steps = {inc for inc in includes_from_code if registry.component_exists(inc)}

                # steps we found in the code but not in the catalog list
                code_only = code_steps - tmpl_steps
                if code_only:
                    warnings.append(
                        f"Template code includes {list(code_only)} which are not in "
                        f"the template components_used list. they may be helper dependencies"
                    )

                # steps we found in the code but not in our plan
                code_missing = code_steps - plan_steps
                if code_missing:
                    warnings.append(
                        f"Template code references {list(code_missing)} which are "
                        f"not in your plan. please verify these are not needed"
                    )
            else:
                warnings.append(f"No source code found for template '{template_id}'. include analysis skipped")

            template_coverage = {
                "template_id": template_id,
                "template_steps": list(tmpl_steps),
                "plan_steps": list(plan_steps),
                "missing_from_plan": list(missing_from_plan),
                "extra_in_plan": list(extra_in_plan),
                "code_includes": includes_from_code,
            }

            if missing_from_plan:
                warnings.append(
                    f"Plan is missing template steps {list(missing_from_plan)}. "
                    f"these may be needed for correct pipeline logic"
                )

    is_valid = len(issues) == 0
    logger.info(f"--- [NODE] CONSULTANT TOOL check plan logic finished. valid is {is_valid}")

    result = {
        "valid": is_valid,
        "checked_ids": len(component_ids),
        "valid_ids": valid_ids,
        "invalid_ids": invalid_ids,
        "channel_flow": channel_report,
        "issues": issues,
        "warnings": warnings,
    }

    if template_coverage:
        result["template_coverage"] = template_coverage

    return json.dumps(result, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# TOOL 7: Find which templates use a component (reverse lookup)
# ──────────────────────────────────────────────────────────────────────────────

@tool
def find_component_usage(component_id: str, runtime: ToolRuntime) -> str:
    """Find real examples of how a specific component is used inside existing templates.
    Use this to see what channels it typically takes, what helpers it uses, etc.

    Args:
        component_id: The exact ID of the component (e.g. 'process_my_component')
    """
    store = runtime.store
    # Check the component exists first
    comp = store.get(("components",), component_id)
    tmpl = store.get(("templates",), component_id) if not comp else None
    if not comp and not tmpl:
        return json.dumps({"error": f"Component '{component_id}' not found in catalog"}, indent=2)

    # Look up the pre-built usage index
    usage_item = store.get(("usage",), component_id)
    if not usage_item:
        return json.dumps({
            "component_id": component_id,
            "used_in_templates": [],
            "note": "This component is not used in any template. You will need to wire it based on its take/emit channels."
        }, indent=2)

    usages = usage_item.value.get("usages", [])
    logger.info(f"--- [NODE] CONSULTANT TOOL find_component_usage: {component_id} found in {len(usages)} templates")

    results = []
    for u in usages:
        results.append({
            "template_id": u["template_id"],
            "template_description": u.get("template_description", ""),
            "usage_snippet": u.get("snippet", "(no snippet)"),
        })

    return json.dumps({
        "component_id": component_id,
        "used_in_templates": results,
        "count": len(results),
    }, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 8: Search helper functions (architect + consultant shared)
# ──────────────────────────────────────────────────────────────────────────────

@tool
def search_helper_functions(query: str, runtime: ToolRuntime) -> str:
    """Search for available helper functions by keyword (e.g., 'input', 'reference', 'metadata').
    Use this to find built-in data retrieval and formatting functions.

    Args:
        query: Search terms describing the helper function (e.g., 'retrieve fastq', 'parse riscd').
    """
    import json
    store = runtime.store
    res_item = store.get(("resources",), "helper_functions")
    res_list = res_item.value.get("list", []) if res_item else []

    if not res_list:
        return json.dumps([{"error": "No helper functions found in the catalog."}], indent=2)

    def dice_coefficient(a: str, b: str) -> float:
        a, b = a.lower(), b.lower()
        if not a or not b: return 0.0
        if a == b: return 1.0
        if len(a) == 1 or len(b) == 1: return 1.0 if a == b else 0.0
        a_bigrams = {a[i:i+2] for i in range(len(a)-1)}
        b_bigrams = {b[i:i+2] for i in range(len(b)-1)}
        overlap = len(a_bigrams & b_bigrams)
        return overlap * 2.0 / (len(a_bigrams) + len(b_bigrams))

    query_tokens = [q.lower() for q in re.split(r'[^a-z0-9]', query.lower()) if len(q) >= 2]
    if not query_tokens:
        return json.dumps([{"error": "Invalid search query."}], indent=2)

    results = []
    for h in res_list:
        score = 0.0
        name = h.get('name', '')
        desc = h.get('description', '')
        keywords = h.get('keywords', [])
        aliases = h.get('aliases', [])
        
        target_strings = [name, desc] + keywords + aliases
        
        for q in query_tokens:
            if q == name.lower():
                score += 10.0
            elif q in name.lower():
                score += 3.0
            best_dice = max((dice_coefficient(q, t) for t in target_strings if t), default=0.0)
            score += best_dice
            # Bonus for exact substring match
            if any(q in t.lower() for t in target_strings if t):
                score += 0.5
                
        if score > 0.3:
            results.append((score, h))

    results.sort(key=lambda x: x[0], reverse=True)

    if not results:
        return json.dumps([{"warning": "No helper functions matched your query."}], indent=2)

    return json.dumps([
        {
            "name": h.get('name'),
            "description": h.get('description'),
            "usage": h.get('usage'),
            "num_args": h.get('num_args', 0),
            "arguments": h.get('arguments', [])
        } for _, h in results[:5]
    ], indent=2)


@tool
def search_design_patterns(query: str, runtime: ToolRuntime) -> str:
    """Search for domain-specific data-shaping design patterns using semantic and keyword hybrid search.
    Use this when you are unsure how to wire domain-specific components.

    Args:
        query: Search terms describing the pattern (e.g., 'host depletion', 'cross multiMap').
    """
    import json
    from core.loader import data_loader
    from core.services.query_normalizer import build_semantic_query, normalize_query

    store = runtime.store
    items = store.search(("patterns",))
    if not items:
        return json.dumps([{"error": "No design patterns found in the catalog."}], indent=2)

    query_info = normalize_query(query)
    query_tokens = query_info["query_tokens"]
    clean_query = query_info["clean_query"]
    
    if not query_tokens:
        return json.dumps([{"warning": "Invalid search query."}], indent=2)

    results = []
    
    # ── 1. Keyword Search (Exact Match) ──
    for item in items:
        p = item.value
        score = 0.0
        
        target_strings = [
            p.get('title', '').lower(),
            p.get('description', '').lower(),
            str(p.get('use_cases', '')).lower(),
            str(p.get('groovy_code', '')).lower()
        ]
        
        for q in query_tokens:
            if len(q) < 3: continue
            if any(q in t for t in target_strings):
                score += 5.0
                
        if score > 0:
            p["_score"] = score
            p["id"] = p.get("title", str(id(p)))
            results.append(p)
            
    # ── 2. FAISS Semantic Search ──
    if data_loader.vector_store:
        semantic_query = build_semantic_query(clean_query, query_tokens)
        if semantic_query:
            with _faiss_lock:
                docs_and_scores = data_loader.vector_store.similarity_search_with_score(
                    semantic_query, k=10
                )
            
            MAX_L2 = 1.0
            for doc, l2_dist in docs_and_scores:
                if l2_dist > MAX_L2: continue
                
                doc_type = doc.metadata.get("type")
                if doc_type != "patterns":
                    continue
                    
                doc_title = doc.metadata.get("id")
                matched_item = None
                for item in items:
                    if item.value.get("title") == doc_title:
                        matched_item = item.value
                        break
                        
                if matched_item:
                    sem_res = dict(matched_item)
                    sem_res["id"] = doc_title
                    sem_res["_score"] = l2_dist
                    sem_res["_semantic"] = True
                    results.append(sem_res)
                    
    # ── RRF Merging ──
    keyword_ranked = []
    semantic_ranked = []
    
    kw_results = [r for r in results if not r.get("_semantic")]
    kw_results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    keyword_ranked = [r["id"] for r in kw_results]
    
    sem_results = [r for r in results if r.get("_semantic")]
    sem_results.sort(key=lambda x: x.get("_score", 999.0))
    semantic_ranked = [r["id"] for r in sem_results]
    
    RRF_K = 60
    MIN_RRF = 0.008
    all_ids = dict.fromkeys(keyword_ranked + semantic_ranked)
    
    rrf_scores = {}
    for pid in all_ids:
        kw_pos = keyword_ranked.index(pid) if pid in keyword_ranked else len(keyword_ranked)
        sem_pos = semantic_ranked.index(pid) if pid in semantic_ranked else len(semantic_ranked)
        rrf_scores[pid] = 1.0 / (RRF_K + kw_pos) + 1.0 / (RRF_K + sem_pos)
        
    merged_ids = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
    
    results_map = {r["id"]: r for r in results}
    final_results = []
    for pid in merged_ids:
        if rrf_scores[pid] >= MIN_RRF:
            r = results_map[pid]
            r.pop("_score", None)
            r.pop("_semantic", None)
            r.pop("id", None)
            final_results.append(r)
            if len(final_results) >= 3:
                break
                
    if not final_results:
        return json.dumps([{"warning": "No design patterns matched your query."}], indent=2)
        
    return json.dumps(final_results, indent=2)



# ──────────────────────────────────────────────────────────────────────────────
# TOOL 7: Grep Codebase
# ──────────────────────────────────────────────────────────────────────────────

@tool
def grep_codebase(pattern: str, runtime: ToolRuntime) -> str:
    """Find pattern matches within the Nextflow templates and components.
    Use this to look for specific variable names, process calls, or groovy syntax.

    Args:
        pattern: The search term or pattern to look for.
    """
    import re
    import json
    from core.loader import data_loader

    try:
        regex = re.compile(pattern, re.IGNORECASE)
        matches = []

        # 1. Search in-memory code_db
        code_db = getattr(data_loader, "code_db", {})
        for name, content in code_db.items():
            if not isinstance(content, str):
                continue
            for line_no, line in enumerate(content.split("\n"), 1):
                if regex.search(line):
                    matches.append({
                        "file": f"{name}.nf",
                        "line": str(line_no),
                        "content": line.strip()
                    })
                    if len(matches) >= 50:
                        break
            if len(matches) >= 50:
                break

        if not matches:
            return json.dumps([{"warning": "No matches found."}], indent=2)

        return json.dumps(matches, indent=2)
    except Exception as e:
        return json.dumps([{"error": str(e)}], indent=2)

@tool
def auto_complete_pipeline_dag(component_ids: list, runtime: ToolRuntime) -> str:
    """Use the Knowledge Graph to automatically find the missing components needed to connect a list of disjoint components.
    
    Args:
        component_ids: A list of component IDs you want to connect.
    """
    import json
    from core.services.knowledge_graph import kg
    store = runtime.store
    
    if not kg.is_built:
        kg.build_graph(store)
        
    if not component_ids:
        return json.dumps({"error": "No component IDs provided."}, indent=2)
        
    if len(component_ids) == 1:
        return json.dumps({"warning": "Only one component provided. Nothing to connect."}, indent=2)
        
    issues = []
    result_path = [component_ids[0]]
    
    for i in range(len(component_ids) - 1):
        source = component_ids[i]
        target = component_ids[i+1]
        
        try:
            path = kg.find_path(source, target, store)
            if not path:
                issues.append(f"No logical path found between {source} and {target}")
                result_path.append(target)
            else:
                for p in path[1:]:
                    result_path.append(p)
        except Exception as e:
            issues.append(f"Error connecting {source} and {target}: {str(e)}")
            result_path.append(target)
            
    final_path = []
    for p in result_path:
        if not final_path or final_path[-1] != p:
            final_path.append(p)
            
    return json.dumps({
        "original_ids": component_ids,
        "completed_ids": final_path,
        "issues": issues,
        "auto_completed": len(final_path) > len(component_ids)
    }, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# EXPORT: Tool list for ToolNode registration
# ──────────────────────────────────────────────────────────────────────────────
# TOPOLOGICAL KNOWLEDGE GRAPH TOOLS: Query, Traversal, and Pathfinding
# ──────────────────────────────────────────────────────────────────────────────

@tool
def query_knowledge_graph(
    question: str,
    mode: str = "bfs",
    depth: int = 2,
    token_budget: int = 2000,
    confidence_filter: Optional[str] = None,
) -> str:
    """Execute natural-language structural search and topological traversal over the Nextflow Component Catalog.

    Returns an extracted subgraph of components and directed dataflow channels with confidence tiers:
      - EXTRACTED: Real Nextflow AST wiring from tool_graph.json
      - INFERRED: Co-occurrence in production pipeline templates
      - AMBIGUOUS: Channel name heuristic match

    Args:
        question: Natural language question or bioinformatic task (e.g. 'how to trim fastq and call variants with freebayes')
        mode: 'bfs' for broad exploration of alternative tools, 'dfs' for tracing a linear execution pipeline
        depth: Traversal hops (1 to 3, default 2)
        token_budget: Maximum tokens in formatted response (default 2000)
        confidence_filter: Optional comma-separated filter (e.g. 'EXTRACTED,INFERRED' to ignore ambiguous edges)
    """
    from core.services.knowledge_graph import kg

    if not kg.is_built:
        return json.dumps({"error": "Knowledge graph not yet built. Will be ready after initialization."})

    filters = [c.strip() for c in confidence_filter.split(",")] if confidence_filter else None
    return kg.query_graph(
        question=question,
        mode=mode,
        depth=depth,
        token_budget=token_budget,
        context_filters=filters,
    )


@tool
def search_component_graph(query: str, depth: int = 2) -> str:
    """Backward-compatible alias for query_knowledge_graph (BFS mode)."""
    return query_knowledge_graph(question=query, mode="bfs", depth=depth)


@tool
def explain_component(component_id_or_name: str) -> str:
    """Inspect full architectural details and all incoming/outgoing dataflow connections for a component.

    Use this when you want to verify what exact channels feed into a tool and what tools can consume its output.

    Args:
        component_id_or_name: Component ID (e.g. 'step_identifier') or tool name (e.g. 'tool_name')
    """
    from core.services.knowledge_graph import kg

    if not kg.is_built:
        return json.dumps({"error": "Knowledge graph not yet built."})

    return kg.explain_node(component_id_or_name)


@tool
def get_component_neighbors(
    component_id: str,
    direction: str = "both",
    relation_filter: Optional[str] = None,
) -> str:
    """Get direct upstream or downstream connected components in the dataflow graph.

    Args:
        component_id: The ID of the component to inspect
        direction: 'in' for upstream producers, 'out' for downstream consumers, or 'both'
        relation_filter: Optional filter ('dataflow', 'co_usage')
    """
    from core.services.knowledge_graph import kg

    if not kg.is_built:
        return json.dumps({"error": "Knowledge graph not yet built."})

    return kg.get_neighbors(component_id, direction=direction, relation_filter=relation_filter)


@tool
def get_community_components(community_id: int) -> str:
    """List all catalog components belonging to a functional subworkflow community/cluster.

    Args:
        community_id: Numeric community ID discovered from query_knowledge_graph or explain_component
    """
    from core.services.knowledge_graph import kg

    if not kg.is_built:
        return json.dumps({"error": "Knowledge graph not yet built."})

    return kg.get_community(community_id)


@tool
def get_catalog_god_nodes(top_n: int = 10) -> str:
    """List the most central, highly-connected tools in the catalog (god nodes / hubs).

    Args:
        top_n: Number of hubs to list (default 10)
    """
    from core.services.knowledge_graph import kg

    if not kg.is_built:
        return json.dumps({"error": "Knowledge graph not yet built."})

    return kg.get_god_nodes(top_n=top_n)


@tool
def find_dataflow_path(source_component: str, target_component: str, directed: bool = True) -> str:
    """Find the shortest dataflow path between two components in the structural knowledge graph.

    Prefers EXTRACTED edges (real Nextflow AST wiring) over INFERRED/AMBIGUOUS edges.

    Args:
        source_component: The ID or name of the producer component
        target_component: The ID or name of the consumer component
        directed: True to follow dataflow direction (source -> target), False for undirected
    """
    from core.services.knowledge_graph import kg

    if not kg.is_built:
        return json.dumps({"error": "Knowledge graph not yet built."})

    return kg.find_path_detailed(source_component, target_component, directed=directed)


# ──────────────────────────────────────────────────────────────────────────────

CONSULTANT_TOOLS = [
    search_components,
    lookup_components_batch,
    query_knowledge_graph,
    check_plan_logic,
    search_design_patterns,
    search_helper_functions,
]


