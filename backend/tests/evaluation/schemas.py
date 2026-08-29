"""
tests/evaluation/schemas.py
Pydantic models for the pairwise comparison evaluation system.

Three tiers of models:
  - PairwiseVerdict: raw output from one judge call (one ordering)
  - PairwiseResult: aggregated result after both orderings (position-bias-controlled)
  - GroundTruthVerdict: three-tier classification for ground-truth comparisons
  - BenchmarkResult: full result for one benchmark example
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ══════════════════════════════════════════════════════════════════════════════
# Raw Judge Output (one dimension, one ordering)
# ══════════════════════════════════════════════════════════════════════════════

class PairwiseVerdict(BaseModel):
    """Result of a single pairwise comparison call (one dimension, one ordering).

    The judge sees Option A and Option B and picks a winner.
    We run this twice with swapped ordering to control for position bias.
    """
    dimension: str = Field(
        description="Which evaluation dimension this verdict is for."
    )
    reasoning: str = Field(
        description="Chain-of-thought reasoning from the judge LLM."
    )
    winner: Literal["A", "B", "tie"] = Field(
        description="Which option the judge selected as better, or 'tie'."
    )
    ordering: Literal["original", "swapped"] = Field(
        description="Whether this was the original or position-swapped ordering."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Position-Bias-Controlled Result (one dimension, both orderings)
# ══════════════════════════════════════════════════════════════════════════════

class PairwiseResult(BaseModel):
    """Aggregated result after running both orderings for one dimension.

    If both orderings agree on the same winner (after remapping), that's the
    verdict. If they disagree, the verdict is 'tie' (conservative approach).
    """
    dimension: str = Field(
        description="Which evaluation dimension this result is for."
    )
    verdict: Literal["A", "B", "tie"] = Field(
        description="Final verdict after position-bias control (majority vote)."
    )
    consistent: bool = Field(
        description="True if both orderings agreed, False if they disagreed (forced tie)."
    )
    reasoning_original: str = Field(
        description="Judge's CoT reasoning from the original ordering."
    )
    reasoning_swapped: str = Field(
        description="Judge's CoT reasoning from the swapped ordering."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Ground Truth Three-Tier Verdict
# ══════════════════════════════════════════════════════════════════════════════

class GroundTruthVerdict(BaseModel):
    """Three-tier classification of LLM output vs. ground truth.

    - MATCH: LLM output is functionally equivalent to ground truth
    - EXCEEDS: LLM output includes all GT elements plus beneficial extras
    - DEFICIENT: LLM output is missing required elements from ground truth
    """
    tier: Literal["MATCH", "EXCEEDS", "DEFICIENT"] = Field(
        description="Classification tier."
    )
    reasoning: str = Field(
        description="Explanation of the classification."
    )
    extra_steps: list[str] = Field(
        default_factory=list,
        description="Steps the LLM added beyond ground truth (potentially beneficial)."
    )
    missing_steps: list[str] = Field(
        default_factory=list,
        description="Required ground-truth steps the LLM omitted."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Deterministic Check Results
# ══════════════════════════════════════════════════════════════════════════════

class DeterministicChecks(BaseModel):
    """Results of deterministic (non-LLM) validation checks."""
    has_code: bool = Field(
        description="Whether the LLM returned any Nextflow code."
    )
    syntax_valid: bool | None = Field(
        default=None,
        description="Whether `nextflow -preview` passed."
    )
    stub_run_valid: bool | None = Field(
        default=None,
        description="Whether `nextflow -stub-run` passed."
    )
    n_processes: int = Field(
        default=0,
        description="Number of distinct process placeholders scheduled."
    )
    expected_processes: int = Field(
        default=0,
        description="Number of processes expected from ground truth."
    )
    error_category: str | None = Field(
        default=None,
        description="Error category if failed (from error_patterns)."
    )
    error_detail: str = Field(
        default="",
        description="Short excerpt of the error."
    )
    # Tool routing metrics
    included_steps: list[str] = Field(
        default_factory=list,
        description="Steps included via `include` statements in LLM code."
    )
    called_steps: list[str] = Field(
        default_factory=list,
        description="Steps called in the workflow block."
    )
    ground_truth_steps: list[str] = Field(
        default_factory=list,
        description="Steps in the ground truth code."
    )
    extra_steps: list[str] = Field(
        default_factory=list,
        description="Steps in LLM output not in ground truth."
    )
    missing_steps: list[str] = Field(
        default_factory=list,
        description="Steps in ground truth not in LLM output."
    )
    hallucinated_steps: list[str] = Field(
        default_factory=list,
        description="Steps that don't exist in the framework."
    )
    tool_routing_precision_pct: float = Field(
        default=0.0,
        description="Precision of step selection vs ground truth."
    )
    tool_routing_recall_pct: float = Field(
        default=0.0,
        description="Recall of step selection vs ground truth."
    )
    tool_routing_f1_pct: float = Field(
        default=0.0,
        description="F1 of step selection vs ground truth."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Full Benchmark Result
# ══════════════════════════════════════════════════════════════════════════════

class BenchmarkResult(BaseModel):
    """Full result for one benchmark example, combining all evaluation layers."""
    example_id: str = Field(
        description="Unique identifier from the benchmark dataset."
    )
    test_type: str = Field(
        description="Test type (code_generation, consultant, rejection, etc.)."
    )
    ground_truth_verdict: GroundTruthVerdict | None = Field(
        default=None,
        description="Three-tier ground truth comparison, if applicable."
    )
    pairwise_results: list[PairwiseResult] = Field(
        default_factory=list,
        description="Pairwise comparison results across applicable dimensions."
    )
    deterministic_checks: DeterministicChecks = Field(
        description="Results of deterministic validation checks."
    )
    llm_output: dict = Field(
        default_factory=dict,
        description="Raw LLM response (reply, code, status, etc.)."
    )
    elapsed_s: float = Field(
        default=0.0,
        description="Total wall-clock time for this example."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Multi-Turn Result
# ══════════════════════════════════════════════════════════════════════════════

class MultiTurnResult(BaseModel):
    """Result for one multi-turn conversation (multiple sequential turns)."""
    conversation_id: str = Field(
        description="Unique conversation identifier."
    )
    category: str = Field(
        default="",
        description="Modification category (add, replace, drop, switch_species)."
    )
    modification_kind: str = Field(
        default="",
        description="Kind of modification applied."
    )
    turn_results: list[BenchmarkResult] = Field(
        default_factory=list,
        description="Per-turn evaluation results."
    )
    all_turns_passed: bool = Field(
        default=False,
        description="Whether every turn produced valid code."
    )
