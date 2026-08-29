"""
tests/test_pairwise_ab.py
A/B model comparison mode: compare outputs from two different model runs
on the same prompts using pairwise evaluation.

Usage:
  1. Run the benchmark twice (different models/configs), saving outputs to JSONL
  2. Set environment variables pointing to the two run files:
     export RUN_A_PATH="tests/reports/runs_model_a.jsonl"
     export RUN_B_PATH="tests/reports/runs_model_b.jsonl"
     export RUN_A_LABEL="mistral-large"
     export RUN_B_LABEL="qwen3-30b"
  3. Run: pytest tests/test_pairwise_ab.py -v

The test compares outputs example-by-example across all matching IDs and
produces an A/B comparison report with Glicko-2 ratings and bootstrap
significance tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evaluation.dimensions import DIMENSIONS, get_dimensions_for_test_type
from tests.evaluation.pairwise import PairwiseEvaluator
from tests.evaluation.schemas import BenchmarkResult, DeterministicChecks
from tests.helpers import (
    CHECKPOINT_DIR,
    compute_step_metrics,
)

# ──────────────────────────────────────────────────────────────
# Run file loading
# ──────────────────────────────────────────────────────────────

def _load_run_outputs(path_str: str | None) -> dict[str, dict]:
    """Load a JSONL file of benchmark run outputs into a dict keyed by example ID.

    Expected format per line (compatible with benchmark's runs.jsonl):
    {
        "id": "A01_mlst_listeria",
        "prompt": "...",
        "llm_response": {"status": "...", "nextflow_code": "...", "reply": "..."},
        ...
    }
    """
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        print(f"  ⚠️  Run file not found: {path}")
        return {}

    runs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            runs[rec["id"]] = rec
    return runs


def _get_test_type_from_id(_example_id: str) -> str:
    """Infer test type from example ID naming convention.

    Benchmark IDs follow patterns like:
    - A01_mlst_listeria (single-step → code_generation)
    - E02_cgmlst_lis_fastp_spades (multi-step → code_generation)
    """
    # All benchmark examples are code generation
    return "code_generation"


# ──────────────────────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────────────────────

@pytest.mark.ab_compare
def test_ab_comparison(request, judge_llm, _store, elo_tracker, report):
    """Compare outputs from two different model runs on the same prompts.

    Environment variables:
    - RUN_A_PATH: path to JSONL file with model A's outputs
    - RUN_B_PATH: path to JSONL file with model B's outputs
    - RUN_A_LABEL: human label for model A (default: "model_a")
    - RUN_B_LABEL: human label for model B (default: "model_b")

    CLI options:
    - --first N: only compare the first N common examples
    """
    run_a_path = os.environ.get("RUN_A_PATH")
    run_b_path = os.environ.get("RUN_B_PATH")
    label_a = os.environ.get("RUN_A_LABEL", "model_a")
    label_b = os.environ.get("RUN_B_LABEL", "model_b")

    if not run_a_path or not run_b_path:
        pytest.skip(
            "A/B comparison requires RUN_A_PATH and RUN_B_PATH env vars. "
            "Run the benchmark with two different models first."
        )

    run_a = _load_run_outputs(run_a_path)
    run_b = _load_run_outputs(run_b_path)

    # Find common example IDs
    common_ids = sorted(set(run_a.keys()) & set(run_b.keys()))

    # Apply --first limit
    first_n = request.config.getoption("--first", default=None)
    if first_n is not None:
        common_ids = common_ids[:first_n]
    if not common_ids:
        pytest.skip("No common example IDs found between the two runs.")

    print(f"\n  🆚 A/B Comparison: {label_a} vs {label_b}")
    print(f"     {len(common_ids)} common examples")

    checkpoint_path = CHECKPOINT_DIR / f"eval_checkpoint_ab_{label_a}_vs_{label_b}.jsonl"
    evaluator = PairwiseEvaluator(judge_llm, checkpoint_path=checkpoint_path)

    for example_id in common_ids:
        test_type = _get_test_type_from_id(example_id)

        # Skip if fully completed
        if evaluator.is_fully_completed(example_id, test_type):
            dims = get_dimensions_for_test_type(test_type)
            cached = [evaluator.get_cached_result(example_id, d) for d in dims]
            for pr in cached:
                if pr:
                    elo_tracker.record_match(label_a, label_b, pr.dimension, pr.verdict)
            continue

        print(f"\n  🔍 [{example_id}] Comparing {label_a} vs {label_b}...")

        # Extract code from both runs
        code_a = (run_a[example_id].get("llm_response", {}).get("nextflow_code", "") or "")
        code_b = (run_b[example_id].get("llm_response", {}).get("nextflow_code", "") or "")
        reply_a = (run_a[example_id].get("llm_response", {}).get("reply", "") or "")
        reply_b = (run_b[example_id].get("llm_response", {}).get("reply", "") or "")

        if not code_a and not code_b:
            print(f"    ⚠️  Both runs returned no code for {example_id}, skipping")
            continue

        # Format for pairwise comparison
        option_a = f"Reply:\n{reply_a[:2000]}\n\nCode:\n{code_a}" if code_a else f"Reply:\n{reply_a[:2000]}\n\n(No code generated)"
        option_b = f"Reply:\n{reply_b[:2000]}\n\nCode:\n{code_b}" if code_b else f"Reply:\n{reply_b[:2000]}\n\n(No code generated)"

        context = {
            "prompt": run_a[example_id].get("prompt", ""),
            "context": "",  # A/B comparison doesn't need catalog context
        }

        pairwise_results = evaluator.evaluate_all_dimensions(
            example_id=example_id,
            test_type=test_type,
            option_a=option_a,
            option_b=option_b,
            context=context,
        )

        # Feed into Glicko-2
        for pr in pairwise_results:
            elo_tracker.record_match(label_a, label_b, pr.dimension, pr.verdict)

    # Bootstrap significance tests across all dimensions
    print(f"\n  📊 Bootstrap significance testing ({label_a} vs {label_b})...")
    significance = elo_tracker.bootstrap_all_dimensions(label_a, label_b)
    report.add_ab_significance(label_a, label_b, significance)

    print(f"\n  ✅ A/B comparison complete: {len(common_ids)} examples")
    for dim, sig in significance.items():
        status = "✅ significant" if sig["significant_at_005"] else "❌ not significant"
        print(f"    {dim}: Δ={sig['mean_delta']:+.1f}, p={sig['p_value']:.4f} {status}")
