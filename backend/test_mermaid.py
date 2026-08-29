#!/usr/bin/env python3
"""
Comprehensive tests for the deterministic Nextflow AST Mermaid renderer.
Verifies that render_mermaid_from_ast() and render_mermaid_from_graph() produce
correct, clean, valid Mermaid flowcharts, prevent duplicate/phantom subworkflow nodes,
correctly resolve complex topologies (nested subworkflows, diamonds, tuple destructuring),
and integrate with the Knowledge Graph.

Usage:
    /Users/grady/.pyenv/versions/3.11.9/bin/python3 backend/test_mermaid.py
"""

import sys
import os
import re

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from core.services.renderer import render_mermaid_from_ast, render_mermaid_from_graph


def check(name: str, mermaid: str, expected_nodes: list[str] | None = None, forbidden_nodes: list[str] | None = None, expected_edges: list[str] | None = None) -> bool:
    """Verify mermaid output contains expected nodes/edges and excludes forbidden nodes."""
    errors = []
    if expected_nodes:
        for node in expected_nodes:
            if node not in mermaid:
                errors.append(f"  MISSING EXPECTED NODE/TEXT: {node}")
    if forbidden_nodes:
        for node in forbidden_nodes:
            if node in mermaid:
                errors.append(f"  FORBIDDEN NODE/TEXT FOUND: {node}")
    if expected_edges:
        for edge in expected_edges:
            if edge not in mermaid:
                errors.append(f"  MISSING EXPECTED EDGE: {edge}")

    if errors:
        print(f"[FAIL] {name}")
        for e in errors:
            print(e)
        print(f"  GOT:\n{mermaid}\n")
        return False
    else:
        print(f"[PASS] {name}")
        return True


def test_simple_single_step():
    """Single process step in entrypoint."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": "trimmed = step_1PP_trimming__fastp(getSingleInput()).trimmed"
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Simple single step", m,
        expected_nodes=["getSingleInput()", "step_1PP_trimming__fastp"],
        expected_edges=["n_entrypoint_getSingleInput_0 --> n_entrypoint_step_1PP_trimming__fastp_0"]
    )


def test_chain_two_steps():
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
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Chain: fastp -> spades", m,
        expected_nodes=["step_1PP_trimming__fastp", "step_2AS_denovo__spades"],
        expected_edges=[
            "n_entrypoint_getSingleInput_0 --> n_entrypoint_step_1PP_trimming__fastp_0",
            "n_entrypoint_step_1PP_trimming__fastp_0 -->",
        ]
    )


def test_subworkflow_duplicate_box_prevention_unassigned():
    """
    CRITICAL INVARIANT:
    When a subworkflow WF_ANALYSIS is called without variable assignment (WF_ANALYSIS(getSingleInput())),
    and its output is referenced as WF_ANALYSIS.out.ch_out, NO phantom box 'var_entrypoint_WF_ANALYSIS'
    or 'WF_ANALYSIS' should appear inside the entrypoint subgraph.
    """
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_ANALYSIS",
                "take_channels": ["reads_in"],
                "emit_channels": ["ch_out = step_a.out"],
                "body_code": "step_a(reads_in)"
            }
        ],
        "entrypoint": {
            "body_code": (
                "WF_ANALYSIS(getSingleInput())\n"
                "step_b(WF_ANALYSIS.out.ch_out)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Subworkflow duplicate box prevention (unassigned invocation)", m,
        expected_nodes=[
            "subgraph sg_entrypoint",
            "subgraph sg_WF_ANALYSIS",
            "in_WF_ANALYSIS_reads_in",
            "out_WF_ANALYSIS_ch_out",
            "step_a",
            "step_b"
        ],
        forbidden_nodes=[
            "var_entrypoint_WF_ANALYSIS",
            'n_entrypoint_WF_ANALYSIS_0["WF_ANALYSIS"]',
            'n_entrypoint_WF_ANALYSIS(["WF_ANALYSIS"])'
        ],
        expected_edges=[
            "n_entrypoint_getSingleInput_0 -->|\"reads_in\"| in_WF_ANALYSIS_reads_in",
            "out_WF_ANALYSIS_ch_out -->|\"ch_out\"| n_entrypoint_step_b_0"
        ]
    )


def test_subworkflow_with_assignment():
    """Subworkflow called with assignment `res = WF(...)` and downstream `step_b(res.out.clean_data)`."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_QC_TRIM",
                "take_channels": ["raw_data"],
                "emit_channels": ["clean_data = step_trim.out"],
                "body_code": "step_trim(raw_data)"
            }
        ],
        "entrypoint": {
            "body_code": (
                "res = WF_QC_TRIM(getSingleInput())\n"
                "step_assembly(res.clean_data)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Subworkflow with variable assignment", m,
        expected_nodes=[
            "subgraph sg_entrypoint",
            "subgraph sg_WF_QC_TRIM",
            "in_WF_QC_TRIM_raw_data",
            "out_WF_QC_TRIM_clean_data",
            "step_trim",
            "step_assembly"
        ],
        forbidden_nodes=[
            "var_entrypoint_WF_QC_TRIM",
            "var_entrypoint_res"
        ],
        expected_edges=[
            "n_entrypoint_getSingleInput_0 -->|\"raw_data\"| in_WF_QC_TRIM_raw_data",
            "out_WF_QC_TRIM_clean_data -->|\"clean_data\"| n_entrypoint_step_assembly_0"
        ]
    )


def test_subworkflow_multi_emit():
    """Subworkflow emitting multiple channels connected to different downstream processes."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_MULTI_EMIT",
                "take_channels": ["ch_in"],
                "emit_channels": [
                    "primary = step_p.out",
                    "secondary = step_s.out"
                ],
                "body_code": (
                    "step_p(ch_in)\n"
                    "step_s(ch_in)"
                )
            }
        ],
        "entrypoint": {
            "body_code": (
                "wf_res = WF_MULTI_EMIT(getSingleInput())\n"
                "downstream_1(wf_res.out.primary)\n"
                "downstream_2(wf_res.out.secondary)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Subworkflow with multiple emit channels", m,
        expected_nodes=[
            "out_WF_MULTI_EMIT_primary",
            "out_WF_MULTI_EMIT_secondary",
            "downstream_1",
            "downstream_2"
        ],
        forbidden_nodes=[
            "var_entrypoint_wf_res",
            "var_entrypoint_WF_MULTI_EMIT"
        ],
        expected_edges=[
            "out_WF_MULTI_EMIT_primary -->|\"primary\"| n_entrypoint_downstream_1_0",
            "out_WF_MULTI_EMIT_secondary -->|\"secondary\"| n_entrypoint_downstream_2_0"
        ]
    )


def test_nested_subworkflows_multi_level():
    """
    Multi-level nested subworkflows:
    ENTRYPOINT -> calls WF_OUTER -> calls WF_INNER -> calls step_core.
    Tests that intermediate takes/emits wire through seamlessly with zero phantom boxes.
    """
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_INNER",
                "take_channels": ["inner_in"],
                "emit_channels": ["inner_out = step_core.out"],
                "body_code": "step_core(inner_in)"
            },
            {
                "name": "WF_OUTER",
                "take_channels": ["outer_in"],
                "emit_channels": ["outer_out = WF_INNER.out.inner_out"],
                "body_code": "WF_INNER(outer_in)"
            }
        ],
        "entrypoint": {
            "body_code": (
                "out = WF_OUTER(getSingleInput())\n"
                "step_final(out.outer_out)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Multi-level nested subworkflows (ENTRY -> OUTER -> INNER)", m,
        expected_nodes=[
            "subgraph sg_entrypoint",
            "subgraph sg_WF_OUTER",
            "subgraph sg_WF_INNER",
            "in_WF_OUTER_outer_in",
            "in_WF_INNER_inner_in",
            "step_core",
            "out_WF_INNER_inner_out",
            "out_WF_OUTER_outer_out",
            "step_final"
        ],
        forbidden_nodes=[
            "var_entrypoint_WF_OUTER",
            "var_WF_OUTER_WF_INNER"
        ],
        expected_edges=[
            "n_entrypoint_getSingleInput_0 -->|\"outer_in\"| in_WF_OUTER_outer_in",
            "in_WF_OUTER_outer_in -->|\"inner_in\"| in_WF_INNER_inner_in",
            "in_WF_INNER_inner_in --> n_WF_INNER_step_core_0",
            "out_WF_OUTER_outer_out -->|\"outer_out\"| n_entrypoint_step_final_0"
        ]
    )


def test_diamond_fan_out_fan_in():
    """
    Diamond dependency pattern:
    step_source -> fans out to WF_PATH_A and WF_PATH_B -> fans in via .mix() to step_sink.
    """
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_PATH_A",
                "take_channels": ["in_a"],
                "emit_channels": ["out_a = step_a.out"],
                "body_code": "step_a(in_a)"
            },
            {
                "name": "WF_PATH_B",
                "take_channels": ["in_b"],
                "emit_channels": ["out_b = step_b.out"],
                "body_code": "step_b(in_b)"
            }
        ],
        "entrypoint": {
            "body_code": (
                "source = step_source(getSingleInput())\n"
                "res_a = WF_PATH_A(source)\n"
                "res_b = WF_PATH_B(source)\n"
                "combined = res_a.out_a.mix(res_b.out_b)\n"
                "step_sink(combined)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Diamond Fan-Out -> Fan-In with .mix()", m,
        expected_nodes=[
            "subgraph sg_WF_PATH_A",
            "subgraph sg_WF_PATH_B",
            "step_source",
            "out_WF_PATH_A_out_a",
            "out_WF_PATH_B_out_b",
            ".mix",
            "step_sink"
        ],
        expected_edges=[
            "n_entrypoint_step_source_0 -->|\"source\"| in_WF_PATH_A_in_a",
            "n_entrypoint_step_source_0 -->|\"source\"| in_WF_PATH_B_in_b",
            "out_WF_PATH_A_out_a -->|\"out_a\"| n_entrypoint_mix_0",
            "out_WF_PATH_B_out_b -->|\"out_b\"| n_entrypoint_mix_0",
            "n_entrypoint_mix_0 -->|\"combined\"| n_entrypoint_step_sink_0"
        ]
    )


def test_tuple_destructuring_assignment():
    """Destructuring tuple process assignment: `(reads, contigs) = WF_DUAL(input)`."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_DUAL",
                "take_channels": ["input_data"],
                "emit_channels": [
                    "reads = step_1.out",
                    "contigs = step_2.out"
                ],
                "body_code": (
                    "step_1(input_data)\n"
                    "step_2(input_data)"
                )
            }
        ],
        "entrypoint": {
            "body_code": (
                "(reads_ch, contigs_ch) = WF_DUAL(getSingleInput())\n"
                "step_eval_reads(reads_ch)\n"
                "step_eval_contigs(contigs_ch)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Tuple destructuring assignment: (r, c) = WF(...)", m,
        expected_nodes=[
            "out_WF_DUAL_reads",
            "out_WF_DUAL_contigs",
            "step_eval_reads",
            "step_eval_contigs"
        ],
        forbidden_nodes=[
            "var_entrypoint_reads_ch",
            "var_entrypoint_contigs_ch"
        ],
        expected_edges=[
            "out_WF_DUAL_reads -->|\"reads\"| n_entrypoint_step_eval_reads_0",
            "out_WF_DUAL_contigs -->|\"contigs\"| n_entrypoint_step_eval_contigs_0"
        ]
    )


def test_operator_chains():
    """Testing operator chains (.cross, .multiMap, .mix, .join, .map, .filter)."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": (
                "reads = getSingleInput()\n"
                "ref = param('ref')\n"
                "paired = reads.cross(ref)\n"
                "filtered = paired.filter { it.size() > 0 }\n"
                "step_align(filtered)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Operator chains (.cross, .filter)", m,
        expected_nodes=[
            "getSingleInput()",
            "param('ref')",
            ".cross()",
            ".filter()",
            "step_align"
        ],
        expected_edges=[
            "n_entrypoint_cross_0 -->|\"paired\"| n_entrypoint_filter_0",
            "n_entrypoint_filter_0 -->|\"filtered\"| n_entrypoint_step_align_0"
        ]
    )


def test_kg_domain_classes_and_styling():
    """Knowledge Graph domain classification assigns distinct visual classDefs (e.g. :::trimming, :::assembly, :::qc)."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": (
                "trimmed = step_1PP_trimming__fastp(getSingleInput()).trimmed\n"
                "assembled = step_2AS_denovo__spades(trimmed)\n"
                "step_1QC_quality__fastqc(trimmed)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Knowledge Graph domain classification styling", m,
        expected_nodes=[
            ":::trimming",
            ":::assembly",
            ":::qc",
            "classDef trimming fill:#3B82F6",
            "classDef assembly fill:#10B981",
            "classDef qc fill:#06B6D4"
        ]
    )


def test_kg_graph_community_rendering():
    """render_mermaid_from_graph groups component nodes into domain subgraphs."""
    class MockKG:
        is_built = True
        def __init__(self):
            import networkx as nx
            self.G = nx.DiGraph()
            self.G.add_node("step_1PP_trimming__fastp", domain="Preprocessing")
            self.G.add_node("step_2AS_denovo__spades", domain="Assembly")
            self.G.add_edge("step_1PP_trimming__fastp", "step_2AS_denovo__spades", channel="trimmed", confidence="0.95")

    kg = MockKG()
    m = render_mermaid_from_graph(["step_1PP_trimming__fastp", "step_2AS_denovo__spades"], kg)
    return check("Knowledge Graph community & domain subgraph clustering", m,
        expected_nodes=[
            "subgraph sg_Preprocessing",
            "subgraph sg_Assembly",
            "step_1PP_trimming__fastp",
            "step_2AS_denovo__spades"
        ],
        expected_edges=[
            "step_1PP_trimming__fastp -->|\"trimmed\"| step_2AS_denovo__spades"
        ]
    )


def test_syntax_safety_and_no_unescaped_quotes():
    """Verifies that mermaid syntax avoids double quotes inside labels and has balanced delimiters."""
    ast = {
        "globals": [{"name": "MY_GLOBAL", "type": "def", "value": "'test'"}],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_TEST",
                "take_channels": ["in_reads"],
                "emit_channels": ["out_ch = step_a.out"],
                "body_code": "step_a(in_reads)"
            }
        ],
        "entrypoint": {
            "body_code": (
                "WF_TEST(getSingleInput())\n"
                "step_b(WF_TEST.out.out_ch)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    for line in m.splitlines():
        if '["' in line:
            assert line.count('"') >= 2, f"Malformed quotes in line: {line}"
    return check("Syntax safety & quote integrity", m, expected_nodes=["flowchart TD", "sg_WF_TEST"])


def test_determinism():
    """Same AST must produce identical output across 10 consecutive renders."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "WF_SAMPLE",
                "take_channels": ["ch1"],
                "emit_channels": ["res = step_x.out"],
                "body_code": "step_x(ch1)"
            }
        ],
        "entrypoint": {
            "body_code": "res = WF_SAMPLE(getSingleInput())\nstep_y(res.res)"
        }
    }
    renders = [render_mermaid_from_ast(ast) for _ in range(10)]
    all_identical = all(r == renders[0] for r in renders)
    if all_identical:
        print("[PASS] Determinism test (10 renders identical)")
        return True
    else:
        print("[FAIL] Determinism test: variations detected across renders")
        return False


def test_resilient_fallback():
    """Malformed or partial AST triggers resilient fallback without throwing errors."""
    ast_malformed = {
        "imports": [{"module_path": "modules/qc", "functions": ["fastqc"]}],
        "sub_workflows": [{"name": "BROKEN", "body_code": None, "take_channels": None}],
        "entrypoint": None
    }
    m = render_mermaid_from_ast(ast_malformed)
    return check("Resilient fallback on malformed AST", m,
        expected_nodes=["flowchart TD", "fastqc"]
    )


def test_entrypoint_cross_multimap_pipeline():
    """Entrypoint cross + multiMap chaining to subworkflow."""
    ast = {
        "sub_workflows": [
            {
                "name": "SEGMENTED",
                "take_channels": ["reads", "reference"],
                "body_code": "step_2AS_mapping__ivar(reads, reference)",
                "emit_channels": ["consensus = step_2AS_mapping__ivar.out.consensus"]
            }
        ],
        "entrypoint": {
            "body_code": (
                "getSingleInput().cross(getReferences('any')) { extractKey(it) }\n"
                "  .multiMap { \n"
                "      reads: it[0]\n"
                "      refs:  it[1][1..3]\n"
                "  }.set { input }\n\n"
                "SEGMENTED(input.reads, input.refs)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Entrypoint cross + multiMap pipeline", m,
        expected_nodes=[
            "subgraph sg_entrypoint",
            "subgraph sg_SEGMENTED",
            "getSingleInput()",
            "getReferences('any')",
            ".cross(extractKey(it))",
            ".multiMap{reads, refs}",
            "step_2AS_mapping__ivar"
        ],
        expected_edges=[
            "n_entrypoint_getSingleInput_0 --> n_entrypoint_cross_0",
            "n_entrypoint_getReferences_0 --> n_entrypoint_cross_0",
            "n_entrypoint_cross_0 --> n_entrypoint_multiMap_0",
            "n_entrypoint_multiMap_0 -->|\"reads\"| in_SEGMENTED_reads",
            "n_entrypoint_multiMap_0 -->|\"refs\"| in_SEGMENTED_reference"
        ]
    )


if __name__ == "__main__":
    tests = [
        test_simple_single_step,
        test_chain_two_steps,
        test_subworkflow_duplicate_box_prevention_unassigned,
        test_subworkflow_with_assignment,
        test_subworkflow_multi_emit,
        test_nested_subworkflows_multi_level,
        test_diamond_fan_out_fan_in,
        test_tuple_destructuring_assignment,
        test_operator_chains,
        test_entrypoint_cross_multimap_pipeline,
        test_kg_domain_classes_and_styling,
        test_kg_graph_community_rendering,
        test_syntax_safety_and_no_unescaped_quotes,
        test_determinism,
        test_resilient_fallback
    ]

    print(f"\nRunning {len(tests)} Comprehensive Mermaid Tests...\n")
    results = [t() for t in tests]
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*50}")
    print(f"  {passed}/{total} Mermaid Tests Passed")
    print(f"{'='*50}\n")
    sys.exit(0 if passed == total else 1)
