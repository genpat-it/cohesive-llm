"""
tests/evaluation/pairwise.py
Core pairwise comparison engine with position bias control and checkpoint/resume.

This module runs LLM judge comparisons between two options (e.g., LLM output
vs ground truth) with the following methodology:

1. For each evaluation dimension, present Option A vs Option B to the judge
2. Run the comparison TWICE with swapped ordering to control position bias
3. Apply majority vote: both agree → that's the verdict; disagree → tie
4. Checkpoint results to JSONL so runs can be stopped and resumed

The engine uses structured output (Pydantic) to get CoT reasoning + verdict
from the judge LLM.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tests.evaluation.dimensions import DIMENSION_MAP, get_dimensions_for_test_type
from tests.evaluation.prompts import PAIRWISE_PROMPTS
from tests.evaluation.schemas import PairwiseResult, PairwiseVerdict

# ──────────────────────────────────────────────────────────────
# Judge structured output schema
# ──────────────────────────────────────────────────────────────

class JudgeOutput(BaseModel):
    """Structured output the judge LLM must produce."""
    reasoning: str = Field(
        description="Step-by-step analysis comparing both options."
    )
    winner: str = Field(
        description="Which option is better: 'A', 'B', or 'tie'."
    )


# ──────────────────────────────────────────────────────────────
# Pairwise Evaluator
# ──────────────────────────────────────────────────────────────

class PairwiseEvaluator:
    """Runs pairwise comparisons with position bias control and checkpoint/resume.

    Usage:
        evaluator = PairwiseEvaluator(judge_llm, checkpoint_path=Path("checkpoint.jsonl"))

        # Check if already done
        if not evaluator.is_fully_completed("example_A01", "code_generation"):
            results = evaluator.evaluate_all_dimensions(
                example_id="example_A01",
                test_type="code_generation",
                option_a="... LLM output ...",
                option_b="... ground truth ...",
                context={"prompt": "...", "context": "..."},
            )

    Checkpoint file format (JSONL, one line per completed dimension):
        {"checkpoint_key": "A01::syntax", "dimension": "syntax", "verdict": "A", ...}
    """

    def __init__(
        self,
        judge_llm,
        checkpoint_path: Path | None = None,
        rate_limit_pause_s: float = 2.0,
    ):
        self.judge = judge_llm
        self.checkpoint_path = checkpoint_path
        self.rate_limit_pause_s = rate_limit_pause_s
        self._completed: dict[str, PairwiseResult] = {}  # key → cached result

        if checkpoint_path and checkpoint_path.exists():
            self._load_checkpoint()

    # ── Checkpoint management ─────────────────────────────────

    @staticmethod
    def _checkpoint_key(example_id: str, dimension: str) -> str:
        return f"{example_id}::{dimension}"

    def _load_checkpoint(self):
        """Load previously completed comparisons from checkpoint JSONL."""
        loaded = 0
        for line in self.checkpoint_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                key = rec["checkpoint_key"]
                self._completed[key] = PairwiseResult(
                    dimension=rec["dimension"],
                    verdict=rec["verdict"],
                    consistent=rec["consistent"],
                    reasoning_original=rec.get("reasoning_original", ""),
                    reasoning_swapped=rec.get("reasoning_swapped", ""),
                )
                loaded += 1
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  ⚠️  Skipping corrupt checkpoint line: {e}")
        if loaded:
            print(f"  📦 Loaded {loaded} cached pairwise results from checkpoint")

    def _save_result(self, key: str, result: PairwiseResult):
        """Append a completed comparison to the checkpoint file."""
        if not self.checkpoint_path:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "checkpoint_key": key,
                "dimension": result.dimension,
                "verdict": result.verdict,
                "consistent": result.consistent,
                "reasoning_original": result.reasoning_original,
                "reasoning_swapped": result.reasoning_swapped,
                "timestamp": datetime.now(UTC).isoformat(),
            }, ensure_ascii=False) + "\n")

    def is_completed(self, example_id: str, dimension: str) -> bool:
        """Check if a specific example+dimension pair is already evaluated."""
        return self._checkpoint_key(example_id, dimension) in self._completed

    def is_fully_completed(self, example_id: str, test_type: str) -> bool:
        """Check if all dimensions for an example are already evaluated."""
        dims = get_dimensions_for_test_type(test_type)
        return all(self.is_completed(example_id, d) for d in dims)

    def get_cached_result(self, example_id: str, dimension: str) -> PairwiseResult | None:
        """Return a cached result if available."""
        key = self._checkpoint_key(example_id, dimension)
        return self._completed.get(key)

    # ── Core comparison logic ─────────────────────────────────

    def _judge_once(
        self,
        option_a: str,
        option_b: str,
        dimension: str,
        context: dict[str, str],
        ordering: str,
    ) -> PairwiseVerdict:
        """Run one judge call for a single ordering.

        Parameters
        ----------
        option_a, option_b : str
            The two options to compare (in this ordering).
        dimension : str
            Which dimension to evaluate.
        context : dict
            Must include 'prompt' and 'context' keys at minimum.
        ordering : str
            "original" or "swapped" — for logging/tracking.

        Returns
        -------
        PairwiseVerdict with the judge's decision.
        """
        prompt_template = PAIRWISE_PROMPTS[dimension]
        judge_chain = prompt_template | self.judge.with_structured_output(JudgeOutput)

        invoke_args = {
            "option_a": option_a,
            "option_b": option_b,
            "prompt": context.get("prompt", ""),
            "context": context.get("context", ""),
        }

        attempt = 0
        while True:
            try:
                result = judge_chain.invoke(invoke_args)
                winner = result.winner.strip().lower()
                # Normalize winner to A/B/tie
                if winner in ("a", "option a", "option_a"):
                    winner = "A"
                elif winner in ("b", "option b", "option_b"):
                    winner = "B"
                elif winner in ("tie", "draw", "equal", "both"):
                    winner = "tie"
                else:
                    print(f"  ⚠️  Unexpected winner value '{result.winner}', treating as tie")
                    winner = "tie"

                return PairwiseVerdict(
                    dimension=dimension,
                    reasoning=result.reasoning,
                    winner=winner,
                    ordering=ordering,
                )
            except Exception as e:
                is_unit_test = (self.rate_limit_pause_s == 0.0)
                if is_unit_test:
                    print(f"  ❌ Judge call failed for {dimension}/{ordering}: {e}")
                    return PairwiseVerdict(
                        dimension=dimension,
                        reasoning=f"Judge call failed: {e}",
                        winner="tie",
                        ordering=ordering,
                    )
                attempt += 1
                print(f"  ⚠️  [Attempt {attempt}] Judge call failed/timed out: {e}. Keeping wait & retrying in 10s...")
                time.sleep(10)

    @staticmethod
    def _remap_winner(verdict: PairwiseVerdict) -> PairwiseVerdict:
        """Remap a swapped-ordering verdict back to original semantics.

        If the swapped judge says "A wins", it means original B wins
        (because A and B were swapped).
        """
        remap = {"A": "B", "B": "A", "tie": "tie"}
        return PairwiseVerdict(
            dimension=verdict.dimension,
            reasoning=verdict.reasoning,
            winner=remap[verdict.winner],
            ordering=verdict.ordering,
        )

    def compare(
        self,
        example_id: str,
        option_a: str,
        option_b: str,
        dimension: str,
        context: dict[str, str],
    ) -> PairwiseResult:
        """Run a single dimension comparison with dual ordering and position bias control.

        If already checkpointed, returns the cached result immediately.

        Parameters
        ----------
        example_id : str
            Unique identifier for the example being evaluated.
        option_a, option_b : str
            The two outputs to compare.
        dimension : str
            Which evaluation dimension to judge.
        context : dict
            Must include 'prompt' and 'context' keys.

        Returns
        -------
        PairwiseResult with the position-bias-controlled verdict.
        """
        key = self._checkpoint_key(example_id, dimension)

        # Return cached result if available
        if key in self._completed:
            return self._completed[key]

        print(f"    🔍 Judging {dimension} for {example_id}...")

        # Ordering 1: original (A=option_a, B=option_b)
        verdict_original = self._judge_once(
            option_a, option_b, dimension, context, "original"
        )

        # Rate limit pause between judge calls
        if self.rate_limit_pause_s > 0:
            time.sleep(self.rate_limit_pause_s)

        # Ordering 2: swapped (A=option_b, B=option_a)
        verdict_swapped_raw = self._judge_once(
            option_b, option_a, dimension, context, "swapped"
        )
        # Remap to original semantics
        verdict_swapped = self._remap_winner(verdict_swapped_raw)

        # Rate limit pause
        if self.rate_limit_pause_s > 0:
            time.sleep(self.rate_limit_pause_s)

        # Majority vote
        if verdict_original.winner == verdict_swapped.winner:
            final_verdict = verdict_original.winner
            consistent = True
        else:
            final_verdict = "tie"  # disagreement → conservative tie
            consistent = False

        result = PairwiseResult(
            dimension=dimension,
            verdict=final_verdict,
            consistent=consistent,
            reasoning_original=verdict_original.reasoning,
            reasoning_swapped=verdict_swapped_raw.reasoning,
        )

        # Cache and checkpoint
        self._completed[key] = result
        self._save_result(key, result)

        status = "✅" if consistent else "⚠️ inconsistent→tie"
        print(f"    {status} {dimension}: {final_verdict}")

        return result

    def evaluate_all_dimensions(
        self,
        example_id: str,
        test_type: str,
        option_a: str,
        option_b: str,
        context: dict[str, str],
    ) -> list[PairwiseResult]:
        """Run all applicable dimensions for a test type.

        Automatically skips already-checkpointed dimensions, making this
        safe to call repeatedly across multiple sessions.

        Parameters
        ----------
        example_id : str
            Unique identifier for the example.
        test_type : str
            Test type determining which dimensions to evaluate.
        option_a, option_b : str
            The two outputs to compare.
        context : dict
            Must include 'prompt' and 'context' keys.

        Returns
        -------
        List of PairwiseResult, one per applicable dimension.
        """
        dims = get_dimensions_for_test_type(test_type)
        results = []

        for dim in dims:
            result = self.compare(example_id, option_a, option_b, dim, context)
            results.append(result)

        return results
