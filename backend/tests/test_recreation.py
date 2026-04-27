"""
tests/test_recreation.py
Level 5 — Code Recreation Tests

Tests that the Architect/execution subgraph can reconstruct specific modules
from the codebase exactly as they appear in code_store_hollow.jsonl.

Iterates over recreation scenarios. Bypasses RAG and Consultant.
"""
import uuid
import pytest
import re

from tests.helpers import (
    build_test_execution_graph,
    rate_limit_pause,
    send_chat,
    run_academic_judge,
    run_pipeline_judge,
    run_diagram_judge,
    run_rejection_judge,
    run_with_retries,
)
from tests.nf_validation import validate_nextflow
from tests.scenarios.level5_recreation import LEVEL5_SCENARIOS, REFERENCE_CODE
from tests.scenarios.level1_simple import LEVEL1_SCENARIOS
from tests.scenarios.level2_medium import LEVEL2_SCENARIOS
from tests.scenarios.level3_complex import LEVEL3_SCENARIOS
from tests.report import report
from app.services.tools import retrieve_rag_context


class _Msg:
    def __init__(self, msg_type: str, content: str):
        self.type = msg_type
        self.content = content

ALL_RECREATION_REV_SCENARIOS = [
    s
    for s in (LEVEL1_SCENARIOS + LEVEL2_SCENARIOS + LEVEL3_SCENARIOS)
    if "RECREATION_REV" in s["id"]
]


def _force_approved_execution(
    api_client,
    session_id: str,
    max_attempts: int = 4,
    
):
    """Keep sending approval until execution returns APPROVED + code, or return None."""
    last = None
    for _ in range(max_attempts):
        last = send_chat(
            api_client,
            session_id,
            "Approved.",
            
        )
        if (
            last.get("success")
            and last.get("status") == "APPROVED"
            and last.get("nextflow_code")
        ):
            return last
    return None


def _extract_context_ids(context: str):
    if not context:
        return []
    return re.findall(r'--- (?:COMPONENT|TEMPLATE): ([\w\_\d]+) ---', context)

@pytest.mark.parametrize(
    "scenario",
    LEVEL5_SCENARIOS,
    ids=[s["id"] for s in LEVEL5_SCENARIOS],
)
def test_code_recreation(scenario, store, judge_llm):
    """Verify the execution subgraph can reproduce a known module's code."""

    module_id = scenario.get("module_id")
    reference_code = REFERENCE_CODE.get(module_id)

    if not reference_code:
        pytest.skip(f"No reference code found for {module_id} in code_store_hollow.jsonl")

    # ── Build execution subgraph ──
    exec_graph = build_test_execution_graph(store)

    # ── Pre-baked initial state ──
    initial_state = {
        "user_query": f"Recreate {module_id}",
        "messages": [],
        "consultant_status": "APPROVED",
        "design_plan": scenario["design_plan"],
        "strategy_selector": scenario.get("expect_strategy", "EXACT_MATCH"),
        "used_template_id": scenario.get("used_template_id"),
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
    mermaid_code = final_state.get("mermaid_code", "")
    
    ast_json = final_state.get("ast_json", {})
    error = final_state.get("error")

    passed = True
    errors = []
    
    details = {
        "module_id": module_id,
        "strategy": initial_state.get("strategy_selector"),
        "nf_code_length": len(nf_code) if nf_code else 0,
        "nf_code": nf_code,
        "reference_length": len(reference_code),
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
        errors.append(f"No Nextflow code generated for {module_id}")
        passed = False

    # ── Nextflow validation ──
    try:
        # Run stub for recreation (Level 5)
        val_res = validate_nextflow(nf_code, run_stub=True)
        details.update(val_res)
        if val_res.get("nf_syntax_passed") == False or val_res.get("nf_stub_passed") == False:
            passed = False
    except Exception as e:
        details["nf_validation_error"] = f"Skipped: {str(e)[:100]}"

    # ── LLM Judge: Code Recreation ──
    if judge_llm:
        from tests.helpers import run_recreation_judge, run_diagram_judge
        try:
            judge_result = run_recreation_judge(
                judge_llm=judge_llm,
                reference_code=reference_code,
                generated_code=nf_code,
            )
            if judge_result:
                scores = {k: v for k, v in judge_result.items() if "score" in k}
                details["judge_scores"] = scores
                for k, v in scores.items():
                    if v < 3:
                        passed = False
                        details[f"{k}_low"] = v
        except Exception as e:
            details["judge_error"] = str(e)[:200]
            
        # ── LLM Judge: Diagrams (BOTH) ──
        ast_json = final_state.get("ast_json", {})
        mermaid_code = final_state.get("mermaid_code")
        
        
        details["has_ast"] = bool(ast_json)
        details["has_mermaid"] = bool(mermaid_code)
        details["has_mermaid_code"] = bool(mermaid_code)
        
        for code_variant, source in [(mermaid_code, "deterministic")]:
            if code_variant:
                try:
                    diagram_result = run_diagram_judge(
                        judge_llm=judge_llm,
                        tech_context=initial_state["technical_context"] or "",
                        nf_code=nf_code,
                        mermaid_code=code_variant,
                        strategy=source,
                    )
                    if diagram_result:
                        d_scores = {f"{source}_{k}": v for k, v in diagram_result.items() if "score" in k}
                        scores.update(d_scores)
                        if "diagram_judge_scores" not in details:
                            details["diagram_judge_scores"] = {}
                        details["diagram_judge_scores"].update(d_scores)
                        for k, v in d_scores.items():
                            if v < 4:
                                passed = False
                                details[f"{k}_low"] = v
                except Exception as e:
                    scores[f"{source}_diagram_judge_passed"] = 0.0
                    details[f"{source}_diagram_judge_error"] = str(e)[:200]

    if errors:
        details["errors"] = errors
        print(f"\n[FAIL] {scenario['id']} test_recreation failed:\n" + "\n".join(errors))

    report.add_result(
        scenario_id=f"[Recreation] {scenario['id']}",
        level=scenario["level"],
        success=passed,
        difficulty=scenario.get("difficulty", "—"),
        description=scenario.get("description", ""),
        scores=scores,
        details=details,
    )

    rate_limit_pause()
    
    assert not errors, f"Recreation test failed:\n" + "\n".join(errors)


@pytest.mark.parametrize(
    "scenario",
    ALL_RECREATION_REV_SCENARIOS,
    ids=[s["id"] for s in ALL_RECREATION_REV_SCENARIOS],
)
def test_recreation_revision_two_stage_flow(scenario, api_client, store, judge_llm):
    """Verify two-stage flow: initial recreation build, then revised build after re-approval."""

    prompts = scenario["chat_messages"]
    if len(prompts) != 3:
        pytest.skip("Two-stage recreation flow only applies to 3-turn revision scenarios")

    if not judge_llm:
        pytest.fail("judge_llm is required for recreation revision flow tests")

    def _attempt_once():
        session_id = f"recreate_rev_{scenario['id']}_{uuid.uuid4().hex[:8]}"
        scores = {}
        details = {}
        failures = []

        def _missing_ids(expected, context_text):
            return [eid for eid in expected if eid not in (context_text or "")]

        # Turn 1: consultant + RAG stage for initial request
        t1 = send_chat(api_client, session_id, prompts[0])
        if not t1.get("success"):
            raise AssertionError(f"Turn 1 failed: {t1.get('error')}")

        initial_context = retrieve_rag_context(prompts[0], store, embed_code=False)
        initial_found = _extract_context_ids(initial_context)
        initial_expected = (
            scenario.get("expect_in_context_initial")
            or scenario.get("template_ids", [])
            or scenario.get("expect_in_context", [])
        )
        missing_initial = _missing_ids(initial_expected, initial_context)
        if missing_initial:
            failures.append(f"Initial-turn RAG missing expected IDs: {missing_initial}")

        scores["initial_rag_recall_pct"] = (
            (len(initial_expected) - len(missing_initial)) / len(initial_expected) * 100
            if initial_expected else 100.0
        )

        # Turn 2: first approval should trigger first architect/execution output
        t2 = send_chat(
            api_client,
            session_id,
            prompts[1],
            
        )
        if not t2.get("success"):
            raise AssertionError(f"Turn 2 failed: {t2.get('error')}")

        # Initial run only needs approval; no mandatory diagram checks here.
        if t2.get("status") != "APPROVED":
            forced_t2 = _force_approved_execution(
                api_client,
                session_id,
                
            )
            if not forced_t2:
                return {
                    "success": False,
                    "scores": {"flow_score": 1.0},
                    "details": {"reason": "Could not reach first APPROVED execution after forced approvals"},
                }
            t2 = forced_t2

        initial_code = t2.get("nextflow_code")
        t3 = send_chat(api_client, session_id, prompts[2])
        if not t3.get("success"):
            raise AssertionError(f"Turn 3 failed: {t3.get('error')}")

        expect_rejection_on_revision = scenario.get("expect_rejection_on_revision", False)
        revision_context = retrieve_rag_context(prompts[2], store, embed_code=False)
        revision_found = _extract_context_ids(revision_context)
        revision_expected = (
            scenario.get("expect_in_context_revision")
            or scenario.get("expect_in_context", [])
        )
        missing_revision = _missing_ids(revision_expected, revision_context)
        if missing_revision:
            failures.append(f"Revision-turn RAG missing expected IDs: {missing_revision}")

        scores["revision_rag_recall_pct"] = (
            (len(revision_expected) - len(missing_revision)) / len(revision_expected) * 100
            if revision_expected else 100.0
        )

        # Judge only the last consultant turn (revision) with revision RAG context.
        # In rejection-mode revisions, use the dedicated rejection judge only.
        if judge_llm and not expect_rejection_on_revision:
            try:
                consultant_judge = run_academic_judge(
                    judge_llm=judge_llm,
                    real_context=revision_context,
                    chat_history=[_Msg("human", prompts[2])],
                    ai_reply=t3.get("reply") or "",
                    design_plan=scenario.get("design_plan", ""),
                )
                if consultant_judge:
                    for k, v in consultant_judge.items():
                        if "score" in k:
                            scores[f"final_consultant_{k}"] = v
                    # Mandatory thresholds for revision consultant quality.
                    if consultant_judge.get("faithfulness_score", 0) < 4:
                        failures.append(
                            "Final consultant faithfulness score below threshold: "
                            f"{consultant_judge.get('faithfulness_score')}"
                        )
                    if consultant_judge.get("relevance_score", 0) < 4:
                        failures.append(
                            "Final consultant relevance score below threshold: "
                            f"{consultant_judge.get('relevance_score')}"
                        )
                    details["final_consultant_judge"] = consultant_judge
                else:
                    failures.append("Final consultant judge returned empty result")
            except Exception as e:
                failures.append(f"Final consultant judge failed: {str(e)[:200]}")

        final_code = None
        final_mermaid = None
        final_mermaid_code = None
        t4 = None

        if expect_rejection_on_revision:
            # For revision rejection scenarios, do NOT force execution.
            if not (t3.get("status") == "CHATTING" and not t3.get("nextflow_code")):
                failures.append("Expected revision rejection (CHATTING/no code)")

            # Optional rejection-quality judge only on final consultant reply.
            if judge_llm:
                try:
                    rej_reason = scenario.get(
                        "rejection_reason_revision",
                        "Revision request should be rejected based on guardrail rules.",
                    )
                    rej_judge = run_rejection_judge(
                        judge_llm=judge_llm,
                        prompt=prompts[2],
                        rejection_reason=rej_reason,
                        reply=t3.get("reply") or "",
                        status=t3.get("status") or "UNKNOWN",
                    )
                    if rej_judge:
                        for k, v in rej_judge.items():
                            if "score" in k:
                                scores[f"final_rejection_{k}"] = v
                        if rej_judge.get("rejection_score", 0) < 4:
                            failures.append(
                                "Revision rejection score below threshold: "
                                f"{rej_judge.get('rejection_score')}"
                            )
                        if rej_judge.get("alternative_score", 0) < 4:
                            failures.append(
                                "Revision rejection alternative score below threshold: "
                                f"{rej_judge.get('alternative_score')}"
                            )
                        details["final_rejection_judge"] = rej_judge
                    else:
                        failures.append("Final rejection judge returned empty result")
                except Exception as e:
                    failures.append(f"Final rejection judge failed: {str(e)[:200]}")

            scores.setdefault("flow_score", 5.0)
        else:
            # Non-rejection revisions must return to consultant stage before final approval.
            if t3.get("status") != "CHATTING" or t3.get("nextflow_code"):
                failures.append(
                    "Revision step must remain CHATTING with no code; "
                    "final execution must happen only after explicit second approval"
                )

            # Turn 4: force approval for revised execution if needed.
            forced_t4 = _force_approved_execution(
                api_client,
                session_id,
                
            )
            if not forced_t4:
                failures.append("Could not reach final APPROVED revised execution after forced approvals")
                t4 = None
                final_code = None
            else:
                t4 = forced_t4
                final_code = t4["nextflow_code"]
                final_mermaid = t4.get("mermaid_code")
                

            if not final_code:
                failures.append("Final revised execution did not return nextflow_code")
            if not final_mermaid:
                failures.append("Final revised execution missing agentic mermaid diagram")
            if not final_mermaid_code:
                failures.append("Final revised execution missing deterministic mermaid diagram")

            if final_code and scenario.get("expect_code_change", False) and initial_code == final_code:
                failures.append("Expected code change, but initial and final code are identical")

            must_include = scenario.get("must_include_final", [])
            missing = []
            if final_code:
                final_lc = final_code.lower()
                missing = [token for token in must_include if token.lower() not in final_lc]
                if missing:
                    failures.append(f"Final revised code missing expected tokens: {missing}")

            # Judge only the final architect output against revision RAG context.
            if judge_llm and final_code:
                try:
                    architect_judge = run_pipeline_judge(
                        judge_llm=judge_llm,
                        design_plan=scenario.get("design_plan", ""),
                        tech_context=revision_context,
                        nf_code=final_code,
                    )
                    if architect_judge:
                        for k, v in architect_judge.items():
                            if "score" in k:
                                scores[f"final_architect_{k}"] = v
                        if architect_judge.get("syntax_score", 0) < 4:
                            failures.append(
                                "Final architect syntax score below threshold: "
                                f"{architect_judge.get('syntax_score')}"
                            )
                        if architect_judge.get("logic_score", 0) < 4:
                            failures.append(
                                "Final architect logic score below threshold: "
                                f"{architect_judge.get('logic_score')}"
                            )
                        details["final_architect_judge"] = architect_judge
                    else:
                        failures.append("Final architect judge returned empty result")
                except Exception as e:
                    failures.append(f"Final architect judge failed: {str(e)[:200]}")
            elif judge_llm and not final_code:
                failures.append("Final architect judge skipped because final code was not generated")

            # Mandatory diagram judges for BOTH final diagram variants.
            if judge_llm and final_code and final_mermaid_code:
                try:
                    det_judge = run_diagram_judge(
                        judge_llm=judge_llm,
                        tech_context=revision_context,
                        nf_code=final_code,
                        mermaid_code=final_mermaid_code,
                        strategy="deterministic",
                    )
                    if det_judge:
                        for k, v in det_judge.items():
                            if "score" in k:
                                scores[f"final_det_{k}"] = v
                        if det_judge.get("syntax_score", 0) < 4:
                            failures.append(
                                "Final deterministic diagram syntax score below threshold: "
                                f"{det_judge.get('syntax_score')}"
                            )
                        if det_judge.get("mapping_score", 0) < 4:
                            failures.append(
                                "Final deterministic diagram mapping score below threshold: "
                                f"{det_judge.get('mapping_score')}"
                            )
                        details["final_det_diagram_judge"] = det_judge
                    else:
                        failures.append("Final deterministic diagram judge returned empty result")
                except Exception as e:
                    failures.append(f"Final deterministic diagram judge failed: {str(e)[:200]}")

            if judge_llm and final_code and final_mermaid:
                try:
                    agent_judge = run_diagram_judge(
                        judge_llm=judge_llm,
                        tech_context=revision_context,
                        nf_code=final_code,
                        mermaid_code=final_mermaid,
                        strategy="agentic",
                    )
                    if agent_judge:
                        for k, v in agent_judge.items():
                            if "score" in k:
                                scores[f"final_agent_{k}"] = v
                        if agent_judge.get("syntax_score", 0) < 4:
                            failures.append(
                                "Final agentic diagram syntax score below threshold: "
                                f"{agent_judge.get('syntax_score')}"
                            )
                        if agent_judge.get("mapping_score", 0) < 4:
                            failures.append(
                                "Final agentic diagram mapping score below threshold: "
                                f"{agent_judge.get('mapping_score')}"
                            )
                        details["final_agent_diagram_judge"] = agent_judge
                    else:
                        failures.append("Final agentic diagram judge returned empty result")
                except Exception as e:
                    failures.append(f"Final agentic diagram judge failed: {str(e)[:200]}")

            if judge_llm and not final_mermaid_code:
                failures.append("Final deterministic diagram judge skipped because deterministic diagram missing")
            if judge_llm and not final_mermaid:
                failures.append("Final agentic diagram judge skipped because agentic diagram missing")

            # Compiler-level validation for final revised code.
            if final_code:
                try:
                    val_res = validate_nextflow(final_code, run_stub=(scenario.get("level", 1) >= 3))
                    details["final_nextflow_validation"] = val_res
                    if val_res.get("nf_syntax_passed") is False:
                        failures.append(
                            "Final revised code failed Nextflow syntax validation: "
                            f"{val_res.get('nf_syntax_error', 'Unknown error')[:200]}"
                        )
                except Exception as e:
                    details["final_nextflow_validation_error"] = str(e)[:200]

            scores.setdefault("flow_score", 5.0)

        details.update(
            {
                "turn1_status": t1.get("status"),
                "turn2_status": t2.get("status"),
                "turn3_status": t3.get("status"),
                "turn4_status": t4.get("status") if t4 else None,
                "turn1_reply": t1.get("reply"),
                "turn2_reply": t2.get("reply"),
                "turn3_reply": t3.get("reply"),
                "turn4_reply": t4.get("reply") if t4 else None,
                "initial_code_length": len(initial_code) if initial_code else 0,
                "final_code_length": len(final_code) if final_code else 0,
                "initial_code": initial_code,
                "final_code": final_code,
                "final_mermaid": t4.get("mermaid_code") if t4 else None,
                
                "expect_code_change": scenario.get("expect_code_change", False),
                "must_include_final": scenario.get("must_include_final", []),
                "revision_consultant_reply": t3.get("reply"),
                "expect_rejection_on_revision": expect_rejection_on_revision,
                "initial_expected_ids": initial_expected,
                "revision_expected_ids": revision_expected,
                "initial_found_ids": initial_found,
                "revision_found_ids": revision_found,
                "initial_missing_ids": missing_initial,
                "revision_missing_ids": missing_revision,
                "initial_rag_context": initial_context,
                "revision_rag_context": revision_context,
                "failures": failures,
            }
        )

        # Mandatory judge evidence per path.
        if expect_rejection_on_revision:
            if not any(k.startswith("final_rejection_") for k in scores.keys()):
                failures.append("Mandatory final rejection judge scores missing")
        else:
            if not any(k.startswith("final_consultant_") for k in scores.keys()):
                failures.append("Mandatory final consultant judge scores missing")
            if not any(k.startswith("final_architect_") for k in scores.keys()):
                failures.append("Mandatory final architect judge scores missing")
            if not any(k.startswith("final_det_") for k in scores.keys()):
                failures.append("Mandatory final deterministic diagram judge scores missing")
            if not any(k.startswith("final_agent_") for k in scores.keys()):
                failures.append("Mandatory final agentic diagram judge scores missing")

        if failures:
            scores["flow_score"] = 1.0
            details["reason"] = failures[0]
            return {
                "success": False,
                "scores": scores,
                "details": details,
            }

        return {
            "success": True,
            "scores": scores,
            "details": details,
        }

    # Best-of-2 retries; skip attempt 2 when average score is already >= 4.0.
    best_result = run_with_retries(_attempt_once, max_retries=2, stop_avg_score=4.0)

    success = bool(best_result.get("success", False)) and not best_result.get("error")
    details = best_result.get("details", {})
    details["retry_summary"] = best_result.get("all_attempts_summary", [])

    report.add_result(
        scenario_id=f"[Recreation-Flow] {scenario['id']}",
        level=scenario["level"],
        success=success,
        difficulty=scenario.get("difficulty", "—"),
        description=scenario.get("description", ""),
        scores=best_result.get("scores", {}),
        details=details,
    )

    rate_limit_pause(3, reason="between recreation revision flow tests")
    assert success, f"Recreation flow failed: {details.get('reason', best_result.get('error', 'unknown'))}"
