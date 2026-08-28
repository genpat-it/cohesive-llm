import re
from typing import Any

from langgraph.store.base import BaseStore

from core.services.graph_state import GraphState
from core.utils.logger import logger



def filter_template_logic(code: str, allowed_components: set) -> str:
    lines = code.split('\n')
    filtered_lines = []

    # Negative lookbehind (?<!\.) ensures we DO NOT match method calls like .view() or ch.join()
    # We only want to match standalone function/component calls like fastqc(ch)
    pattern = re.compile(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(')

    ignore_list = {
        'Channel', 'path', 'tuple', 'val', 'set', 'map', 'branch', 'multiMap', 'mix',
        'if', 'else', 'while', 'for', 'switch', 'catch', 'finally', 'def', 'return',
        'print', 'println', 'error', 'exit', 'include'
    }

    for line in lines:
        match = pattern.search(line)
        if match:
            func_name = match.group(1)
            if func_name not in ignore_list and func_name not in allowed_components:
                filtered_lines.append(f"    // [REMOVED BY PLAN] {line.strip()}")
                continue

        filtered_lines.append(line)

    return "\n".join(filtered_lines)


def _build_exact_match(used_template_id: str, context_parts: list[str], store: BaseStore) -> None:
    tmpl_item = store.get(("templates",), used_template_id)
    template_def = tmpl_item.value if tmpl_item else None

    context_parts.append(f"### STRICT TEMPLATE MODE: {used_template_id}")
    if template_def:
        context_parts.append(f"Description: {template_def.get('description')}")
        code_item = store.get(("code",), used_template_id)
        tmpl_code = code_item.value.get("content") if code_item else None

        if tmpl_code:
            context_parts.append(f"[[TEMPLATE SOURCE CODE: {used_template_id}]]")
            context_parts.append("INSTRUCTION: Use the logic in this workflow block exactly.")
            context_parts.append(f"```groovy\n{tmpl_code.strip()}\n```")
            context_parts.append("[[END TEMPLATE SOURCE]]")


def _build_adapted_match(used_template_id: str, component_ids: list[str], context_parts: list[str], store: BaseStore, helper_names: list[str]) -> None:
    context_parts.append(f"### ADAPTED TEMPLATE MODE: Based on {used_template_id}")
    t_item = store.get(("code",), used_template_id)
    tmpl_code = t_item.value.get("content") if t_item else None

    if tmpl_code:
        import re
        allowed_ids = {used_template_id, *component_ids, *helper_names}
        filtered_code = filter_template_logic(tmpl_code, allowed_ids)

        take_match = re.search(r'^\s*take:\s*([a-zA-Z0-9_,\s]+)(?=\s*main:|\s*emit:|\})', tmpl_code, flags=re.MULTILINE)
        if take_match:
            takes = [t.strip() for t in take_match.group(1).split() if t.strip()]
            if takes:
                context_parts.append(
                    f"WARNING: The template below defines 'take:' parameters ({', '.join(takes)}). "
                    f"If you adapt this logic inline into the main entrypoint, you MUST explicitly fetch "
                    f"or define these inputs first (e.g. `{takes[0]} = getSingleInput()`). DO NOT use them undefined!"
                )

        context_parts.append(f"[[TEMPLATE SOURCE CODE: {used_template_id}]]")
        context_parts.append("INFO: Some steps in this template have been commented out because they are not in your Design Plan.")
        context_parts.append("INSTRUCTION: Reuse the logic that remains, but FILL THE GAPS using your new components.")
        context_parts.append(f"```groovy\n{filtered_code.strip()}\n```")


def _build_custom_match(component_ids: list[str], context_parts: list[str], store: BaseStore) -> None:
    context_parts.append("### CUSTOM BUILD MODE")
    context_parts.append("INSTRUCTION: You must build the logic entirely from scratch using the provided components.")


def hydrator_node(state: GraphState, store: BaseStore) -> Any:
    logger.info("node_start", node="hydrator")

    if state.get("error"):
        return {"error": state["error"]}

    context_parts = []

    strategy = state.get('strategy_selector', 'CUSTOM_BUILD')
    used_template_id = state.get('used_template_id')
    component_ids = state.get('selected_component_ids', [])
    plan_text = state.get('design_plan', '')

    res_item = store.get(("resources",), "helper_functions")
    res_list = res_item.value.get("list", []) if res_item else []
    helper_names = [r['name'] for r in res_list]

    if strategy == "EXACT_MATCH" and used_template_id:
        _build_exact_match(used_template_id, context_parts, store)
    elif strategy == "ADAPTED_MATCH" and used_template_id:
        _build_adapted_match(used_template_id, component_ids, context_parts, store, helper_names)
    else:
        _build_custom_match(component_ids, context_parts, store)

    resolved_ids = []
    
    if component_ids:
        context_parts.append("### EXACT COMPONENT SCHEMAS")
        context_parts.append("Use the following deterministic input/output signatures to build the pipeline correctly. You do not need to use check_component_channels for these components:")
        
        from core.loader import data_loader
        
        for node_id in component_ids:
            comp_item = store.get(("components",), node_id)
            if comp_item and comp_item.value:
                if node_id not in resolved_ids:
                    resolved_ids.append(node_id)
            else:
                logger.warning(f"Hydrator: Hallucinated ID '{node_id}'. Attempting direct FAISS resolution...")
                try:
                    if data_loader.vector_store:
                        # Direct FAISS search without strict L2 distance thresholds
                        docs_and_scores = data_loader.vector_store.similarity_search_with_score(node_id, k=5)
                        
                        # Filter to components only
                        valid_matches = []
                        for doc, score in docs_and_scores:
                            meta = doc.metadata
                            if meta.get("namespace") == "component":
                                valid_matches.append(meta.get("id"))
                                
                        if valid_matches:
                            # Take top 2 matches to handle ambiguity but prevent prompt explosion
                            top_matches = valid_matches[:2]
                            logger.info(f"Hydrator: Semantically resolved '{node_id}' to {top_matches}")
                            for match_id in top_matches:
                                if match_id not in resolved_ids:
                                    resolved_ids.append(match_id)
                        else:
                            logger.error(f"Hydrator: Direct FAISS yielded no components for '{node_id}'")
                            if node_id not in resolved_ids:
                                resolved_ids.append(node_id)
                    else:
                        logger.error(f"Hydrator: Vector store not loaded, cannot resolve '{node_id}'")
                        if node_id not in resolved_ids:
                            resolved_ids.append(node_id)
                except Exception as e:
                    logger.error(f"Hydrator: FAISS resolution failed for '{node_id}': {e}")
                    if node_id not in resolved_ids:
                        resolved_ids.append(node_id)
                        
        for match_id in resolved_ids:
            match_item = store.get(("components",), match_id)
            if match_item and match_item.value:
                data = match_item.value
                inputs = data.get("input_channels") or data.get("input_types") or []
                raw_outputs = data.get("output_channels") or data.get("out") or []
                outputs = [f"{match_id}.out.{o}" for o in raw_outputs] if raw_outputs else []
                context_parts.append(
                    f"<component id='{match_id}'>\n"
                    f"  <inputs>{', '.join(inputs) if inputs else 'none'}</inputs>\n"
                    f"  <outputs>{', '.join(outputs) if outputs else 'none'}</outputs>\n"
                    f"</component>"
                )

        # ── Graphify Topological Blueprint for Architect ──
        try:
            from core.services.knowledge_graph import kg
            if not kg.is_built and store:
                kg.build_nx_graph(store)
            if kg.is_built and resolved_ids:
                blueprint = kg.generate_architect_blueprint(resolved_ids, store)
                if blueprint:
                    context_parts.append(blueprint)
        except Exception as e:
            logger.warning(f"Hydrator: Failed to generate Graphify blueprint: {e}")

    # Compile the final technical context
    full_context = "\n\n".join(context_parts)

    return {
        "technical_context": full_context,
        "selected_component_ids": resolved_ids if resolved_ids else component_ids
    }
