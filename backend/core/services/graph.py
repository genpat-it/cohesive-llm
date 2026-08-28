from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from core.config import settings
from core.nodes.architect import architect_generate_node, architect_precheck_node, architect_reason_node
from core.nodes.consultant import consultant_extract_node, consultant_node
from core.services.graph_state import GraphState
from core.services.renderer import renderer_node
from core.services.repair import repair_node, should_repair
from core.tool_registry import get_architect_tools, get_consultant_tools
from core.utils.logger import logger

# Safety cap on tool-calling iterations to prevent runaway loops
MAX_TOOL_ITERATIONS = settings.MAX_TOOL_ITERATIONS
MAX_TOOL_ITERATIONS_APPROVAL = settings.MAX_TOOL_ITERATIONS_APPROVAL

# Memory compaction settings
MEMORY_KEEP_LAST_N = settings.MEMORY_KEEP_LAST_N
MEMORY_MAX_TOOL_FACTS = settings.MEMORY_MAX_TOOL_FACTS


def sanitize_orphaned_tool_calls(state: GraphState) -> Any:
    """Inject stub ToolMessage responses for any AIMessage tool_calls that lack
    a matching ToolMessage.  This prevents the LLM API from rejecting the
    history with 'Not the same number of function calls and responses'.

    Typically triggered when the tool-iteration safety cap forces routing away
    from the tools node before all pending calls are answered.
    """
    messages = state.get("messages", [])
    if not messages:
        return {}

    # Find the start of the current phase: last HumanMessage in state
    phase_start = 0
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            phase_start = i
            break
    phase_messages = messages[phase_start:]

    # Collect IDs of tool calls that already have a ToolMessage response
    answered_ids = set()
    for msg in phase_messages:
        if isinstance(msg, LCToolMessage):
            answered_ids.add(msg.tool_call_id)

    # Walk the messages and find unanswered tool calls
    stub_messages = []
    for msg in phase_messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tc_id = tc.get("id") or tc.get("tool_call_id")
                if tc_id and tc_id not in answered_ids:
                    stub_messages.append(
                        LCToolMessage(
                            content="[Tool call skipped — iteration limit reached]",
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                        )
                    )
                    answered_ids.add(tc_id)  # avoid duplicates

    if stub_messages:
        logger.info(f"--- [NODE] SANITIZE injected {len(stub_messages)} stub ToolMessages for orphaned calls")
        return {"messages": stub_messages}
    return {}

def check_consultant_status(state: GraphState) -> str:
    if state.get("consultant_status") == "APPROVED":
        return "approved"
    return "chatting"


def check_diagram_generation(state: GraphState) -> str:
    if state.get("generate_diagrams", True):
        return "with_diagrams"
    return "no_diagrams"


def compact_memory_node(state: GraphState) -> Any:  # noqa: C901
    """Lossless memory compaction: instead of deleting messages and losing info,
    extract tool call facts into structured tool_memory before removing old messages.

    Strategy:
    - Keep first 2 messages (initial context) always
    - Keep last N messages (MEMORY_KEEP_LAST_N) always
    - Never remove HumanMessages or content-bearing AIMessages
    - For tool-loop messages (AIMessage with only tool_calls, ToolMessages) outside
      the keep window: extract structured facts into tool_memory, then remove
    """
    messages = state.get("messages", [])
    existing_tool_memory = state.get("tool_memory", []) or []

    if len(messages) <= MEMORY_KEEP_LAST_N:
        return {}

    # Build the keep set: first 2 + last N
    keep_indices = set(range(min(2, len(messages))))  # First 2
    keep_indices.update(range(max(0, len(messages) - MEMORY_KEEP_LAST_N), len(messages)))  # Last N

    # Also always keep SystemMessages and HumanMessages
    for i, msg in enumerate(messages):
        if isinstance(msg, (SystemMessage, HumanMessage)):
            keep_indices.add(i)
        elif isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            # Keep AI messages that have real content (not just tool call stubs)
            keep_indices.add(i)

    # Extract facts from messages we're about to remove
    new_facts = []
    delete_actions = []

    for i, msg in enumerate(messages):
        if i in keep_indices:
            continue

        # Extract tool call info from AI messages before removing
        if isinstance(msg, AIMessage) and getattr(msg, 'tool_calls', None):
            for tc in msg.tool_calls:
                args_str = str(tc.get('args', {}))[:200]
                new_facts.append({
                    "tool": tc.get('name', 'unknown'),
                    "args": args_str,
                    "result": None,  # Will be filled from the ToolMessage
                })
            delete_actions.append(RemoveMessage(id=msg.id))

        # Extract tool results before removing
        elif isinstance(msg, LCToolMessage):
            result_preview = str(msg.content)[:500] if msg.content else "(empty)"
            # Try to attach to the last fact that has no result yet
            for fact in reversed(new_facts):
                if fact["result"] is None:
                    fact["result"] = result_preview
                    break
            else:
                # Standalone tool result — create a new fact
                new_facts.append({
                    "tool": getattr(msg, 'name', 'unknown'),
                    "args": "(from prior call)",
                    "result": result_preview,
                })
            delete_actions.append(RemoveMessage(id=msg.id))

    # Merge new facts into existing tool memory, cap at MEMORY_MAX_TOOL_FACTS
    merged_memory = existing_tool_memory + new_facts
    merged_memory = merged_memory[-MEMORY_MAX_TOOL_FACTS:]

    updates = {}
    if delete_actions:
        updates["messages"] = delete_actions
    if new_facts:
        updates["tool_memory"] = merged_memory

    if updates:
        logger.info(f"--- [NODE] GRAPH compacted {len(delete_actions)} messages and got {len(new_facts)} facts")
        return updates
    return {}

def graph_rag_node(state: GraphState, store: BaseStore) -> Any:
    """Queries the Structural Knowledge Graph and injects a deterministic topological blueprint
    into the context before the Consultant LLM reasoning begins.
    """
    messages = state.get("messages", [])
    if not messages:
        return {}

    # Extract user query
    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            user_query = str(msg.content)
            break
        elif isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "user":
            user_query = str(msg[1])
            break
        elif getattr(msg, "type", None) == "human":
            user_query = str(msg.content)
            break
            
    if not user_query:
        return {}

    from core.services.knowledge_graph import kg
    from core.services.query_normalizer import normalize_query

    # Ensure graph is built
    if not kg.is_built:
        kg.build_graph(store)

    q_info = normalize_query(user_query)
    query_tokens = set(q_info["query_tokens"])

    # Basic topological search based on tokens
    found_nodes = set()
    for comp_id in kg.component_takes.keys():
        comp_tokens = set(comp_id.replace("_", " ").lower().split())
        if comp_tokens.intersection(query_tokens):
            found_nodes.add(comp_id)

    if not found_nodes:
        return {}

    # GraphRAG Neighborhood Retrieval (N-Depth Subgraphs)
    blueprint = "GRAPH RAG TOPOLOGICAL BLUEPRINT:\\n"
    blueprint += "The following GraphRAG Neighborhoods have been extracted via Semantic Dataflow Topology:\\n\\n"
    
    neighborhoods = 0
    found_nodes_list = list(found_nodes)
    for anchor in found_nodes_list:
        upstream = kg.get_upstream_nodes(anchor, max_depth=2, store=store)
        downstream = kg.get_downstream_nodes(anchor, max_depth=2, store=store)
        
        if upstream or downstream:
            neighborhoods += 1
            takes = list(kg.component_takes.get(anchor, []))
            emits = list(kg.component_emits.get(anchor, []))
            blueprint += f"Anchor Node: `{anchor}`\\n"
            blueprint += f"  - Takes: {takes}\\n"
            blueprint += f"  - Emits: {emits}\\n"
            
            if upstream:
                blueprint += "  ↑ Upstream Ancestors (Semantic Producers):\\n"
                for up_node, depth in upstream:
                    blueprint += f"      - [Depth {depth}] `{up_node}` -> `{anchor}`\\n"
            
            if downstream:
                blueprint += "  ↓ Downstream Children (Semantic Consumers):\\n"
                for dn_node, depth in downstream:
                    blueprint += f"      - [Depth {depth}] `{anchor}` -> `{dn_node}`\\n"
            blueprint += "\\n"

    # Identify potential connecting paths between multiple anchor nodes
    paths = []
    for i in range(len(found_nodes_list)):
        for j in range(len(found_nodes_list)):
            if i != j:
                path = kg.find_path(found_nodes_list[i], found_nodes_list[j], store)
                if path:
                    paths.append(path)

    if paths:
        blueprint += "Connecting Paths between Anchor Nodes:\\n"
        for p in paths:
            blueprint += f"- {' -> '.join(p)}\\n"
        blueprint += "\\n"

    # NEW LOGIC: Fetch and inject exact schemas for all discovered nodes
    unique_schema_nodes = set(found_nodes_list)
    for anchor in found_nodes_list:
        upstream = kg.get_upstream_nodes(anchor, max_depth=2, store=store)
        downstream = kg.get_downstream_nodes(anchor, max_depth=2, store=store)
        for up_node, _ in upstream:
            unique_schema_nodes.add(up_node)
        for dn_node, _ in downstream:
            unique_schema_nodes.add(dn_node)
            
    for p in paths:
        for node in p:
            unique_schema_nodes.add(node)
            
    if unique_schema_nodes:
        blueprint += "\\n=== EXACT COMPONENT SCHEMAS ===\\n"
        blueprint += "Use the following deterministic input/output signatures to build the pipeline correctly. You do not need to use search tools for these components:\\n\\n"
        for node_id in unique_schema_nodes:
            comp_item = store.get(("components",), node_id)
            if comp_item and comp_item.value:
                data = comp_item.value
                inputs = data.get("input_channels") or data.get("input_types") or []
                raw_outputs = data.get("output_channels") or data.get("out") or []
                outputs = [f"{node_id}.out.{o}" for o in raw_outputs] if raw_outputs else []
                
                # Hyper-compact XML token format
                in_str = ','.join(inputs) if inputs else 'none'
                out_str = ','.join(outputs) if outputs else 'none'
                blueprint += f'<c id="{node_id}" in="{in_str}" out="{out_str}"/>\n'
        blueprint += "\\n"

    blueprint += "PRIORITIZE THESE PATHS AND SCHEMAS when designing the architecture.\\n"
    blueprint += "CRITICAL NOTE: This is a macro-topology map. It shows WHAT components connect.\\n"
    blueprint += "If a producer emits a sub-output (e.g. [meta, fasta, gfa]) and the consumer only takes [meta, fasta], you must use native Nextflow channel shaping (e.g. .map{}) to isolate the sub-output!\\n"
    
    logger.info(f"--- [NODE] GRAPH RAG injected topology with {neighborhoods} neighborhoods and {len(paths)} paths.")
    
    # Inject as a HumanMessage so it doesn't break vLLM chat templates which enforce SystemMessage at index 0
    new_msg = HumanMessage(content=blueprint)
    
    # Since we can't easily insert into the middle of the message list using LangGraph's standard 
    # reducer without overwriting, we append it. The LLM will see it.
    return {"messages": [new_msg]}

def build_consultant_subgraph(store: Any = None) -> Any:
    """Consultant subgraph with ReAct tool-calling loop:

    consultant → [tool_calls?] → tools → consultant (loop)
                    ↓ (no tool_calls)
               sanitize → consultant_extract → compact_memory → END
    """
    sub = StateGraph(GraphState)

    # Nodes
    # NOTE: graph_rag_node is intentionally removed from the subgraph.
    # The knowledge graph is now built offline at load time (loader.py → kg.build_nx_graph).
    # The LLM accesses graph data via search_component_graph / find_dataflow_path tools.
    sub.add_node("consultant", consultant_node)
    sub.add_node("tools", ToolNode(get_consultant_tools(), handle_tool_errors=True))
    sub.add_node("sanitize", sanitize_orphaned_tool_calls)
    sub.add_node("consultant_extract", consultant_extract_node)
    sub.add_node("compact_memory", compact_memory_node)

    # Entry — consultant is now the direct entry point
    sub.set_entry_point("consultant")

    # Routing: if consultant produced tool_calls → tools, else → sanitize → extract
    def route_consultant(state: GraphState) -> str:
        from langchain_core.messages import AIMessage
        messages = state.get("messages", [])
        if not messages:
            return "sanitize"
        last_msg = messages[-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            # Check for silent tool loop (repeating exact same calls)
            prev_ai = None
            for m in reversed(messages[:-1]):
                if isinstance(m, AIMessage) and getattr(m, 'tool_calls', None):
                    prev_ai = m
                    break
            
            if prev_ai and prev_ai.tool_calls == last_msg.tool_calls:
                logger.info("--- [NODE] GRAPH tool loop detected. Silently forcing extraction.")
                return "sanitize"

            # Count tool messages only since the last HumanMessage (per-turn reset)
            tool_msg_count = 0
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    break  # Hit the current turn boundary — stop counting
                if isinstance(m, LCToolMessage):
                    tool_msg_count += 1

            from core.nodes.consultant import _detect_approval
            is_approval = _detect_approval(messages)

            logger.info(f"--- [NODE] GRAPH routing: is_approval={is_approval}")

            effective_limit = settings.MAX_TOOL_ITERATIONS_APPROVAL if is_approval else settings.MAX_TOOL_ITERATIONS

            if tool_msg_count >= effective_limit:
                logger.info(f"--- [NODE] GRAPH tool limit of {effective_limit} reached (is_approval={is_approval}, count={tool_msg_count}). forcing extraction")
                return "sanitize"
            return "tools"
        return "sanitize"

    sub.add_conditional_edges("consultant", route_consultant, {
        "tools": "tools",
        "sanitize": "sanitize"
    })

    # After tools execute, loop back to consultant for next reasoning step
    sub.add_edge("tools", "consultant")

    # After sanitizing orphaned tool calls, proceed to extraction
    sub.add_edge("sanitize", "consultant_extract")

    # After extraction, compact memory (lossless) and exit
    sub.add_edge("consultant_extract", "compact_memory")
    sub.add_edge("compact_memory", END)

    # Pass store so LangGraph can inject it into store-dependent nodes (consultant_node, consultant_extract_node)
    return sub.compile(store=store)

def build_execution_subgraph(store: Any = None) -> Any:
    sub = StateGraph(GraphState)
    sub.add_node("architect_precheck", architect_precheck_node)
    
    # State-Aware Reasoning Node (Handles both Research and Repair)
    sub.add_node("architect_reason", architect_reason_node)
    sub.add_node("sanitize_architect", sanitize_orphaned_tool_calls)
    
    sub.add_node("architect_generate", architect_generate_node)
    sub.add_node("repair", repair_node)
    sub.add_node("renderer", renderer_node)

    # Inner loop for tool calling
    max_architect_tool_iterations = settings.MAX_ARCHITECT_TOOL_ITERATIONS
    max_architect_tool_iterations_custom = settings.MAX_ARCHITECT_TOOL_ITERATIONS_CUSTOM_BUILD

    sub.set_entry_point("architect_precheck")
    sub.add_edge("architect_precheck", "architect_generate")  # Fast-path directly to code generation!

    # Architect generate → check if valid
    sub.add_conditional_edges(
        "architect_generate",
        should_repair,
        {
            "success": "renderer",
            "repair": "repair",
            "fail": "renderer"
        }
    )

    # Repair → architect_reason (on retry, investigate with tools first)
    sub.add_edge("repair", "architect_reason")

    # Architect reason routing: tool calls → tools loop, else → generate
    # Uses state-tracked arch_tool_iterations counter (reset to 0 by repair_node each repair cycle)
    def route_architect_reason(state: GraphState) -> str:
        from langchain_core.messages import AIMessage
        messages = state.get("messages", [])
        if not messages:
            return "architect_generate"
        last_msg = messages[-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            # Check for silent tool loop (repeating exact same calls)
            prev_ai = None
            for m in reversed(messages[:-1]):
                if isinstance(m, AIMessage) and getattr(m, 'tool_calls', None):
                    prev_ai = m
                    break
            
            if prev_ai and prev_ai.tool_calls == last_msg.tool_calls:
                logger.info("--- [NODE] GRAPH architect tool loop detected. Silently forcing generation.")
                return "sanitize_architect"

            arch_tool_count = state.get("arch_tool_iterations", 0)
            strategy = state.get("strategy_selector", "CUSTOM_BUILD")
            effective_limit = (
                max_architect_tool_iterations_custom
                if strategy == "CUSTOM_BUILD"
                else max_architect_tool_iterations
            )
            if arch_tool_count >= effective_limit:
                logger.info(f"--- [NODE] GRAPH architect tool limit reached ({arch_tool_count}/{effective_limit}, strategy={strategy}). proceeding to generate")
                return "sanitize_architect"
            return "architect_tools"
        return "architect_generate"

    # Standard ToolNode — registered normally so LangGraph runtime injects the store
    # into ToolRuntime for all store-reading tools. DO NOT call .invoke() directly.
    sub.add_node("architect_tools", ToolNode(get_architect_tools(), handle_tool_errors=True))

    # Tiny node that bumps arch_tool_iterations after each tool round.
    # Kept separate so it doesn't touch ToolNode's store injection path.
    def _incr_arch_iters(state: GraphState) -> Any:
        return {"arch_tool_iterations": state.get("arch_tool_iterations", 0) + 1}
    sub.add_node("incr_arch_iters", _incr_arch_iters)

    sub.add_conditional_edges("architect_reason", route_architect_reason, {
        "architect_tools": "architect_tools",
        "sanitize_architect": "sanitize_architect",
        "architect_generate": "architect_generate"
    })

    # After architect tools, increment counter then loop back to reason
    sub.add_edge("architect_tools", "incr_arch_iters")
    sub.add_edge("incr_arch_iters", "architect_reason")
    sub.add_edge("sanitize_architect", "architect_generate")
    
    # Renderer deterministically renders Mermaid DAG and compiles Groovy -> Direct to END
    sub.add_edge("renderer", END)

    # Pass store so LangGraph can inject it into store-dependent nodes (architect_precheck_node, architect_reason_node)
    return sub.compile(store=store)

def build_graph() -> Any:
    # Retaining InMemorySaver as requested by the user
    checkpointer = InMemorySaver()
    store = InMemoryStore()

    # Pass store to subgraphs so LangGraph injects it into store-dependent nodes
    sub_planner = build_consultant_subgraph(store=store)
    sub_executor = build_execution_subgraph(store=store)

    workflow = StateGraph(GraphState)
    workflow.add_node("planner", sub_planner)
    workflow.add_node("executor", sub_executor)

    workflow.set_entry_point("planner")

    workflow.add_conditional_edges(
        "planner",
        check_consultant_status,
        {
            "chatting": END,
            "approved": "executor"
        }
    )

    workflow.add_edge("executor", END)

    return workflow.compile(checkpointer=checkpointer, store=store), store

app_graph, global_store = build_graph()
