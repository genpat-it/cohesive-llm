"""
tests/test_benchmark_single_turn.py
Pairwise evaluation of single-turn benchmark examples.

Each example is sent through the full agent pipeline (/chat endpoint),
then evaluated against ground truth via:
  1. Deterministic checks (code presence, step matching, P/R/F1)
  2. Ground-truth three-tier verdict (MATCH / EXCEEDS / DEFICIENT)
  3. Pairwise comparison across applicable dimensions (dual ordering)
  4. Glicko-2 rating updates

Subset control (via argparse now instead of pytest):
  python3 tests/test_benchmark_single_turn.py                       # all 205
  python3 tests/test_benchmark_single_turn.py --first 10            # first 10
  python3 tests/test_benchmark_single_turn.py --complexity 1        # mono-step only
  python3 tests/test_benchmark_single_turn.py --max-complexity 2    # mono + 2step
  python3 tests/test_benchmark_single_turn.py --category mono-typing
  python3 tests/test_benchmark_single_turn.py --ids A01_mlst_listeria,B01_spades_listeria
"""
from __future__ import annotations

import argparse
import contextlib
import sys
import uuid

# Enable automatic LangGraph trace dumping for all benchmark runs
import os
import time
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
run_timestamp = time.strftime("%Y%m%d_%H%M%S")
os.environ["IZS_LOG_DIR"] = str(PROJECT_ROOT / "tests" / "reports" / "logs" / f"run_{run_timestamp}")

from dotenv import load_dotenv

load_dotenv()

from tests.benchmark.loader import load_single_turn_examples
from tests.evaluation.dimensions import DIMENSION_MAP
from tests.evaluation.pairwise import PairwiseEvaluator
from tests.evaluation.schemas import (
    BenchmarkResult,
    DeterministicChecks,
    GroundTruthVerdict,
)
from tests.helpers import (
    CHECKPOINT_DIR,
    compute_step_metrics,
    format_ground_truth_for_pairwise,
    format_llm_output_for_pairwise,
    get_exact_context,
    rate_limit_pause,
    run_multi_turn_chat,
    send_chat,
)
from tests.nf_validation import validate_nextflow

CHECKPOINT_PATH = CHECKPOINT_DIR / "eval_checkpoint_single.jsonl"


# ──────────────────────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────────────────────

import pytest


def pytest_generate_tests(metafunc):
    """Dynamically parametrize single-turn tests based on CLI options."""
    if "example" not in metafunc.fixturenames:
        return

    config = metafunc.config
    kwargs = {"test_type": config.getoption("--test-type", default="level_unified")}

    first = config.getoption("--first", default=None)
    if first is not None:
        kwargs["limit"] = first

    ids_str = config.getoption("--ids", default=None)
    if ids_str:
        kwargs["only_ids"] = set(ids_str.split(","))

    cat_str = config.getoption("--category", default=None)
    if cat_str:
        kwargs["only_categories"] = set(cat_str.split(","))

    examples = load_single_turn_examples(**kwargs)

    metafunc.parametrize(
        "example",
        examples,
        ids=[e["id"] for e in examples],
    )

@pytest.mark.single_turn
def test_single_turn_benchmark(example, api_client, judge_llm, store, elo_tracker, report):
    """Pytest entrypoint for the single-turn benchmark."""
    run_single_turn_benchmark(example, api_client, judge_llm, store, elo_tracker, report)

def run_single_turn_benchmark(example, api_client, judge_llm, store, elo_tracker, report):
    """Full pipeline test: prompt → /chat → pairwise comparison vs ground truth.

    Steps:
    1. Check if already completed (checkpoint)
    2. Send prompt through API (full agent pipeline)
    3. Run deterministic checks
    4. Classify ground truth (MATCH / EXCEEDS / DEFICIENT)
    5. Pairwise comparison per dimension (with position bias control)
    6. Feed results into Glicko-2 tracker
    7. Record to report
    """
    example_id = example["id"]

    if example["test_type"] == "level_unified":
        return _run_level_unified_benchmark(example, api_client, judge_llm, store, elo_tracker, report)

    # ── 0. Check if already fully completed ──
    evaluator = PairwiseEvaluator(judge_llm, checkpoint_path=CHECKPOINT_PATH)
    if evaluator.is_fully_completed(example_id, example["test_type"]):
        # Still load cached results into elo_tracker and report
        dims = DIMENSION_MAP[example["test_type"]]
        cached_pairwise = [
            evaluator.get_cached_result(example_id, d) for d in dims
        ]
        cached_pairwise = [r for r in cached_pairwise if r is not None]
        for pr in cached_pairwise:
            elo_tracker.record_match("llm_output", "ground_truth", pr.dimension, pr.verdict)
        print(f"  ⏭️  Skipping {example_id} (already completed — loaded from checkpoint)")
        return None

    # ── 1. Send prompt through API ──
    session_id = f"bench_{example_id}_{uuid.uuid4().hex[:8]}"
    print(f"\n  🔬 [{example_id}] cat={example.get('category','')} "
          f"complexity={example.get('complexity','')} — Sending to /chat...")

    raw_chat_messages = example.get("chat_messages", [example.get("prompt", "")])
    if example["test_type"] == "modification":
        chat_messages = [msg for i, msg in enumerate(raw_chat_messages) if i % 2 == 0]
        chat_messages.append("I approve the plan, please build the pipeline.")
    else:
        chat_messages = raw_chat_messages

    expect_rej = example.get("expect_rejection", False)
    result = run_multi_turn_chat(api_client, chat_messages, expect_rejection=expect_rej, session_id=session_id)

    if not result["success"]:
        print(f"  ❌ [{example_id}] API call failed: {result.get('error')}")

    # ── 2. Deterministic checks ──
    nf_code = result.get("nextflow_code", "") or ""
    gt_code = example.get("nextflow_code", "") or ""

    step_metrics = compute_step_metrics(nf_code, gt_code)

    val_result = validate_nextflow(nf_code, run_stub=True)
    if "nf_syntax_passed" not in val_result:
        raise RuntimeError("Nextflow validation skipped (requires NF_FRAMEWORK_DIR)")

    det_checks = DeterministicChecks(
        has_code=bool(nf_code),
        syntax_valid=val_result.get("nf_syntax_passed"),
        stub_run_valid=val_result.get("nf_stub_passed"),
        expected_processes=example.get("expected_processes", 0),
        included_steps=step_metrics["llm_steps"],
        called_steps=step_metrics["llm_steps"],
        ground_truth_steps=step_metrics["gt_steps"],
        extra_steps=step_metrics["extra_steps"],
        missing_steps=step_metrics["missing_steps"],
        tool_routing_precision_pct=step_metrics["precision"],
        tool_routing_recall_pct=step_metrics["recall"],
        tool_routing_f1_pct=step_metrics["f1"],
    )

    # ── 3. Ground-truth three-tier verdict ──
    gt_verdict = _classify_ground_truth(step_metrics)

    # ── 4. Pairwise comparison ──
    pairwise_results = []
    if nf_code and judge_llm:
        context_str = ""
        if store and example.get("component_ids"):
            try:
                context_str = get_exact_context(
                    example.get("template_ids", []),
                    example["component_ids"],
                    store,
                )
            except Exception as e:
                print(f"  ⚠️  Context build failed: {e}")
                context_str = ""

        context = {
            "prompt": chat_messages[-1] if chat_messages else "",
            "context": context_str,
        }

        llm_formatted = format_llm_output_for_pairwise(result)
        gt_formatted = format_ground_truth_for_pairwise(example)

        pairwise_results = evaluator.evaluate_all_dimensions(
            example_id=example_id,
            test_type=example["test_type"],
            option_a=llm_formatted,
            option_b=gt_formatted,
            context=context,
        )

    # ── 5. Feed into Glicko-2 tracker ──
    for pr in pairwise_results:
        elo_tracker.record_match("llm_output", "ground_truth", pr.dimension, pr.verdict)

    # ── 6. Record to report ──
    benchmark_result = BenchmarkResult(
        example_id=example_id,
        test_type=example["test_type"],
        ground_truth_verdict=gt_verdict,
        pairwise_results=pairwise_results,
        deterministic_checks=det_checks,
        llm_output={
            "status": result.get("status"),
            "reply": (result.get("reply") or "")[:500],
            "has_code": bool(nf_code),
            "code_lines": nf_code.count("\n") + 1 if nf_code else 0,
        },
        elapsed_s=result.get("elapsed", 0.0),
    )
    report.add_benchmark_result(benchmark_result)

    # Rate limit pause between tests
    rate_limit_pause(2, "between benchmark examples")

    # ── 7. Log results ──
    if not nf_code:
        print(f"  ⚠️  [{example_id}] No code returned — error: {result.get('error')}")
    else:
        print(f"  ✅ [{example_id}] GT={gt_verdict.tier} "
              f"P={det_checks.tool_routing_precision_pct}% "
              f"R={det_checks.tool_routing_recall_pct}% "
              f"F1={det_checks.tool_routing_f1_pct}%")
    return None


# ──────────────────────────────────────────────────────────────
# Ground truth classification helper
# ──────────────────────────────────────────────────────────────

def _classify_ground_truth(step_metrics: dict) -> GroundTruthVerdict:
    """Classify LLM output against ground truth using step-set comparison.

    This is the deterministic classification (not LLM-judged).
    The LLM pairwise comparison provides the nuanced quality assessment.
    """
    missing = step_metrics["missing_steps"]
    extra = step_metrics["extra_steps"]

    if not missing and not extra:
        return GroundTruthVerdict(
            tier="MATCH",
            reasoning="LLM output uses exactly the same steps as ground truth.",
            extra_steps=[],
            missing_steps=[],
        )
    if not missing and extra:
        return GroundTruthVerdict(
            tier="EXCEEDS",
            reasoning=(
                f"LLM output includes all ground-truth steps plus {len(extra)} "
                f"additional steps: {', '.join(extra)}. These may be beneficial "
                f"best-practice additions."
            ),
            extra_steps=extra,
            missing_steps=[],
        )
    return GroundTruthVerdict(
        tier="DEFICIENT",
        reasoning=(
            f"LLM output is missing {len(missing)} ground-truth steps: "
            f"{', '.join(missing)}."
            + (f" It also added {len(extra)} extra steps: {', '.join(extra)}."
               if extra else "")
        ),
        extra_steps=extra,
        missing_steps=missing,
    )

# ──────────────────────────────────────────────────────────────
# Level Unified Benchmark execution
# ──────────────────────────────────────────────────────────────

def _run_level_unified_benchmark(example, api_client, judge_llm, store, elo_tracker, report):
    example_id = example["id"]
    evaluator = PairwiseEvaluator(judge_llm, checkpoint_path=CHECKPOINT_PATH)

    # We must check two dimensions types: level_unified_turn1 and level_unified_turn2
    dims_t1 = DIMENSION_MAP["level_unified_turn1"]
    dims_t2 = DIMENSION_MAP["level_unified_turn2"]

    if evaluator.is_fully_completed(example_id, "level_unified_turn1") and \
       evaluator.is_fully_completed(example_id, "level_unified_turn2"):
        cached_pairwise = []
        for d in dims_t1 + dims_t2:
            pr = evaluator.get_cached_result(example_id, d)
            if pr is not None:
                cached_pairwise.append(pr)
                elo_tracker.record_match("llm_output", "ground_truth", pr.dimension, pr.verdict)
        print(f"  ⏭️  Skipping {example_id} (already completed — loaded from checkpoint)")
        return

    print(f"\n  🔬 [{example_id}] Unified 2-Turn Test — Sending Turn 1 (Consultant) ...")

    chat_messages = example.get("chat_messages", [example.get("prompt", "")])
    session_id = f"bench_uni_{example_id}_{uuid.uuid4().hex[:8]}"

    turn1_result = run_multi_turn_chat(api_client, chat_messages, expect_rejection=False, session_id=session_id)

    if not turn1_result["success"]:
        print(f"  ❌ [{example_id}] Turn 1 failed: {turn1_result.get('error')}")

    pairwise_results = []

    # Evaluate Turn 1 (Consultant dimensions)
    if judge_llm:
        t1_llm_formatted = format_llm_output_for_pairwise(turn1_result)
        # Create a temporary consultant example
        t1_gt_example = {"consultant_reply": example.get("consultant_reply", ""), "test_type": "consultant"}
        t1_gt_formatted = format_ground_truth_for_pairwise(t1_gt_example)

        pr_t1 = evaluator.evaluate_all_dimensions(
            example_id=example_id,
            test_type="level_unified_turn1",
            option_a=t1_llm_formatted,
            option_b=t1_gt_formatted,
            context={"prompt": chat_messages[-1], "context": ""}
        )
        pairwise_results.extend(pr_t1)

    # ── Turn 2 ──
    print(f"  🔬 [{example_id}] Unified 2-Turn Test — Sending Turn 2 (Approval) ...")
    turn2_result = send_chat(api_client, session_id, "I approve the plan, please build the pipeline.")

    if not turn2_result["success"]:
        print(f"  ❌ [{example_id}] Turn 2 failed: {turn2_result.get('error')}")

    nf_code = turn2_result.get("nextflow_code", "") or ""
    gt_code = example.get("nextflow_code", "") or ""

    step_metrics = compute_step_metrics(nf_code, gt_code)

    val_result = validate_nextflow(nf_code, run_stub=True)
    if "nf_syntax_passed" not in val_result:
        raise RuntimeError("Nextflow validation skipped (requires NF_FRAMEWORK_DIR)")

    det_checks = DeterministicChecks(
        has_code=bool(nf_code),
        syntax_valid=val_result.get("nf_syntax_passed"),
        stub_run_valid=val_result.get("nf_stub_passed"),
        expected_processes=example.get("expected_processes", 0),
        included_steps=step_metrics["llm_steps"],
        called_steps=step_metrics["llm_steps"],
        ground_truth_steps=step_metrics["gt_steps"],
        extra_steps=step_metrics["extra_steps"],
        missing_steps=step_metrics["missing_steps"],
        tool_routing_precision_pct=step_metrics["precision"],
        tool_routing_recall_pct=step_metrics["recall"],
        tool_routing_f1_pct=step_metrics["f1"],
    )

    gt_verdict = _classify_ground_truth(step_metrics)

    if nf_code and judge_llm:
        context_str = ""
        if store and example.get("component_ids"):
            with contextlib.suppress(Exception):
                context_str = get_exact_context(example.get("template_ids", []), example["component_ids"], store)

        t2_llm_formatted = format_llm_output_for_pairwise(turn2_result)
        # Format ground truth (diagram + code ONLY, not consultant reply)
        t2_gt_example = {
            "diagram_code": example.get("diagram_code", ""),
            "nextflow_code": example.get("nextflow_code", "")
        }
        t2_gt_formatted = format_ground_truth_for_pairwise(t2_gt_example)

        pr_t2 = evaluator.evaluate_all_dimensions(
            example_id=example_id,
            test_type="level_unified_turn2",
            option_a=t2_llm_formatted,
            option_b=t2_gt_formatted,
            context={"prompt": "I approve the plan, please build the pipeline.", "context": context_str}
        )
        pairwise_results.extend(pr_t2)

    for pr in pairwise_results:
        elo_tracker.record_match("llm_output", "ground_truth", pr.dimension, pr.verdict)

    benchmark_result = BenchmarkResult(
        example_id=example_id,
        test_type=example["test_type"],
        ground_truth_verdict=gt_verdict,
        pairwise_results=pairwise_results,
        deterministic_checks=det_checks,
        llm_output={
            "status": turn2_result.get("status"),
            "reply": (turn2_result.get("reply") or "")[:500],
            "has_code": bool(nf_code),
            "code_lines": nf_code.count("\n") + 1 if nf_code else 0,
        },
        elapsed_s=turn1_result.get("elapsed", 0.0) + turn2_result.get("elapsed", 0.0) if "elapsed" in turn2_result else 0.0,
    )
    report.add_benchmark_result(benchmark_result)

    rate_limit_pause(2, "between unified tests")

    if not nf_code:
        print(f"  ⚠️  [{example_id}] No code returned in Turn 2")
    else:
        print(f"  ✅ [{example_id}] GT={gt_verdict.tier} F1={det_checks.tool_routing_f1_pct}%")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run benchmark single turn tests without pytest")
    parser.add_argument("--first", type=int, default=None, help="Limit number of tests")
    parser.add_argument("--complexity", type=int, default=None, help="Exact complexity to test")
    parser.add_argument("--max-complexity", type=int, default=None, help="Max complexity to test")
    parser.add_argument("--category", type=str, default=None, help="Comma separated categories")
    parser.add_argument("--ids", type=str, default=None, help="Comma separated ids")
    parser.add_argument("--test-type", type=str, default="level_unified", help="The type of tests to run (default: level_unified)")

    args = parser.parse_args()

    kwargs = {"test_type": args.test_type}
    if args.first is not None:
        kwargs["limit"] = args.first
    if args.complexity is not None:
        kwargs["min_complexity"] = args.complexity
        kwargs["max_complexity"] = args.complexity
    if args.max_complexity is not None:
        kwargs["max_complexity"] = args.max_complexity
    if args.category:
        kwargs["only_categories"] = set(args.category.split(","))
    if args.ids:
        kwargs["only_ids"] = set(args.ids.split(","))

    examples = load_single_turn_examples(**kwargs)
    print(f"Loaded {len(examples)} examples.")

    import os

    from fastapi.testclient import TestClient
    from langgraph.store.memory import InMemoryStore

    from core.api import app
    from core.loader import data_loader
    from core.services.llm import get_judge_llm
    from tests.evaluation.elo import Glicko2Tracker
    from tests.report import PairwiseReport

    with TestClient(app) as api_client:
        store = InMemoryStore()
        print("Loading database...")
        data_loader.load_all(store=store)

        judge_enabled = True
        judge_llm_instance = None
        if judge_enabled and os.environ.get("JUDGE_BASE_URL"):
            judge_llm_instance = get_judge_llm(temperature=0.0)

        elo_tracker = Glicko2Tracker()
        report = PairwiseReport()

        for example in examples:
            run_single_turn_benchmark(example, api_client, judge_llm_instance, store, elo_tracker, report)

        report.save(elo_tracker)
    print("Done! Evaluation report finalized.")
