import unittest
from unittest.mock import MagicMock

from tests.evaluation.pairwise import JudgeOutput, PairwiseEvaluator
from tests.evaluation.schemas import PairwiseResult


class RunnableMock:
    """A mock that implements __call__ so coerce_to_runnable treats it as a valid Runnable."""
    def __init__(self):
        self.mock_call = MagicMock()

    def with_structured_output(self, *_args, **_kwargs):
        return self

    def __call__(self, *args, **kwargs):
        return self.mock_call(*args, **kwargs)

class TestPairwiseEvaluatorUnit(unittest.TestCase):
    def setUp(self):
        self.mock_judge = RunnableMock()
        # Create evaluator with mock judge and no rate limit pause for fast tests
        self.evaluator = PairwiseEvaluator(judge_llm=self.mock_judge, rate_limit_pause_s=0.0)

    def test_compare_consistent_winner_a(self):
        # Original: A wins. Swapped raw: B wins (remaps to A)
        res_orig = JudgeOutput(winner="A", reasoning="A is better because...")
        res_swap = JudgeOutput(winner="B", reasoning="B (which is original A) is better...")
        self.mock_judge.mock_call.side_effect = [res_orig, res_swap]

        result = self.evaluator.compare(
            example_id="test_ex",
            option_a="output_a",
            option_b="output_b",
            dimension="syntax",
            context={"prompt": "test prompt", "context": "test context"}
        )

        self.assertEqual(result.verdict, "A")
        self.assertTrue(result.consistent)
        self.assertEqual(result.reasoning_original, "A is better because...")
        self.assertEqual(result.reasoning_swapped, "B (which is original A) is better...")

    def test_compare_consistent_winner_b(self):
        # Original: B wins. Swapped raw: A wins (remaps to B)
        res_orig = JudgeOutput(winner="B", reasoning="B is better...")
        res_swap = JudgeOutput(winner="A", reasoning="A (which is original B) is better...")
        self.mock_judge.mock_call.side_effect = [res_orig, res_swap]

        result = self.evaluator.compare(
            example_id="test_ex",
            option_a="output_a",
            option_b="output_b",
            dimension="syntax",
            context={"prompt": "test prompt", "context": ""}
        )

        self.assertEqual(result.verdict, "B")
        self.assertTrue(result.consistent)

    def test_compare_consistent_tie(self):
        # Original: tie. Swapped raw: tie (remaps to tie)
        res_orig = JudgeOutput(winner="tie", reasoning="Both are equal...")
        res_swap = JudgeOutput(winner="tie", reasoning="No difference...")
        self.mock_judge.mock_call.side_effect = [res_orig, res_swap]

        result = self.evaluator.compare(
            example_id="test_ex",
            option_a="output_a",
            option_b="output_b",
            dimension="syntax",
            context={"prompt": "test prompt", "context": ""}
        )

        self.assertEqual(result.verdict, "tie")
        self.assertTrue(result.consistent)

    def test_compare_inconsistent_disagreement(self):
        # Original: A wins. Swapped raw: A wins (remaps to B, hence disagreement)
        res_orig = JudgeOutput(winner="A", reasoning="A is better...")
        res_swap = JudgeOutput(winner="A", reasoning="A is better in swapped too (position bias!)")
        self.mock_judge.mock_call.side_effect = [res_orig, res_swap]

        result = self.evaluator.compare(
            example_id="test_ex",
            option_a="output_a",
            option_b="output_b",
            dimension="syntax",
            context={"prompt": "test prompt", "context": ""}
        )

        self.assertEqual(result.verdict, "tie")
        self.assertFalse(result.consistent)

    def test_compare_unexpected_winner_value(self):
        # Test behavior when the judge returns an invalid/unexpected string
        res_orig = JudgeOutput(winner="invalid_value", reasoning="Confused...")
        res_swap = JudgeOutput(winner="tie", reasoning="Equal...")
        self.mock_judge.mock_call.side_effect = [res_orig, res_swap]

        result = self.evaluator.compare(
            example_id="test_ex",
            option_a="output_a",
            option_b="output_b",
            dimension="syntax",
            context={"prompt": "test prompt", "context": ""}
        )

        # Invalid value normalized to tie, swapped is tie -> consistent tie
        self.assertEqual(result.verdict, "tie")
        self.assertTrue(result.consistent)

    def test_compare_judge_exception_handled_as_tie_and_warns(self):
        # If judge throws an exception, it should not crash, but log a warning and return a tie.
        # No timeout is introduced; we simply catch the exception.
        self.mock_judge.mock_call.side_effect = Exception("LLM connection failed")

        result = self.evaluator.compare(
            example_id="test_ex",
            option_a="output_a",
            option_b="output_b",
            dimension="syntax",
            context={"prompt": "test prompt", "context": ""}
        )

        self.assertEqual(result.verdict, "tie")
        self.assertTrue(result.consistent)
        self.assertIn("Judge call failed: LLM connection failed", result.reasoning_original)
        self.assertIn("Judge call failed: LLM connection failed", result.reasoning_swapped)
