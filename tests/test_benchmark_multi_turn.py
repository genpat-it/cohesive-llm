"""
tests/test_benchmark_multi_turn.py
Pairwise evaluation of multi-turn benchmark conversations.

Each conversation is driven through the /chat endpoint using the same
session_id across turns, simulating a real user modifying their pipeline
request. Each turn is evaluated independently against its ground truth.

Subset control:
  pytest tests/test_benchmark_multi_turn.py                        # all 159
  pytest tests/test_benchmark_multi_turn.py --first 5              # first 5
  pytest tests/test_benchmark_multi_turn.py --mod-kind add         # only "add" modifications
  pytest tests/test_benchmark_multi_turn.py --mod-kind drop --mod-kind replace
  pytest tests/test_benchmark_multi_turn.py --ids MOD_001,MOD_002
  pytest tests/test_benchmark_multi_turn.py -k "MOD_001"           # pytest native
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

# Enable automatic LangGraph trace dumping for all benchmark runs
import time
PROJECT_ROOT = Path(__file__).resolve().parent.parent
run_timestamp = time.strftime("%Y%m%d_%H%M%S")
os.environ["IZS_LOG_DIR"] = str(PROJECT_ROOT / "tests" / "reports" / "logs" / f"run_{run_timestamp}")

from dotenv import load_dotenv

load_dotenv()

try:
    import pytest
    has_pytest = True
except ImportError:
    has_pytest = False
    class DummyMark:
        def __getattr__(self, name):
            return lambda func: func
    class DummyPytest:
        mark = DummyMark()
    pytest = DummyPytest()

from tests.benchmark.loader import load_multi_turn_examples
from tests.evaluation.dimensions import DIMENSION_MAP
from tests.evaluation.pairwise import PairwiseEvaluator
from tests.evaluation.schemas import (
    BenchmarkResult,
    DeterministicChecks,
    GroundTruthVerdict,
    MultiTurnResult,
)
from tests.helpers import (
    CHECKPOINT_DIR,
    compute_step_metrics,
    format_ground_truth_for_pairwise,
    format_llm_output_for_pairwise,
    get_exact_context,
    rate_limit_pause,
    send_chat,
)
from tests.nf_validation import validate_nextflow

CHECKPOINT_PATH = CHECKPOINT_DIR / "eval_checkpoint_multi.jsonl"


# ──────────────────────────────────────────────────────────────
# Dynamic parametrization from CLI options
# ──────────────────────────────────────────────────────────────

def pytest_generate_tests(metafunc):
    """Dynamically parametrize multi-turn tests based on CLI options."""
    if "conv" not in metafunc.fixturenames:
        return

    config = metafunc.config
    kwargs = {}

    first = config.getoption("--first", default=None)
    if first is not None:
        kwargs["limit"] = first

    ids_str = config.getoption("--ids", default=None)
    if ids_str:
        kwargs["only_ids"] = set(ids_str.split(","))

    mod_kinds = config.getoption("--mod-kind", default=None)
    if mod_kinds:
        kwargs["only_mod_kinds"] = set(mod_kinds)

    conversations = load_multi_turn_examples(**kwargs)

    metafunc.parametrize(
        "conv",
        conversations,
        ids=[c["id"] for c in conversations],
    )


# ──────────────────────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────────────────────

@pytest.mark.multi_turn
def test_multi_turn_benchmark(conv, api_client, judge_llm, store, elo_tracker, report):
    """Multi-turn conversation test: same session, sequential turns.

    Steps per turn:
    1. Send prompt through API (using the same session_id)
    2. Run deterministic checks against that turn's ground truth
    3. Classify ground truth (MATCH / EXCEEDS / DEFICIENT)
    4. Pairwise comparison per dimension
    5. Feed into Glicko-2 tracker
    """
    conv_id = conv["id"]
    session_id = f"bench_mt_{conv_id}_{uuid.uuid4().hex[:8]}"
    evaluator = PairwiseEvaluator(judge_llm, checkpoint_path=CHECKPOINT_PATH)

    turn_results: list[BenchmarkResult] = []
    all_turns_passed = True

    print(f"\n  🔬 [{conv_id}] Starting multi-turn conversation "
          f"({len(conv['turns'])} turns, kind={conv.get('modification_kind', '?')})...")

    for turn_idx, turn in enumerate(conv.get("turns", [])):
        turn_key = f"{conv_id}_t{turn_idx}"
        turn_prompt = turn["prompt"]

        print(f"    📩 Turn {turn_idx + 1}: {turn_prompt[:80]}...")

        # ── Send prompt through API (Generates Plan) ──
        _plan_result = send_chat(api_client, session_id, turn_prompt)

        # ── Send approval to trigger Architect (Generates Code) ──
        result = send_chat(api_client, session_id, "I approve the plan, please build the pipeline.")

        nf_code = result.get("nextflow_code", "") or ""
        gt_code = turn.get("nextflow_code", "") or ""

        if not nf_code:
            all_turns_passed = False
            print(f"    ⚠️  Turn {turn_idx + 1}: No code returned")

        # ── Deterministic checks ──
        step_metrics = compute_step_metrics(nf_code, gt_code)

        val_result = validate_nextflow(nf_code, run_stub=True)
        if not val_result.get("nf_syntax_passed", False):
            print(f"❌ Syntax error in {turn_key}: {val_result.get('nf_syntax_error', 'Unknown Error')}")
        elif not val_result.get("nf_stub_passed", True):
            print(f"❌ Stub error in {turn_key}: {val_result.get('nf_stub_error', 'Unknown Error')}")

        with open(f"/Users/grady/.gemini/antigravity-ide/brain/e6c889b1-5343-40cb-b833-447a216f1102/artifacts/{turn_key}_comparison.md", "w") as f:
            f.write(f"# Comparison for {turn_key}\n\n")
            f.write("## Ground Truth\n```nextflow\n" + gt_code + "\n```\n\n")
            f.write("## Generated Output\n```nextflow\n" + nf_code + "\n```\n")

        det_checks = DeterministicChecks(
            has_code=bool(nf_code),
            syntax_valid=val_result.get("nf_syntax_passed"),
            stub_run_valid=val_result.get("nf_stub_passed"),
            expected_processes=turn.get("expected_processes", 0),
            included_steps=step_metrics["llm_steps"],
            called_steps=step_metrics["llm_steps"],
            ground_truth_steps=step_metrics["gt_steps"],
            extra_steps=step_metrics["extra_steps"],
            missing_steps=step_metrics["missing_steps"],
            tool_routing_precision_pct=step_metrics["precision"],
            tool_routing_recall_pct=step_metrics["recall"],
            tool_routing_f1_pct=step_metrics["f1"],
        )

        # ── Ground-truth verdict ──
        missing = step_metrics["missing_steps"]
        extra = step_metrics["extra_steps"]

        if not missing and not extra:
            gt_verdict = GroundTruthVerdict(
                tier="MATCH", reasoning="Same steps as ground truth.",
                extra_steps=[], missing_steps=[],
            )
        elif not missing and extra:
            gt_verdict = GroundTruthVerdict(
                tier="EXCEEDS",
                reasoning=f"All GT steps present + {len(extra)} extras: {', '.join(extra)}",
                extra_steps=extra, missing_steps=[],
            )
        else:
            gt_verdict = GroundTruthVerdict(
                tier="DEFICIENT",
                reasoning=f"Missing {len(missing)} steps: {', '.join(missing)}",
                extra_steps=extra, missing_steps=missing,
            )

        # ── Pairwise comparison ──
        pairwise_results = []
        if nf_code and judge_llm:
            context_str = ""
            if store and turn.get("component_ids"):
                try:
                    context_str = get_exact_context(
                        turn.get("template_ids", []),
                        turn["component_ids"],
                        store,
                    )
                except Exception:
                    context_str = ""

            context = {
                "prompt": turn_prompt,
                "context": context_str,
            }

            is_final_turn = (turn_idx == len(conv.get("turns", [])) - 1)
            eval_test_type = "level_unified_turn2" if is_final_turn else "code_generation"

            pairwise_results = evaluator.evaluate_all_dimensions(
                example_id=turn_key,
                test_type=eval_test_type,
                option_a=format_llm_output_for_pairwise(result),
                option_b=format_ground_truth_for_pairwise(turn),
                context=context,
            )

        # ── Feed into Glicko-2 ──
        for pr in pairwise_results:
            elo_tracker.record_match("llm_output", "ground_truth", pr.dimension, pr.verdict)

        turn_results.append(BenchmarkResult(
            example_id=turn_key,
            test_type=eval_test_type,
            ground_truth_verdict=gt_verdict,
            pairwise_results=pairwise_results,
            deterministic_checks=det_checks,
            llm_output={
                "status": result.get("status"),
                "reply": (result.get("reply") or "")[:300],
                "has_code": bool(nf_code),
            },
            elapsed_s=result.get("elapsed", 0.0),
        ))

        # Rate limit between turns
        rate_limit_pause(2, "between multi-turn turns")

    # Record full conversation result
    mt_result = MultiTurnResult(
        conversation_id=conv_id,
        category=conv.get("category", ""),
        modification_kind=conv.get("modification_kind", ""),
        turn_results=turn_results,
        all_turns_passed=all_turns_passed,
    )
    report.add_multi_turn_result(mt_result)

    # Summary log
    passed_turns = sum(1 for tr in turn_results if tr.deterministic_checks.has_code)
    print(f"  {'✅' if all_turns_passed else '⚠️'} [{conv_id}] "
          f"{passed_turns}/{len(turn_results)} turns with code")

    rate_limit_pause(2, "between multi-turn conversations")


if __name__ == "__main__":
    import argparse
    import os

    from fastapi.testclient import TestClient
    from langgraph.store.memory import InMemoryStore

    from core.api import app
    from core.loader import data_loader
    from core.services.llm import get_judge_llm
    from tests.evaluation.elo import Glicko2Tracker
    from tests.report import PairwiseReport

    parser = argparse.ArgumentParser(description="Run benchmark multi turn tests without pytest")
    parser.add_argument("--first", type=int, default=None, help="Limit number of conversations")
    parser.add_argument("--ids", type=str, default=None, help="Comma separated conversation ids")
    parser.add_argument("--mod-kind", action="append", default=None, help="Modification kinds to include (can be specified multiple times)")

    args = parser.parse_args()

    kwargs = {}
    if args.first is not None:
        kwargs["limit"] = args.first
    if args.ids:
        kwargs["only_ids"] = set(args.ids.split(","))
    if args.mod_kind:
        kwargs["only_mod_kinds"] = set(args.mod_kind)

    conversations = load_multi_turn_examples(**kwargs)
    print(f"Loaded {len(conversations)} conversations.")

    with TestClient(app) as api_client:
        store = InMemoryStore()
        print("Loading database...")
        data_loader.load_all(store=store)

        judge_enabled = True
        judge_llm_instance = None
        if judge_enabled:
            judge_llm_instance = get_judge_llm(temperature=0.0)

        elo_tracker = Glicko2Tracker()
        report = PairwiseReport()

        for conv in conversations:
            test_multi_turn_benchmark(conv, api_client, judge_llm_instance, store, elo_tracker, report)

        report.save(elo_tracker)
    print("Done! Multi-turn evaluation report finalized.")
