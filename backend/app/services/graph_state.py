from typing import TypedDict, Any, Dict, Optional, List, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class GraphState(TypedDict):
    user_query: str
    
    # --- Planner / Consultant State ---
    consultant_status: Optional[str]
    design_plan: Optional[str]
    
    # --- Hydrator Routing State ---
    strategy_selector: Optional[str]      # e.g., EXACT_MATCH, ADAPTED_MATCH, CUSTOM_BUILD
    used_template_id: Optional[str]       # The specific template ID if applicable
    selected_module_ids: List[str]        # List of individual tool IDs from RAG
    technical_context: Optional[str]      # The final assembled Groovy code string
    
    # --- Architect & Renderer State ---
    ast_json: Optional[Dict[str, Any]]
    nextflow_code: Optional[str]
    mermaid_code: Optional[str]
    
    # --- Memory & Errors ---
    error: Optional[str]
    validation_error: Optional[str]
    retries: int

    # The add_messages reducer handles our short-term memory trimming
    messages: Annotated[List[BaseMessage], add_messages]