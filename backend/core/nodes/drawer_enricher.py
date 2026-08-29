from typing import Any
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

import structlog

from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_drawer_enricher_prompt
from core.tool_registry import get_consultant_tools
from core.utils.retry import with_exponential_backoff

logger = structlog.get_logger(__name__)


class DrawerEnrichmentOutput(BaseModel):
    """Structured extraction for drawer enrichment."""
    draft_plan: str = Field(
        ...,
        description="Detailed Nextflow DSL2 architectural blueprint including channel operators (.cross, .collect, .map) and parameter bindings.",
    )
    selected_component_ids: list[str] = Field(
        ...,
        description="Exact list of all required component IDs (both placed and any newly injected prerequisite steps).",
    )
    strategy_selector: str = Field(
        default="CUSTOM_BUILD",
        description="Execution strategy (CUSTOM_BUILD or ADAPTED_MATCH).",
    )
    used_template_id: str | None = Field(
        default=None,
        description="Base template ID if applicable, otherwise null.",
    )


def drawer_enrich_node(state: GraphState) -> Any:
    """Enriches a visual canvas graph by analyzing channel schemas, injecting required

    Nextflow DSL2 operators (.cross, .collect, .map), and bridging missing prerequisite steps.
    """
    logger.info("node_start", node="drawer_enrich")

    visual_topology = state.get("visual_topology") or {}
    components = visual_topology.get("components", state.get("selected_component_ids", []))
    wires = visual_topology.get("wires", [])

    # Format visual topology for LLM reasoning
    wiring_lines = []
    for w in wires:
        src = w.get("source_id")
        tgt = w.get("target_id")
        src_ch = w.get("source_channel") or w.get("source_port", "out")
        tgt_ch = w.get("target_channel") or w.get("target_port", "in")
        wiring_lines.append(f"- `{src}` (out: `{src_ch}`) -> `{tgt}` (in: `{tgt_ch}`)")

    wiring_text = "\n".join(wiring_lines) if wiring_lines else "No explicit wires drawn."
    # ── Hydrate schemas from Knowledge Graph & Component DB ───────────────────
    from core.services.knowledge_graph import kg
    from core.loader import data_loader

    schema_blocks = []

    for cid in components:
        node_info = kg.G.nodes.get(cid, {}) if (kg.is_built and cid in kg.G) else {}
        comp_info = data_loader.comp_db.get(cid, {}) if hasattr(data_loader, "comp_db") else {}

        takes = node_info.get("inputs") or comp_info.get("input_channels") or comp_info.get("input_types", [])
        emits = node_info.get("outputs") or comp_info.get("output_channels") or comp_info.get("out", [])
        desc = node_info.get("description") or comp_info.get("description", "")

        schema_blocks.append(
            f"- Component: `{cid}`\n"
            f"  - Inputs (takes): {takes if takes else ['(none or entrypoint input)']}\n"
            f"  - Outputs (emits): {emits if emits else ['(void / publishDir only)']}\n"
            f"  - Description: {desc[:200]}"
        )

    schemas_text = "\n".join(schema_blocks) if schema_blocks else "No component schema details found."

    # ── Path & Connectivity Analysis from Knowledge Graph ────────────────────
    path_insights = []
    if kg.is_built and len(components) > 1:
        for i in range(len(components) - 1):
            u, v = components[i], components[i + 1]
            path = kg.find_path(u, v)
            if path:
                path_insights.append(f"- Verified Dataflow Path from `{u}` to `{v}`: {' -> '.join(path)}")
            elif kg.G.has_node(u) and kg.G.has_node(v):
                path_insights.append(f"- Direct link `{u}` -> `{v}`: verify channel operator compatibility.")

    path_text = "\n".join(path_insights) if path_insights else "Direct linear connectivity."

    context_text = (
        f"### USER PLACED COMPONENTS & SCHEMAS (FROM KNOWLEDGE GRAPH & CATALOG):\n{schemas_text}\n\n"
        f"### VISUAL CANVAS WIRES & DATAFLOW:\n{wiring_text}\n\n"
        f"### KNOWLEDGE GRAPH DATAFLOW INSIGHTS:\n{path_text}\n\n"
        f"### TASK:\n"
        f"Complete and enrich ('melengkapi') this visual pipeline design into a rigorous Nextflow DSL2 architecture.\n"
        f"1. Check channel types and metadata tuple compatibility.\n"
        f"2. Inject necessary DSL2 channel operators (.cross(), .join(), .multiMap{{}}, .collect(), .map{{}}, param() for references).\n"
        f"3. Add any missing intermediate prerequisite components if required.\n"
        f"4. Output the finalized blueprint and complete selected_component_ids list."
    )

    llm = get_llm()
    system_prompt = load_drawer_enricher_prompt()

    enricher_agent = llm.with_structured_output(DrawerEnrichmentOutput, method="json_schema", include_raw=True)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context_text),
    ]

    try:
        response_dict = with_exponential_backoff(enricher_agent.invoke)(messages)
        parsed: DrawerEnrichmentOutput | None = response_dict.get("parsed") if isinstance(response_dict, dict) else response_dict

        if parsed:
            final_ids = parsed.selected_component_ids or components
            final_plan = parsed.draft_plan
            logger.info("drawer_enrich_success", extracted_ids=len(final_ids))
            return {
                "design_plan": final_plan,
                "selected_component_ids": final_ids,
                "strategy_selector": parsed.strategy_selector or "CUSTOM_BUILD",
                "used_template_id": parsed.used_template_id,
                "consultant_status": "APPROVED",
                "messages": [AIMessage(content="Visual pipeline design enriched and approved.")],
            }

        # Fallback if structured output parsed is None
        logger.warning("drawer_enrich_parsed_none_fallback")
        return {
            "design_plan": state.get("design_plan") or context_text,
            "selected_component_ids": components,
            "strategy_selector": "CUSTOM_BUILD",
            "used_template_id": None,
            "consultant_status": "APPROVED",
            "messages": [AIMessage(content="Visual pipeline design approved.")],
        }

    except Exception as e:
        logger.error("drawer_enrich_error", error=str(e))
        return {
            "design_plan": state.get("design_plan") or context_text,
            "selected_component_ids": components,
            "strategy_selector": "CUSTOM_BUILD",
            "used_template_id": None,
            "consultant_status": "APPROVED",
            "messages": [AIMessage(content="Visual pipeline design approved.")],
        }
