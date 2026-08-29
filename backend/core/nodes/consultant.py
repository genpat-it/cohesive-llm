import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.store.base import BaseStore

from core.config import settings
from core.models.consultant_structure import ConsultantOutput, PipelineIntentClassification
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_consultant_prompt, load_extractor_prompt
from core.utils.logger import logger
from core.utils.retry import with_exponential_backoff

CONSULTANT_SYSTEM_PROMPT = load_consultant_prompt()
EXTRACTOR_SYSTEM_PROMPT = load_extractor_prompt()

# ──────────────────────────────────────────────────────────────────────────────
# Approval & Intent Detection utilities
# ──────────────────────────────────────────────────────────────────────────────

_APPROVAL_PHRASES = (
    "approved", "i approve", "yes, build", "yes build",
    "please build", "go ahead", "looks good", "lgtm", "sounds good",
    "build the pipeline", "build it", "proceed", "confirm", "execute",
    "generate the pipeline", "generate it", "run it", "start building",
    "yes please", "yes", "yeah", "yep", "sure", "ok", "okay", "agreed", "great", "perfect",
)


def _detect_approval(messages: list) -> bool:
    """Abolished: All conversational user messages are processed by the Consultant LLM reasoning node.
    Approval is triggered explicitly via state action='approve' or consultant_status='APPROVED'."""
    return False


def _detect_explicit_build_request(messages: list) -> bool:
    """Return True if the user request is an explicit command to construct a pipeline."""
    for m in messages:
        if isinstance(m, HumanMessage):
            content = str(m.content).strip().lower()
            explicit_prefixes = (
                "build", "generate", "create", "assemble", "construct", "produce",
                "run", "make", "draft", "screen", "align", "characterize", "end-to-end",
                "pipeline for", "workflow for"
            )
            return any(content.startswith(v) or f" {v} " in content for v in explicit_prefixes)
    return False


def _classify_intent_with_llm(messages: list) -> PipelineIntentClassification:
    """Classify the user intent semantically using LLM structured output without brittle regexes."""
    combined_text = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            content = m.content
            if isinstance(content, list):
                combined_text += " " + " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
            elif isinstance(content, str):
                combined_text += " " + content

    if not combined_text.strip():
        return PipelineIntentClassification()

    try:
        llm = get_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are an expert scientific pipeline intent classifier. Analyze the user query and classify:\n"
                "- data_level: 'raw_reads' (unprocessed FASTQ reads), 'intermediate_sequence' (FASTA contigs, assemblies), 'variant_data' (VCF), 'alignment_data' (BAM/SAM), 'tabular_metadata' (sample sheets), 'hybrid_multimodal' (both short and long reads), or 'unspecified'.\n"
                "- domain_category: 'virology', 'bacteriology', 'metagenomics', 'parasitology_mycology', 'epidemiological_surveillance', 'general_bioinformatics', or 'unspecified'.\n"
                "- workflow_scope:\n"
                "  * 'diagnostic_probe': conceptual questions, questions asking what tools/databases/algorithms are supported, or inquiries without instruction to build or run a workflow.\n"
                "  * 'targeted': request to perform/run a specific analysis goal on data (e.g. assemble contigs, screen resistance).\n"
                "  * 'full_pipeline': request to build an end-to-end multi-stage pipeline from raw files to final report.\n"
                "  * 'qc_only': request to inspect or calculate quality metrics only.\n"
                "- skip_preprocessing: True if the user explicitly asks to skip QC/trimming or states data is pre-cleaned/assembled.\n"
                "- technology: 'illumina', 'nanopore', 'pacbio', 'sanger', 'hybrid', or 'unspecified'.\n"
                "- explicit_tool_requests: list of tool names explicitly named to be used.\n"
                "- excluded_items: list of tool names or operations explicitly asked to be excluded, avoided, or omitted.\n"
                "- analysis_goals: list of generic analysis operations requested (e.g. QC, Preprocessing, Trimming, Assembly, Mapping, Variant Calling, Annotation, AMR Screening, Typing, Lineage, Clustering, Metagenomics).\n"
                "Remain 100% domain-agnostic and strictly faithful to the user's intent."
            )),
            ("human", "{query}")
        ])
        classifier = llm.with_structured_output(PipelineIntentClassification, method="json_schema", include_raw=False)
        chain = prompt | classifier
        return chain.invoke({"query": combined_text.strip()})
    except Exception as e:
        logger.warning(f"intent_classification_fallback: {e}")
        return PipelineIntentClassification()



# ──────────────────────────────────────────────────────────────────────────────
# Message sanitisation
# ──────────────────────────────────────────────────────────────────────────────

def _sanitize_messages_for_api(messages: list) -> list:
    """Ensures every tool call has a corresponding tool response, strips redundant older revision headers,
    truncates oversized tool outputs, and filters duplicate embedded SystemMessages."""
    filtered_messages = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        # Strip legacy/older revision context headers from prior HumanMessages to avoid quadratic prompt bloat
        if isinstance(m, HumanMessage) and "### CURRENT PIPELINE STATE & REVISION CONTEXT" in str(m.content):
            content_str = str(m.content)
            parts = content_str.split("\n\n", 1)
            clean_content = parts[1] if len(parts) > 1 else content_str
            filtered_messages.append(HumanMessage(content=clean_content))
        else:
            filtered_messages.append(m)

    answered_ids = {m.tool_call_id for m in filtered_messages if isinstance(m, LCToolMessage)}

    patched = []
    for msg in filtered_messages:
        patched.append(msg)
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tc_id = tc.get("id") or tc.get("tool_call_id")
                if tc_id and tc_id not in answered_ids:
                    patched.append(LCToolMessage(
                        content="[Tool call skipped — iteration limit reached]",
                        tool_call_id=tc_id,
                        name=tc.get("name", "unknown"),
                    ))
                    answered_ids.add(tc_id)

    # ── Context Headroom Protection (Guard against input_tokens + max_tokens > 65536) ──
    # If the message history character count exceeds ~140,000 chars (~38,000 tokens),
    # safely prune intermediate tool executions from older completed turns while keeping:
    # 1) The first 2 messages (original user request)
    # 2) The last 10 messages (current active turn & recent context)
    # 3) All HumanMessages and content-bearing AIMessages (all plans and discussions)
    total_chars = sum(len(str(m.content)) for m in patched)
    if total_chars > 140000 and len(patched) > 12:
        keep_indices = set(range(min(2, len(patched))))
        keep_indices.update(range(max(0, len(patched) - 10), len(patched)))
        for i, m in enumerate(patched):
            if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None)):
                keep_indices.add(i)

        pruned = [m for i, m in enumerate(patched) if i in keep_indices]
        pruned_answered = {m.tool_call_id for m in pruned if isinstance(m, LCToolMessage)}
        final_patched = []
        for msg in pruned:
            final_patched.append(msg)
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    if tc_id and tc_id not in pruned_answered:
                        final_patched.append(LCToolMessage(
                            content="[Historical tool call compacted]",
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                        ))
                        pruned_answered.add(tc_id)
        return final_patched

    return patched


# ──────────────────────────────────────────────────────────────────────────────
# Consultant node
# ──────────────────────────────────────────────────────────────────────────────

def consultant_node(state: GraphState) -> Any:
    logger.info("node_start", node="consultant")
    llm = get_llm()
    current_messages = state.get("messages", [])
    current_plan = state.get("design_plan", "No plan generated yet.")
    current_components = state.get("selected_component_ids", [])
    current_template = state.get("used_template_id", "None")
    tool_memory = state.get("tool_memory", []) or []

    formatted_facts = ""
    if tool_memory:
        fact_lines = []
        for fact in tool_memory:
            if isinstance(fact, dict):
                tool_name = fact.get('tool', '?')
                args = fact.get('args', '')
                result = fact.get('result', '(no result)')
                fact_lines.append(f"  - {tool_name}({args}) → {str(result)[:300]}")
            else:
                fact_lines.append(f"  - {fact}")
        formatted_facts = "\n".join(fact_lines)

    is_approval_turn = (state.get("action") == "approve") or _detect_approval(current_messages)

    revision_context = f"""### CURRENT PIPELINE STATE & REVISION CONTEXT
- Current Modules: {current_components}
- Current Template: {current_template}
- Current Plan: {current_plan}

## Previously Gathered Tool Facts:
{formatted_facts if formatted_facts else '(none yet)'}"""

    from langchain_core.messages import SystemMessage
    # [Head / Fixed Prefix]: Invariant system prompt ensures 100% vLLM prefix cache hits across turns
    system_msg = SystemMessage(content=CONSULTANT_SYSTEM_PROMPT)
    prompt = ChatPromptTemplate.from_messages([
        system_msg,
        MessagesPlaceholder(variable_name="messages")
    ])

    from core.tool_registry import get_consultant_tools
    if is_approval_turn:
        # Skip LLM entirely — the extract node shortcircuits this turn anyway.
        # Calling the model without tools causes Devstral/Mistral to emit
        # "I don't have the tools to help" which pollutes message history.
        logger.info("consultant_approval_turn_no_tools")
        approval_msg = AIMessage(content="Understood — proceeding to build the pipeline as planned.")
        logger.info("consultant_tool_calls", count=0)
        return {"messages": [approval_msg], "consultant_status": "APPROVED"}

    llm_with_tools = llm.bind_tools(get_consultant_tools())
    chain = prompt | llm_with_tools
    safe_messages = _sanitize_messages_for_api(current_messages)

    # [Middle-Tail / Dynamic Context]: Attach revision state to latest human message if state/facts exist
    has_active_state = (current_plan != "No plan generated yet.") or bool(current_components) or bool(formatted_facts)
    if has_active_state and safe_messages:
        # Prepend revision context to the latest HumanMessage in the current turn:
        injected = False
        for i in range(len(safe_messages) - 1, -1, -1):
            if isinstance(safe_messages[i], HumanMessage):
                if "### CURRENT PIPELINE STATE" not in str(safe_messages[i].content):
                    safe_messages = safe_messages[:i] + [HumanMessage(content=f"{revision_context.strip()}\n\n{safe_messages[i].content}")] + safe_messages[i+1:]
                injected = True
                break
        if not injected:
            safe_messages = [HumanMessage(content=revision_context.strip())] + safe_messages

    try:
        result = with_exponential_backoff(chain.invoke)({"messages": safe_messages})

        tool_call_count = len(result.tool_calls) if getattr(result, "tool_calls", None) else 0
        logger.info("consultant_tool_calls", count=tool_call_count)
        return {"messages": [result]}

    except Exception as e:
        logger.error("consultant_error", error=str(e))
        return {"messages": [AIMessage(content="I encountered an error while processing. Please try again.")], "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Consultant extract helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_approval_shortcircuit(messages: list, state: GraphState) -> dict:
    approval_reply = ""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            approval_reply = m.content
            break

    prior_plan = state.get("design_plan")
    if not prior_plan:
        for m in reversed(messages):
            if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None) and m.content != approval_reply:
                prior_plan = m.content
                break
        prior_plan = prior_plan or approval_reply or "Pipeline approved by user."

    selected_ids = state.get("selected_component_ids") or []
    logger.info(
        "consultant_extract_approval_shortcircuit",
        plan_chars=len(prior_plan),
        extracted_ids=len(selected_ids),
        final_ids=selected_ids,
    )

    return {
        "messages": [AIMessage(content=approval_reply or "Understood — proceeding to build the pipeline.")],
        "consultant_status": "APPROVED",
        "design_plan": prior_plan,
        "strategy_selector": state.get("strategy_selector") or "CUSTOM_BUILD",
        "used_template_id": state.get("used_template_id"),
        "selected_component_ids": selected_ids,
        "tool_memory": state.get("tool_memory") or [],
        "error": None,
    }


def _get_ai_content(messages: list) -> str:
    """Extract real assistant response text without injecting artificial filler."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            return str(msg.content)

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)

    return ""


def _validate_approved_components(
    result: ConsultantOutput,
    store: BaseStore,
    input_datatype: str = "unspecified",
    allow_auto_trimming: bool = False,
) -> None:
    from core.services.knowledge_graph import kg
    if store and not kg.is_built:
        kg.build_nx_graph(store)

    if result.used_template_id:
        proj_tmpl = kg.project_vertex(result.used_template_id) if kg.is_built else None
        if proj_tmpl:
            result.used_template_id = proj_tmpl
        elif store and not store.get(("templates",), result.used_template_id):
            logger.warning("hallucinated_template", id=result.used_template_id)
            result.used_template_id = None

    if kg.is_built and result.selected_component_ids:
        # Check and bridge topological path reachability
        bridged = kg.bridge_pipeline_path(
            result.selected_component_ids,
            input_datatype=input_datatype,
            allow_auto_trimming=allow_auto_trimming,
        )
        result.selected_component_ids = bridged

    for mod_id in result.selected_component_ids:
        if store and not store.get(("components",), mod_id) and not store.get(("templates",), mod_id):
            logger.warning("hallucinated_module_passed_to_hydrator", id=mod_id)


def _build_extraction_context(messages: list) -> tuple[str, list[dict]]:
    """Builds the string context and captures any new tool facts for memory."""
    conversation_summary = []
    tool_memory_new = []

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    for msg in messages[-settings.CONTEXT_WINDOW_EXTRACT:]:
        if isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    conversation_summary.append(f"[TOOL CALL] {tc.get('name', '?')}({tc.get('args', '?')})")
            if msg.content:
                conversation_summary.append(f"[CONSULTANT] {msg.content}")
        elif hasattr(msg, 'type') and msg.type == 'tool':
            result_str = str(msg.content)[:settings.MAX_TOOL_RESULT_PREVIEW] if msg.content else "(empty)"
            conversation_summary.append(f"[TOOL RESULT] {result_str}")
            if msg.content:
                tool_memory_new.append({
                    "tool": getattr(msg, 'name', 'unknown'),
                    "args": "(from conversation)",
                    "result": result_str
                })
        elif isinstance(msg, HumanMessage):
            conversation_summary.append(f"[USER] {msg.content}")
        elif isinstance(msg, SystemMessage):
            conversation_summary.append(f"[SYSTEM] {msg.content}")

    return "\n".join(conversation_summary), tool_memory_new


def _get_verified_plan_shortcircuit(messages: list, state: GraphState, store: BaseStore) -> dict | None:
    """If the consultant called check_plan_logic with valid components and emitted a plan,
    extract the plan deterministically in <1ms without calling the extraction LLM."""
    verified_comps = []
    template_id = None

    # Walk backwards through messages to find the last check_plan_logic call
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if tc.get("name") == "check_plan_logic":
                    args = tc.get("args", {})
                    comps = args.get("component_ids", [])
                    if comps and isinstance(comps, list):
                        verified_comps = comps
                        template_id = args.get("template_id")
                        break
            if verified_comps:
                break

    if not verified_comps:
        return None

    last_ai_content = _get_ai_content(messages)
    if not last_ai_content or len(last_ai_content.strip()) < 10:
        return None

    # Check if approval or direct execution mode
    is_direct_mode = state.get("execution_mode") == "direct"
    is_approved = is_direct_mode or (state.get("consultant_status") == "APPROVED") or (state.get("action") == "approve")
    status = "APPROVED" if is_approved else "CHATTING"

    from core.services.knowledge_graph import kg
    if store and not kg.is_built:
        kg.build_nx_graph(store)

    if kg.is_built and verified_comps:
        # Dynamic query decomposition to capture all scientific sub-goals across conversation turns
        user_msgs = [
            str(m.content).lower() for m in state.get("messages", [])
            if hasattr(m, "content") and m.content and type(m).__name__ in ("HumanMessage", "UserMessage")
        ]
        user_msg = " ".join(user_msgs)

        valid_comps, _ = kg.partition_raw_ids(verified_comps)
        verified_comps = kg.expand_composite_components(valid_comps, store=store)

        intent = _classify_intent_with_llm(state.get("messages", []))
        excluded_set = set(intent.excluded_items) if intent.excluded_items else set()

        if excluded_set:
            verified_comps = [
                c for c in verified_comps
                if not any(ex.lower() in c.lower() for ex in excluded_set)
            ]

        if not template_id and user_msg:
            # 1. Project all explicitly mentioned tools from user prompt, respecting LLM-detected exclusions
            all_named = kg.project_all_vertices(user_msg, excluded_items=excluded_set)
            if len(all_named) >= 8:
                verified_comps = all_named
            else:
                for p in all_named:
                    p_prefix = "_".join(p.split("_")[:3]) if "_" in p else p
                    replaced = False
                    for i, vc in enumerate(verified_comps):
                        vc_prefix = "_".join(vc.split("_")[:3]) if "_" in vc else vc
                        if p_prefix == vc_prefix and p != vc:
                            tool_name_p = p.split("__")[-1] if "__" in p else p
                            tool_name_vc = vc.split("__")[-1] if "__" in vc else vc
                            if tool_name_p.lower() in user_msg and (tool_name_vc.lower() not in user_msg or tool_name_vc.lower() in excluded_set):
                                verified_comps[i] = p
                                replaced = True
                                break
                    if not replaced and p not in verified_comps:
                        verified_comps.append(p)

        allow_trim = (
            intent.workflow_scope == "full_pipeline"
            and intent.data_level in ("raw_reads", "hybrid_multimodal")
            and not intent.skip_preprocessing
        )
        if not template_id:
            verified_comps = kg.bridge_pipeline_path(
                verified_comps,
                input_datatype=intent.data_level,
                allow_auto_trimming=allow_trim,
            )

    logger.info("consultant_extract_fastpath_hit", status=status, components=verified_comps)

    return {
        "messages": [AIMessage(content=last_ai_content)],
        "consultant_status": status,
        "design_plan": last_ai_content,
        "strategy_selector": "TEMPLATE" if template_id else "CUSTOM_BUILD",
        "used_template_id": template_id,
        "selected_component_ids": verified_comps,
        "tool_memory": state.get("tool_memory") or [],
        "error": None
    }


def consultant_extract_node(state: GraphState, store: BaseStore) -> Any:  # noqa: C901
    logger.info("node_start", node="consultant_extract")
    messages = state.get("messages", [])

    is_approval_action = (state.get("consultant_status") == "APPROVED") or (state.get("action") == "approve") or _detect_approval(messages)
    if is_approval_action and state.get("selected_component_ids"):
        return _get_approval_shortcircuit(messages, state)

    # ── Fast-Path Deterministic Extraction (0ms, 0 GPU tokens) ───────────────
    fastpath_res = _get_verified_plan_shortcircuit(messages, state, store)
    if fastpath_res:
        return fastpath_res

    llm = get_llm()
    last_ai_content = _get_ai_content(messages)
    context_text, tool_memory_new = _build_extraction_context(messages)
    if not last_ai_content and not context_text:
        return {
            "messages": [AIMessage(content="I couldn't generate a response. Please try rephrasing your request.")],
            "error": "No consultant response to extract from"
        }

    reasoning_payload = last_ai_content if last_ai_content else "(Direct tool actions — see conversation context above)"
    extraction_prompt = ChatPromptTemplate.from_messages([
        ("system", EXTRACTOR_SYSTEM_PROMPT),
        ("human", "CONVERSATION CONTEXT:\n{context}\n\nFINAL CONSULTANT MESSAGE:\n{reasoning}")
    ])

    extractor = llm.with_structured_output(ConsultantOutput, method="json_schema", include_raw=False)
    chain = extraction_prompt | extractor

    try:
        result = chain.invoke({
            "context": context_text,
            "reasoning": reasoning_payload
        })

        is_direct_mode = state.get("execution_mode") == "direct"
        is_approved = is_direct_mode or (state.get("consultant_status") == "APPROVED") or (state.get("action") == "approve")
        if is_approved:
            result.status = "APPROVED"
        else:
            result.status = "CHATTING"

        logger.info("consultant_extract_status", status=result.status)

        # 1. Active Bipartite Vertex Partitioning & Canonical Projection on Knowledge Graph
        from core.services.knowledge_graph import kg
        if store and not kg.is_built:
            kg.build_nx_graph(store)

        intent = _classify_intent_with_llm(messages)
        excluded_set = set(intent.excluded_items) if intent.excluded_items else set()

        if kg.is_built:
            # Context-aware disambiguation based on conversation user prompts
            user_msgs = [
                str(m.content).lower() for m in state.get("messages", [])
                if hasattr(m, "content") and m.content and type(m).__name__ in ("HumanMessage", "UserMessage")
            ]
            user_msg = " ".join(user_msgs)

            named_tools = kg.project_all_vertices(user_msg, excluded_items=excluded_set) if user_msg else []
            if len(named_tools) >= 8:
                result.selected_component_ids = named_tools
            elif result.selected_component_ids:
                if excluded_set:
                    result.selected_component_ids = [
                        c for c in result.selected_component_ids
                        if not any(ex.lower() in c.lower() for ex in excluded_set)
                    ]
                valid_comps, _helper_funcs = kg.partition_raw_ids(result.selected_component_ids)
                result.selected_component_ids = kg.expand_composite_components(valid_comps, store=store)

        allow_trim = (
            intent.workflow_scope == "full_pipeline"
            and intent.data_level in ("raw_reads", "hybrid_multimodal")
            and not intent.skip_preprocessing
        )
        if result.status == "APPROVED":
            if not result.selected_component_ids:
                extracted = []
                # Fallback: extract from verified tool memory
                for fact in ((state.get("tool_memory", []) or []) + tool_memory_new):
                    tool_name = fact.get("tool")
                    if tool_name in ("lookup_catalog_item", "lookup_components_batch"):
                        try:
                            data = json.loads(fact.get("result", "{}"))
                            if isinstance(data, dict):
                                if tool_name == "lookup_components_batch":
                                    for k, v in data.items():
                                        if isinstance(v, dict) and v.get("valid") and k not in extracted:
                                            extracted.append(k)
                                elif data.get("valid") and data.get("id") and data.get("id") not in extracted:
                                    extracted.append(data.get("id"))
                        except Exception:
                            pass
                if not extracted and is_direct_mode and kg.is_built:
                    user_msg = ""
                    for m in state.get("messages", []):
                        if hasattr(m, "content") and m.content and type(m).__name__ in ("HumanMessage", "UserMessage"):
                            user_msg = str(m.content).lower()
                            break
                    if user_msg:
                        named_tools = kg.project_all_vertices(user_msg, excluded_items=excluded_set)
                        if named_tools:
                            extracted = kg.bridge_pipeline_path(
                                named_tools,
                                input_datatype=intent.data_level,
                                allow_auto_trimming=allow_trim,
                            )
                result.selected_component_ids = kg.expand_composite_components(extracted, store=store) if kg.is_built else extracted

            _validate_approved_components(
                result,
                store,
                input_datatype=intent.data_level,
                allow_auto_trimming=allow_trim,
            )

        is_hard_reset = (result.status == "CHATTING" and not result.draft_plan and len(result.selected_component_ids) == 0)

        # In CHATTING mode without a concrete draft plan (e.g. diagnostic probing / conflict refusal), ensure components list stays empty
        if result.status == "CHATTING" and (not result.draft_plan or len(str(result.draft_plan).strip()) < 15):
            result.selected_component_ids = []
            final_ids = []
        else:
            final_ids = result.selected_component_ids if result.selected_component_ids else ([] if is_hard_reset else state.get("selected_component_ids", []))
            if final_ids and kg.is_built:
                final_ids = kg.expand_composite_components(final_ids, store=store)

        # FIX: Ensure we slice the *combined* array, not just the new additions, to prevent infinite growth.
        combined_memory = (state.get("tool_memory", []) or []) + tool_memory_new
        pruned_memory = combined_memory[-settings.MEMORY_MAX_TOOL_FACTS:]

        if result.draft_plan:
            edges_str = "\n".join([f"- {e.upstream_component} -> [{e.channel}] -> {e.downstream_component}" for e in result.semantic_edges]) if result.semantic_edges else "None"
            inputs_str = "\n".join([f"- `{i.variable}` = `{i.source_helper}`" for i in result.input_assignments]) if result.input_assignments else "None"
            enriched_plan = f"{result.draft_plan}\n\n### INPUT ASSIGNMENTS:\n{inputs_str}\n\n### SEMANTIC BRIDGES:\n{edges_str}"
        else:
            enriched_plan = None

        state_updates = {
            "messages": [AIMessage(content=result.response_to_user)],
            "consultant_status": result.status,
            "design_plan": enriched_plan if enriched_plan else (None if is_hard_reset else state.get("design_plan")),
            "strategy_selector": result.strategy_selector if result.strategy_selector else (None if is_hard_reset else state.get("strategy_selector", "CUSTOM_BUILD")),
            "used_template_id": result.used_template_id if result.used_template_id else (None if is_hard_reset else state.get("used_template_id")),
            "selected_component_ids": final_ids,
            "tool_memory": pruned_memory,
            "error": None
        }

        if result.status == "CHATTING" or (result.status == "APPROVED" and state.get("nextflow_code")):
            state_updates["nextflow_code"] = None
            state_updates["mermaid_agent"] = None
            state_updates["mermaid_deterministic"] = None
            state_updates["ast_json"] = None

        return state_updates

    except Exception as e:
        logger.error("consultant_extract_error", error=str(e))
        return {
            "messages": [AIMessage(content="I encountered an error structuring the response. Please try again.")],
            "error": f"Consultant Extract Failed: {e!s}"
        }
