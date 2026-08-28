from typing import Any
from core.services.graph_state import GraphState
from core.services.renderer import render_mermaid_from_ast
from core.utils.logger import logger

def diagram_reason_node(state: GraphState) -> dict:
    logger.info("node_start", node="diagram_reason")
    if state.get("error"):
        return {}

    ast_json = state.get("ast_json")
    if not ast_json:
        return {"error": "No AST generated. Cannot diagram."}

    try:
        mermaid_string = render_mermaid_from_ast(ast_json)
        return {
            "mermaid_deterministic": mermaid_string,
            "mermaid_agent": mermaid_string,
        }
    except Exception as e:
        logger.error(f"Failed to generate deterministic Mermaid diagram: {e}")
        fallback_diag = "flowchart TD\n    start([Start]) --> finish([Finish])"
        return {
            "mermaid_deterministic": fallback_diag,
            "mermaid_agent": fallback_diag,
        }

