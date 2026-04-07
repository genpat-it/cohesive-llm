from langchain_core.messages import HumanMessage
from langgraph.graph import END
from app.services.graph_state import GraphState
from app.services.agents import ARCHITECT_SYSTEM_PROMPT

def repair_node(state: GraphState):
    print("--- [NODE] REPAIR ---")
    error_msg = state.get("validation_error", "Unknown validation error.")

    repair_instruction = f"""
    **VALIDATION FAILED**
    **THE ERROR:** {error_msg}
    
    ⚠️ **CRITICAL: YOU ARE DRIFTING FROM THE SCHEMA**
    **HERE IS THE STRICT RULEBOOK. READ IT AGAIN:**
    {ARCHITECT_SYSTEM_PROMPT}
    
    **INSTRUCTION:**
    1. Read the error message above.
    2. Generate the **FULLY CORRECTED** JSON AST.
    """
    
    # Just return the new message. The reducer handles the rest.
    return {"messages": [HumanMessage(content=repair_instruction)]}

def should_repair(state: GraphState):
    MAX_RETRIES = 8
    error = state.get("validation_error")
    retries = state.get("retries", 0)

    if not error: return "success"
    if retries >= MAX_RETRIES: return "fail"
    return "repair"