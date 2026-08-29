"""
tests/helpers.py
Shared helper functions for the pairwise evaluation test suite.

API Helpers:
  - send_chat(): sends a message to the /chat API endpoint
  - run_multi_turn_chat(): drives a full multi-turn conversation
  - rate_limit_pause(): sleeps between API calls

Context Helpers:
  - get_exact_context(): bypasses vector search, injects exact catalog items
  - format_llm_output_for_pairwise(): format LLM response for pairwise comparison
  - format_ground_truth_for_pairwise(): format ground truth for pairwise comparison
  - compute_step_metrics(): compute P/R/F1 on step selection

Legacy judge functions (run_academic_judge, run_pipeline_judge, etc.) have been
moved to tests/legacy/helpers.py. This module only contains the pairwise system helpers.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_DIR = PROJECT_ROOT / "tests" / "reports"
REPORTS_DIR = PROJECT_ROOT / "tests" / "reports"


# ──────────────────────────────────────────────────────────────
# Rate Limit Protection
# ──────────────────────────────────────────────────────────────

def rate_limit_pause(seconds: int = 5, reason: str = "rate limit protection"):
    """Pause execution for rate limit protection.

    Respects JUDGE_RATE_LIMIT env var: if "true", pauses for judge calls too.
    Otherwise, judge-related pauses are skipped.
    """
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
    timeout: int = 600,
    generate_diagrams: bool = True,
) -> dict:
    """Send a single message to the /chat API endpoint.

    Returns a dict with keys:
        success, status, reply, nextflow_code, mermaid_agent,
        mermaid_deterministic, ast_json, elapsed, error, tool_calls
    """
    url = "/chat"
    payload = {
        "session_id": session_id,
        "message": message,
        "generate_diagrams": generate_diagrams,
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
                "mermaid_agent": None,
                "mermaid_deterministic": None,
                "ast_json": None,
                "elapsed": elapsed,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                "tool_calls": [],
            }

        data = resp.json()
        return {
            "success": True,
            "status": data.get("status", "UNKNOWN"),
            "reply": data.get("reply"),
            "nextflow_code": data.get("nextflow_code"),
            "mermaid_agent": data.get("mermaid_agent"),
            "mermaid_deterministic": data.get("mermaid_deterministic"),
            "ast_json": data.get("ast_json"),
            "elapsed": elapsed,
            "error": data.get("error"),
            "tool_calls": data.get("tool_calls") or [],
        }

    except Exception as e:
        if 'timeout' in str(e).lower():
            return {
                "success": False, "status": "TIMEOUT", "reply": None,
                "nextflow_code": None, "mermaid_agent": None,
                "mermaid_deterministic": None, "ast_json": None,
                "elapsed": time.time() - start, "error": "Request timed out",
                "tool_calls": [],
            }
        return {
            "success": False, "status": "CONNECTION_ERROR", "reply": None,
            "nextflow_code": None, "mermaid_agent": None,
            "mermaid_deterministic": None, "ast_json": None,
            "elapsed": time.time() - start, "error": str(e),
            "tool_calls": [],
        }


def run_multi_turn_chat(
    client,
    chat_messages: list[str],
    expect_rejection: bool = False,
    pause_between_turns: int = 5,
    session_id: str | None = None,
) -> dict:
    """Drive a full multi-turn conversation through the API.

    Parameters
    ----------
    chat_messages : list[str]
        List of user messages to send in order.
    expect_rejection : bool
        If True, return after the first response.

    Returns
    -------
    dict with: success, status, reply, nextflow_code, mermaid_agent,
    mermaid_deterministic, ast_json, elapsed, turns, all_replies, session_id
    """
    session_id = session_id or f"test_{uuid.uuid4().hex[:12]}"
    total_start = time.time()
    all_replies = []

    for turn_idx, user_msg in enumerate(chat_messages):
        result = send_chat(client, session_id, user_msg)

        if not result["success"]:
            result["turns"] = turn_idx + 1
            result["all_replies"] = all_replies
            result["session_id"] = session_id
            return result

        all_replies.append({
            "turn": turn_idx + 1,
            "reply": result["reply"],
            "status": result["status"],
            "tool_calls": result.get("tool_calls") or [],
        })

        if expect_rejection:
            result["turns"] = turn_idx + 1
            result["elapsed"] = time.time() - total_start
            result["all_replies"] = all_replies
            result["session_id"] = session_id
            return result

        if result["status"] == "APPROVED" and result.get("nextflow_code"):
            result["turns"] = turn_idx + 1
            result["elapsed"] = time.time() - total_start
            result["all_replies"] = all_replies
            result["session_id"] = session_id
            return result

        if turn_idx < len(chat_messages) - 1:
            rate_limit_pause(pause_between_turns, f"between turn {turn_idx + 1} and {turn_idx + 2}")

    result["turns"] = len(chat_messages)
    result["elapsed"] = time.time() - total_start
    result["all_replies"] = all_replies
    result["session_id"] = session_id
    return result


def build_modification_chat_messages(raw_chat_messages: list[str]) -> list[str]:
    """Build the message sequence for a modification benchmark example.

    Modification examples have interleaved user/assistant turns in the raw
    chat_messages field (indices 0, 2, 4, … are user turns). We keep only
    the user turns and append a canonical approval message so the agent
    proceeds from plan to generation.

    Parameters
    ----------
    raw_chat_messages : list[str]
        Raw chat_messages from the benchmark example dict.

    Returns
    -------
    list[str]
        User-only messages plus a trailing approval message.
    """
    user_turns = [msg for i, msg in enumerate(raw_chat_messages) if i % 2 == 0]
    user_turns.append("I approve the plan, please build the pipeline.")
    return user_turns


# ──────────────────────────────────────────────────────────────
# Context Helpers
# ──────────────────────────────────────────────────────────────

def get_exact_context(template_ids: list[str], component_ids: list[str], store) -> str:
    """Bypasses vector search to inject exact catalog items for deterministic testing.

    Pulls items directly from the InMemoryStore using the same
    _inject_template / _inject_component functions used by the
    production RAG pipeline, but skips all scoring and ranking.
    """
    from core.services.tools import _inject_component, _inject_template

    found_ids: set[str] = set()
    context_blocks: list[str] = []
    for tid in template_ids:
        _inject_template(tid, found_ids, context_blocks, store, embed_code=False)
    for cid in component_ids:
        _inject_component(cid, found_ids, context_blocks, store, embed_code=False)
    return "\n".join(context_blocks) + "\n\n"


# ──────────────────────────────────────────────────────────────
# Step Extraction & Metrics
# ──────────────────────────────────────────────────────────────

# Matches: include { step_XXX } from '../steps/step_XXX'
_STEP_INCLUDE_RE = re.compile(r"include\s*\{\s*([^}]+?)\s*\}\s*from\s*'([^']+)'")


def extract_steps_from_code(nf_code: str) -> list[str]:
    """Extract step IDs from Nextflow include statements and capture critical channel operators.

    >>> extract_steps_from_code("include { step_4TY_MLST__mlst } from '../steps/step_4TY_MLST__mlst'\\ninput.collect().set { out }")
    ['_OP_collect', '_OP_set', 'step_4TY_MLST__mlst']
    """
    if not nf_code:
        return []

    step_ids: set[str] = set()
    
    # 1. Extract Components (no hardcoded path exclusions)
    for sym_list, path in _STEP_INCLUDE_RE.findall(nf_code):
        for sym in sym_list.split(";"):
            sym = sym.strip()
            # Handle "name as alias", just take the original name
            step_name = sym.split()[0]
            if step_name:
                step_ids.add(step_name)
                
    # 2. Extract Dynamic Data-Shaping Operators
    # We strip out comments first to avoid matching commented operators
    code_no_comments = re.sub(r'//.*', '', nf_code)
    code_no_comments = re.sub(r'/\*.*?\*/', '', code_no_comments, flags=re.DOTALL)
    
    operators = re.findall(r'\.([a-zA-Z0-9_]+)\s*(?:\{|\()', code_no_comments)
    for op in operators:
        # Ignore common native string methods that aren't NF operators just to reduce noise, though it doesn't strictly matter
        if op not in ('toString', 'getName', 'getBaseName', 'getSimpleName', 'Extension'):
            step_ids.add(f"_OP_{op}")

    return sorted(step_ids)


def compute_step_metrics(llm_code: str, gt_code: str) -> dict:
    """Compute precision/recall/F1 on step selection between LLM and ground truth.

    Returns a dict with:
        llm_steps, gt_steps, common_steps, extra_steps, missing_steps,
        precision, recall, f1
    """
    llm_steps = set(extract_steps_from_code(llm_code))
    gt_steps = set(extract_steps_from_code(gt_code))

    common = llm_steps & gt_steps
    extra = sorted(llm_steps - gt_steps)
    missing = sorted(gt_steps - llm_steps)

    precision = (len(common) / len(llm_steps) * 100) if llm_steps else 0.0
    recall = (len(common) / len(gt_steps) * 100) if gt_steps else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "llm_steps": sorted(llm_steps),
        "gt_steps": sorted(gt_steps),
        "common_steps": sorted(common),
        "extra_steps": extra,
        "missing_steps": missing,
        "precision": round(precision, 1),
        "recall": round(recall, 1),
        "f1": round(f1, 1),
    }


# ──────────────────────────────────────────────────────────────
# Pairwise Formatting
# ──────────────────────────────────────────────────────────────

def format_llm_output_for_pairwise(result: dict) -> str:
    """Format the LLM response for pairwise comparison.

    Combines the natural language reply and the generated code into a single
    string for the judge to evaluate. Truncates the reply to avoid overwhelming
    the judge with very long responses.
    """
    parts = []

    reply = (result.get("reply") or "").strip()
    if reply:
        parts.append(f"Reply:\n{reply[:3000]}")

    code = (result.get("nextflow_code") or "").strip()
    if code:
        parts.append(f"Nextflow Code:\n{code}")
    else:
        parts.append("(No Nextflow code was generated)")

    mermaid = (result.get("mermaid_deterministic") or result.get("mermaid_agent") or "").strip()
    if mermaid:
        parts.append(f"Mermaid Diagram:\n```mermaid\n{mermaid}\n```")

    return "\n\n".join(parts)


def format_ground_truth_for_pairwise(example: dict) -> str:
    """Format the ground truth for pairwise comparison.

    Depending on the test type, the ground truth might be code, a consultant
    reply, a Mermaid diagram, or a rejection response.
    """
    parts = []

    # Text-based ground truths (Consultant, Rejection, Recreation)
    gt_reply = (
        example.get("consultant_reply")
        or example.get("rejection_reason")
        or example.get("rejection_reply")
        or example.get("recreation_reply")
    )
    if gt_reply:
        parts.append(f"Reply:\n{gt_reply}")

    # Diagram ground truth
    diagram_code = example.get("diagram_code")
    if diagram_code:
        parts.append(f"Mermaid Diagram:\n```mermaid\n{diagram_code}\n```")

    # Code-based ground truth
    code = (example.get("nextflow_code") or "").strip()
    if code:
        parts.append(f"Nextflow Code:\n{code}")

    if not parts:
        return "(No ground truth available)"

    return "\n\n".join(parts)
