"""
tests/test_consultant.py
Isolated Consultant Agent Tests

Tests that the consultant makes the correct decisions when provided with
deterministic context. Bypasses RAG to ensure isolated testing.

Iterates over scenarios from all complexity levels.
"""
import pytest
from langchain_core.messages import HumanMessage

from app.models.consultant_structure import ConsultantOutput
from tests.helpers import (
    get_exact_context,
    force_approve_consultant,
    run_academic_judge,
    rate_limit_pause,
)
from tests.scenarios.level1_simple import LEVEL1_SCENARIOS
from tests.scenarios.level2_medium import LEVEL2_SCENARIOS
from tests.scenarios.level3_complex import LEVEL3_SCENARIOS
from tests.report import report

ALL_SCENARIOS = [
    s
    for s in (LEVEL1_SCENARIOS + LEVEL2_SCENARIOS + LEVEL3_SCENARIOS)
    if "RECREATION_REV" not in s.get("id", "")
]

@pytest.mark.parametrize(
    "scenario",
    ALL_SCENARIOS,
    ids=[s["id"] for s in ALL_SCENARIOS],
)
def test_consultant_logic(scenario, store, llm, judge_llm):
    """Verify that the consultant approves the correct strategy and modules."""

    if "template_ids" not in scenario or "component_ids" not in scenario:
        pytest.skip("Mock data for exact context not defined in scenario.")

    # ── Build deterministic context (no RAG) ──
    real_context = get_exact_context(
        scenario["template_ids"],
        scenario["component_ids"],
        store,
    )

    # ── Build agent chain ──
    agent = llm.with_structured_output(ConsultantOutput)

    # ── Map string array to HumanMessages ──
    chat_history = [HumanMessage(content=msg) for msg in scenario["chat_messages"]]

    # ── Drive to APPROVED ──
    result = force_approve_consultant(agent, real_context, chat_history)

    # ── Deterministic assertions ──
    passed = True
    errors = []
    details = {
        "status": result.status,
        "strategy": result.strategy_selector,
        "template_id": result.used_template_id,
        "module_ids": result.selected_module_ids,
        "ai_reply": result.response_to_user,
    }

    if result.status != "APPROVED":
        errors.append(f"Expected APPROVED status, got {result.status}")
        passed = False

    if scenario.get("expect_strategy"):
        if result.strategy_selector != scenario["expect_strategy"]:
            # User noted: Strategy mismatch shouldn't fail the test as it can still be a correct agent logic path
            msg = f"Strategy mismatch: Expected {scenario['expect_strategy']}, got {result.strategy_selector}"
            details["strategy_mismatch"] = msg
            print(f"  ⚠️  [INFO] {msg}")

    if scenario.get("expect_template_id"):
        if result.used_template_id != scenario["expect_template_id"]:
            passed = False
            msg = f"Template mismatch: Expected {scenario['expect_template_id']}, got {result.used_template_id}"
            errors.append(msg)
            details["template_mismatch"] = msg

    # ── LLM Judge (optional) ──
    scores = {}
    if judge_llm:
        try:
            from tests.helpers import run_academic_judge
            
            judge_result = run_academic_judge(
                judge_llm=judge_llm,
                real_context=real_context,
                chat_history=chat_history,
                ai_reply=result.response_to_user,
                design_plan=result.draft_plan,
            )
            if judge_result:
                scores = {k: v for k, v in judge_result.items() if "score" in k}
                details["judge_scores"] = scores
                for k, v in scores.items():
                    if v < 4:
                        passed = False
                        details[f"{k}_low"] = v
        except Exception as e:
            scores["judge_passed"] = 0.0
            details["judge_error"] = f"Judge failed: {str(e)}"

    if errors:
        details["errors"] = errors
        print(f"\n[FAIL] {scenario['id']} test_consultant failed:\n" + "\n".join(errors))

    report.add_result(
        scenario_id=f"[Consultant] {scenario['id']}",
        level=scenario["level"],
        success=passed,
        difficulty=scenario.get("difficulty", "—"),
        description=scenario.get("description", ""),
        scores=scores,
        details=details,
    )

    rate_limit_pause()
    
    assert not errors, f"Consultant test failed:\n" + "\n".join(errors)
