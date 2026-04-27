"""
tests/helpers.py
Shared helper functions for the IZS test suite.

API Helpers (for L1–L5 tests via /chat endpoint):
  - send_chat(): sends a message to the API and returns the parsed response
  - run_multi_turn_chat(): drives a full multi-turn conversation through the API
  - run_with_retries(): runs a test function up to N times, keeps the best result
  - rate_limit_pause(): sleeps between API calls to respect rate limits

Isolated Helpers (for direct agent/judge invocation, no API):
  - get_exact_context(): bypasses vector search, injects exact catalog items
  - force_approve_consultant(): runs the agent chain directly with auto-retry
  - run_academic_judge(): invokes the consultant LLM judge
  - run_pipeline_judge(): invokes the Nextflow code LLM judge
  - run_diagram_judge(): invokes the Mermaid diagram LLM judge
"""
import os
import time
import uuid


# ──────────────────────────────────────────────────────────────
# Rate Limit Protection
# ──────────────────────────────────────────────────────────────

DEFAULT_PAUSE_BETWEEN_TURNS = 5    # seconds between turns in a conversation
DEFAULT_PAUSE_BETWEEN_TESTS = 15   # seconds between separate test scenarios
DEFAULT_PAUSE_ON_ERROR = 30        # seconds to wait after a rate limit / server error


def rate_limit_pause(seconds=15, reason="rate limit protection"):
    """Pause execution for rate limit protection."""
    # Hardcoded default to False (disabled for judge), then check environment
    disable_judge_rate = True 
    if os.environ.get("JUDGE_RATE_LIMIT", "false").lower() == "true":
        disable_judge_rate = False

    is_judge = "judge" in reason.lower()

    if is_judge and disable_judge_rate:
        return

    print(f"\n⏳ Pausing {seconds}s ({reason})...")
    time.sleep(seconds)
    print("▶️ Resuming.")


# ──────────────────────────────────────────────────────────────
# API Client
# ──────────────────────────────────────────────────────────────

def send_chat(
    client,
    session_id: str,
    message: str,
    timeout: int = 120,
) -> dict:
    """
    Send a single message to the /chat API endpoint.

    Returns a dict with keys:
        success, status, reply, nextflow_code, mermaid_code,
        ast_json, elapsed, error
    """
    url = "/chat"
    payload = {
        "session_id": session_id,
        "message": message,
    }

    start = time.time()
    try:
        resp = client.post(url, json=payload, timeout=timeout)
        elapsed = time.time() - start

        if resp.status_code != 200:
            return {
                "success": False,
                "status": "HTTP_ERROR",
                "reply": None,
                "nextflow_code": None,
                "mermaid_code": None,
                "ast_json": None,
                "elapsed": elapsed,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }

        data = resp.json()
        return {
            "success": True,
            "status": data.get("status", "UNKNOWN"),
            "reply": data.get("reply"),
            "nextflow_code": data.get("nextflow_code"),
            "mermaid_code": data.get("mermaid_code"),
            "ast_json": data.get("ast_json"),
            "elapsed": elapsed,
            "error": data.get("error"),
        }

    except Exception as e:
        if 'timeout' in str(e).lower():
            return {
                "success": False, "status": "TIMEOUT", "reply": None,
                "nextflow_code": None, "mermaid_code": None,
                "ast_json": None,
                "elapsed": time.time() - start, "error": "Request timed out",
            }
        return {
            "success": False, "status": "CONNECTION_ERROR", "reply": None,
            "nextflow_code": None, "mermaid_code": None,
            "ast_json": None,
            "elapsed": time.time() - start, "error": str(e),
        }


def run_multi_turn_chat(
    client,
    chat_messages: list[str],
    expect_rejection: bool = False,
    pause_between_turns: int = DEFAULT_PAUSE_BETWEEN_TURNS,
) -> dict:
    """
    Drive a full multi-turn conversation through the API.

    Parameters
    ----------
    chat_messages : list[str]
        List of user messages to send in order. The AI replies between them
        are driven by the API (via thread memory).
    expect_rejection : bool
        If True, return after the first response (we expect CHATTING / rejection).

    Returns
    -------
    dict with: success, status, reply, nextflow_code, mermaid_code,
    ast_json, elapsed, turns, all_replies
    """
    session_id = f"test_{uuid.uuid4().hex[:12]}"
    total_start = time.time()
    all_replies = []

    for turn_idx, user_msg in enumerate(chat_messages):
        result = send_chat(client, session_id, user_msg)

        if not result["success"]:
            result["turns"] = turn_idx + 1
            result["all_replies"] = all_replies
            return result

        all_replies.append({"turn": turn_idx + 1, "reply": result["reply"], "status": result["status"]})

        # For rejection tests, return after first response
        if expect_rejection:
            result["turns"] = turn_idx + 1
            result["elapsed"] = time.time() - total_start
            result["all_replies"] = all_replies
            return result

        # If we got APPROVED with code, we're done
        if result["status"] == "APPROVED" and result.get("nextflow_code"):
            result["turns"] = turn_idx + 1
            result["elapsed"] = time.time() - total_start
            result["all_replies"] = all_replies
            return result

        # Pause between turns
        if turn_idx < len(chat_messages) - 1:
            rate_limit_pause(pause_between_turns, f"between turn {turn_idx + 1} and {turn_idx + 2}")

    # If we exhausted all messages without APPROVED, return last result
    result["turns"] = len(chat_messages)
    result["elapsed"] = time.time() - total_start
    result["all_replies"] = all_replies
    return result


def run_with_retries(
    test_fn,
    max_retries=2,
    pause_between=DEFAULT_PAUSE_ON_ERROR,
    stop_avg_score=5.0,
):
    """
    Run a test function up to max_retries times, keeping the BEST result.
    
    The test_fn should return a dict with at least a 'scores' dict.
    The "best" result is the one with the highest average score.

    Retries even on "success" unless stop_avg_score is achieved.

    Returns
    -------
    best_result
    """
    all_results = []
    # Hardcoded default to False, then check env
    disable_judge_rate = True
    if os.environ.get("JUDGE_RATE_LIMIT", "false").lower() == "true":
        disable_judge_rate = False

    for attempt in range(1, max_retries + 1):
        print(f"\n{'='*60}")
        print(f"  ATTEMPT {attempt} / {max_retries}")
        print(f"{'='*60}")

        try:
            result = test_fn()
            result["attempt"] = attempt
            result["error"] = None
            all_results.append(result)
            
            # EARLY EXIT: If we hit the configured score threshold, stop retrying.
            avg_score = _avg_scores(result.get("scores", {}))
            if avg_score >= stop_avg_score:
                print(
                    f"🌟 Early stop threshold ({stop_avg_score}) achieved on "
                    f"attempt {attempt} with avg score {avg_score}. Skipping further trials."
                )
                break
                
        except Exception as e:
            error_str = str(e).lower()
            all_results.append({"attempt": attempt, "error": str(e), "scores": {}})

            # If rate limit, pause longer
            if "429" in error_str or "rate limit" in error_str:
                is_judge_error = "judge" in error_str
                if not (is_judge_error and disable_judge_rate):
                    print(f"\n⚠️ Rate limit hit on attempt {attempt}. Pausing {pause_between}s...")
                    rate_limit_pause(pause_between, "rate limit recovery")
            elif attempt < max_retries:
                rate_limit_pause(pause_between // 2, "error recovery")

        # Pause between retries to protect the main LLM (but not if it's judge-only)
        if attempt < max_retries:
            rate_limit_pause(DEFAULT_PAUSE_BETWEEN_TESTS, "between retry attempts (main llm)")

    # Pick the best result (highest average score)
    best = None
    best_avg = -1
    for r in all_results:
        avg = _avg_scores(r.get("scores", {}))
        if avg > best_avg:
            best_avg = avg
            best = r

    if best is None:
        best = all_results[-1] if all_results else {"error": "No results", "scores": {}}

    best["total_attempts"] = len(all_results)
    best["all_attempts_summary"] = [
        {"attempt": r.get("attempt"), "error": r.get("error"), "avg_score": _avg_scores(r.get("scores", {}))}
        for r in all_results
    ]

    return best


def _avg_scores(scores: dict) -> float:
    """Calculate average of all score fields in a dict."""
    vals = [v for k, v in scores.items() if isinstance(v, (int, float)) and "score" in k]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


# ──────────────────────────────────────────────────────────────
# Isolated Testing Helpers (Direct Invocation, No API)
# ──────────────────────────────────────────────────────────────

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from app.services.tools import _inject_template, _inject_component
from tests.evaluation.prompts import (
    CONSULTANT_TEST_PROMPT,
    JUDGE_PROMPT,
    PIPELINE_JUDGE_PROMPT,
    DIAGRAM_JUDGE_PROMPT,
)
from tests.evaluation.schemas import AcademicEval, ArchitectEval, DiagramEval, RejectionEval, CodeRecreationEval


def get_exact_context(template_ids, component_ids, store):
    """Bypasses vector search to inject exact catalog items for deterministic logic testing.

    Pulls items directly from the InMemoryStore using the same
    _inject_template / _inject_component functions used by the
    production RAG pipeline, but skips all scoring and ranking.
    """
    found_ids = set()
    context_blocks = []
    for tid in template_ids:
        _inject_template(tid, found_ids, context_blocks, store, embed_code=False)
    for cid in component_ids:
        _inject_component(cid, found_ids, context_blocks, store, embed_code=False)
    return "\n".join(context_blocks) + "\n\n"


def force_approve_consultant(agent, real_context, chat_history, max_attempts=3):
    """Runs the consultant agent chain and auto-retries if it stays in CHATTING status.

    Uses direct chain invocation (prompt | agent), matching the
    production pattern from agents.py consultant_node.

    Parameters
    ----------
    agent : Runnable
        The LLM wrapped with `.with_structured_output(ConsultantOutput)`.
    real_context : str
        Deterministic RAG context from `get_exact_context`.
    chat_history : list[BaseMessage]
        The conversation messages to feed to the agent.
    max_attempts : int
        Maximum retries before failing the test.

    Returns
    -------
    ConsultantOutput
        The agent's structured output with status == APPROVED.
    """
    chain = CONSULTANT_TEST_PROMPT | agent
    for attempt in range(max_attempts):
        result = chain.invoke({"context": real_context, "messages": chat_history})

        if result.status == "APPROVED":
            return result

        print(f"\n[Attempt {attempt+1}] Agent returned status '{result.status}'. Nudging for approval...")
        chat_history.append(AIMessage(content=result.response_to_user))
        chat_history.append(HumanMessage(content="I explicitly APPROVE this pipeline plan. I am ready to build. Please change your status to APPROVED and output the module IDs."))

    pytest.fail(f"Agent stubbornly refused to approve after {max_attempts} attempts. Final status: {result.status} | AI said: {result.response_to_user}")


def run_academic_judge(judge_llm, real_context, chat_history, ai_reply, design_plan=None):
    """Runs the LLM judge for the Consultant (faithfulness + relevance).

    Invokes the production-grade JUDGE_PROMPT with AcademicEval
    structured output. Returns a dictionary of results.
    """
    judge = judge_llm.with_structured_output(AcademicEval)
    formatted_chat = "\n".join([f"{m.type.capitalize()}: {m.content}" for m in chat_history])
    evaluation = (JUDGE_PROMPT | judge).invoke({
        "context": real_context,
        "chat": formatted_chat,
        "reply": ai_reply,
        "design_plan": design_plan or "No design plan generated."
    })
    
    if not evaluation:
        return {}
        
    print(f"\nFaithfulness {evaluation.faithfulness_score} - {evaluation.faithfulness_reason}")
    print(f"Relevance {evaluation.relevance_score} - {evaluation.relevance_reason}")
    return evaluation.model_dump()


def run_pipeline_judge(judge_llm, design_plan, tech_context, nf_code, strategy=None):
    """Runs the LLM judge for the rendered Nextflow code (syntax + logic)."""
    judge = judge_llm.with_structured_output(ArchitectEval)
    evaluation = (PIPELINE_JUDGE_PROMPT | judge).invoke({
        "plan": design_plan,
        "context": tech_context,
        "code": nf_code
    })
    
    if not evaluation:
        return {}

    print(f"\nSyntax {evaluation.syntax_score} - {evaluation.syntax_reason}")
    print(f"Logic {evaluation.logic_score} - {evaluation.logic_reason}")
    return evaluation.model_dump()


def run_diagram_judge(judge_llm, tech_context, nf_code, mermaid_code, strategy):
    """Runs the LLM judge for the generated Mermaid diagram (syntax + mapping)."""
    judge = judge_llm.with_structured_output(DiagramEval)
    evaluation = (DIAGRAM_JUDGE_PROMPT | judge).invoke({
        "diagram_source": strategy,
        "context": tech_context,
        "nf_code": nf_code,
        "mermaid_code": mermaid_code
    })
    
    if not evaluation:
        return {}

    print(f"\nDiagram Syntax {evaluation.syntax_score} - {evaluation.syntax_reason}")
    print(f"Diagram Mapping {evaluation.mapping_score} - {evaluation.mapping_reason}")
    return evaluation.model_dump()

def run_rejection_judge(judge_llm, prompt, rejection_reason, reply, status):
    """Runs the LLM judge for Guardrail / Rejection logic."""
    from tests.evaluation.prompts import REJECTION_JUDGE_PROMPT
    judge = judge_llm.with_structured_output(RejectionEval)
    evaluation = (REJECTION_JUDGE_PROMPT | judge).invoke({
        "prompt": prompt,
        "rejection_reason": rejection_reason,
        "reply": reply,
        "status": status,
    })
    
    if not evaluation:
        return {}
        
    print(f"\nRejection {evaluation.rejection_score} - {evaluation.rejection_reason}")
    print(f"Alternative {evaluation.alternative_score} - {evaluation.alternative_reason}")
    return evaluation.model_dump()
    
def run_recreation_judge(judge_llm, reference_code, generated_code):
    """Runs the LLM judge for code recreation against a reference."""
    from tests.evaluation.prompts import CODE_RECREATION_JUDGE_PROMPT
    judge = judge_llm.with_structured_output(CodeRecreationEval)
    evaluation = (CODE_RECREATION_JUDGE_PROMPT | judge).invoke({
        "reference_code": reference_code,
        "generated_code": generated_code,
    })
    
    if not evaluation:
        return {}
        
    print(f"\nStructural {evaluation.structural_score} - {evaluation.structural_reason}")
    print(f"Channel {evaluation.channel_score} - {evaluation.channel_reason}")
    return evaluation.model_dump()


def build_test_execution_graph(store):
    """Build the execution subgraph for direct isolated testing.

    Mirrors the production build_execution_subgraph() from graph.py,
    but compiles with an explicit store so hydrator_node can read catalog data.
    """
    from langgraph.graph import StateGraph, END
    from app.services.graph_state import GraphState
    from app.services.agents import hydrator_node, architect_node, diagram_node
    from app.services.repair import repair_node, should_repair
    from app.services.renderer import renderer_node

    builder = StateGraph(GraphState)
    builder.add_node("hydrator", hydrator_node)
    builder.add_node("architect", architect_node)
    builder.add_node("repair", repair_node)
    builder.add_node("renderer", renderer_node)
    builder.add_node("diagram", diagram_node)

    builder.set_entry_point("hydrator")
    builder.add_edge("hydrator", "architect")
    builder.add_conditional_edges(
        "architect", should_repair,
        {"success": "renderer", "repair": "repair", "fail": "renderer"}
    )
    builder.add_edge("repair", "architect")
    builder.add_edge("renderer", "diagram")
    builder.add_edge("diagram", END)

    return builder.compile(store=store)

