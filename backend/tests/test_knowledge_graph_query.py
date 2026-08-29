"""
Tests for Graphify-powered KnowledgeGraph query and traversal engine.
"""
import json
import os
import tempfile
import pytest
from langgraph.store.memory import InMemoryStore

from core.services.knowledge_graph import KnowledgeGraph, _query_terms, _score_query, _pick_seeds
from core.services.consultant_tools import (
    query_knowledge_graph,
    explain_component,
    get_component_neighbors,
    get_community_components,
    get_catalog_god_nodes,
    find_dataflow_path,
)


@pytest.fixture
def populated_kg():
    kg = KnowledgeGraph()
    store = InMemoryStore()

    # 1. Setup mock components
    components = {
        "step_1PP_trimming__fastp": {
            "tool": "fastp",
            "domain": "preprocessing",
            "description": "Fast QC and trimming of fastq sequencing reads.",
            "input_channels": ["reads"],
            "output_channels": ["trimmed_reads", "report"],
        },
        "step_1PP_filtering__krakentools": {
            "tool": "krakentools",
            "domain": "preprocessing",
            "description": "Taxonomic filtering of host reads.",
            "input_channels": ["trimmed_reads"],
            "output_channels": ["depleted_reads"],
        },
        "step_2AS_mapping__bwa": {
            "tool": "bwa",
            "domain": "alignment",
            "description": "Map short reads against reference genome.",
            "input_channels": ["depleted_reads", "reference"],
            "output_channels": ["bam"],
        },
        "step_3VC_calling__freebayes": {
            "tool": "freebayes",
            "domain": "variant_calling",
            "description": "Haplotype-based variant detection across bam files.",
            "input_channels": ["bam", "reference"],
            "output_channels": ["vcf"],
        },
        "step_4QC_multiqc": {
            "tool": "multiqc",
            "domain": "qc",
            "description": "Aggregate QC reports across all pipeline steps.",
            "input_channels": ["report", "vcf"],
            "output_channels": [],
        },
    }

    for comp_id, data in components.items():
        store.put(("components",), comp_id, data)

    # 2. Setup mock tool_graph.json AST edges
    tool_graph_data = {
        "nodes": list(components.keys()),
        "edges": [
            {
                "source": "step_1PP_trimming__fastp",
                "target": "step_1PP_filtering__krakentools",
                "via": ["trimmed_reads"],
                "edge_type": "dataflow",
            },
            {
                "source": "step_1PP_filtering__krakentools",
                "target": "step_2AS_mapping__bwa",
                "via": ["depleted_reads"],
                "edge_type": "dataflow",
            },
            {
                "source": "step_2AS_mapping__bwa",
                "target": "step_3VC_calling__freebayes",
                "via": ["bam"],
                "edge_type": "dataflow",
            },
            {
                "source": "step_1PP_trimming__fastp",
                "target": "step_4QC_multiqc",
                "via": ["report"],
                "edge_type": "dataflow",
            },
        ]
    }
    store.put(("graph",), "adjacency", tool_graph_data)

    # 3. Setup mock usage index
    usage_data = {
        "usages": [
            {"template_id": "test_pipeline", "snippet": "step_1PP_trimming__fastp(reads)\nstep_2AS_mapping__bwa(fastp.out.trimmed_reads)"}
        ]
    }
    store.put(("usage",), "step_1PP_trimming__fastp", usage_data)


    kg.build_nx_graph(store)
    return kg


def test_query_terms_extraction():
    q = "how to trim fastq reads using fastp and calls bwa to map"
    terms = _query_terms(q)
    assert "trim" in terms
    assert "fastq" in terms
    assert "reads" in terms
    assert "fastp" in terms
    assert "bwa" in terms
    assert "how" not in terms
    assert "to" not in terms
    assert "using" not in terms


def test_knowledge_graph_stats(populated_kg):
    stats = populated_kg.graph_stats()
    assert "Nodes:" in stats
    assert "Edges:" in stats
    assert "EXTRACTED:" in stats


def test_knowledge_graph_bfs_query(populated_kg):
    res = populated_kg.query_graph("trim fastq with fastp", mode="bfs", depth=2)
    assert "GRAPH QUERY:" in res
    assert "step_1PP_trimming__fastp" in res


def test_knowledge_graph_dfs_query(populated_kg):
    res = populated_kg.query_graph("pipeline from fastp to freebayes", mode="dfs", depth=3)
    assert "GRAPH QUERY:" in res
    assert "step_1PP_trimming__fastp" in res
    assert "step_3VC_calling__freebayes" in res


def test_explain_node(populated_kg):
    res = populated_kg.explain_node("fastp")
    assert "COMPONENT: step_1PP_trimming__fastp" in res
    assert "Tool: fastp" in res
    assert "UPSTREAM" in res
    assert "DOWNSTREAM" in res
    assert "step_1PP_filtering__krakentools" in res


def test_get_neighbors(populated_kg):
    res = populated_kg.get_neighbors("fastp", direction="out")
    assert "step_1PP_filtering__krakentools" in res
    assert "step_4QC_multiqc" in res


def test_get_god_nodes(populated_kg):
    res = populated_kg.get_god_nodes(top_n=3)
    assert "Catalog Architectural Hubs" in res
    assert "connections" in res


def test_find_path_detailed_forward(populated_kg):
    res_str = populated_kg.find_path_detailed("fastp", "freebayes")
    res = json.loads(res_str)
    assert res["hops"] == 3
    assert res["path"] == [
        "step_1PP_trimming__fastp",
        "step_1PP_filtering__krakentools",
        "step_2AS_mapping__bwa",
        "step_3VC_calling__freebayes",
    ]


def test_find_path_detailed_reversed(populated_kg):
    res_str = populated_kg.find_path_detailed("freebayes", "fastp")
    res = json.loads(res_str)
    assert "reversed_path" in res
    assert "Data flows in reverse" in res["hint"]


def test_export_graph_json(populated_kg):
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = os.path.join(tmpdir, "graph.json")
        saved_path = populated_kg.export_graph_json(out_file)
        assert os.path.exists(saved_path)

        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "nodes" in data
        assert "links" in data
        assert len(data["nodes"]) >= 5