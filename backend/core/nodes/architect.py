import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from langgraph.store.base import BaseStore

from core.config import settings
from core.models.ast_structure import NextflowPipelineAST
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_architect_prompt
from core.utils.logger import logger

ARCHITECT_SYSTEM_PROMPT = load_architect_prompt()

ARCHITECT_RESEARCH_SYSTEM_PROMPT = """You are a Nextflow DSL2 code architect preparing to generate a pipeline AST.
You must use your tools to research how to implement the Consultant's plan before generating code.

You have access to the TECHNICAL CONTEXT provided in the conversation which contains the component source code.
**CRITICAL**: The TECHNICAL CONTEXT ONLY contains component source code. It DOES NOT contain helper functions or design patterns. You MUST use your tools to find those.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.
4. `search_helper_functions(query)` - Find built-in helper functions.
5. `search_design_patterns(query)` - Find reusable data-shaping patterns (e.g. host depletion branching, cross+multiMap).
6. `auto_complete_pipeline_dag(src, tgt)` - Use the Knowledge Graph to find valid paths between components.

MANDATORY RESEARCH WORKFLOW:
Step 1: Use `check_component_channels` on the components listed in the plan to get their EXACT take/emit signatures.
Step 2: Use `search_helper_functions` to find the exact syntax for fetching the input data described in the plan.
Step 3: Use `search_design_patterns` to understand how to route data if the plan contains complex logic.
Step 4: Once you have all the necessary syntax and logic, output a detailed summary of your research findings."""

ARCHITECT_REPAIR_SYSTEM_PROMPT = """You are a Nextflow DSL2 code architect. You previously attempted to generate a pipeline AST but validation failed. You now have tools to investigate and fix the issue.

You have access to the TECHNICAL CONTEXT provided in the conversation which contains the component source code.
**CRITICAL**: The TECHNICAL CONTEXT ONLY contains component source code. It DOES NOT contain helper functions or design patterns. You MUST use your tools to find those.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping to see if you forgot to instantiate any variables.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.
4. `search_helper_functions(query)` - Find built-in helper functions.
5. `search_design_patterns(query)` - Find reusable data-shaping patterns (e.g. host depletion branching, cross+multiMap).
6. `auto_complete_pipeline_dag(src, tgt)` - Use the Knowledge Graph to find valid paths between components.

INCREMENTAL REASONING WORKFLOW (Mandatory):
Step 1: Use `check_component_channels` to fetch the EXACT take/emit signature of the components involved in the error.
Step 2: Use `verify_dataflow_plan` to propose and test your DataFlow plan. Do NOT proceed until the tool returns "SUCCESS".
Step 3: Use `validate_body_code` to test any tricky groovy snippets you intend to write.
Step 4: Once all tests pass, output your final reasoning.

CRITICAL DSL2 RULES (common mistakes):
- body_code must NOT contain 'workflow name {}', 'take:', 'main:', or 'emit:' keywords — the rendering template handles these automatically
- Sub-workflows must NOT define active data channels (e.g. fetching inputs/references) — these go in the entrypoint only, data is passed via take_channels
- Void tools must NOT be assigned to variables — call them directly
- The entrypoint workflow calls sub-workflows: data = my_input(); subworkflow_name(data)
- The sub-workflow receives data via take_channels, processes it, and emits results via emit_channels
- DO NOT emit channels from a sub-workflow unless they are EXPLICITLY required by the entrypoint. Be minimal.
- .branch { name: predicate } creates named output channels accessible as result.name — you MUST assign the branch result to use the names
- .multiMap { name: expr } creates named output channels similarly
- Prefer using standard catalog components over custom `inline_processes`"""


def architect_reason_node(state: GraphState) -> Any:
    logger.info("node_start", node="architect_reason")
    if state.get("error"):
        return {"error": state['error']}

    llm = get_llm()
    validation_error = state.get("validation_error", "")
    plan = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    from core.tool_registry import get_architect_tools
    llm_with_tools = llm.bind_tools(get_architect_tools())

    # [Head / Fixed Prefix]: Invariant system message ensures 100% prefix cache hits in vLLM
    system_msg = SystemMessage(
        content=ARCHITECT_REPAIR_SYSTEM_PROMPT if validation_error else ARCHITECT_RESEARCH_SYSTEM_PROMPT
    )

    # [Middle / Static Reference]: Technical context and design plan anchored at front of conversation
    anchor_content = (
        f"### TECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}\n\n"
        f"### APPROVED PLAN:\n{plan}\n\n"
    )
    if validation_error:
        anchor_content += (
            f"### VALIDATION ERROR TO FIX:\n{validation_error}\n\n"
            "TASK: Investigate the validation error above using your tools. Explain what needs to be fixed before retrying."
        )
    else:
        anchor_content += (
            "TASK: Please research the necessary helper functions and design patterns for the provided plan. "
            "Call tools to investigate, or output your findings if you are done."
        )

    state_messages = state.get("messages", [])
    relevant_messages = []
    
    if validation_error:
        # Extract the conversation history specifically for the current repair loop
        for msg in reversed(state_messages):
            relevant_messages.insert(0, msg)
            if isinstance(msg, HumanMessage) and "**VALIDATION FAILED**" in msg.content:
                break
    else:
        # Research mode: collect all messages from the end of the consultant turn onward.
        limit = settings.CONTEXT_WINDOW_REASON * 3
        count = 0
        in_tool_block = False

        for msg in reversed(state_messages):
            relevant_messages.insert(0, msg)
            count += 1

            if isinstance(msg, LCToolMessage):
                in_tool_block = True
            elif isinstance(msg, AIMessage) and getattr(msg, 'tool_calls', None):
                in_tool_block = False

            if isinstance(msg, HumanMessage) and "**VALIDATION FAILED**" not in msg.content:
                break  # found the real user-turn boundary
                
            if not in_tool_block and count >= limit:
                break

        # Strip any trailing AIMessages that are NOT followed by tool results.
        while relevant_messages and isinstance(relevant_messages[-1], AIMessage) and not getattr(relevant_messages[-1], 'tool_calls', None):
            relevant_messages.pop()

    # Anchor the technical context and plan as the first HumanMessage
    if not relevant_messages or not isinstance(relevant_messages[0], HumanMessage):
        relevant_messages.insert(0, HumanMessage(content=anchor_content))
    else:
        # Prepend anchor context to the first HumanMessage if not already there
        if "### TECHNICAL CONTEXT" not in str(relevant_messages[0].content):
            relevant_messages[0] = HumanMessage(content=f"{anchor_content}\n\n{relevant_messages[0].content}")

    messages = [system_msg, *relevant_messages]

    # Enforce mandatory tool calling on the first pass of the loop
    if len(relevant_messages) == 1 and isinstance(relevant_messages[0], HumanMessage):
        from core.tool_registry import get_architect_tools
        llm_with_tools = llm.bind_tools(get_architect_tools(), tool_choice="any")
        logger.info("architect_reason_mandatory_tool_enforced")
    try:
        result = llm_with_tools.invoke(messages)
        # Tag this message as internal so the API doesn't send it to the user chat
        result.additional_kwargs["internal_agent"] = "architect"
        logger.info("architect_reason_tool_calls", count=len(result.tool_calls) if result.tool_calls else 0)
        return {"messages": [result]}
    except Exception as e:
        logger.error("architect_reason_error", error=str(e))
        return {"error": f"Failed to reason: {e}"}


def architect_generate_node(state: GraphState) -> Any:
    logger.info("node_start", node="architect_generate")
    if state.get("error"):
        return {"error": state['error']}

    llm = get_llm()
    architect_agent = llm.with_structured_output(NextflowPipelineAST, method="function_calling", include_raw=True)

    architect_findings = ""
    messages = state.get("messages", [])

    for msg in reversed(messages[-settings.CONTEXT_WINDOW_REASON * 4:]):
        if not isinstance(msg, AIMessage) or not msg.content or getattr(msg, 'tool_calls', None):
            continue

        content_lower = msg.content.lower()
        if any(kw in content_lower for kw in ("channel", "emit", "take", "connection", "validation", "helper", "design", "pattern", "syntax", "research")):
            architect_findings = msg.content
            break

    plan_text = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    # [Middle / Static Reference]: TECHNICAL CONTEXT + APPROVED PLAN + APPROVED COMPONENTS at front
    selected_ids = state.get("selected_component_ids", [])
    selected_clause = ""
    if selected_ids:
        selected_clause = f"\n\n### MANDATORY APPROVED COMPONENTS:\nYou MUST instantiate EXACTLY these {len(selected_ids)} approved components in the pipeline AST (do NOT substitute with other tools):\n" + "\n".join(f"- `{cid}`" for cid in selected_ids)

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
        f"{directives_clause}"
    )
    # [Tail / Dynamic Payload]: Variable research findings
    if architect_findings:
        human_msg += f"\n\n### RESEARCH & ANALYSIS FINDINGS:\n{architect_findings}"

    gen_messages = [
        SystemMessage(content=ARCHITECT_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ]

    try:
        response_dict = architect_agent.invoke(gen_messages)
        parsed = response_dict.get("parsed") if isinstance(response_dict, dict) else response_dict
        raw_msg = response_dict.get("raw") if isinstance(response_dict, dict) else None

        if parsed is not None:
            logger.info("architect_generate_success")
            return {
                "ast_json": parsed.model_dump(),
                "validation_error": None
            }

        # Fallback to raw message parsing
        raw_ast = {}
        if raw_msg:
            if getattr(raw_msg, "tool_calls", None):
                raw_ast = raw_msg.tool_calls[0].get("args", {})
            elif hasattr(raw_msg, "content") and raw_msg.content:
                content = str(raw_msg.content)
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                try:
                    raw_ast = json.loads(content)
                except Exception:
                    pass

        if raw_ast:
            validated = NextflowPipelineAST.model_validate(raw_ast)
            logger.info("architect_generate_success_via_fallback")
            return {
                "ast_json": validated.model_dump(),
                "validation_error": None
            }

        parsing_err = response_dict.get("parsing_error") if isinstance(response_dict, dict) else None
        raise ValueError(f"Model returned invalid AST output: {parsing_err or 'No structured output received'}")
    except Exception as e:
        logger.error("architect_validation_failed", error=str(e))
        raw_ast = {}
        llm_output = getattr(e, "llm_output", None)

        # Robust fallback extraction for malformed JSON
        if llm_output and isinstance(llm_output, str):
            try:
                content = llm_output
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                raw_ast = json.loads(content)
            except Exception:
                pass

        # Clean up Pydantic validation error to save context tokens
        error_str = str(e)
        error_str = re.sub(r'\[type=.*?, input_value=.*?input_type=dict\]', '', error_str, flags=re.DOTALL)
        error_str = re.sub(r'For further information visit https://errors\.pydantic\.dev/.*?$', '', error_str, flags=re.MULTILINE)

        ast_str = json.dumps(raw_ast, sort_keys=True)
        try:
            import os
            dump_dir = os.path.join(settings.BASE_DIR, "scratch")
            os.makedirs(dump_dir, exist_ok=True)
            with open(os.path.join(dump_dir, "last_ast_dump.json"), "w") as f:
                f.write(ast_str)
        except Exception:
            pass
        import hashlib
        ast_hash = hashlib.md5(ast_str.encode()).hexdigest()

        past_hashes = state.get("past_ast_hashes", [])
        if ast_hash in past_hashes:
            logger.error("AST generation loop detected. Small model is incapable of fixing this. Aborting repair loop early.")
            return {
                "ast_json": raw_ast,
                "error": f"AST Repair Loop Detected: Repeated identical failed AST structure. Model is unable to repair {error_str.strip()}"
            }
        
        return {
            "ast_json": raw_ast,
            "validation_error": error_str.strip(),
            "retries": state.get("retries", 0) + 1,
            "past_ast_hashes": past_hashes + [ast_hash]
        }


def _get_channels_for_component(mid: str, store: BaseStore) -> dict[str, list[str]]:
    """Helper to extract takes/emits dynamically, resolving the None-fallback bugs."""
    from core.services.consultant_tools import _parse_nextflow_channels

    code_item = store.get(("code",), mid)
    code = code_item.value.get("content", "") if code_item else ""
    parsed = _parse_nextflow_channels(code)

    if not parsed["takes"]:
        meta = store.get(("components",), mid) or store.get(("templates",), mid)
        if meta:
            parsed["takes"] = meta.value.get("input_channels", meta.value.get("input_types", [])) or []

    if not parsed["emits"]:
        meta = store.get(("components",), mid) or store.get(("templates",), mid)
        if meta:
            parsed["emits"] = meta.value.get("output_channels", meta.value.get("out", [])) or []

    return parsed


def architect_precheck_node(state: GraphState, store: BaseStore) -> Any:
    logger.info("node_start", node="architect_precheck")
    if state.get("error"):
        return {"error": state["error"]}

    component_ids = state.get("selected_component_ids", [])
    strategy = state.get("strategy_selector", "CUSTOM_BUILD")

    if strategy == "EXACT_MATCH" or len(component_ids) < 2:
        logger.info("architect_precheck_skipped")
        return {}

    from core.services.ast_compiler import _is_void_tool

    warnings = []

    from core.services.knowledge_graph import kg
    if not kg.is_built:
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
                            
                        if score > 0:
                            scored.append((score, h_name, h_desc))
                    
                    if scored:
                        # Sort by score descending, get top 2
                        scored.sort(key=lambda x: x[0], reverse=True)
                        top_helpers = scored[:2]
                        for score, h_name, h_desc in top_helpers:
                            helper_injections.append(f"- For input `{take}` -> Use `{h_name}()`: {h_desc}")
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

    # ── Topological Wireframe Synthesis (Zero-Repair Blueprint) ─────────────
    wireframe_lines = []
    if component_ids:
        try:
            topological_seq = component_ids
            if kg.is_built:
                import networkx as nx
                sub_g = kg.G.subgraph(set(component_ids))
                if nx.is_directed_acyclic_graph(sub_g):
                    topological_seq = list(nx.topological_sort(sub_g))

            # Track variable names assigned to process outputs
            assigned_vars = {}
            cross_directives = kg.detect_cross_multimap_routing(topological_seq, store=store) if kg.is_built else []
            cross_inserted = set()

            # Prepend standard entrypoint input getter
            wireframe_lines.append("    // 1. Initial Input Channel")
            wireframe_lines.append("    rawreads = getSingleInput()")
            assigned_vars['rawreads'] = 'rawreads'

            for proc_id in topological_seq:
                parsed = _get_channels_for_component(proc_id, store)
                takes = parsed.get("takes", [])
                emits = parsed.get("emits", [])

                # Check if this process is a consumer of a cross-join directive that hasn't been emitted yet
                for cd in cross_directives:
                    if cd["consumer"] == proc_id and cd["joined_var"] not in cross_inserted:
                        wireframe_lines.append("")
                        wireframe_lines.append("    // Keyed channel join & decomposition for parallel asynchronous streams")
                        wireframe_lines.append(f"    {cd['idiom']}")
                        wireframe_lines.append("")
                        cross_inserted.add(cd["joined_var"])

                # Determine variable name for process output
                var_name = None
                if emits and emits[0] not in ("none", "void", ""):
                    primary_emit = emits[0]
                    var_name = primary_emit
                    assigned_vars[primary_emit] = primary_emit
                    assigned_vars[proc_id] = primary_emit

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
                                if proc_id in cd.get("consumers", []) or take_ch in (cd.get("channel1"), cd.get("stream_name"), cd.get("channel2"), "species", "genus_species"):
                                    if take_ch in (cd.get("channel1"), cd.get("stream_name"), "assembly", "assembled", "data"):
                                        arg_expr = f"{cd['joined_var']}.{cd.get('channel1', cd.get('stream_name'))}"
                                        matched_cross = True
                                        break
                                    elif take_ch in ("species", "genus_species", "assigned_species", cd.get("channel2")):
                                        arg_expr = f"{cd['joined_var']}.species"
                                        matched_cross = True
                                        break

                        if not matched_cross:
                            # For multi-sample consumers, prioritize typed intermediate channels (alleles, matrix, vcf, counts)
                            if is_multi and take_ch in ("input", "data", "alleles"):
                                found_typed = False
                                for preferred_ch in ("alleles", "matrix", "vcf", "counts", "table", "tree"):
                                    if preferred_ch in assigned_vars:
                                        arg_expr = assigned_vars[preferred_ch]
                                        found_typed = True
                                        break
                                if not found_typed:
                                    for prev_proc in reversed(topological_seq):
                                        if prev_proc == proc_id: continue
                                        prev_parsed = _get_channels_for_component(prev_proc, store)
                                        if prev_parsed.get("emits"):
                                            pe = prev_parsed["emits"][0]
                                            if pe not in ("none", "void", ""):
                                                arg_expr = assigned_vars.get(pe, f"{prev_proc}.out.{pe}")
                                                break
                            else:
                                # Semantic channel alias mapping:
                                if take_ch in ("rawreads", "reads") and "trimmed" in assigned_vars:
                                    arg_expr = assigned_vars["trimmed"]
                                elif take_ch in ("rawreads", "reads") and "clean_reads" in assigned_vars:
                                    arg_expr = assigned_vars["clean_reads"]
                                elif "filter" in proc_id and take_ch in ("data", "input", "report"):
                                    found_scr = False
                                    for prev_proc in reversed(topological_seq):
                                        if prev_proc == proc_id: continue
                                        prev_parsed = _get_channels_for_component(prev_proc, store)
                                        if any(out_name in prev_parsed.get("emits", []) for out_name in ("report", "results", "table", "summary", "hits", "data")):
                                            arg_expr = assigned_vars.get(prev_proc, f"{prev_proc}.out")
                                            found_scr = True
                                            break
                                    if not found_scr:
                                        arg_expr = "assembly"
                                else:
                                    # Look for upstream producer of this exact take channel
                                    for prev_proc in reversed(topological_seq):
                                        if prev_proc == proc_id: continue
                                        prev_parsed = _get_channels_for_component(prev_proc, store)
                                        if take_ch in prev_parsed.get("emits", []):
                                            if prev_proc in assigned_vars:
                                                arg_expr = assigned_vars[prev_proc]
                                            else:
                                                arg_expr = f"{prev_proc}.out.{take_ch}"
                                            break
                                        elif take_ch in ("data", "input") and prev_parsed.get("emits"):
                                            pe = prev_parsed["emits"][0]
                                            if pe not in ("none", "void", ""):
                                                arg_expr = assigned_vars.get(pe, f"{prev_proc}.out.{pe}")
                                                break

                        # If multi-sample consumer, wrap data channel in .collect()
                        if is_multi and not arg_expr.endswith(".collect()") and not any(kw in take_ch for kw in ("schema", "param", "meta")):
                            arg_expr = f"{arg_expr}.collect()"

                        # If take channel is a parameter/schema helper
                        if take_ch in ("schema", "scheme"):
                            arg_expr = "getSchema()"
                        elif take_ch in ("coverage", "identity", "threshold", "min_len", "threads", "raw_metadata", "geodata", "nomenclature", "genus_species"):
                            arg_expr = f"param('{take_ch}')"

                        args.append(arg_expr)

                args_str = ", ".join(args) if args else "/* input channel */"
                if var_name:
                    assigned_vars[proc_id] = var_name
                    assigned_vars[var_name] = var_name
                    wireframe_lines.append(f"    {var_name} = {proc_id}({args_str}).{emits[0] if emits else var_name}")
                else:
                    wireframe_lines.append(f"    {proc_id}({args_str})")
        except Exception as e:
            logger.warning(f"Error synthesizing topological wireframe: {e}")

    if template_parts or warnings or channel_map_lines or helper_injections or pattern_injections or dataflow_directives or wireframe_lines:
        precheck_block = ""
        if template_parts:
            precheck_block += "\n".join(template_parts) + "\n\n"
        if wireframe_lines:
            precheck_block += "## TOPOLOGICAL EXECUTION WIREFRAME (Zero-Repair Blueprint)\n"
            precheck_block += "The Knowledge Graph deduced the exact workflow execution sequence and dataflow connections:\n```groovy\nworkflow {\n"
            precheck_block += "\n".join(wireframe_lines) + "\n}\n```\n\n"
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
        return {"technical_context": state.get("technical_context", "") + "\n\n" + precheck_block}

    logger.info("architect_precheck_clear")
    return {}



