from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict, total=False):
    """LangGraph state representation for the 100% plugin-agnostic Nextflow pipeline generator.
    
    INVARIANT RULE: All nodes and fields must remain completely plugin-agnostic with zero
    hardcoded tool names or biology-specific strings in core/. All tools and channel types
    are discovered and wired dynamically via runtime catalog reflection.
    """
    user_query: str
    execution_mode: str | None  # "interactive" (default: awaits user approval) or "direct" (auto-approved)
    generate_diagrams: bool | None

    # --- Planner / Consultant State ---
    consultant_status: str | None
    design_plan: str | None
    tool_memory: list[dict] | None  # Structured: [{tool, args, result}, ...]

    # --- Hydrator Routing State ---
    strategy_selector: str | None      # e.g., EXACT_MATCH, ADAPTED_MATCH, CUSTOM_BUILD
    used_template_id: str | None       # The specific template ID if applicable
    selected_component_ids: list[str]        # List of individual tool IDs from RAG
    technical_context: str | None      # The final assembled Groovy code string

    # --- Architect & Renderer State ---
    ast_json: dict[str, Any] | None
    nextflow_code: str | None
    mermaid_agent: str | None
    mermaid_deterministic: str | None

    # --- Memory & Errors ---
    error: str | None
    validation_error: str | None
    retries: int
    arch_tool_iterations: int  # Resets each repair phase; counts architect tool calls
    past_ast_hashes: list[str] | None  # Tracks AST hashes to prevent repair loops

    # The add_messages reducer handles our short-term memory trimming
    messages: Annotated[list[BaseMessage], add_messages]
    diagram_messages: Annotated[list[BaseMessage], add_messages]
    diagram_data: dict[str, Any] | None
