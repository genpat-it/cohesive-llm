import unittest

from tests.helpers import compute_step_metrics, extract_steps_from_code


class TestMetricsHelpersUnit(unittest.TestCase):
    def test_extract_steps_from_code_empty_and_invalid(self):
        self.assertEqual(extract_steps_from_code(""), [])
        self.assertEqual(extract_steps_from_code(None), [])
        self.assertEqual(extract_steps_from_code("workflow MY_WF {}"), [])

    def test_extract_steps_from_code_simple_include(self):
        code = "include { step_A } from '../steps/step_A'"
        self.assertEqual(extract_steps_from_code(code), ["step_A"])

    def test_extract_steps_from_code_multiple_in_one_statement(self):
        code = "include { step_A; step_B } from '../steps/step_A'"
        self.assertEqual(extract_steps_from_code(code), ["step_A", "step_B"])

    def test_extract_steps_from_code_with_aliases(self):
        code = "include { step_A as tool_a; step_B as tool_b } from '../steps/step_A'"
        self.assertEqual(extract_steps_from_code(code), ["step_A", "step_B"])

    def test_compute_step_metrics_exact_match(self):
        llm_code = "include { step_A } from 'a'; include { step_B } from 'b'"
        gt_code = "include { step_A } from 'a'; include { step_B } from 'b'"
        metrics = compute_step_metrics(llm_code, gt_code)

        self.assertEqual(metrics["precision"], 100.0)
        self.assertEqual(metrics["recall"], 100.0)
        self.assertEqual(metrics["f1"], 100.0)
        self.assertEqual(metrics["extra_steps"], [])
        self.assertEqual(metrics["missing_steps"], [])

    def test_compute_step_metrics_partial_match(self):
        # LLM has A, B, C. GT has B, C, D.
        # Common: B, C. Extra: A. Missing: D.
        llm_code = "include { step_A; step_B; step_C } from 'x'"
        gt_code = "include { step_B; step_C; step_D } from 'x'"
        metrics = compute_step_metrics(llm_code, gt_code)

        # Precision: 2/3 = 66.7%
        self.assertEqual(metrics["precision"], 66.7)
        # Recall: 2/3 = 66.7%
        self.assertEqual(metrics["recall"], 66.7)
        # F1: 66.7%
        self.assertEqual(metrics["f1"], 66.7)

        self.assertEqual(metrics["extra_steps"], ["step_A"])
        self.assertEqual(metrics["missing_steps"], ["step_D"])

    def test_compute_step_metrics_no_overlap(self):
        llm_code = "include { step_A } from 'x'"
        gt_code = "include { step_B } from 'y'"
        metrics = compute_step_metrics(llm_code, gt_code)

        self.assertEqual(metrics["precision"], 0.0)
        self.assertEqual(metrics["recall"], 0.0)
        self.assertEqual(metrics["f1"], 0.0)
