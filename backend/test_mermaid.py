#!/usr/bin/env python3
"""
Tests for the deterministic Mermaid renderer.
Verifies that render_mermaid_from_ast() produces correct, valid Mermaid
from various AST structures.

Usage:
    python test_mermaid.py
"""

from app.services.renderer import render_mermaid_from_ast


def check(name, mermaid, expected_nodes, expected_edges):
    """Verify a mermaid output contains expected nodes and edges."""
    errors = []
    for node in expected_nodes:
        if node not in mermaid:
            errors.append(f"  MISSING NODE: {node}")
    for edge in expected_edges:
        if edge not in mermaid:
            errors.append(f"  MISSING EDGE: {edge}")

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
    """L1: Single step, no sub-workflows."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": "trimmed = step_1PP_trimming__fastp(getSingleInput()).trimmed"
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Simple single step (fastp)", m,
        expected_nodes=["getSingleInput()", "step_1PP_trimming__fastp"],
        expected_edges=["getSingleInput --> entrypoint_step_1PP_trimming__fastp"]
    )


def test_simple_void_step():
    """L1: Void step (QC), no assignment."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": "module_qc_fastqc(getSingleInput())"
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Void step (fastqc)", m,
        expected_nodes=["getSingleInput()", "module_qc_fastqc"],
        expected_edges=["getSingleInput --> entrypoint_module_qc_fastqc"]
    )


def test_chain_two_steps():
    """L2: Two steps chained."""
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
            "getSingleInput --> entrypoint_step_1PP_trimming__fastp",
            "entrypoint_step_1PP_trimming__fastp --> entrypoint_step_2AS_denovo__spades",
        ]
    )


def test_fan_out():
    """L3: One step feeds multiple downstream steps."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": (
                "kmerfinder_out = step_3TX_species__kmerfinder(getSingleInput())\n"
                "step_4TY_MLST__mlst(kmerfinder_out.assigned_species)\n"
                "step_4AN_AMR__abricate(kmerfinder_out.assigned_species)\n"
                "step_4AN_AMR__staramr(kmerfinder_out.assigned_species, kmerfinder_out.assigned_species)"
            )
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Fan-out: kmerfinder -> mlst, abricate, staramr", m,
        expected_nodes=[
            "step_3TX_species__kmerfinder",
            "step_4TY_MLST__mlst",
            "step_4AN_AMR__abricate",
            "step_4AN_AMR__staramr",
        ],
        expected_edges=[
            "getSingleInput --> entrypoint_step_3TX_species__kmerfinder",
            "entrypoint_step_3TX_species__kmerfinder -->",  # at least connects to something
        ]
    )


def test_sub_workflow_with_take():
    """L2: Sub-workflow with take channels connected from entrypoint."""
    ast = {
        "globals": [
            {"type": "def", "name": "referenceCode", "value": "'NC_045512.2'"},
        ],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "module_covid_emergency",
                "take_channels": ["trimmed"],
                "emit_channels": [],
                "body_code": (
                    "consensus = step_2AS_mapping__ivar(trimmed, reference).consensus\n"
                    "step_4TY_lineage__pangolin(consensus)"
                )
            }
        ],
        "entrypoint": {
            "body_code": "module_covid_emergency(getSingleInput())"
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Sub-workflow: COVID emergency", m,
        expected_nodes=[
            "getSingleInput()",
            "trimmed",
            "step_2AS_mapping__ivar",
            "step_4TY_lineage__pangolin",
            "referenceCode",
        ],
        expected_edges=[
            "entrypoint_getSingleInput -->",  # entrypoint -> take (with label)
            "module_covid_emergency_in_trimmed --> module_covid_emergency_step_2AS_mapping__ivar",  # take -> first step
            "module_covid_emergency_step_2AS_mapping__ivar --> module_covid_emergency_step_4TY_lineage__pangolin",  # ivar -> pangolin
        ]
    )


def test_sub_workflow_with_emit():
    """Sub-workflow that emits a channel."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [
            {
                "name": "wf_filter_assemble",
                "take_channels": ["reads", "reference"],
                "emit_channels": ["assembled"],
                "body_code": (
                    "filtered = step_1PP_filtering__bowtie(reads, reference)\n"
                    "assembled = step_2AS_denovo__spades(filtered)"
                )
            }
        ],
        "entrypoint": {
            "body_code": "wf_filter_assemble(getSingleInput(), getReference('fa'))"
        }
    }
    m = render_mermaid_from_ast(ast)
    return check("Sub-workflow with emit", m,
        expected_nodes=[
            "getSingleInput()",
            "getReference('fa')",
            "reads",
            "reference",
            "step_1PP_filtering__bowtie",
            "step_2AS_denovo__spades",
            "assembled",
        ],
        expected_edges=[
            "entrypoint_getSingleInput -->",  # connects to sub-workflow
            "wf_filter_assemble_in_reads --> wf_filter_assemble_step_1PP_filtering__bowtie",
            "wf_filter_assemble_in_reference --> wf_filter_assemble_step_1PP_filtering__bowtie",
            "wf_filter_assemble_step_1PP_filtering__bowtie --> wf_filter_assemble_step_2AS_denovo__spades",
        ]
    )


def test_determinism():
    """Same AST must produce identical Mermaid every time."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {
            "body_code": (
                "kmerfinder_out = step_3TX_species__kmerfinder(getSingleInput())\n"
                "step_4TY_MLST__mlst(kmerfinder_out.assigned_species)"
            )
        }
    }
    results = [render_mermaid_from_ast(ast) for _ in range(10)]
    all_same = all(r == results[0] for r in results)
    if all_same:
        print("[PASS] Determinism: 10 runs identical")
        return True
    else:
        print("[FAIL] Determinism: outputs differ!")
        return False


def test_empty_pipeline():
    """Edge case: empty entrypoint."""
    ast = {
        "globals": [],
        "inline_processes": [],
        "sub_workflows": [],
        "entrypoint": {"body_code": ""}
    }
    m = render_mermaid_from_ast(ast)
    if m.startswith("flowchart TD"):
        print("[PASS] Empty pipeline: valid mermaid header")
        return True
    else:
        print(f"[FAIL] Empty pipeline: {m}")
        return False


if __name__ == "__main__":
    tests = [
        test_simple_single_step,
        test_simple_void_step,
        test_chain_two_steps,
        test_fan_out,
        test_sub_workflow_with_take,
        test_sub_workflow_with_emit,
        test_determinism,
        test_empty_pipeline,
    ]

    print(f"\nRunning {len(tests)} Mermaid tests...\n")
    results = [t() for t in tests]
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*40}")
    print(f"  {passed}/{total} passed")
    print(f"{'='*40}")
    exit(0 if passed == total else 1)
