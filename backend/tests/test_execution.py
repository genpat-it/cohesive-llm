"""
tests/test_execution.py
Isolated Execution Subgraph Tests

Tests the architect subgraph directly: hydrator → architect → repair → renderer → diagram.
Provides pre-baked consultant output based on the scenario state.
Bypasses RAG and Consultant.

Iterates over scenarios from all complexity levels.
"""
import uuid
import pytest

from tests.helpers import (
    build_test_execution_graph,
    run_pipeline_judge,
    run_diagram_judge,
    rate_limit_pause,
)
from tests.nf_validation import validate_nextflow
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
def test_execution_subgraph(scenario, store, judge_llm):
    """Verify the execution subgraph produces valid code from pre-baked state."""

    if "design_plan" not in scenario:
        pytest.skip("Mock data for execution plan not defined in scenario.")

    # ── Build execution subgraph with test store ──
    exec_graph = build_test_execution_graph(store)

    # ── Pre-baked initial state ──
    initial_state = {
        "user_query": scenario["chat_messages"][0],
        "messages": [],
        "consultant_status": "APPROVED",
        "design_plan": scenario["design_plan"],
        "strategy_selector": scenario.get("expect_strategy", "CUSTOM_BUILD"),
        "used_template_id": scenario.get("expect_template_id"),
        "selected_module_ids": scenario.get("selected_module_ids", []),
        "nextflow_code": None,
        "mermaid_code": None,
        "ast_json": None,
        "technical_context": None,
        "validation_error": None,
        "retries": 0,
        "error": None,
    }

    config = {"configurable": {"thread_id": f"test_{scenario['id']}_{uuid.uuid4().hex[:8]}"}}

    # ── Invoke execution subgraph ──
    final_state = exec_graph.invoke(initial_state, config=config)

    # ── Extract outputs ──
    nf_code = final_state.get("nextflow_code", "")
    ast_json = final_state.get("ast_json", {})
    mermaid_code = final_state.get("mermaid_code", "")
    tech_context = final_state.get("technical_context", "")
    error = final_state.get("error")

    passed = True
    errors = []

    details = {
        "strategy": initial_state.get("strategy_selector"),
        "template": initial_state.get("used_template_id"),
        "modules": initial_state.get("selected_module_ids"),
        "nf_code_length": len(nf_code) if nf_code else 0,
        "nf_code": nf_code,
        "has_ast": bool(ast_json),
        "mermaid_code_length": len(mermaid_code) if mermaid_code else 0,
        "mermaid_code": mermaid_code,
        "error": error,
    }
    scores = {}

    if error:
        errors.append(f"Execution subgraph error: {error}")
        passed = False
    if not nf_code or len(nf_code) <= 50:
        errors.append("No Nextflow code generated")
        passed = False
    if not ast_json:
        errors.append("No AST JSON generated")
        passed = False

    # ── Nextflow compiler validation ──
    try:
        # Run stub only for complex scenarios (level >= 3)
        val_res = validate_nextflow(nf_code, run_stub=(scenario["level"] >= 3))
        details.update(val_res)
        if val_res.get("nf_syntax_passed") == False:
            errors.append(f"Nextflow Syntax Check Failed: {val_res.get('nf_syntax_error', 'Unknown error')[:200]}")
            passed = False
        if val_res.get("nf_stub_passed") == False:
            errors.append(f"Nextflow Stub Execution Failed: {val_res.get('nf_stub_error', 'Unknown error')[:200]}")
            passed = False
    except Exception as e:
        details["nf_validation_error"] = f"Skipped: {str(e)[:100]}"

    # ── LLM Judge: Pipeline ──
    if judge_llm:
        from tests.helpers import run_pipeline_judge, run_diagram_judge
        
        try:
            pipeline_result = run_pipeline_judge(
                judge_llm=judge_llm,
                design_plan=initial_state["design_plan"],
                tech_context=tech_context,
                nf_code=nf_code,
            )
            if pipeline_result:
                p_scores = {k: v for k, v in pipeline_result.items() if "score" in k}
                scores.update(p_scores)
                details["pipeline_judge_scores"] = p_scores
                for k, v in p_scores.items():
                    if v < 4:
                        passed = False
                        details[f"{k}_low"] = v
        except Exception as e:
            scores["pipeline_judge_passed"] = 0.0
            details["pipeline_judge_error"] = str(e)[:200]

        # ── LLM Judge: Diagram ──
        if mermaid_code:
            try:
                diagram_result = run_diagram_judge(
                    judge_llm=judge_llm,
                    tech_context=tech_context,
                    nf_code=nf_code,
                    mermaid_code=mermaid_code,
                    strategy="deterministic",
                )
                if diagram_result:
                    d_scores = {k: v for k, v in diagram_result.items() if "score" in k}
                    scores.update(d_scores)
                    details["diagram_judge_scores"] = d_scores
                    for k, v in d_scores.items():
                        if v < 4:
                            passed = False
                            details[f"{k}_low"] = v
            except Exception as e:
                scores["diagram_judge_passed"] = 0.0
                details["diagram_judge_error"] = str(e)[:200]

    if errors:
        details["errors"] = errors
        print(f"\n[FAIL] {scenario['id']} test_execution failed:\n" + "\n".join(errors))

    report.add_result(
        scenario_id=f"[Execution] {scenario['id']}",
        level=scenario["level"],
        success=passed,
        difficulty=scenario.get("difficulty", "—"),
        description=scenario.get("description", ""),
        scores=scores,
        details=details,
    )

    rate_limit_pause()
    
    assert not errors, f"Test failed with errors:\n" + "\n".join(errors)
