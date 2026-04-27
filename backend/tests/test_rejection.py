"""
tests/test_rejection.py
Level 4 — Negative: Rejection / Guardrail Tests

Tests that the consultant agent correctly rejects invalid requests.
Uses get_exact_context to inject real alternatives, then invokes
the consultant directly. The agent should stay CHATTING and explain
why the request is invalid.

Bypasses RAG and the execution subgraph.
"""
import pytest
from langchain_core.messages import HumanMessage

from app.models.consultant_structure import ConsultantOutput
from tests.evaluation.prompts import CONSULTANT_TEST_PROMPT
from tests.helpers import get_exact_context, rate_limit_pause
from tests.scenarios.level4_guardrails import LEVEL4_SCENARIOS
from tests.report import report

@pytest.mark.parametrize(
    "scenario",
    LEVEL4_SCENARIOS,
    ids=[s["id"] for s in LEVEL4_SCENARIOS],
)
def test_rejection_guardrail(scenario, store, llm, judge_llm):
    """Verify that the consultant rejects invalid tool requests."""

    if "template_ids" not in scenario or "component_ids" not in scenario:
        pytest.skip("Mock data for alternatives not defined in scenario.")

    # ── Build deterministic context with REAL alternatives ──
    real_context = get_exact_context(
        scenario["template_ids"],
        scenario["component_ids"],
        store,
    )

    # ── Build agent chain ──
    agent = llm.with_structured_output(ConsultantOutput)
    chain = CONSULTANT_TEST_PROMPT | agent

    # ── Invoke with the invalid request ──
    chat_history = [HumanMessage(content=scenario["chat_messages"][0])]
    result = chain.invoke({"context": real_context, "messages": chat_history})

    # ── Deterministic assertions ──
    passed = True
    errors = []
    details = {
        "status": result.status,
        "ai_reply": result.response_to_user,
        "expected_rejection_reason": scenario["rejection_reason"],
    }
    scores = {}

    if result.status != "CHATTING":
        passed = False
        msg = f"Agent APPROVED an invalid request! Expected CHATTING, got {result.status}"
        errors.append(msg)

    if result.draft_plan and result.draft_plan.strip() != "":
        passed = False
        msg = "Agent generated a plan for an invalid request."
        errors.append(msg)

    # ── LLM Judge: Rejection quality ──
    if judge_llm:
        try:
            from tests.helpers import run_rejection_judge
            judge_result = run_rejection_judge(
                judge_llm=judge_llm,
                prompt=scenario["chat_messages"][0],
                rejection_reason=scenario["rejection_reason"],
                reply=result.response_to_user,
                status=result.status,
            )
            if judge_result:
                scores = {k: v for k, v in judge_result.items() if "score" in k}
                details["judge_scores"] = scores
                for k, v in scores.items():
                    if v < 4:
                        passed = False
                        details[f"{k}_low"] = v
        except Exception as e:
            details["judge_error"] = str(e)[:200]

    if errors:
        details["errors"] = errors
        print(f"\n[FAIL] {scenario['id']} test_rejection failed:\n" + "\n".join(errors))

    report.add_result(
        scenario_id=f"[Rejection] {scenario['id']}",
        level=scenario["level"],
        success=passed,
        difficulty=scenario.get("difficulty", "—"),
        description=scenario.get("description", ""),
        scores=scores,
        details=details,
    )

    rate_limit_pause()
    
    assert not errors, f"Rejection test failed:\n" + "\n".join(errors)
