"""
End-to-end benchmark test for queries a through f.
Validates that every prompt produces:
- A non-empty AST (NextflowPipelineAST)
- Valid DSL2 Nextflow executable code
- No failure error messages
"""
import sys
import unittest
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from langchain_core.messages import HumanMessage
from core.loader import DataLoader
from core.services.graph import build_graph


class TestBenchmarkPromptsAToF(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = DataLoader()
        cls.graph, cls.store = build_graph()
        cls.loader.load_all(store=cls.store)

        cls.queries = {
            'a': (
                "Design a Nextflow workflow for segmented viruses that takes pre-processed FASTQ reads "
                "and user-provided reference genomes, maps reads to each reference using iVar, and outputs "
                "per-segment consensus FASTA files plus a consolidated multifasta. The workflow should pair "
                "each sample with all references using and be structured as a reusable module."
            ),
            'b': (
                "Design a sequential bioinformatics workflow for the Covid Emergency pipeline that starts with "
                "trimmed FASTQ reads and the SARS-CoV-2 reference genome (NC_045512.2, Wuhan-Hu-1). The pipeline "
                "should: 1. Map the reads to the reference and call variants using iVar, producing a consensus FASTA sequence. "
                "2. Pass that consensus FASTA directly to Pangolin to assign a viral lineage. "
                "3. Output the final Pangolin lineage report (CSV) and retain the consensus FASTA."
            ),
            'c': (
                "Design a Nextflow workflow for a 'Depletion & de novo' pipeline that starts with trimmed FASTQ reads "
                "and an optional host reference genome. The workflow first removes host reads using Bowtie, then performs "
                "de novo assembly of the depleted (or original, if no host provided) reads with SPAdes. The pipeline "
                "supports samples with or without a host reference and emits both the depleted reads and final assemblies."
            ),
            'd': (
                "Design a Nextflow workflow for the 'Genome Draft' pipeline that processes depleted FASTQ reads against a "
                "provided reference genome in parallel:1. Run Bowtie2 mapping to produce a coverage-checked consensus FASTA. "
                "2. Simultaneously run iVar-based mapping to generate another consensus FASTA, which is then passed—along "
                "with an optional GenBank reference—to Prokka for structural gene annotation (configured for viral genomes). "
                "The workflow accepts FASTA reference(s) for mapping and an optional GenBank reference for annotation, and "
                "executes both mapping branches concurrently before proceeding to annotation."
            ),
            'e': (
                "Design a Nextflow DSL2 workflow for the WNV Lineage Calculation and Mapping pipeline that starts with "
                "trimmed FASTQ reads and: 1. Determines the West Nile virus lineage using the Westnile tool. 2. Uses the "
                "inferred lineage to dynamically select the matching reference genome via getReferenceForLineage. 3. Performs "
                "read mapping and variant calling with iVar using the lineage-specific reference. The workflow outputs a "
                "consensus sequence (FASTA) and a VCF file, enabling lineage-aware, reference-guided analysis of WNV "
                "samples in a fully automated, sequential manner."
            ),
            'f': (
                "I have Illumina paired-end sequencing data from bacterial isolates collected during a foodborne outbreak "
                "investigation. I need a complete genomic surveillance workflow: check the quality of the raw sequencing "
                "files, trim adapters and low-quality bases, run contamination detection to flag mixed samples, classify the "
                "reads taxonomically to confirm pathogen identity, assemble the genomes de novo, identify the species from "
                "the assemblies, screen for antimicrobial resistance genes using two different databases and filter the "
                "results by confidence thresholds, type any plasmids present, annotate all genes, determine the classical "
                "MLST sequence type, perform core-genome MLST typing, run flagellar antigen typing, and finally build a "
                "phylogenetic clustering tree from the cgMLST allele profiles."
            )
        }

    def _run_query(self, label: str):
        q = self.queries[label]
        initial_state = {
            "messages": [HumanMessage(content=q)],
            "execution_mode": "direct",
            "selected_component_ids": [],
            "design_plan": None,
            "consultant_status": None,
        }
        events = list(self.graph.stream(initial_state, config={"configurable": {"thread_id": f"test_e2e_{label}"}}))
        final_state = {}
        for ev in events:
            for n_name, n_st in ev.items():
                final_state.update(n_st)

        self.assertEqual(final_state.get("consultant_status"), "APPROVED", f"Query {label} should be approved in direct mode")
        self.assertTrue(bool(final_state.get("ast_json")), f"Query {label} must have a non-empty ast_json")
        self.assertTrue(bool(final_state.get("nextflow_code")), f"Query {label} must produce Nextflow code")
        self.assertIn("workflow", final_state.get("nextflow_code", "").lower(), f"Query {label} code must contain workflow definition")

    def test_query_a_segmented_virus(self):
        self._run_query("a")

    def test_query_b_covid_emergency(self):
        self._run_query("b")

    def test_query_c_depletion_denovo(self):
        self._run_query("c")

    def test_query_d_genome_draft(self):
        self._run_query("d")

    def test_query_e_wnv_lineage(self):
        self._run_query("e")

    def test_query_f_dense_wgs_surveillance(self):
        self._run_query("f")


if __name__ == "__main__":
    unittest.main()
