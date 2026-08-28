from typing import Any

from langchain_core.messages import HumanMessage

from core.config import settings
from core.services.graph_state import GraphState
from core.utils.logger import logger


from langgraph.store.base import BaseStore
import re

def repair_node(state: GraphState, store: BaseStore | None = None) -> Any:
    logger.info("repair_pipeline")
    error_msg = state.get("validation_error", "Unknown validation error.")

    # Build targeted advice based on the error type
    advice = ""
    if "UNDEFINED VAR" in error_msg:
        advice = """
HINT: The variable might actually be defined via .branch { name: ... } or .multiMap { name: ... } or (var1, var2) = ... patterns.
Check: Is it in take_channels? Is it assigned via = in body_code? Is it a .branch/.multiMap output name?
Use `search_design_patterns` to see if a specific idiom is needed to shape the variables correctly."""
    elif "HALLUCINATION" in error_msg and "Emitting undefined" in error_msg:
        advice = """
HINT: You are emitting a variable that was never assigned. Check: Did the process actually produce this output?
Use `check_component_channels` to verify what the process emits. Use the EXACT emit name, not a guess."""
    elif "CATALOG ERROR" in error_msg:
        # Extract the hallucinated component name
        match = re.search(r"Missing components/processes:\s*\n\s*-\s*([^\s\(]+)", error_msg)
        hallucinated = match.group(1) if match else "unknown"
        
        dynamic_hint = ""
        if hallucinated != "unknown" and store:
            try:
                res_item = store.get(("resources",), "helper_functions")
                if res_item and res_item.value:
                    helpers = res_item.value.get("list", [])
                    hal_lower = hallucinated.lower()
                    scored = []
                    for h in helpers:
                        h_name = h.get("name", "")
                        h_desc = h.get("description", "")
                        h_lower = h_name.lower()
                        score = 0
                        if hal_lower in h_lower:
                            score = 10 if hal_lower == h_lower.replace("get", "") else 5
                        elif hal_lower in h_desc.lower():
                            score = 2
                        if score > 0:
                            scored.append((score, h_name, h_desc))
                    if scored:
                        scored.sort(key=lambda x: x[0], reverse=True)
                        top_h = scored[0]
                        dynamic_hint = f"\nAre you trying to fetch input files? The closest valid helper function is `{top_h[1]}()` ({top_h[2]}). Use this instead of inventing a component."
            except Exception as e:
                logger.warning(f"Error checking helper functions in repair: {e}")
                
        advice = f"""
HINT: You used a component name '{hallucinated}' that doesn't exist in the catalog. This is NOT a real component.{dynamic_hint}
If this is meant to be a component, use `search_components` to find the correct component name. Do NOT invent process names."""
    elif "VOID TOOL" in error_msg:
        advice = """
HINT: This process produces no output channels. Call it directly without assigning to a variable. Do NOT try to .set or emit its result."""

    repair_instruction = f"""
**VALIDATION FAILED**
**THE ERROR:** {error_msg}
{advice}
**INSTRUCTION:**
You MUST use your tools to definitively find the exact component name, helper function, or syntax required. 
Do NOT tell me to use tools. YOU are the one with tools. 
Once you have the exact solution, output a clear, explicit explanation of the exact changes required in the AST to fix the error. Generate the **FULLY CORRECTED** JSON AST.
"""

    return {"messages": [HumanMessage(content=repair_instruction.strip())], "arch_tool_iterations": 0}

def should_repair(state: GraphState) -> str:
    # If a fatal error was set by any node, abort the repair loop immediately
    if state.get("error"):
        return "fail"

    max_retries = settings.MAX_REPAIR_RETRIES
    error = state.get("validation_error")
    retries = state.get("retries", 0)

    if not error: return "success"
    if retries >= max_retries: return "fail"
    return "repair"
