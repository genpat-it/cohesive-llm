import re
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.messages import RemoveMessage


from app.models.ast_structure import NextflowPipelineAST
from app.services.llm import get_llm
from app.services.tools import retrieve_rag_context
from app.services.graph_state import GraphState
from app.models.consultant_structure import ConsultantOutput
from app.core.loader import data_loader
from langgraph.store.base import BaseStore
from app.services.prompt_loader import load_consultant_prompt, load_architect_prompt

# ==========================================
# 1. SYSTEM PROMPTS
# ==========================================

# All prompts are now loaded from external markdown files for cleaner management
# Files:
#   - prompts/consultant_base.md + prompts/rejection_rules.md + data/catalog/TOOL_WHITELIST.md
#   - prompts/architect.md
#   - prompts/diagram.md
CONSULTANT_SYSTEM_PROMPT = load_consultant_prompt()
ARCHITECT_SYSTEM_PROMPT = load_architect_prompt()


# ==========================================
# 2. GRAPH NODES
# ==========================================

def consultant_node(state: GraphState, store: BaseStore):
    print("--- [NODE] CONSULTANT (Interactive Planner) ---")
    llm = get_llm()
    
    current_messages = state.get("messages", [])
    latest_query = state.get('user_query', '')
    if current_messages:
        latest_query = current_messages[-1].content

    metadata_context = retrieve_rag_context(latest_query, store, embed_code=False)
    print(f"[Consultant] RAG Context Retrieved: {len(metadata_context)} chars")
    
    print("\n" + "═" * 60)
    print("                 RAG METADATA CONTEXT")
    print("═" * 60)
    print(metadata_context)
    print("═" * 60 + "\n")

    current_plan = state.get("design_plan", "No plan generated yet.")
    current_modules = state.get("selected_module_ids", [])
    
    revision_context = f"""
    # CURRENT PIPELINE STATE
    If you are making a revision, here is the current approved state of the pipeline:
    - Current Modules: {current_modules}
    - Current Plan: {current_plan}
    """
    # --------------------------------

    prompt = ChatPromptTemplate.from_messages([
        ("system", CONSULTANT_SYSTEM_PROMPT + "\n\nAVAILABLE RAG CONTEXT (Tools & Templates):\n{context}\n\n" + revision_context),
        MessagesPlaceholder(variable_name="messages")
    ])

    consultant_agent = llm.with_structured_output(ConsultantOutput)
    chain = prompt | consultant_agent

    try:
        result = chain.invoke({
            "context": metadata_context,
            "messages": current_messages
        })
        
        print(f"[Consultant] Status: {result.status}")

        if result.status == "APPROVED":
            
            # 1. Verify Template ID against the Store
            if result.used_template_id:
                tmpl_item = store.get(("templates",), result.used_template_id)
                if not tmpl_item:
                    print(f"⚠️ Consultant Hallucinated Template ID: '{result.used_template_id}'. Stripping from plan.")
                    result.used_template_id = None
                
            # 2. Verify Component IDs against the Store
            verified_modules = []
            for mod_id in result.selected_module_ids:
                comp_item = store.get(("components",), mod_id)
                if comp_item:
                    verified_modules.append(mod_id)
                else:
                    # Check if they accidentally put a template ID in the module list
                    tmpl_fallback = store.get(("templates",), mod_id)
                    if tmpl_fallback:
                        pass 
                    else:
                        print(f"⚠️ Consultant Hallucinated Module ID: '{mod_id}'. Stripping from plan.")
            
            result.selected_module_ids = verified_modules

        # Detect a "Hard Reset" from the LLM (user asked to start over completely)
        is_hard_reset = (result.status == "CHATTING" and result.draft_plan == "" and len(result.selected_module_ids) == 0)

        # Prepare the baseline state updates
        state_updates = {
            "messages": [AIMessage(content=result.response_to_user)],
            "consultant_status": result.status,
            "design_plan": result.draft_plan if (result.status == "APPROVED" or is_hard_reset) else state.get("design_plan"),
            "strategy_selector": result.strategy_selector if result.status == "APPROVED" else state.get("strategy_selector", "CUSTOM_BUILD"),
            "used_template_id": result.used_template_id if (result.status == "APPROVED" or is_hard_reset) else state.get("used_template_id"),
            "selected_module_ids": result.selected_module_ids if (result.status == "APPROVED" or is_hard_reset) else state.get("selected_module_ids", []),
            "error": None
        }

        # POST-GENERATION REVISION TRIGGER
        # Wipe the old execution data so the frontend knows we are rebuilding or resetting
        if result.status == "CHATTING" or (result.status == "APPROVED" and state.get("nextflow_code")):
            state_updates["nextflow_code"] = None
            state_updates["mermaid_code"] = None
            state_updates["ast_json"] = None

        return state_updates
        
    except Exception as e:
        print(f"💥 Consultant Node Failed: {str(e)}")
        return {"error": f"Consultant Agent Failed: {str(e)}"}


def architect_node(state: GraphState):
    print("--- [NODE] ARCHITECT (Hybrid Code Generator) ---")
    if state.get("error"): return {"error": state['error']}
    
    llm = get_llm()
    architect_agent = llm.with_structured_output(NextflowPipelineAST, method="json_schema", include_raw=False)

    prompt = ChatPromptTemplate.from_messages([
        ("system", ARCHITECT_SYSTEM_PROMPT),
        ("human", "APPROVED PLAN:\n{plan}\n\nTECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}")
    ])
        
    messages = prompt.invoke({
        "plan": state.get('design_plan', 'No plan provided.'),
        "tech_context": state.get('technical_context', 'No context provided.')
    }).to_messages()

    try:
        result = architect_agent.invoke(messages)
        print("[Architect] Successfully generated Hybrid AST.")
        return {
            "ast_json": result.model_dump(),
            "validation_error": None
        }
    except Exception as e:
        print(f"⚠️ Architect Validation Failed: {str(e)}")
        
        raw_ast = {}
        # Attempt best-effort extraction from OutputParserException or ValidationError
        llm_output = getattr(e, "llm_output", None)
        if llm_output and isinstance(llm_output, str):
            import json, re
            try:
                content = llm_output
                match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                raw_ast = json.loads(content)
            except Exception:
                pass
                
        return {
            "ast_json": raw_ast,
            "validation_error": str(e),
            "retries": state.get("retries", 0) + 1
        }
    

def diagram_node(state: GraphState):
    print("--- [NODE] DIAGRAM (Deterministic AST -> Mermaid) ---")
    if state.get("error"): return {"error": state['error']}

    ast_json = state.get("ast_json", {})
    if not ast_json:
        print("[Diagram] Warning: No AST found.")
        return {"mermaid_code": "flowchart TD\n    Empty[No AST generated]"}

    try:
        from app.services.renderer import render_mermaid_from_ast
        mermaid_string = render_mermaid_from_ast(ast_json)
        print(f"[Diagram] Mermaid generated from AST ({len(mermaid_string)} chars)")
        return {"mermaid_code": mermaid_string}
    except Exception as e:
        print(f"[Diagram] Error: {e}")
        return {"mermaid_code": f'flowchart TD\n    Error["Diagram error: {str(e)[:100]}"]'}
    
def filter_template_logic(code: str, allowed_components: set) -> str:
    lines = code.split('\n')
    filtered_lines = []
    
    pattern = re.compile(r'\b((?:step_|module_|multi_)[a-zA-Z0-9_]+)\s*\(')
    
    for line in lines:
        match = pattern.search(line)
        if match:
            func_name = match.group(1)
            
            if func_name not in allowed_components:
                filtered_lines.append(f"    // [REMOVED BY PLAN] {line.strip()}")
                continue
        
        filtered_lines.append(line)
        
    return "\n".join(filtered_lines)

def hydrator_node(state: GraphState, store: BaseStore):
    print("--- [NODE] HYDRATOR (Context Assembly) ---")

    if state.get("error"):
        return {"error": state["error"]}
    
    context_parts = []
    detected_helpers = set()

    strategy = state.get('strategy_selector', 'CUSTOM_BUILD')
    used_template_id = state.get('used_template_id')
    module_ids = state.get('selected_module_ids', [])
    plan_text = state.get('design_plan', '')

    # Access Data from Store
    RES_ITEM = store.get(("resources",), "helper_functions")

    RES_LIST = RES_ITEM.value.get("list", []) if RES_ITEM else []

    helper_names = [r['name'] for r in RES_LIST]

    # ==========================================
    # PATH A: STRICT TEMPLATE MODE
    # ==========================================
    if strategy == "EXACT_MATCH" and used_template_id:
        tmpl_id = used_template_id
        tmpl_item = store.get(("templates",), tmpl_id)
        template_def = tmpl_item.value if tmpl_item else None

        context_parts.append(f"### STRICT TEMPLATE MODE: {tmpl_id}")
        if template_def:
            context_parts.append(f"Description: {template_def.get('description')}")
            
            code_item = store.get(("code",), tmpl_id)

            # print("code_item", code_item)

            tmpl_code = code_item.value.get("content") if code_item else None

            # print("tmpl_code", tmpl_code)
            
            if tmpl_code:
                context_parts.append(f"[[TEMPLATE SOURCE CODE: {tmpl_id}]]")
                context_parts.append("INSTRUCTION: Use the logic in this workflow block exactly.")
                context_parts.append(f"```groovy\n{tmpl_code.strip()}\n```")
                context_parts.append(f"[[END TEMPLATE SOURCE]]")
                
                for h in helper_names:
                    if h in tmpl_code: detected_helpers.add(h)
            
            for step in template_def.get('logic_flow', []):
                if 'step' in step:
                    comp_id = step['step']
                    c_item = store.get(("code",), comp_id)
                    code = c_item.value.get("content") if c_item else None
                    if code:
                        context_parts.append(f"[[REFERENCE FOR STEP: {comp_id}]]")
                        context_parts.append(f"```groovy\n{code.strip()}\n```")
                        context_parts.append(f"[[END REFERENCE]]")
                        
                        for h in helper_names:
                            if h in code: detected_helpers.add(h)

    # ==========================================
    # PATH B: ADAPTED OR CUSTOM MODE
    # ==========================================
    else:
        if strategy == "ADAPTED_MATCH" and used_template_id:
            context_parts.append(f"### ADAPTED TEMPLATE MODE: Based on {used_template_id}")
            t_item = store.get(("code",), used_template_id)
            tmpl_code = t_item.value.get("content") if t_item else None

            if tmpl_code:
                # We combine the template ID and the new module IDs into the allowed list
                allowed_ids = set([used_template_id] + module_ids)
                
                filtered_code = filter_template_logic(tmpl_code, allowed_ids)

                context_parts.append(f"[[TEMPLATE SOURCE CODE: {used_template_id}]]")
                context_parts.append("INFO: Some steps in this template have been commented out because they are not in your Design Plan.")
                context_parts.append("INSTRUCTION: Reuse the logic that remains, but FILL THE GAPS using your new components.")
                context_parts.append(f"```groovy\n{filtered_code.strip()}\n```")
                
                for h in helper_names:
                    if h in tmpl_code: detected_helpers.add(h)        
        else:
            context_parts.append("### CUSTOM BUILD MODE")

        # We loop through the simple list of strings now
        for comp_id in module_ids:
            if comp_id == used_template_id and strategy == "ADAPTED_MATCH":
                continue

            code_item = store.get(("code",), comp_id)
            source_code = code_item.value.get("content") if code_item else None
            
            if source_code:
                context_parts.append(f"[[REFERENCE FOR STEP: {comp_id}]]")
                context_parts.append(f"Component ID: {comp_id}")
                context_parts.append(f"```groovy\n{source_code.strip()}\n```")
                context_parts.append(f"[[END REFERENCE: {comp_id}]]")
                for h in helper_names:
                    if h in source_code: detected_helpers.add(h)

    # ==========================================
    # RESOURCE INJECTION
    # ==========================================
    if plan_text and ("cross" in plan_text or "multiMap" in plan_text):
        detected_helpers.add("extractKey")
    
    if detected_helpers:
        context_parts.append("\n### AVAILABLE HELPER FUNCTIONS")
        for h_name in detected_helpers:
            res_def = next((r for r in RES_LIST if r['name'] == h_name), None)
            if res_def:
                context_parts.append(f"- {h_name}: {res_def.get('description')}")
                context_parts.append(f"  Usage: `{res_def.get('usage')}`")
                
    full_context = "\n\n".join(context_parts)
    # print(f"technical_context: {full_context}")

    return {"technical_context": full_context}