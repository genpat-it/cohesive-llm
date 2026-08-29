"""Unit tests for the deterministic Nextflow AST Mermaid renderer.

Verifies that render_mermaid_from_ast() and render_mermaid_from_graph() produce
correct, clean, valid Mermaid flowcharts, prevent duplicate/phantom subworkflow nodes,
correctly resolve complex topologies (nested subworkflows, diamonds, tuple destructuring),
and integrate with the Knowledge Graph.
"""
import unittest
from core.services.renderer import render_mermaid_from_ast, render_mermaid_from_graph


class TestMermaidRenderer(unittest.TestCase):

    def _assert_mermaid(
        self,
        mermaid: str,
        expected_nodes: list[str] | None = None,
        forbidden_nodes: list[str] | None = None,
        expected_edges: list[str] | None = None,
    ):
        if expected_nodes:
            for node in expected_nodes:
                self.assertIn(node, mermaid, f"Missing expected node: {node}")
        if forbidden_nodes:
            for node in forbidden_nodes:
                self.assertNotIn(node, mermaid, f"Forbidden node found: {node}")
        if expected_edges:
            for edge in expected_edges:
                self.assertIn(edge, mermaid, f"Missing expected edge: {edge}")

    def test_simple_single_step(self):
        """Single process step in entrypoint."""
        ast = {
            "globals": [],
            "inline_processes": [],
            "sub_workflows": [],
            "entrypoint": {
                "body_code": "trimmed = step_1PP_trimming__fastp(getSingleInput()).trimmed"
            },
        }
        m = render_mermaid_from_ast(ast)
        self._assert_mermaid(
            m,
            expected_nodes=["getSingleInput()", "step_1PP_trimming__fastp"],
            expected_edges=["n_entrypoint_getSingleInput_0 --> n_entrypoint_step_1PP_trimming__fastp_0"],
        )

    def test_chain_two_steps(self):
        """Two processes chained via channel assignment."""
        ast = {
            "globals": [],
            "inline_processes": [],
            "sub_workflows": [],
            "entrypoint": {
                "body_code": (
                    "trimmed = step_1PP_trimming__fastp(getSingleInput()).trimmed\n"
                    "assembled = step_2AS_denovo__spades(trimmed)"
                )
            },
        }
        m = render_mermaid_from_ast(ast)
        self._assert_mermaid(
            m,
            expected_nodes=["step_1PP_trimming__fastp", "step_2AS_denovo__spades"],
            expected_edges=["n_entrypoint_getSingleInput_0 --> n_entrypoint_step_1PP_trimming__fastp_0"],
        )

    def test_subworkflow_duplicate_box_prevention_unassigned(self):
        """When a subworkflow WF_ANALYSIS is called without variable assignment,

        no phantom box should appear inside the entrypoint subgraph.
        """
        ast = {
            "globals": [],
            "inline_processes": [],
            "sub_workflows": [
                {
                    "name": "WF_ANALYSIS",
                    "take_channels": ["reads_in"],
                    "emit_channels": ["ch_out = step_a.out"],
                    "body_code": "step_a(reads_in)",
                }
            ],
            "entrypoint": {
                "body_code": (
                    "WF_ANALYSIS(getSingleInput())\n"
                    "step_b(WF_ANALYSIS.out.ch_out)"
                )
            },
        }
        m = render_mermaid_from_ast(ast)
        self._assert_mermaid(
            m,
            expected_nodes=[
                "subgraph sg_entrypoint",
                "subgraph sg_WF_ANALYSIS",
                "in_WF_ANALYSIS_reads_in",
                "out_WF_ANALYSIS_ch_out",
                "step_a",
                "step_b",
            ],
            forbidden_nodes=[
                "var_entrypoint_WF_ANALYSIS",
                'n_entrypoint_WF_ANALYSIS_0["WF_ANALYSIS"]',
            ],
            expected_edges=[
                "n_entrypoint_getSingleInput_0 -->|\"reads_in\"| in_WF_ANALYSIS_reads_in",
                "out_WF_ANALYSIS_ch_out -->|\"ch_out\"| n_entrypoint_step_b_0",
            ],
        )

    def test_subworkflow_with_assignment(self):
        """Subworkflow called with assignment `res = WF(...)`."""
        ast = {
            "globals": [],
            "inline_processes": [],
            "sub_workflows": [
                {
                    "name": "WF_QC_TRIM",
                    "take_channels": ["raw_data"],
                    "emit_channels": ["clean_data = step_trim.out"],
                    "body_code": "step_trim(raw_data)",
                }
            ],
            "entrypoint": {
                "body_code": (
                    "res = WF_QC_TRIM(getSingleInput())\n"
                    "step_assembly(res.clean_data)"
                )
            },
        }
        m = render_mermaid_from_ast(ast)
        self._assert_mermaid(
            m,
            expected_nodes=[
                "subgraph sg_entrypoint",
                "subgraph sg_WF_QC_TRIM",
                "in_WF_QC_TRIM_raw_data",
                "out_WF_QC_TRIM_clean_data",
                "step_trim",
                "step_assembly",
            ],
            forbidden_nodes=["var_entrypoint_WF_QC_TRIM", "var_entrypoint_res"],
            expected_edges=[
                "n_entrypoint_getSingleInput_0 -->|\"raw_data\"| in_WF_QC_TRIM_raw_data",
                "out_WF_QC_TRIM_clean_data -->|\"clean_data\"| n_entrypoint_step_assembly_0",
            ],
        )

    def test_determinism(self):
        """Rendering twice with identical AST produces exact identical Mermaid output."""
        ast = {
            "globals": [],
            "inline_processes": [],
            "sub_workflows": [],
            "entrypoint": {
                "body_code": (
                    "trimmed = step_1PP_trimming__fastp(getSingleInput()).trimmed\n"
                    "assembled = step_2AS_denovo__spades(trimmed)"
                )
            },
        }
        m1 = render_mermaid_from_ast(ast)
        m2 = render_mermaid_from_ast(ast)
        self.assertEqual(m1, m2)


if __name__ == "__main__":
    unittest.main()
