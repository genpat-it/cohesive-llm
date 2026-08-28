import json
from langchain_core.tools import tool
from core.models.diagram_structure import DiagramData, Node, Edge
from core.services.consultant_tools import lookup_catalog_item, find_component_usage
from core.services.renderer import render_mermaid_from_json
from langgraph.types import Command

@tool
def submit_diagram_structure(data: DiagramData) -> str:
    """
    Submits the final JSON structure for the diagram and ends your reasoning phase.
    You MUST call this when you are confident in the diagram structure.
    
    Args:
        data: The complete diagram data containing nodes and edges.
    """
    try:
        diagram_data = data
        
        # Render the diagram immediately
        mermaid_string = render_mermaid_from_json(diagram_data)
        
        result_dict = {
            "diagram_data": diagram_data.model_dump(),
            "mermaid_deterministic": mermaid_string,
            "mermaid_agent": mermaid_string
        }
        return json.dumps(result_dict)
    except Exception as e:
        # If validation fails, just return string error so the LLM can try again
        return f"ERROR: Validation failed. {e!s}"
