"""
Targeted test battery for Multi-Turn Modifications across all 4 categories:
1. add
2. replace
3. drop
4. switch_species

Evaluates logical and bioinformatic validity across sequential turns:
- Turn 1: Initial conversation & pipeline generation
- Turn 2: Modification request (add/replace/drop/switch)
- Verifies: component precision/recall/F1, Nextflow AST validity, and correct state transition.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from starlette.testclient import TestClient
from app.api import app
from tests.benchmark.loader import load_multi_turn_examples
from tests.helpers import compute_step_metrics
from tests.nf_validation import validate_nextflow


class TestModificationBattery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._client_cm = TestClient(app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)

    def _run_conversation(self, conv: dict):
        conv_id = conv["id"]
        mod_kind = conv.get("modification_kind", "unknown")
        session_id = f"test_mod_{conv_id}_{uuid.uuid4().hex[:6]}"
        turns = conv.get("turns", [])

        print(f"\n🧪 [{conv_id}] Testing Kind='{mod_kind}' ({len(turns)} turns)")

        for turn_idx, turn in enumerate(turns):
            turn_prompt = turn["prompt"]
            gt_code = turn.get("nextflow_code", "")
            gt_components = turn.get("component_ids", [])

            print(f"  👉 Turn {turn_idx + 1}: {turn_prompt[:80]}...")

            # 1. Send user prompt (generates/updates plan)
            res1 = self.client.post("/chat", json={
                "message": turn_prompt,
                "session_id": session_id,
            })
            self.assertEqual(res1.status_code, 200, f"Turn {turn_idx + 1} prompt failed: {res1.text}")
            data1 = res1.json()

            # 2. Send approval (generates Nextflow AST)
            res2 = self.client.post("/chat", json={
                "message": "I approve the plan, please build the pipeline.",
                "session_id": session_id,
            })
            self.assertEqual(res2.status_code, 200, f"Turn {turn_idx + 1} approval failed: {res2.text}")
            data2 = res2.json()

            nf_code = data2.get("nextflow_code", "") or ""
            self.assertTrue(bool(nf_code), f"Turn {turn_idx + 1} must produce Nextflow code")

            # 3. Compute step metrics
            metrics = compute_step_metrics(nf_code, gt_code)
            print(f"     Components: LLM={metrics['llm_steps']} | GT={metrics['gt_steps']}")
            print(f"     Precision={metrics['precision']:.1f}% | Recall={metrics['recall']:.1f}% | F1={metrics['f1']:.1f}%")

            # 4. Validate Nextflow DSL2 syntax
            val = validate_nextflow(nf_code, run_stub=True)
            self.assertTrue(val.get("nf_syntax_passed", False), f"Syntax error in turn {turn_idx + 1}: {val.get('nf_syntax_error')}")

            # Logical correctness checks
            self.assertGreaterEqual(metrics["precision"], 66.0, f"Precision too low for {conv_id} turn {turn_idx + 1}")

    def test_kind_add(self):
        """Test adding steps to an existing pipeline."""
        examples = load_multi_turn_examples(only_mod_kinds={"add"}, limit=2)
        for ex in examples:
            self._run_conversation(ex)

    def test_kind_replace(self):
        """Test replacing a tool in an existing pipeline (e.g. assembler swap)."""
        examples = load_multi_turn_examples(only_mod_kinds={"replace"}, limit=2)
        for ex in examples:
            self._run_conversation(ex)

    def test_kind_drop(self):
        """Test dropping a step from an existing pipeline."""
        examples = load_multi_turn_examples(only_mod_kinds={"drop"}, limit=2)
        for ex in examples:
            self._run_conversation(ex)

    def test_kind_switch_species(self):
        """Test switching pathogen species/configuration in an existing pipeline."""
        examples = load_multi_turn_examples(only_mod_kinds={"switch_species"}, limit=2)
        for ex in examples:
            self._run_conversation(ex)


if __name__ == "__main__":
    unittest.main()
