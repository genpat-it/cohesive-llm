"""
Comprehensive Edge-Case & Stress-Test Suite for
LLM Intent Classification & Pipeline Anti-Bloat Architecture.

Tests cover:
1. Complex multi-tool negations & exclusions (zero regex reliance).
2. Scientist outcome-oriented colloquial phrasing on pre-assembled FASTA contigs.
3. Full SOP raw sequencing pipeline requests (legitimate multi-stage trimming).
4. Hybrid sequencing technology detection (Illumina + Nanopore).
5. Diagnostic / conceptual inquiries (no-pipeline guard).
6. Multi-turn conversational corrections and intent reversals.
"""
import json
import sys
import unittest
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.store.memory import InMemoryStore

from core.loader import DataLoader
from core.models.consultant_structure import PipelineIntentClassification
from core.nodes.consultant import _classify_intent_with_llm
from core.services.consultant_tools import classify_pipeline_intent
from core.services.knowledge_graph import kg


class TestIntentAndBloatCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = InMemoryStore()
        cls.loader = DataLoader()
        cls.loader._load_lookups(cls.store)
        kg.build_nx_graph(cls.store)

    def test_edge_case_1_complex_multiple_negations(self):
        """Test complex natural language with multiple distinct negation styles."""
        query = (
            "Assemble our avian influenza sequences using shovill, but under no circumstances use fastp, "
            "do not use quast, avoid unicycler, and exclude bwa."
        )
        # 1. Test LLM Intent tool invocation
        raw_res = classify_pipeline_intent.invoke({"user_query": query})
        data = json.loads(raw_res)
        excluded = [x.lower() for x in data.get("excluded_items", [])]

        self.assertIn("fastp", excluded, "LLM should detect 'fastp' as excluded")
        
        # 2. Test Knowledge Graph vertex projection with excluded items
        projected = kg.project_all_vertices(query, excluded_items=set(excluded))
        
        self.assertTrue(
            any("shovill" in p for p in projected),
            f"Expected shovill in projected vertices, got {projected}"
        )
        for bad_tool in ("fastp", "quast", "unicycler", "bwa"):
            self.assertFalse(
                any(bad_tool in p for p in projected),
                f"Tool '{bad_tool}' should be excluded from projection, got {projected}"
            )

    def test_edge_case_2_scientist_swab_preassembled_contigs_no_bloat(self):
        """Scientist asks for AMR and typing on assembled FASTA contigs from swab samples.
        Must NOT auto-prepend trimming or raw read QC."""
        query = (
            "We collected clinical duck lung swabs and already generated de novo assembled contigs in FASTA format. "
            "Please run AMR gene screening with abricate and viral lineage typing. Do not re-trim or re-assemble."
        )
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertIn(
            intent.data_level,
            ("intermediate_sequence", "unspecified"),
            f"Data level should be sequence/contigs, got {intent.data_level}"
        )

        # Bridging check: Should NOT prepend trimming
        candidate_comps = ["step_3SC_screening__abricate"]
        allow_trim = (
            intent.workflow_scope == "full_pipeline"
            and intent.data_level in ("raw_reads", "hybrid_multimodal")
            and not intent.skip_preprocessing
        )
        bridged = kg.bridge_pipeline_path(
            candidate_comps,
            input_datatype=intent.data_level,
            allow_auto_trimming=allow_trim
        )

        self.assertEqual(
            bridged,
            ["step_3SC_screening__abricate"],
            f"Expected strictly 1 targeted component without trimming, got {bridged}"
        )

    def test_edge_case_3_raw_fastq_full_sop_pipeline_retention(self):
        """Full SOP pipeline request from raw Illumina reads must legitimately retain quality trimming."""
        query = (
            "We received raw paired-end Illumina FASTQ reads from an unknown outbreak sample. "
            "Build an end-to-end standard operating procedure pipeline from raw reads to trimmed assembly."
        )
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertEqual(
            intent.workflow_scope,
            "full_pipeline",
            f"Workflow scope should be full_pipeline, got {intent.workflow_scope}"
        )
        self.assertEqual(
            intent.data_level,
            "raw_reads",
            f"Data level should be raw_reads, got {intent.data_level}"
        )

        # Bridging check: SHOULD prepend trimming
        candidate_comps = ["step_2AS_assembly__shovill"]
        allow_trim = (
            intent.workflow_scope == "full_pipeline"
            and intent.data_level in ("raw_reads", "hybrid_multimodal")
            and not intent.skip_preprocessing
        )
        bridged = kg.bridge_pipeline_path(
            candidate_comps,
            input_datatype=intent.data_level,
            allow_auto_trimming=allow_trim
        )

        self.assertTrue(
            len(bridged) >= 2 and any("trimming" in c for c in bridged),
            f"Expected trimming prepended for raw reads full pipeline, got {bridged}"
        )

    def test_edge_case_4_hybrid_sequencing_technology(self):
        """Scientist mentions both Illumina short reads and Nanopore long reads."""
        query = "I have raw paired-end Illumina FASTQ and Oxford Nanopore long reads for a bacterial outbreak hybrid assembly."
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertIn(
            intent.data_level,
            ("hybrid_multimodal", "raw_reads"),
            f"Expected hybrid or raw reads, got {intent.data_level}"
        )
        self.assertIn(
            intent.technology,
            ("hybrid", "illumina", "nanopore"),
            f"Expected hybrid/illumina/nanopore technology, got {intent.technology}"
        )

    def test_edge_case_5_diagnostic_probe_no_pipeline(self):
        """Conceptual inquiry without pipeline building intent."""
        query = "What databases and algorithms does your platform support for screening antimicrobial resistance in bacteria?"
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertEqual(
            intent.workflow_scope,
            "diagnostic_probe",
            f"Expected diagnostic_probe scope, got {intent.workflow_scope}"
        )

    def test_edge_case_6_multi_turn_correction_and_reversal(self):
        """Turn 1 proposes trimming + assembly, Turn 2 scientist cancels trimming because data is pre-cleaned."""
        messages = [
            HumanMessage(content="Generate an Illumina pipeline with fastp and shovill."),
            AIMessage(content="I propose a pipeline with fastp (trimming) and shovill (assembly)."),
            HumanMessage(content="Wait, scratch fastp. I already cleaned the reads on our cluster, so omit fastp and only run shovill on the pre-cleaned data.")
        ]
        intent: PipelineIntentClassification = _classify_intent_with_llm(messages)

        excluded = [x.lower() for x in intent.excluded_items]
        self.assertIn("fastp", excluded, f"fastp should be excluded after user reversal, got {excluded}")
        self.assertTrue(
            intent.skip_preprocessing or intent.data_level == "intermediate_sequence",
            f"Should recognize pre-cleaned data / skip_preprocessing, got skip={intent.skip_preprocessing}, data_level={intent.data_level}"
        )

        projected = kg.project_all_vertices(str(messages[-1].content), excluded_items=set(excluded))
        self.assertFalse(any("fastp" in p for p in projected), f"fastp must not be in projected vertices: {projected}")

    def test_edge_case_7_virology_domain_classification(self):
        """Scientist asks for viral genome lineage assignment and clade determination from assembled FASTA."""
        query = "Identify the clade and lineage for these West Nile virus assembled genomes in FASTA format."
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertEqual(
            intent.domain_category,
            "virology",
            f"Expected virology domain, got {intent.domain_category}"
        )
        self.assertEqual(
            intent.data_level,
            "intermediate_sequence",
            f"Expected intermediate_sequence data level, got {intent.data_level}"
        )

    def test_edge_case_8_bacteriology_domain_classification(self):
        """Scientist asks for bacterial isolate AMR screening and plasmid typing."""
        query = "Perform antimicrobial resistance gene profiling and plasmid replicon typing on Salmonella enterica isolates."
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertEqual(
            intent.domain_category,
            "bacteriology",
            f"Expected bacteriology domain, got {intent.domain_category}"
        )

    def test_edge_case_9_metagenomics_domain_classification(self):
        """Scientist asks for clinical swab metagenomic taxonomic classification."""
        query = "Run taxonomic profiling on raw shotgun metagenomics reads from animal nasal swabs to detect mixed infections."
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertEqual(
            intent.domain_category,
            "metagenomics",
            f"Expected metagenomics domain, got {intent.domain_category}"
        )

    def test_edge_case_10_epidemiological_surveillance_clustering(self):
        """Scientist asks for outbreak transmission chain reconstruction and SNP distance clustering."""
        query = "Construct a minimum spanning tree and SNP distance matrix from VCF variant tables to trace hospital outbreak transmission chains."
        msg = [HumanMessage(content=query)]
        intent: PipelineIntentClassification = _classify_intent_with_llm(msg)

        self.assertEqual(
            intent.domain_category,
            "epidemiological_surveillance",
            f"Expected epidemiological_surveillance domain, got {intent.domain_category}"
        )
        self.assertEqual(
            intent.data_level,
            "variant_data",
            f"Expected variant_data data level, got {intent.data_level}"
        )


if __name__ == "__main__":
    unittest.main()
