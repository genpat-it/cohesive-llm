import pytest
import os
import json
from langgraph.store.memory import InMemoryStore
from core.loader import data_loader
from core.services.knowledge_graph import kg, _pick_seeds, _query_terms, _score_query


@pytest.fixture(scope="module")
def initialized_graph():
    """Builds the full real IZS bioinformatics knowledge graph."""
    os.environ["NF_AGENT_PLUGIN"] = "izs"
    store = InMemoryStore()
    data_loader.load_all(store)
    if not kg.is_built:
        kg.build_nx_graph(store)
    return kg


DENSE_OUTBREAK_QUERY = (
    "We are investigating a foodborne Campylobacter outbreak from raw Illumina and IonTorrent reads "
    "with possible host contamination. Check raw FASTQ quality with fastq stats, perform conditional trimming "
    "with fastp and trimmomatic, run host depletion with bowtie hostdepl, assemble de novo with spades, "
    "identify bacterial species with kmerfinder, dynamically map reads to the identified reference with bowtie mapping, "
    "run MLST typing, cgMLST with chewbbaca, flagellar typing with flaA, AMR screening with abricate and staramr, "
    "plasmid reconstruction with mobsuite, annotate genes with prokka, cluster cgMLST allele profiles with "
    "grapetree and reportree using metadata and geodata, and construct core pan-genome with panaroo."
)


def test_dense_query_terms_extraction(initialized_graph):
    """Verifies that query tokenization captures domain keywords across all 5 sub-workflows."""
    terms = _query_terms(DENSE_OUTBREAK_QUERY)
    
    # Must capture terms from each phase:
    assert "fastp" in terms or "trimming" in terms
    assert "spades" in terms or "assemble" in terms
    assert "kmerfinder" in terms or "species" in terms
    assert "mlst" in terms
    assert "chewbbaca" in terms
    assert "abricate" in terms
    assert "staramr" in terms
    assert "mobsuite" in terms or "plasmid" in terms
    assert "prokka" in terms or "annotate" in terms
    assert "grapetree" in terms
    assert "reportree" in terms
    assert "panaroo" in terms


def test_dense_seed_selection_and_coverage(initialized_graph):
    """Verifies that Graphify picks seeds spanning across all stages without dropping any."""
    scores = _score_query(initialized_graph.G, _query_terms(DENSE_OUTBREAK_QUERY))
    seeds = _pick_seeds(scores.ranked, max_k=20, G=initialized_graph.G, best_seed_by_term=scores.best_seed_by_term)

    # Preprocessing
    assert any("fastp" in s or "trimmomatic" in s or "hostdepl" in s for s in seeds)
    # Assembly
    assert any("spades" in s for s in seeds)
    # Typing & AMR
    assert any("mlst" in s.lower() or "chewbbaca" in s.lower() or "srst2" in s.lower() for s in seeds)
    assert any("chewbbaca" in s or "grapetree" in s for s in seeds)
    assert any("abricate" in s or "staramr" in s for s in seeds)
    assert any("mobsuite" in s for s in seeds)
    assert any("prokka" in s for s in seeds)
    # Clustering & Pangenome
    assert any("grapetree" in s or "reportree" in s or "panaroo" in s for s in seeds)


def test_dense_bfs_parallel_branch_discovery(initialized_graph):
    """Verifies BFS expansion discovers parallel downstream typers from seeds."""
    result = kg.query_graph(DENSE_OUTBREAK_QUERY, mode="bfs", depth=3, token_budget=4000)
    
    assert "GRAPH QUERY" in result
    assert "step_2AS_denovo__spades" in result
    assert "step_4TY_MLST__mlst" in result
    assert "step_4TY_cgMLST__chewbbaca" in result
    assert "step_4AN_AMR__abricate" in result
    assert "step_4AN_genes__prokka" in result
    assert "step_4TY_plasmid__mobsuite" in result


def test_dense_dfs_chain_traversal(initialized_graph):
    """Verifies DFS explores linear dataflow paths without crashing."""
    result = kg.query_graph(DENSE_OUTBREAK_QUERY, mode="dfs", depth=3, token_budget=4000)
    
    assert "GRAPH QUERY" in result
    assert "Mode: DFS" in result
    assert len(result.split("\n")) > 10


def test_dense_all_inter_stage_dataflow_reachability(initialized_graph):
    """Tests critical dataflow connections across the entire outbreak workflow."""
    # 1. Host depletion to assembly
    p1 = json.loads(kg.find_path_detailed("step_1PP_hostdepl__bowtie", "step_2AS_denovo__spades"))
    assert p1 is not None and "hops" in p1

    # 2. Assembly to MLST
    p2 = json.loads(kg.find_path_detailed("step_2AS_denovo__spades", "step_4TY_MLST__mlst"))
    assert p2 is not None and "hops" in p2

    # 3. Assembly to chewBBACA
    p3 = json.loads(kg.find_path_detailed("step_2AS_denovo__spades", "step_4TY_cgMLST__chewbbaca"))
    assert p3 is not None and "hops" in p3

    # 4. Assembly to flaA
    p4 = json.loads(kg.find_path_detailed("step_2AS_denovo__spades", "step_4TY_flaA__flaA"))
    assert p4 is not None and "hops" in p4

    # 5. Assembly to ABRicate
    p5 = json.loads(kg.find_path_detailed("step_2AS_denovo__spades", "step_4AN_AMR__abricate"))
    assert p5 is not None and "hops" in p5

    # 6. Assembly to StarAMR
    p6 = json.loads(kg.find_path_detailed("step_2AS_denovo__spades", "step_4AN_AMR__staramr"))
    assert p6 is not None and "hops" in p6

    # 7. Assembly to Prokka
    p7 = json.loads(kg.find_path_detailed("step_2AS_denovo__spades", "step_4AN_genes__prokka"))
    assert p7 is not None and "hops" in p7

    # 8. Mapping Bowtie to iVar
    p8 = json.loads(kg.find_path_detailed("step_2AS_mapping__bowtie", "step_2AS_mapping__ivar"))
    assert p8 is not None and "hops" in p8

    # 9. Trimming fastp to krakentools
    p9 = json.loads(kg.find_path_detailed("step_1PP_trimming__fastp", "step_1PP_filtering__krakentools"))
    assert p9 is not None and "hops" in p9

    # 10. iVar to Pangolin
    p10 = json.loads(kg.find_path_detailed("step_2AS_mapping__ivar", "step_4TY_lineage__pangolin"))
    assert p10 is not None and "hops" in p10


def test_dense_hub_node_explanations(initialized_graph):
    """Verifies detailed node explanations for primary pipeline hubs."""
    # SPAdes explanation
    spades_exp = kg.explain_node("step_2AS_denovo__spades")
    assert "COMPONENT: step_2AS_denovo__spades" in spades_exp
    assert "Total Degree:" in spades_exp

    # KmerFinder explanation
    kmer_exp = kg.explain_node("step_3TX_species__kmerfinder")
    assert "COMPONENT: step_3TX_species__kmerfinder" in kmer_exp

    # chewBBACA explanation
    chew_exp = kg.explain_node("step_4TY_cgMLST__chewbbaca")
    assert "COMPONENT: step_4TY_cgMLST__chewbbaca" in chew_exp

    # Prokka explanation
    prokka_exp = kg.explain_node("step_4AN_genes__prokka")
    assert "COMPONENT: step_4AN_genes__prokka" in prokka_exp


def test_edge_case_disjoint_subgraph_query(initialized_graph):
    """Edge Case 1: Query spanning disjoint subgraphs (bacterial MLST + viral pangolin + panaroo)."""
    disjoint_query = "Run MLST sequence typing, SARS-CoV-2 pangolin lineage, and Panaroo pangenome analysis."
    result = kg.query_graph(disjoint_query, mode="bfs", depth=2, token_budget=3000)
    assert "step_4TY_MLST__mlst" in result
    assert "step_4TY_lineage__pangolin" in result
    assert "multi_pangenome__panaroo" in result


def test_edge_case_reverse_and_sibling_pathfinding(initialized_graph):
    """Edge Case 2: Reverse path and disconnected queries."""
    # Reverse path: MLST -> Fastp (backward flow or bridged path)
    raw = kg.find_path_detailed("step_4TY_MLST__mlst", "step_1PP_trimming__fastp")
    rev_res = json.loads(raw)
    assert "path" in rev_res or "hops" in rev_res or "reversed_path" in rev_res

    # Sibling / helper bridged check: West Nile Lineage and MOB-suite plasmids
    raw_dis = kg.find_path_detailed("step_4TY_lineage__westnile", "step_4TY_plasmid__mobsuite")
    dis_res = json.loads(raw_dis)
    assert "path" in dis_res or "hops" in dis_res or "error" in dis_res


def test_edge_case_synonym_and_jargon_mapping(initialized_graph):
    """Edge Case 3: Biological jargon & synonym handling."""
    jargon_query = "perform antimicrobial resistance screening and wgMLST core genome profiling"
    scores = _score_query(initialized_graph.G, _query_terms(jargon_query))
    seeds = _pick_seeds(scores.ranked, max_k=5, G=initialized_graph.G, best_seed_by_term=scores.best_seed_by_term)
    # Should identify AMR (abricate/staramr) or cgMLST (chewbbaca)
    assert any("abricate" in s or "staramr" in s or "chewbbaca" in s or "mlst" in s for s in seeds)


def test_edge_case_extreme_budget_and_unknown_node(initialized_graph):
    """Edge Case 5 & 6: Tight budget clipping and unknown node handling."""
    # Tight budget
    clipped = kg.query_graph(DENSE_OUTBREAK_QUERY, mode="bfs", depth=3, token_budget=200)
    assert "[!] TRUNCATED" in clipped

    # Unknown node
    unknown_exp = kg.explain_node("non_existent_tool_xyz")
    assert "error" in unknown_exp.lower() or "not found" in unknown_exp.lower()
