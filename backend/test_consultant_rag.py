import pytest
import json
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.store.memory import InMemoryStore

from app.services.tools import retrieve_rag_context, _inject_template, _inject_component
from app.services.agents import CONSULTANT_SYSTEM_PROMPT
from app.models.consultant_structure import ConsultantOutput
from app.services.llm import get_llm, get_judge_llm, with_rate_limit_retry
from app.core.loader import data_loader

from langgraph.graph import StateGraph, END
from app.services.graph_state import GraphState

from app.services.graph import hydrator_node, architect_node, diagram_node
from app.services.repair import repair_node, should_repair
from app.services.renderer import renderer_node

store = InMemoryStore()

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    print("Loading the real vector store and catalog for testing.")
    data_loader.load_all(store=store)
    print("Database loaded successfully.")

# ==========================================
# GLOBAL SCHEMAS & PROMPTS
# ==========================================

class AcademicEval(BaseModel):
    faithfulness_reason: str = Field(description="Step by step reasoning explaining if the AI invented tools or stuck perfectly to the RAG context.")
    faithfulness_score: int = Field(description="Score 1 to 5 based on the rubric.")
    relevance_reason: str = Field(description="Step by step reasoning explaining if the AI answered the specific biological scenario from the user.")
    relevance_score: int = Field(description="Score 1 to 5 based on the rubric.")

class ArchitectEval(BaseModel):
    syntax_reason: str = Field(description="Step by step reasoning evaluating the AST's representation of Nextflow DSL2 syntax.")
    syntax_score: int = Field(description="Score 1 to 5 based on the rubric.")
    logic_reason: str = Field(description="Step by step reasoning evaluating if the workflows and channels match the Consultant's design plan.")
    logic_score: int = Field(description="Score 1 to 5 based on the rubric.")

class DiagramEval(BaseModel):
    syntax_reason: str = Field(description="Step by step reasoning evaluating the Mermaid graph syntax.")
    syntax_score: int = Field(description="Score 1 to 5 based on the rubric.")
    mapping_reason: str = Field(description="Step by step reasoning evaluating if the graph matches the Nextflow code.")
    mapping_score: int = Field(description="Score 1 to 5 based on the rubric.")

# Reusable Agent Prompt
CONSULTANT_TEST_PROMPT = ChatPromptTemplate.from_messages([
    ("system", CONSULTANT_SYSTEM_PROMPT + "\n\nAVAILABLE RAG CONTEXT\n{context}"),
    ("placeholder", "{messages}")
])

# Reusable Strict Judge Prompt for Consultant
JUDGE_SYSTEM_STRING = """You are a very strict academic reviewer evaluating an AI system for bioinformatics. 
Read the RAG context and the conversation. Write your reasoning first then give the score based on these exact rules.

FAITHFULNESS SCORE RUBRIC
5 Perfect. The AI only uses the tools from the catalog and nothing else.
4 Good. The AI uses the tools but maybe adds a tiny general fact that is not in the text.
3 Okay. The AI uses the tools but talks a bit too much about outside tools.
2 Bad. The AI tries to put a tool in the pipeline that is not in the catalog.
1 Very bad. The AI completely makes up fake tools and fake pipeline names.

RELEVANCE SCORE RUBRIC
5 Perfect. The AI gives the exact right pipeline and follows all rules like checking the data type.
4 Good. The AI gives the right pipeline but maybe misses a very small preference from the user.
3 Okay. The AI gives a pipeline but ignores a big rule (like using a short read tool for long read data).
2 Bad. The AI picks the wrong template (like giving a bacteria pipeline when the user has a virus).
1 Very bad. The AI completely ignores what the user asked for.
"""

JUDGE_TEST_PROMPT = ChatPromptTemplate.from_messages([
    ("system", JUDGE_SYSTEM_STRING),
    ("human", "RAG Context\n{context}\n\nConversation History\n{chat}\n\nFinal AI Reply to Evaluate\n{reply}")
])

# Reusable Strict Judge Prompt for Architect
PIPELINE_JUDGE_SYSTEM_STRING = """You are a senior Bioinformatics Software Engineer evaluating AI-generated Nextflow DSL2 code.
The AI used the '{strategy}' strategy to build this.

CRITICAL INSTRUCTION. You are grading ONLY the code inside the <ai_generated_code_to_grade> tags. 
The <reference_technical_context> is just the original blueprint. Do not grade the reference material.

Read the Design Plan, the Technical Context, and the generated Nextflow Code. Write your reasoning first then score based on these exact rules.

SYNTAX SCORE RUBRIC
5 Perfect. Valid Nextflow DSL2 syntax with proper imports and correct channel emissions and valid workflow scopes.
4 Good. Valid syntax but has minor stylistic quirks.
3 Okay. Missing a minor output definition or slight channel mismatch.
2 Bad. Major Nextflow syntax violations.
1 Very bad. Not recognizable as valid Nextflow code.

LOGIC SCORE RUBRIC
5 Perfect. If EXACT_MATCH or ADAPTED_MATCH, it rigorously follows the template logic. If CUSTOM_BUILD, it stitches the requested components together logically from scratch.
4 Good. Implements the plan but misses a minor tool option or parameter from the original context.
3 Okay. Implements the core plan but forgets a requested module or deviates slightly from the provided blueprints.
2 Bad. Re-invents the pipeline and largely ignores the specific template logic provided in the Technical Context.
1 Very bad. Completely fails to implement the Design Plan and ignores the context entirely.
"""

PIPELINE_JUDGE_TEST_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PIPELINE_JUDGE_SYSTEM_STRING),
    ("human", "<design_plan>\n{plan}\n</design_plan>\n\n<reference_technical_context>\n{context}\n</reference_technical_context>\n\n<ai_generated_code_to_grade>\n{code}\n</ai_generated_code_to_grade>")
])

DIAGRAM_JUDGE_SYSTEM_STRING = """You are a strict code reviewer checking a Mermaid.js diagram.
The pipeline was built using the '{strategy}' strategy.

CRITICAL INSTRUCTION. You are grading ONLY the code inside the <ai_generated_mermaid_to_grade> tags.

Read the Technical Context, the generated Nextflow code, and the generated Mermaid code. Write your reasoning first then give the score based on these rules.

SYNTAX SCORE RUBRIC
5 Perfect. Valid Mermaid syntax with clear nodes and proper arrows.
4 Good. Valid syntax but maybe a bit cluttered.
3 Okay. Syntax is mostly fine but has a minor typo that might break one line.
2 Bad. Syntax uses invalid characters for node IDs or broken shapes.
1 Very bad. Not recognizable as Mermaid code.

MAPPING SCORE RUBRIC
5 Perfect. The graph maps all Nextflow processes from the code and matches the intent of the original Technical Context.
4 Good. Maps the code well but maybe misses one small channel name.
3 Okay. Captures the main idea but forgets several actual processes.
2 Bad. Invents steps that do not exist in the Nextflow code or connects them completely wrong.
1 Very bad. Completely ignores the Nextflow code and original context.
"""

DIAGRAM_JUDGE_TEST_PROMPT = ChatPromptTemplate.from_messages([
    ("system", DIAGRAM_JUDGE_SYSTEM_STRING),
    ("human", "<reference_technical_context>\n{context}\n</reference_technical_context>\n\n<reference_nextflow_code>\n{nf_code}\n</reference_nextflow_code>\n\n<ai_generated_mermaid_to_grade>\n{mermaid_code}\n</ai_generated_mermaid_to_grade>")
])

# ==========================================
# GLOBAL HELPER FUNCTIONS
# ==========================================

def get_exact_context(template_ids, component_ids, store):
    """Bypasses vector search to inject exact catalog items for deterministic logic testing."""
    found_ids = set()
    context_blocks = []
    for tid in template_ids:
        _inject_template(tid, found_ids, context_blocks, store, embed_code=False)
    for cid in component_ids:
        _inject_component(cid, found_ids, context_blocks, store, embed_code=False)
    return "\n".join(context_blocks) + "\n\n"

def force_approve_consultant(agent, real_context, chat_history, max_attempts=3):
    """Runs the agent and automatically retries if it gets stuck in CHATTING status."""
    chain = CONSULTANT_TEST_PROMPT | agent
    for attempt in range(max_attempts):
        result = chain.invoke({"context": real_context, "messages": chat_history})
        
        if result.status == "APPROVED":
            return result
            
        print(f"\n[Attempt {attempt+1}] Agent returned status '{result.status}'. Nudging for approval...")
        chat_history.append(AIMessage(content=result.response_to_user))
        chat_history.append(HumanMessage(content="I explicitly APPROVE this pipeline plan. I am ready to build. Please change your status to APPROVED and output the module IDs."))
    
    pytest.fail(f"Agent stubbornly refused to approve after {max_attempts} attempts. Final status: {result.status} | AI said: {result.response_to_user}")

def run_academic_judge(judge_llm, real_context, chat_history, ai_reply):
    """Runs the LLM judge for the Consultant."""
    formatted_chat = "\n".join([f"{m.type.capitalize()}: {m.content}" for m in chat_history])
    evaluation = (JUDGE_TEST_PROMPT | judge_llm).invoke({
        "context": real_context,
        "chat": formatted_chat,
        "reply": ai_reply
    })
    
    print("\nFaithfulness " + str(evaluation.faithfulness_score) + " - " + evaluation.faithfulness_reason)
    print("Relevance " + str(evaluation.relevance_score) + " - " + evaluation.relevance_reason)
    
    assert evaluation.faithfulness_score >= 4, f"Faithfulness score ({evaluation.faithfulness_score}) is too low. Reason {evaluation.faithfulness_reason}"
    assert evaluation.relevance_score >= 4, f"Relevance score ({evaluation.relevance_score}) is too low. Reason {evaluation.relevance_reason}"

def run_pipeline_judge(judge_llm, design_plan, tech_context, nf_code, strategy):
    """Runs the LLM judge for the rendered Nextflow code."""
    evaluation = (PIPELINE_JUDGE_TEST_PROMPT | judge_llm).invoke({
        "strategy": strategy,
        "plan": design_plan,
        "context": tech_context,
        "code": nf_code
    })
    
    print("\nSyntax " + str(evaluation.syntax_score) + " - " + evaluation.syntax_reason)
    print("Logic " + str(evaluation.logic_score) + " - " + evaluation.logic_reason)
    
    assert evaluation.syntax_score >= 4, f"Pipeline Syntax score ({evaluation.syntax_score}) is too low. Reason {evaluation.syntax_reason}"
    assert evaluation.logic_score >= 4, f"Pipeline Logic score ({evaluation.logic_score}) is too low. Reason {evaluation.logic_reason}"

def run_diagram_judge(judge_llm, tech_context, nf_code, mermaid_code, strategy):
    """Runs the LLM judge for the generated Mermaid diagram."""
    evaluation = (DIAGRAM_JUDGE_TEST_PROMPT | judge_llm).invoke({
        "strategy": strategy,
        "context": tech_context,
        "nf_code": nf_code,
        "mermaid_code": mermaid_code
    })
    
    print("\nDiagram Syntax " + str(evaluation.syntax_score) + " - " + evaluation.syntax_reason)
    print("Diagram Mapping " + str(evaluation.mapping_score) + " - " + evaluation.mapping_reason)
    
    assert evaluation.syntax_score >= 4, f"Diagram Syntax score ({evaluation.syntax_score}) is too low. Reason {evaluation.syntax_reason}"
    assert evaluation.mapping_score >= 4, f"Diagram Mapping score ({evaluation.mapping_score}) is too low. Reason {evaluation.mapping_reason}"

# ==========================================
# CONSULTANT TESTS
# ==========================================

def test_rag_retrieval_virologist_wnv():
    query = "We have a large bird die-off in the area. I suspect it is West Nile. I need a pipeline to analyze the sequence data and figure out the exact viral lineage."
    context = retrieve_rag_context(query, store, embed_code=False)
    
    assert "module_westnile" in context, "Context Relevance Failed. Missing template."
    assert "step_4TY_lineage__westnile" in context, "Context Relevance Failed. Missing tool."
    
@with_rate_limit_retry(max_attempts=3, delay_seconds=25)
def test_consultant_logic_and_quality_virologist_wnv():
    llm = get_llm()
    agent = llm.with_structured_output(ConsultantOutput)
    judge_llm = get_judge_llm(temperature=0.0).with_structured_output(AcademicEval)
    
    real_context = get_exact_context(["module_westnile"], ["step_4TY_lineage__westnile"], store)
    chat_history = [
        HumanMessage(content="We're dealing with a sudden cluster of dead crows and blue jays. We suspect a flavivirus, likely West Nile. I have paired-end Illumina reads. I need to figure out the exact viral lineage to trace the origin."),
        AIMessage(content="I can assist with that. The West Nile Virus surveillance pipeline (`module_westnile`) is designed exactly for this. It uses `step_4TY_lineage__westnile` to compute the lineage and then dynamically maps the reads. Does that sound like what you need?"),
        HumanMessage(content="That sounds right, but my reads are already trimmed by our core facility using fastp. Will that work with this pipeline?"),
        AIMessage(content="Yes, absolutely. The `module_westnile` template explicitly accepts `fastq_trimmed` as input, so your data is perfect for this. Shall I go ahead and finalize the pipeline design for the Architect?"),
        HumanMessage(content="Perfect. Yes, I completely approve the plan. I am ready to build.")
    ]
    
    result = force_approve_consultant(agent, real_context, chat_history)
    
    assert result.status == "APPROVED", f"Logic Failed. Expected status 'APPROVED', got '{result.status}'"
    assert result.strategy_selector == "EXACT_MATCH", f"Logic Failed. Expected strategy 'EXACT_MATCH', got '{result.strategy_selector}'"
    assert result.used_template_id == "module_westnile", f"Logic Failed. Expected 'module_westnile', got '{result.used_template_id}'"
    assert "step_4TY_lineage__westnile" in result.selected_module_ids, "Logic Failed. Missing 'step_4TY_lineage__westnile' module in selected_module_ids."
    
    assert result.draft_plan, "Logic Failed. 'draft_plan' cannot be empty when approved."
    assert len(result.draft_plan) > 20, "Logic Failed. 'draft_plan' is not adequately detailed."
    assert result.response_to_user, "Logic Failed. 'response_to_user' cannot be empty."

    run_academic_judge(judge_llm, real_context, chat_history, result.response_to_user)


# ==========================================
# ARCHITECT TESTS
# ==========================================

@with_rate_limit_retry(max_attempts=3, delay_seconds=25)
def test_execution_subgraph_westnile():
    judge_llm = get_judge_llm(temperature=0.0).with_structured_output(ArchitectEval)
    diagram_judge_llm = get_judge_llm(temperature=0.0).with_structured_output(DiagramEval)
    
    builder = StateGraph(GraphState)
    builder.add_node("hydrator", hydrator_node)
    builder.add_node("architect", architect_node)
    builder.add_node("repair", repair_node)
    builder.add_node("renderer", renderer_node)
    builder.add_node("diagram", diagram_node)
    
    builder.set_entry_point("hydrator")
    builder.add_edge("hydrator", "architect")
    builder.add_conditional_edges("architect", should_repair, {"success": "renderer", "repair": "repair", "fail": END})
    builder.add_edge("repair", "architect")
    builder.add_edge("renderer", "diagram")
    builder.add_edge("diagram", END)
    
    test_exec_graph = builder.compile(store=store)
    
    initial_state = {
        "user_query": "I have a large bird die-off. I need to figure out the exact viral lineage for West Nile.",
        "consultant_status": "APPROVED",
        "design_plan": "Execute the standard West Nile Virus surveillance pipeline.",
        "strategy_selector": "EXACT_MATCH",
        "used_template_id": "module_westnile",
        "selected_module_ids": ["step_4TY_lineage__westnile"],
        "retries": 0,
        "messages": [
            HumanMessage(content="I have a large bird die-off. I need to figure out the exact viral lineage for West Nile."),
            AIMessage(content="I will build the West Nile Virus surveillance pipeline for you. Please approve."),
            HumanMessage(content="I approve. Build it.")
        ]
    }
    
    config = {"configurable": {"thread_id": "test_execution_1"}}
    final_state = test_exec_graph.invoke(initial_state, config=config)
    
    assert final_state.get("validation_error") is None, f"Architect failed after {final_state.get('retries')} retries. Error {final_state.get('validation_error')}"
    
    tech_context = final_state.get("technical_context")
    assert tech_context is not None and len(tech_context) > 0, "Hydrator failed to assemble dynamic technical context."
    assert "process " in tech_context or "workflow " in tech_context, "Technical context appears to lack actual Nextflow component definitions."

    ast_json = final_state.get("ast_json")
    assert ast_json is not None, "Architect failed to generate the ast_json structure."
    assert isinstance(ast_json, dict), "ast_json must be a parsed dictionary object."

    nf_code = final_state.get("nextflow_code")
    assert nf_code is not None and len(nf_code) > 0, "Renderer generated completely empty Nextflow code."
    assert "workflow {" in nf_code or "workflow " in nf_code, "Rendered code chunk appears to be missing a main workflow definition block."
    
    mermaid_code = final_state.get("mermaid_code")
    assert mermaid_code is not None and len(mermaid_code) > 0, "Diagram node emitted unexpectedly empty Mermaid code string."
    assert "flowchart" in mermaid_code or "graph" in mermaid_code, "Diagram result string does not contain valid Mermaid diagram definitions."

    strategy = final_state.get("strategy_selector")

    print("\n--- Running Code Judge ---")
    run_pipeline_judge(judge_llm, final_state["design_plan"], tech_context, nf_code, strategy)
    
    print("\n--- Running Diagram Judge ---")
    run_diagram_judge(diagram_judge_llm, tech_context, nf_code, mermaid_code, strategy)