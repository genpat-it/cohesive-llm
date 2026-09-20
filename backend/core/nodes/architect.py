import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.store.base import BaseStore

from core.config import settings
from core.models.ast_structure import NextflowPipelineAST
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_architect_prompt
from core.utils.logger import logger

ARCHITECT_SYSTEM_PROMPT = load_architect_prompt()




def architect_generate_node(state: GraphState) -> Any:
    """Generate a NextflowPipelineAST from the LLM.
    The KG wireframe is injected as context in technical_context by precheck — the LLM
    always runs and is the sole AST producer. No kg_ast bypass, no repair loop.
    """
    logger.info("node_start", node="architect_generate")
    if state.get("error"):
        return {"error": state['error']}

    llm = get_llm()
    architect_agent = llm.with_structured_output(NextflowPipelineAST, method="json_schema", include_raw=True)

    plan_text = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    # [Middle / Static Reference]: TECHNICAL CONTEXT + APPROVED PLAN + APPROVED COMPONENTS at front
    selected_ids = state.get("selected_component_ids", [])
    selected_clause = ""
    if selected_ids:
        selected_clause = f"\n\n### MANDATORY APPROVED COMPONENTS:\nYou MUST instantiate EXACTLY these {len(selected_ids)} approved components in the pipeline AST (do NOT substitute with other tools):\n" + "\n".join(f"- `{cid}`" for cid in selected_ids)

    visual_topology = state.get("visual_topology")
    topology_clause = ""
    if visual_topology and isinstance(visual_topology, dict):
        wires = visual_topology.get("wires", [])
        if wires:
            topology_clause = (
                "\n\n### MANDATORY VISUAL CANVAS TOPOLOGY & CHANNEL WIRING:\n"
                "Connect processes matching this explicit visual DAG wiring:\n"
            )
            for w in wires:
                src_cid = w.get("source_id")
                tgt_cid = w.get("target_id")
                src_ch = w.get("source_channel") or w.get("source_port", "out")
                tgt_ch = w.get("target_channel") or w.get("target_port", "in")
                topology_clause += f"- `{src_cid}` (out: `{src_ch}`) -> `{tgt_cid}` (in: `{tgt_ch}`)\n"

    directives_clause = (
        "\n\n### CRITICAL BIOLOGICAL DATAFLOW RULES (MUST FOLLOW):\n"
        "1. Sequential Preprocessing & Assembly: QC and trimming consume `rawreads` and produce `trimmed`. Read-based classification, screening, and de novo assembly consume `trimmed`.\n"
        "2. Species-Aware Stream Crossing: Pair assembled contigs with species identification using standard Nextflow DSL2 `.cross()` and `.multiMap{}` before typing tools.\n"
        "3. Assembly & Downstream Profiling: De novo assembly produces `assembly`. Downstream species identification, AMR screening, plasmid typing, gene annotation, and typing consume `assembly`.\n"
        "4. Cohort Clustering & Multi-Sample Aggregation: Aggregate sample-level intermediate channels using `.collect()` before invoking multi-sample cohort analysis tools.\n"
    )

    human_msg = (
        f"### TECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}\n\n"
        f"### APPROVED PLAN:\n{plan_text}"
        f"{selected_clause}"
        f"{topology_clause}"
        f"{directives_clause}"
    )

    gen_messages = [
        SystemMessage(content=ARCHITECT_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ]

    raw_ast = {}
    raw_msg = None

    try:
        response_dict = architect_agent.invoke(gen_messages)
        parsed = response_dict.get("parsed") if isinstance(response_dict, dict) else response_dict
        raw_msg = response_dict.get("raw") if isinstance(response_dict, dict) else None

        def _ensure_valid_ast_emits(ast_obj: NextflowPipelineAST) -> NextflowPipelineAST:
            if ast_obj and ast_obj.sub_workflows:
                for sw in ast_obj.sub_workflows:
                    if not sw.emit_channels:
                        lines = [l.strip() for l in sw.body_code.splitlines() if l.strip() and not l.strip().startswith("//")]
                        last_ident = None
                        for l in reversed(lines):
                            if "=" in l:
                                lhs = l.split("=")[0].strip()
                                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', lhs):
                                    last_ident = lhs
                                    break
                            elif "(" in l:
                                proc_match = re.match(r'^([a-zA-Z0-9_]+)\s*\(', l)
                                if proc_match:
                                    last_ident = f"{proc_match.group(1)}.out"
                                    break
                        sw.emit_channels = [f"final_result = {last_ident or 'rawreads'}"]
            return ast_obj

        if parsed is not None:
            if parsed.sub_workflows or (parsed.entrypoint and parsed.entrypoint.body_code.strip() not in ('', '// Generated entrypoint')):
                parsed = _ensure_valid_ast_emits(parsed)
                logger.info("architect_generate_success")
                return {
                    "ast_json": parsed.model_dump(),
                    "validation_error": None
                }

        # Fallback to raw message parsing
        if raw_msg:
            if getattr(raw_msg, "tool_calls", None) and raw_msg.tool_calls:
                tc = raw_msg.tool_calls[0]
                args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                if isinstance(args, str):
                    try:
                        raw_ast = json.loads(args)
                    except Exception:
                        raw_ast = {}
                elif isinstance(args, dict):
                    raw_ast = args

            if not raw_ast and hasattr(raw_msg, "content") and raw_msg.content:
                content = str(raw_msg.content).strip()
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                try:
                    raw_ast = json.loads(content)
                except Exception:
                    pass

        if raw_ast:
            if isinstance(raw_ast, str):
                raw_ast = json.loads(raw_ast)
            if isinstance(raw_ast, dict) and (raw_ast.get("sub_workflows") or raw_ast.get("entrypoint")):
                try:
                    validated = NextflowPipelineAST.model_validate(raw_ast)
                    if validated.sub_workflows or (validated.entrypoint and validated.entrypoint.body_code.strip() not in ('', '// Generated entrypoint')):
                        validated = _ensure_valid_ast_emits(validated)
                        logger.info("architect_generate_success_via_fallback")
                        return {
                            "ast_json": validated.model_dump(),
                            "validation_error": None
                        }
                except Exception:
                    pass

        parsing_err = response_dict.get("parsing_error") if isinstance(response_dict, dict) else None
        raise ValueError(f"Model returned invalid AST output: {parsing_err or 'No structured output received'}")
    except Exception as e:
        logger.error("architect_validation_failed", error=str(e))

        # Try to salvage any raw AST from tool calls or LLM output
        llm_output = getattr(e, "llm_output", None)
        if not raw_ast and raw_msg and getattr(raw_msg, "tool_calls", None) and raw_msg.tool_calls:
            tc = raw_msg.tool_calls[0]
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            if isinstance(args, str):
                try:
                    raw_ast = json.loads(args)
                except Exception:
                    pass
            elif isinstance(args, dict):
                raw_ast = args

        if not raw_ast and llm_output and isinstance(llm_output, str):
            try:
                content = llm_output
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                raw_ast = json.loads(content)
            except Exception:
                pass

        # Clean up error string
        error_str = str(e)
        error_str = re.sub(r'\[type=.*?, input_value=.*?input_type=dict\]', '', error_str, flags=re.DOTALL)
        error_str = re.sub(r'For further information visit https://errors\.pydantic\.dev/.*?$', '', error_str, flags=re.MULTILINE)

        # No repair loop — set validation_error for renderer to emit warning message
        return {
            "ast_json": raw_ast or {},
            "validation_error": error_str.strip()
        }


def _get_channels_for_component(mid: str, store: BaseStore | None = None) -> dict[str, list[str]]:
    """Helper to extract takes/emits dynamically, resolving the None-fallback bugs."""
    from core.services.consultant_tools import _parse_nextflow_channels

    code = ""
    if store is not None:
        try:
            code_item = store.get(("code",), mid)
            code = code_item.value.get("content", "") if code_item else ""
        except Exception:
            pass

    parsed = _parse_nextflow_channels(code) if code else {"takes": [], "emits": []}

    if not parsed.get("takes") and store is not None:
        try:
            meta = store.get(("components",), mid) or store.get(("templates",), mid)
            if meta and meta.value:
                parsed["takes"] = meta.value.get("input_channels", meta.value.get("input_types", [])) or []
        except Exception:
            pass

    if not parsed.get("emits") and store is not None:
        try:
            meta = store.get(("components",), mid) or store.get(("templates",), mid)
            if meta and meta.value:
                parsed["emits"] = meta.value.get("output_channels", meta.value.get("out", [])) or []
        except Exception:
            pass

    if not parsed.get("takes") or not parsed.get("emits"):
        try:
            from core.catalog_registry import get_registry
            reg = get_registry()
            sig = reg.get_component_signature(mid)
            if sig:
                if not parsed.get("takes"):
                    parsed["takes"] = sig.get("take_channels", sig.get("input_channels", [])) or []
                if not parsed.get("emits"):
                    parsed["emits"] = sig.get("emit_channels", sig.get("output_channels", [])) or []
        except Exception:
            pass

    takes = [c for c in parsed.get("takes", []) if c and c.lower() not in ("none", "void", "")]
    emits = [c for c in parsed.get("emits", []) if c and c.lower() not in ("none", "void", "")]
    return {"takes": takes, "emits": emits}


def architect_precheck_node(state: GraphState, store: BaseStore | None = None) -> Any:
    logger.info("node_start", node="architect_precheck")
    if state.get("error"):
        return {"error": state["error"]}

    component_ids = state.get("selected_component_ids", [])
    if not component_ids:
        logger.info("architect_precheck_skipped_no_components")
        return {}

    from core.services.ast_compiler import _is_void_tool

    warnings = []

    from core.services.knowledge_graph import kg
    if not kg.is_built and store is not None:
        kg.build_graph(store)

    # ── Channel mismatch check ───────────────────────────────────────────────
    for i in range(len(component_ids) - 1):
        src_id = component_ids[i]
        tgt_id = component_ids[i + 1]

        # Use Knowledge Graph to deterministically check path
        path = kg.find_path(src_id, tgt_id, store)
        
        src_parsed = _get_channels_for_component(src_id, store)
        tgt_parsed = _get_channels_for_component(tgt_id, store)
        src_lower = {ch.lower() for ch in src_parsed["emits"]}
        tgt_lower = {ch.lower() for ch in tgt_parsed["takes"]}

        if not path:
            if src_lower and tgt_lower and not (src_lower & tgt_lower) and len(tgt_parsed["takes"]) != 1:
                warnings.append(
                    f"MISMATCH {src_id} → {tgt_id}: emits {list(src_lower)}, takes {list(tgt_lower)}. "
                    "No valid graph path found. Use .map or rename to adapt."
                )

    # ── Void tool detection ──────────────────────────────────────────────────
    void_tools = [mid for mid in component_ids if _is_void_tool(mid)]
    if void_tools:
        warnings.append(f"VOID TOOLS (no output): {void_tools}. Call directly, no assignment, no emit.")

    # ── Missing code check ───────────────────────────────────────────────────
    if store is not None:
        missing_code = [mid for mid in component_ids if not store.get(("code",), mid)]
        if missing_code:
            warnings.append(f"NO SOURCE CODE: {missing_code}. Rely on catalog metadata for channel names.")

    # ── Assembly without preprocessing check ────────────────────────────────
    user_query = state.get("user_query", "").lower()
    has_assembly = any("denovo" in mid or "mapping" in mid for mid in component_ids)
    has_preprocessing = any("1pp_" in mid.lower() or "trimming" in mid.lower() for mid in component_ids)
    if "raw read" in user_query and has_assembly and not has_preprocessing:
        warnings.append(
            "WARNING: The user requested downstream processing from 'raw reads', but no preprocessing/trimming "
            "step is in the pipeline. Downstream tasks typically consume cleaned input."
        )

    # ── Template & Strategy Hydration ─────────────────────────────────────────
    strategy = state.get('strategy_selector', 'CUSTOM_BUILD')
    used_template_id = state.get('used_template_id')
    template_parts = []
    if used_template_id:
        t_item = store.get(("code",), used_template_id)
        tmpl_code = t_item.value.get("content") if t_item else None
        if tmpl_code:
            template_parts.append(f"### TEMPLATE BASE: {used_template_id}\n```groovy\n{tmpl_code.strip()}\n```")
    elif strategy == "CUSTOM_BUILD":
        template_parts.append("### STRATEGY: CUSTOM_BUILD (Synthesize pipeline modularly)")
    
    # ── Deterministic Helper Injection for Unmet Inputs ─────────────────────
    all_takes = set()
    all_emits = set()
    
    for mid in component_ids:
        parsed = _get_channels_for_component(mid, store)
        # Handle parsed takes correctly since they might have spaces
        for t in parsed.get("takes", []):
            if t and t.lower() not in ["none", ""]:
                all_takes.add(t.strip())
        for e in parsed.get("emits", []):
            if e and e.lower() not in ["none", ""]:
                all_emits.add(e.strip())
                
    unmet_takes = all_takes - all_emits
    
    helper_injections = []
    if unmet_takes:
        try:
            res_item = store.get(("resources",), "helper_functions")
            if res_item and res_item.value:
                helpers = res_item.value.get("list", [])
                
                for take in unmet_takes:
                    take_lower = take.lower()
                    # Score helpers: 10 if exact match in name, 5 if partial, 2 if in desc
                    scored = []
                    for h in helpers:
                        h_name = h.get("name", "")
                        h_desc = h.get("description", "")
                        h_lower = h_name.lower()
                        score = 0
                        if take_lower in h_lower:
                            score = 10 if take_lower == h_lower.replace("get", "") else 5
                        elif take_lower in h_desc.lower():
                            score = 2
                        else:
                            # the channel name never resembles the function name
                            # ('rawreads' vs 'getSingleInput'): without looking at the keywords
                            # no helper is ever proposed, and the model is left to guess
                            # which one to use and where to import it from.
                            kws = [k.lower() for k in (h.get("keywords") or [])]
                            matched = [k for k in kws if k and k in take_lower]
                            if matched:
                                score = 3 + len(matched)
                            
                        if score > 0:
                            scored.append((score, h_name, h_desc, h.get("path", ""), h.get("usage", "")))
                    
                    if scored:
                        # Sort by score descending, get top 2
                        scored.sort(key=lambda x: x[0], reverse=True)
                        top_helpers = scored[:2]
                        for score, h_name, h_desc, h_path, h_usage in top_helpers:
                            # without the path the model has to guess the import, and
                            # for getInput it guesses the wrong clustering module.
                            # without the signature it calls the helper with no arguments
                            # even when it takes two, and the validator does not notice.
                            origin = f" from `{h_path}.nf`" if h_path else ""
                            call = h_usage.replace("def ", "") if h_usage else f"{h_name}()"
                            helper_injections.append(
                                f"- For input `{take}` -> Use `{call}`{origin} "
                                f"(call it with exactly these arguments): {h_desc}")
        except Exception as e:
            logger.warning(f"Error extracting helper functions: {e}")

    # ── Build channel map for architect context ──────────────────────────────
    from core.loader import data_loader
    channel_map_lines = []
    for mid in component_ids:
        parsed = _get_channels_for_component(mid, store)
        e = "VOID" if _is_void_tool(mid) else ", ".join(parsed["emits"]) or "unknown"
        t = ", ".join(parsed["takes"]) or "unknown"

        # Annotate with known graph edge channels
        line = f"- {mid}: take=[{t}] emit=[{e}]"
        
        # See what downstream components we can reach directly
        downstream = []
        for tgt in component_ids:
            if mid != tgt:
                p = kg.find_path(mid, tgt, store)
                if p and len(p) == 2: # Direct edge
                    downstream.append(tgt)
                    
        if downstream:
            line += f" → {', '.join(downstream)}"

        channel_map_lines.append(line)

    # ── Deterministic Pattern Injection ──────────────────────────────────────
    pattern_injections = []
    try:
        patterns = store.search(("patterns",))
        matched_patterns = []
        for p in patterns:
            code = str(p.value.get("groovy_code", ""))
            if not code: continue
            
            # Count how many of the selected components appear in this pattern
            matched_comps = [mid for mid in component_ids if mid in code]
            if matched_comps:
                # Score by how many components match, to rank them
                matched_patterns.append((len(matched_comps), p.value.get("title", ""), code))
                
        if matched_patterns:
            # Sort by number of matched components descending
            matched_patterns.sort(key=lambda x: x[0], reverse=True)
            for score, title, code in matched_patterns[:5]: # Take top 5
                pattern_injections.append(f"### {title}\n```groovy\n{code}\n```")
    except Exception as e:
        logger.warning(f"Error extracting patterns: {e}")

    # ── Dynamic AST Dataflow Operator Directives ──────────────────────────────
    dataflow_directives = []
    try:
        dataflow_directives = kg.synthesize_dataflow_directives(component_ids, store=store)
    except Exception as e:
        logger.warning(f"Error synthesizing dataflow directives: {e}")

    # ── Topological Wireframe Synthesis ─────────────────────────────────────
    wireframe_lines = []
    topological_seq = component_ids
    if component_ids:
        try:
            if kg.is_built:
                import networkx as nx
                sub_g = kg.G.subgraph(set(component_ids))
                if nx.is_directed_acyclic_graph(sub_g):
                    topological_seq = list(nx.topological_sort(sub_g))

            # Track variable names assigned to process outputs
            assigned_vars = {}
            cross_directives = kg.detect_cross_multimap_routing(topological_seq, store=store) if kg.is_built else []
            cross_inserted = set()

            # Fan-in merge detection
            mix_info = kg.detect_fan_in_mix(topological_seq, store=store) if kg.is_built else []
            mix_inserted = set()

            from core.catalog_registry import get_registry
            from core.plugin_loader import get_active_plugin
            active_plug = get_active_plugin()
            plugin_helpers = set(active_plug.helper_imports.keys()) if active_plug else set()
            reg = get_registry()

            for proc_id in topological_seq:
                parsed = _get_channels_for_component(proc_id, store)
                takes = parsed.get("takes", [])
                emits = parsed.get("emits", [])

                # Insert cross-join code block if this process is a consumer
                for cd in cross_directives:
                    if cd["consumer"] == proc_id and cd["joined_var"] not in cross_inserted:
                        wireframe_lines.append("")
                        wireframe_lines.append("    // Keyed channel join & decomposition for parallel asynchronous streams")
                        wireframe_lines.append(f"    {cd['idiom']}")
                        wireframe_lines.append("")
                        cross_inserted.add(cd["joined_var"])

                # Insert fan-in merge code block if this process is a consumer
                for mi in mix_info:
                    if mi["consumer"] == proc_id and mi["consumer"] not in mix_inserted:
                        wireframe_lines.append(f"    {mi['idiom']}")
                        mix_inserted.add(mi["consumer"])

                # Determine variable name for process output
                var_name = None
                if emits and emits[0] not in ("none", "void", ""):
                    primary_emit = emits[0]
                    clean_emit = primary_emit.split(".")[-1] if "." in primary_emit else primary_emit
                    var_name = re.sub(r'[^a-zA-Z0-9_]', '_', clean_emit)
                    assigned_vars[primary_emit] = var_name
                    assigned_vars[var_name] = var_name
                    assigned_vars[proc_id] = var_name

                # Determine arguments for this process based on channel types & upstream dataflow
                args = []
                is_multi = False
                if kg.is_built:
                    collect_info = kg.detect_collection_cardinality(proc_id, store=store)
                    if collect_info or proc_id.startswith("multi_"):
                        is_multi = True

                # Check if arity projection is needed
                arity_proj = None
                if takes and kg.is_built:
                    for prev in topological_seq:
                        if prev == proc_id: break
                        proj = kg.deduce_tuple_arity_projection(prev, proc_id, store=store)
                        if proj and "idiom" in proj:
                            arity_proj = proj["idiom"]
                            break

                if arity_proj:
                    args.append(arity_proj)
                else:
                    for take_ch in takes:
                        if not take_ch or take_ch.lower() in ("none", ""): continue
                        arg_expr = take_ch

                        # Check if channel comes from a cross-joined multiMap variable
                        matched_cross = False
                        for cd in cross_directives:
                            if cd["joined_var"] in cross_inserted:
                                if proc_id in cd.get("consumers", []):
                                    branch = take_ch if take_ch in cd.get("branches", []) else cd.get("channel1", cd.get("stream_name", take_ch))
                                    arg_expr = f"{cd['joined_var']}.{branch}"
                                    matched_cross = True
                                    break

                        # Check if channel comes from a fan-in merge
                        if not matched_cross:
                            for mi in mix_info:
                                if mi["consumer"] == proc_id and mi["channel"] == take_ch:
                                    arg_expr = mi.get("merged_var", take_ch)
                                    matched_cross = True
                                    break

                        if not matched_cross:
                            # 1. Match direct helper function from active plugin
                            if take_ch in plugin_helpers or reg.get_function_import_path(take_ch):
                                arg_expr = f"{take_ch}()"
                            # 2. Match upstream process emit
                            else:
                                matched_upstream = False
                                for prev_proc in reversed(topological_seq):
                                    if prev_proc == proc_id: continue
                                    prev_parsed = _get_channels_for_component(prev_proc, store)
                                    prev_emits = prev_parsed.get("emits", [])
                                    if take_ch in prev_emits:
                                        arg_expr = assigned_vars.get(take_ch, f"{prev_proc}.out.{take_ch}")
                                        matched_upstream = True
                                        break
                                    elif take_ch in ("data", "input") and prev_emits and prev_emits[0] not in ("none", "void", ""):
                                        arg_expr = assigned_vars.get(prev_emits[0], f"{prev_proc}.out.{prev_emits[0]}")
                                        matched_upstream = True
                                        break
                                
                                # 3. Fallback: if unmet parameter / config
                                if not matched_upstream and take_ch not in assigned_vars:
                                    if take_ch not in ("rawreads", "reads", "input", "data"):
                                        arg_expr = f"param('{take_ch}')"

                        # If multi-sample consumer, wrap data channel in .collect()
                        if is_multi and not arg_expr.endswith(".collect()") and not arg_expr.startswith("param(") and not arg_expr.endswith("()"):
                            arg_expr = f"{arg_expr}.collect()"

                        args.append(arg_expr)

                args_str = ", ".join(args) if args else "/* input channel */"
                if var_name:
                    assigned_vars[proc_id] = var_name
                    assigned_vars[var_name] = var_name
                    clean_emit_access = emits[0].split(".")[-1] if (emits and "." in emits[0]) else (emits[0] if emits else var_name)
                    clean_emit_access = re.sub(r'[^a-zA-Z0-9_]', '_', clean_emit_access)
                    wireframe_lines.append(f"    {var_name} = {proc_id}({args_str}).{clean_emit_access}")
                else:
                    wireframe_lines.append(f"    {proc_id}({args_str})")
        except Exception as e:
            logger.warning(f"Error synthesizing topological wireframe: {e}")

    # ── Variable Liveness Analysis ────────────────────────────────────────────
    # Remove assignments whose LHS is never consumed downstream or in emit
    if wireframe_lines:
        try:
            # Build set of all referenced variables across all lines
            all_text = "\n".join(wireframe_lines)
            live_lines = []
            for wl in wireframe_lines:
                stripped = wl.strip()
                if not stripped or stripped.startswith("//"):
                    live_lines.append(wl)
                    continue
                # Check if this is an assignment: var = ...
                assign_m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)', stripped)
                if assign_m:
                    lhs_var = assign_m.group(1)
                    # Check if lhs_var is referenced in any OTHER line
                    other_text = "\n".join(l for l in wireframe_lines if l != wl)
                    if re.search(rf'\b{re.escape(lhs_var)}\b', other_text):
                        live_lines.append(wl)
                    else:
                        # Check if it's the LAST assignment (terminal output) — keep it for emit
                        is_last_assign = True
                        for later_wl in wireframe_lines[wireframe_lines.index(wl) + 1:]:
                            later_stripped = later_wl.strip()
                            if later_stripped and not later_stripped.startswith("//") and "=" in later_stripped:
                                later_m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', later_stripped)
                                if later_m:
                                    is_last_assign = False
                                    break
                        if is_last_assign:
                            live_lines.append(wl)
                        else:
                            logger.info(f"wireframe_liveness: pruned dead var '{lhs_var}'")
                else:
                    live_lines.append(wl)
            wireframe_lines = live_lines
        except Exception as e:
            logger.warning(f"Error in liveness analysis: {e}")

    # ── Meaningful Sub-Workflow Naming ─────────────────────────────────────────
    sw_name = "PIPELINE"
    used_template_id = state.get('used_template_id', '')
    if used_template_id:
        # Use template name (e.g., "module_typing_bacteria" -> "TYPING_BACTERIA")
        clean = used_template_id.replace("module_", "").replace("template_", "")
        sw_name = re.sub(r'[^a-zA-Z0-9_]', '_', clean).upper()
    else:
        # Derive from step categories present in components
        categories = set()
        for cid in topological_seq:
            # Extract category codes like PP, AS, TY, SC from step_1PP_xxx, step_2AS_xxx
            cat_match = re.match(r'(?:step_\d+|multi_)([A-Z]{2,3})_', cid)
            if cat_match:
                categories.add(cat_match.group(1))
        
        category_names = {
            "PP": "PREPROCESSING", "QC": "QC", "AS": "ASSEMBLY",
            "TY": "TYPING", "SC": "SCREENING", "AN": "ANNOTATION",
            "CL": "CLUSTERING", "MP": "MAPPING"
        }
        if categories:
            named_cats = [category_names.get(c, c) for c in sorted(categories)]
            if len(named_cats) <= 3:
                sw_name = "_AND_".join(named_cats)
            else:
                sw_name = "WGS_ANALYSIS"

    if template_parts or warnings or channel_map_lines or helper_injections or pattern_injections or dataflow_directives or wireframe_lines:
        precheck_block = ""
        if template_parts:
            precheck_block += "\n".join(template_parts) + "\n\n"
        if wireframe_lines:
            take_names = ["rawreads"]
            for take_ch in unmet_takes:
                if take_ch not in take_names and take_ch not in ("none", ""):
                    take_names.append(take_ch)

            # Dedup: remove 'reads' if 'rawreads' already present (synonyms)
            if "rawreads" in take_names and "reads" in take_names:
                take_names.remove("reads")

            takes_str = "\n        ".join(take_names)
            last_assigned = None
            for p in reversed(topological_seq):
                if p in assigned_vars:
                    last_assigned = assigned_vars[p]
                    break
            if not last_assigned and topological_seq:
                last_assigned = f"{topological_seq[-1]}.out"

            # Multi-emit: emit all terminal sink outputs, not just the last
            terminal_emits = []
            downstream_map = {}
            for i, p in enumerate(topological_seq):
                downstream_map[p] = set()
                for j in range(i + 1, len(topological_seq)):
                    later = topological_seq[j]
                    later_text = "\n".join(wireframe_lines)
                    if p in assigned_vars and re.search(rf'\b{re.escape(assigned_vars[p])}\b', later_text):
                        downstream_map[p].add(later)

            for p in topological_seq:
                if p in assigned_vars and not downstream_map.get(p):
                    # This is a terminal node — emit it
                    terminal_emits.append(f"{assigned_vars[p]} = {assigned_vars[p]}")

            if not terminal_emits:
                clean_last = last_assigned.split(".")[-1] if (last_assigned and "." in last_assigned and not last_assigned.endswith(".out")) else (last_assigned or "rawreads")
                clean_last = re.sub(r'[^a-zA-Z0-9_.]', '_', clean_last)
                terminal_emits = [f"final_result = {clean_last}"]

            emit_str = "\n        ".join(terminal_emits)

            precheck_block += "## TOPOLOGICAL EXECUTION WIREFRAME (Draft for LLM Review)\n"
            precheck_block += "The Knowledge Graph deduced the following modular Nextflow DSL2 sub-workflow. "
            precheck_block += "**YOU MUST REVIEW AND REFINE THIS DRAFT**: trim unnecessary steps, fix variable names, add complex operators (.cross, .multiMap, .branch, .mix), and ensure modularity.\n```groovy\n"
            precheck_block += f"workflow {sw_name} {{\n    take:\n        {takes_str}\n    main:\n"
            precheck_block += "\n".join(wireframe_lines) + "\n"
            precheck_block += f"    emit:\n        {emit_str}\n"
            precheck_block += "}\n\nworkflow {\n"
            precheck_block += "    rawreads = getSingleInput()\n"
            for t in take_names[1:]:
                precheck_block += f"    {t} = param('{t}')\n"
            precheck_block += f"    {sw_name}({', '.join(take_names)})\n}}\n```\n\n"
        precheck_block += "## CHANNEL MAP (verified from code store)\n"
        precheck_block += "\n".join(channel_map_lines)
        if dataflow_directives:
            precheck_block += "\n\n## DYNAMIC NEXTFLOW DSL2 OPERATOR DIRECTIVES\n"
            precheck_block += "The system deduced the following structural operator requirements from the active code AST:\n"
            precheck_block += "\n\n".join(dataflow_directives)
        if helper_injections:
            precheck_block += "\n\n## DEDUCED HELPER FUNCTIONS (For Unmet Inputs)\n"
            precheck_block += "You MUST use these specific helper functions to instantiate the missing input channels in your entrypoint:\n"
            precheck_block += "\n".join(helper_injections)
        if pattern_injections:
            precheck_block += "\n\n## RELEVANT DESIGN PATTERNS (Deterministically Matched)\n"
            precheck_block += "These verified patterns use the exact components in your plan. Use these idioms for complex data-shaping:\n\n"
            precheck_block += "\n\n".join(pattern_injections)
        if warnings:
            precheck_block += "\n\n## WARNINGS\n" + "\n".join(warnings)

        logger.info("architect_precheck_warnings", count=len(warnings), directives=len(dataflow_directives), wireframe=len(wireframe_lines))
        # Wireframe is injected as text context only — no kg_ast bypass
        return {"technical_context": state.get("technical_context", "") + "\n\n" + precheck_block}

    logger.info("architect_precheck_clear")
    return {}

