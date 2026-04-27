"""
tests/scenarios/level4_guardrails.py
Level 4 — Negative: Rejection / Guardrail Tests

These scenarios test that the system correctly REJECTS invalid requests.
The user asks for tools or combinations that are impossible or unavailable.

Complexity: MEDIUM (the request itself is simple, but correct rejection requires domain expertise)
  - Tools not in the catalog (BWA, Canu, TrimGalore, GATK)
  - Tools applied to wrong organism (Pangolin for bacteria)
  - Tools for wrong sequencing tech (Flye for Illumina)
  - Tools for wrong purpose (iVar for de novo assembly)

Expected behavior:
  - Status remains CHATTING (never APPROVED)
  - No pipeline code is generated
  - AI explains WHY and suggests valid alternatives
"""

import os

LEVEL4_SCENARIOS = [
    {
        "id": "L4_01_bwa_not_available",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: BWA not in catalog",
        "chat_messages": [
            "I want to do mapping with BWA on my Illumina paired-end reads.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy"],
        "expect_in_context": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy"],
        "rejection_reason": (
            "BWA and BWA-MEM2 are NOT available in this framework. "
            "Available mapping tools include: Bowtie2 (step_2AS_mapping__bowtie), "
            "Minimap2 (step_2AS_mapping__minimap2), iVar (step_2AS_mapping__ivar), "
            "Snippy (step_2AS_mapping__snippy)."
        ),
    },
    {
        "id": "L4_02_canu_not_available",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Canu not in catalog",
        "chat_messages": [
            "I want to assemble my Nanopore long reads with Canu.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_denovo__flye", "step_2AS_hybrid__unicycler"],
        "expect_in_context": ["step_2AS_denovo__flye", "step_2AS_hybrid__unicycler"],
        "rejection_reason": (
            "Canu is NOT available in this framework. "
            "Available long-read assemblers include: Flye (step_2AS_denovo__flye). "
            "For hybrid assembly: Unicycler (step_2AS_hybrid__unicycler)."
        ),
    },
    {
        "id": "L4_03_pangolin_bacteria",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Pangolin for Salmonella (wrong organism)",
        "chat_messages": [
            "I have Salmonella samples and want to determine lineage with Pangolin.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_cgMLST__chewbbaca"],
        "expect_in_context": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_cgMLST__chewbbaca"],
        "rejection_reason": (
            "Pangolin (step_4TY_lineage__pangolin) is exclusively for SARS-CoV-2 lineage "
            "classification using the PANGO nomenclature. It CANNOT be applied to bacterial "
            "genomes. For Salmonella typing: MLST (step_4TY_MLST__mlst), "
            "cgMLST (step_4TY_cgMLST__chewbbaca)."
        ),
    },
    {
        "id": "L4_04_ivar_denovo",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: iVar for de novo assembly (wrong purpose)",
        "chat_messages": [
            "I want to do de novo assembly of my bacterial isolates using iVar.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__ivar", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "expect_in_context": ["step_2AS_mapping__ivar", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "rejection_reason": (
            "iVar (step_2AS_mapping__ivar) is a reference-based consensus caller — it requires "
            "a reference genome and CANNOT perform de novo assembly. For de novo assembly: "
            "SPAdes (step_2AS_denovo__spades), Shovill (step_2AS_denovo__shovill), "
            "Unicycler (step_2AS_denovo__unicycler)."
        ),
    },
    {
        "id": "L4_05_trimgalore_not_available",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: TrimGalore not in catalog",
        "chat_messages": [
            "I want to run TrimGalore on my Illumina reads for quality trimming.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_1PP_trimming__fastp", "step_1PP_trimming__trimmomatic", "step_1PP_trimming__chopper"],
        "expect_in_context": ["step_1PP_trimming__fastp", "step_1PP_trimming__trimmomatic", "step_1PP_trimming__chopper"],
        "rejection_reason": (
            "TrimGalore is NOT available in this framework. "
            "Available trimming tools include: fastp (step_1PP_trimming__fastp), "
            "Trimmomatic (step_1PP_trimming__trimmomatic). "
            "For Nanopore: Chopper (step_1PP_trimming__chopper)."
        ),
    },
    {
        "id": "L4_06_gatk_not_available",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: GATK not in catalog",
        "chat_messages": [
            "I want to use GATK for variant calling on my bacterial whole genome sequencing data.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__ivar", "step_2AS_mapping__snippy", "step_2AS_mapping__medaka"],
        "expect_in_context": ["step_2AS_mapping__ivar", "step_2AS_mapping__snippy", "step_2AS_mapping__medaka"],
        "rejection_reason": (
            "GATK (Genome Analysis Toolkit) is NOT available in this framework. "
            "For variant calling/consensus: iVar (step_2AS_mapping__ivar), "
            "Snippy (step_2AS_mapping__snippy), Medaka (step_2AS_mapping__medaka for Nanopore)."
        ),
    },
    {
        "id": "L4_REV_01_kallisto_not_available",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: Kallisto not in catalog",
        "chat_messages": [
            "I want to align my sequences. Please use kallisto for the alignment.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2", "step_2AS_mapping__ivar"],
        "expect_in_context": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2", "step_2AS_mapping__ivar"],
        "rejection_reason": (
            "Kallisto is NOT available in this framework. "
            "Use supported alignment/mapping tools such as Bowtie2, Minimap2, or iVar depending on data type and objective."
        ),
    },
    {
        "id": "L4_REV_02_chewbbaca_wrong_organism",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: chewBBACA requested for SARS-CoV-2",
        "chat_messages": [
            "I have a sars cov 2 sample. I want to run chewbbaca on it to find the sequence type.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_cgMLST__chewbbaca", "step_4TY_lineage__pangolin"],
        "expect_in_context": ["step_4TY_cgMLST__chewbbaca", "step_4TY_lineage__pangolin"],
        "rejection_reason": (
            "chewBBACA is a bacterial cgMLST typing tool and is not valid for SARS-CoV-2. "
            "For SARS-CoV-2 typing/lineage use Pangolin."
        ),
    },
    {
        "id": "L4_REV_03_bowtie_wrong_for_nanopore_long",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Bowtie for Nanopore long-read mapping",
        "chat_messages": [
            "I have nanopore long reads. I want to map them to my reference using bowtie.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2"],
        "expect_in_context": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2"],
        "rejection_reason": (
            "Bowtie2 is intended for short reads. Nanopore long reads should be mapped with Minimap2 in this framework."
        ),
    },
    {
        "id": "L4_REV_04_medaka_wrong_for_illumina",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Medaka requested for Illumina",
        "chat_messages": [
            "I have illumina reads and a reference. I want to map them using medaka.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__medaka", "step_2AS_mapping__bowtie", "step_2AS_mapping__ivar"],
        "expect_in_context": ["step_2AS_mapping__medaka", "step_2AS_mapping__bowtie", "step_2AS_mapping__ivar"],
        "rejection_reason": (
            "Medaka is designed for Nanopore consensus polishing and is incompatible with Illumina reads. "
            "Use Bowtie2 or iVar for Illumina mapping workflows."
        ),
    },
    {
        "id": "L4_REV_05_abricate_missing_prereq_reads",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: ABRicate directly on raw FASTQ",
        "chat_messages": [
            "I have raw fastq reads. I want to run abricate directly on them to find amr genes.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4AN_AMR__abricate", "step_2AS_denovo__spades", "step_2AS_denovo__shovill"],
        "expect_in_context": ["step_4AN_AMR__abricate", "step_2AS_denovo__spades", "step_2AS_denovo__shovill"],
        "rejection_reason": (
            "ABRicate requires assembled contigs, not raw FASTQ reads. "
            "Assemble first with a supported assembler (e.g., SPAdes or Shovill), then run ABRicate."
        ),
    },
    {
        "id": "L4_REV_06_bwa_not_available_alt",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: BWA explicitly requested",
        "chat_messages": [
            "I want to align my short reads using bwa. Please build this pipeline.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2", "step_2AS_mapping__ivar"],
        "expect_in_context": ["step_2AS_mapping__bowtie", "step_2AS_mapping__minimap2", "step_2AS_mapping__ivar"],
        "rejection_reason": (
            "BWA is not part of the available tool catalog in this framework. "
            "Use Bowtie2 or iVar for short-read reference mapping depending on workflow needs."
        ),
    },
    {
        "id": "L4_REV_07_pangolin_wrong_for_salmonella",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Pangolin requested for Salmonella",
        "chat_messages": [
            "I have some salmonella reads. I want to map them and then use pangolin to find the variant lineage.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_cgMLST__chewbbaca"],
        "expect_in_context": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_cgMLST__chewbbaca"],
        "rejection_reason": (
            "Pangolin applies only to SARS-CoV-2 lineage classification and is invalid for Salmonella. "
            "Use bacterial typing tools such as MLST or cgMLST instead."
        ),
    },
    {
        "id": "L4_REV_08_ivar_wrong_purpose_denovo",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: iVar requested for de novo assembly",
        "chat_messages": [
            "I want to do a de novo assembly of my bacterial genome. Please use ivar for the assembly step.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__ivar", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "expect_in_context": ["step_2AS_mapping__ivar", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "rejection_reason": (
            "iVar is for reference-guided consensus and cannot perform de novo assembly. "
            "Use SPAdes, Shovill, or Unicycler for de novo assembly tasks."
        ),
    },
    {
        "id": "L4_REV_09_fastp_wrong_for_nanopore",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: fastp requested for Nanopore long reads",
        "chat_messages": [
            "I have nanopore long reads. I want to trim them first. Please use fastp for the trimming.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_1PP_trimming__fastp", "step_1PP_trimming__chopper"],
        "expect_in_context": ["step_1PP_trimming__fastp", "step_1PP_trimming__chopper"],
        "rejection_reason": (
            "fastp is intended for short-read data. Nanopore long-read trimming should use Chopper in this framework."
        ),
    },
    {
        "id": "L4_REV_10_prokka_missing_prereq_reads",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: Prokka directly on raw FASTQ",
        "chat_messages": [
            "I have raw fastq reads from my sequencer. I want to run prokka on them to find the genes.",
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4AN_genes__prokka", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "expect_in_context": ["step_4AN_genes__prokka", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "rejection_reason": (
            "Prokka annotates assembled contigs/genomes and cannot run directly on raw reads. "
            "Assemble first, then run Prokka for gene annotation."
        ),
    },
]

_LEGACY_LEVEL4_SCENARIOS = LEVEL4_SCENARIOS

MODULE_REJECTION_SCENARIOS = [
    {
        "id": "N1_26_missing_tool_cutadapt",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: missing tools Cutadapt and BWA",
        "chat_messages": [
            "I want to trim my reads using Cutadapt and map them with BWA."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_1PP_trimming__fastp", "step_1PP_trimming__trimmomatic", "step_2AS_mapping__bowtie", "step_2AS_mapping__ivar"],
        "expect_in_context": ["step_1PP_trimming__fastp", "step_1PP_trimming__trimmomatic", "step_2AS_mapping__bowtie", "step_2AS_mapping__ivar"],
        "rejection_reason": (
            "Cutadapt and BWA are not available in this framework. Use supported trimming and mapping tools such as "
            "fastp/Trimmomatic and Bowtie2/iVar depending on sequencing type and objective."
        ),
    },
    {
        "id": "N1_27_wrong_organism_pangolin",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: Pangolin on E. coli",
        "chat_messages": [
            "I have some E. coli reads and want to determine their lineage using Pangolin."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_cgMLST__chewbbaca"],
        "expect_in_context": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_cgMLST__chewbbaca"],
        "rejection_reason": (
            "Pangolin is SARS-CoV-2 specific and cannot be used for bacterial lineage typing. "
            "Use bacterial typing methods such as MLST or cgMLST instead."
        ),
    },
    {
        "id": "N1_28_incompatible_tech_spades",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: SPAdes on Nanopore long reads",
        "chat_messages": [
            "I have Nanopore long reads and want to do de novo assembly using SPAdes."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_denovo__spades", "step_2AS_denovo__flye"],
        "expect_in_context": ["step_2AS_denovo__spades", "step_2AS_denovo__flye"],
        "rejection_reason": (
            "SPAdes is for short-read workflows in this framework. For Nanopore long-read de novo assembly use Flye."
        ),
    },
    {
        "id": "N2_29_wrong_purpose_ivar",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: iVar requested for de novo assembly",
        "chat_messages": [
            "Can you use iVar to do a de novo assembly of my unknown virus?"
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_mapping__ivar", "step_2AS_denovo__spades", "step_2AS_denovo__unicycler"],
        "expect_in_context": ["step_2AS_mapping__ivar", "step_2AS_denovo__spades", "step_2AS_denovo__unicycler"],
        "rejection_reason": (
            "iVar is a reference-based mapping/consensus tool and cannot perform de novo assembly."
        ),
    },
    {
        "id": "N2_30_missing_tool_canu_gatk",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Canu and GATK are unavailable",
        "chat_messages": [
            "I want to assemble my Illumina paired-end data using Canu and call variants with GATK."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy"],
        "expect_in_context": ["step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy"],
        "rejection_reason": (
            "Canu and GATK are not in the supported toolset. Use supported assemblers and mapping/variant tools instead."
        ),
    },
    {
        "id": "N2_31_missing_prerequisite_mlst",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: MLST and Prokka directly on raw FASTQ",
        "chat_messages": [
            "I have raw Illumina FASTQ files and want to immediately run MLST and Prokka on them."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_MLST__mlst", "step_4AN_genes__prokka", "step_2AS_denovo__spades", "step_2AS_denovo__shovill"],
        "expect_in_context": ["step_4TY_MLST__mlst", "step_4AN_genes__prokka", "step_2AS_denovo__spades", "step_2AS_denovo__shovill"],
        "rejection_reason": (
            "MLST and Prokka require assembled contigs/genomes as input. Assemble first, then run typing/annotation."
        ),
    },
    {
        "id": "N2_32_wrong_organism_purpose_westnile",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Westnile and KmerFinder used for wrong purposes",
        "chat_messages": [
            "I want to use Westnile to find the lineage of my Salmonella samples, and Kmerfinder to assign lineages."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_lineage__westnile", "step_3TX_species__kmerfinder", "step_4TY_MLST__mlst"],
        "expect_in_context": ["step_4TY_lineage__westnile", "step_3TX_species__kmerfinder", "step_4TY_MLST__mlst"],
        "rejection_reason": (
            "Westnile lineage tooling is specific to West Nile Virus, and KmerFinder is for species ID, not lineage assignment."
        ),
    },
    {
        "id": "N3_33_complex_missing_tools",
        "level": 4,
        "difficulty": "complex",
        "description": "Reject: Trimgalore, HISAT2, and MEGAHIT are unavailable",
        "chat_messages": [
            "Build a pipeline that trims Nanopore reads with Trimgalore, maps to human with HISAT2, and uses MEGAHIT for metagenomic assembly."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_1PP_trimming__chopper", "step_2AS_mapping__minimap2", "step_2MG_denovo__metaspades"],
        "expect_in_context": ["step_1PP_trimming__chopper", "step_2AS_mapping__minimap2", "step_2MG_denovo__metaspades"],
        "rejection_reason": (
            "Requested tools are not available in this framework. Use supported alternatives for Nanopore trimming, mapping, and metagenomic assembly."
        ),
    },
    {
        "id": "N3_34_complex_prerequisite_mismatch",
        "level": 4,
        "difficulty": "complex",
        "description": "Reject: multiple incompatibilities in one request",
        "chat_messages": [
            "Take raw Illumina reads, run Pangolin first, then assemble with Flye, and run flaA typing to check if it's Staphylococcus."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_4TY_lineage__pangolin", "step_2AS_denovo__flye", "step_4TY_flaA__flaA", "step_2AS_denovo__spades"],
        "expect_in_context": ["step_4TY_lineage__pangolin", "step_2AS_denovo__flye", "step_4TY_flaA__flaA", "step_2AS_denovo__spades"],
        "rejection_reason": (
            "The request combines incompatible operations: Pangolin requires SARS-CoV-2 consensus input, Flye is for long reads, and flaA typing is Campylobacter-specific."
        ),
    },
    {
        "id": "N3_35_complex_wrong_purpose",
        "level": 4,
        "difficulty": "complex",
        "description": "Reject: FreeBayes missing and Porechop misused",
        "chat_messages": [
            "Use FreeBayes for de novo assembly, then run Porechop on assembled contigs to remove adapters."
        ],
        "expect_rejection": True,
        "template_ids": [],
        "component_ids": ["step_2AS_denovo__spades", "step_2AS_denovo__unicycler", "step_1PP_trimming__chopper"],
        "expect_in_context": ["step_2AS_denovo__spades", "step_2AS_denovo__unicycler", "step_1PP_trimming__chopper"],
        "rejection_reason": (
            "FreeBayes is not available and is not a de novo assembler. Adapter trimming tools are applied to raw reads, not assembled contigs."
        ),
    },
    {
        "id": "module_draft_genome_reject_bwa",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: module_draft_genome request with unavailable BWA",
        "chat_messages": [
            "I have some short reads and a reference genome. I want to build a consensus. Please map the reads with bwa first."
        ],
        "expect_rejection": True,
        "template_ids": ["module_draft_genome"],
        "component_ids": ["step_2AS_mapping__bowtie", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy"],
        "expect_in_context": ["module_draft_genome", "step_2AS_mapping__bowtie", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy"],
        "rejection_reason": (
            "BWA is not available in this framework. For short-read consensus workflows use supported mapping/consensus tools "
            "such as Bowtie2 (step_2AS_mapping__bowtie), iVar (step_2AS_mapping__ivar), or Snippy "
            "(step_2AS_mapping__snippy) depending on the workflow design."
        ),
    },
    {
        "id": "module_denovo_reject_spades_nanopore",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: module_denovo request using SPAdes on Nanopore",
        "chat_messages": [
            "I want to remove host dna from my nanopore reads. Then I want to assemble them into contigs using spades."
        ],
        "expect_rejection": True,
        "template_ids": ["module_denovo"],
        "component_ids": ["step_1PP_hostdepl__minimap2", "step_2AS_denovo__spades", "step_2AS_denovo__flye"],
        "expect_in_context": ["module_denovo", "step_1PP_hostdepl__minimap2", "step_2AS_denovo__spades", "step_2AS_denovo__flye"],
        "rejection_reason": (
            "For Nanopore long reads, SPAdes (step_2AS_denovo__spades) is not the recommended assembler under the guardrail rules. "
            "Use Minimap2-based host depletion (step_1PP_hostdepl__minimap2) and Flye (step_2AS_denovo__flye) for long-read assembly."
        ),
    },
    {
        "id": "module_covid_emergency_reject_missing_mapping",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: Pangolin requested directly on raw SARS-CoV-2 reads",
        "chat_messages": [
            "I have raw sars cov 2 fastq reads. I want to run pangolin on them to find the lineage."
        ],
        "expect_rejection": True,
        "template_ids": ["module_covid_emergency"],
        "component_ids": ["step_4TY_lineage__pangolin", "step_2AS_mapping__ivar"],
        "expect_in_context": ["module_covid_emergency", "step_4TY_lineage__pangolin", "step_2AS_mapping__ivar"],
        "rejection_reason": (
            "Pangolin (step_4TY_lineage__pangolin) requires a consensus sequence, not raw FASTQ reads. "
            "Run reference-based consensus first with iVar (step_2AS_mapping__ivar), then classify lineage with Pangolin."
        ),
    },
    {
        "id": "module_typing_bacteria_reject_mlst_virus",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: MLST requested for SARS-CoV-2 assembly",
        "chat_messages": [
            "I have a sars cov 2 assembly. I need to find the genes and run mlst to type it."
        ],
        "expect_rejection": True,
        "template_ids": ["module_typing_bacteria"],
        "component_ids": ["step_4TY_MLST__mlst", "step_4TY_lineage__pangolin", "step_4AN_genes__prokka"],
        "expect_in_context": ["module_typing_bacteria", "step_4TY_MLST__mlst", "step_4TY_lineage__pangolin", "step_4AN_genes__prokka"],
        "rejection_reason": (
            "MLST (step_4TY_MLST__mlst) is for bacterial typing and is invalid for SARS-CoV-2. "
            "For SARS-CoV-2 typing/lineage use Pangolin (step_4TY_lineage__pangolin)."
        ),
    },
    {
        "id": "module_reads_processing_reject_trimgalore",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: TrimGalore not available in module_reads_processing request",
        "chat_messages": [
            "I want to check my raw reads quality. Then I want to trim them using trimgalore."
        ],
        "expect_rejection": True,
        "template_ids": ["module_reads_processing"],
        "component_ids": ["step_1PP_trimming__fastp", "step_1PP_trimming__trimmomatic", "step_0SQ_rawreads__fastq"],
        "expect_in_context": ["module_reads_processing", "step_1PP_trimming__fastp", "step_1PP_trimming__trimmomatic", "step_0SQ_rawreads__fastq"],
        "rejection_reason": (
            "TrimGalore is not available in this framework. For read trimming use fastp "
            "(step_1PP_trimming__fastp) or Trimmomatic (step_1PP_trimming__trimmomatic)."
        ),
    },
    {
        "id": "module_wgs_bacteria_reject_canu",
        "level": 4,
        "difficulty": "easy",
        "description": "Reject: Canu not available for bacterial Illumina de novo assembly",
        "chat_messages": [
            "I have bacterial illumina sequencing data. I want to do a de novo assembly using canu."
        ],
        "expect_rejection": True,
        "template_ids": ["module_wgs_bacteria"],
        "component_ids": ["step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "expect_in_context": ["module_wgs_bacteria", "step_2AS_denovo__spades", "step_2AS_denovo__shovill", "step_2AS_denovo__unicycler"],
        "rejection_reason": (
            "Canu is not available in this framework. For bacterial Illumina de novo assembly use supported options such as "
            "SPAdes (step_2AS_denovo__spades), Shovill (step_2AS_denovo__shovill), or Unicycler (step_2AS_denovo__unicycler)."
        ),
    },
    {
        "id": "module_filtered_denovo_reject_bowtie_longreads",
        "level": 4,
        "difficulty": "medium",
        "description": "Reject: Bowtie requested for Nanopore long-read filtering",
        "chat_messages": [
            "I want to filter my nanopore long reads using bowtie. Then I need to assemble them into contigs."
        ],
        "expect_rejection": True,
        "template_ids": ["module_filtered_denovo"],
        "component_ids": ["step_1PP_filtering__bowtie", "step_1PP_filtering__minimap2", "step_2AS_denovo__flye"],
        "expect_in_context": ["module_filtered_denovo", "step_1PP_filtering__bowtie", "step_1PP_filtering__minimap2", "step_2AS_denovo__flye"],
        "rejection_reason": (
            "Bowtie2 (step_1PP_filtering__bowtie) is intended for short-read mapping/filtering. "
            "For Nanopore long reads use Minimap2 filtering (step_1PP_filtering__minimap2), then assemble with Flye "
            "(step_2AS_denovo__flye)."
        ),
    },
    {
        "id": "module_draft_genome_reject_spades_consensus",
        "level": 4,
        "difficulty": "complex",
        "description": "Reject: SPAdes requested for reference-based consensus",
        "chat_messages": [
            "I have some short reads and a reference genome. I want to map the reads and build a consensus using spades. After that I need to find the genes."
        ],
        "expect_rejection": True,
        "template_ids": ["module_draft_genome"],
        "component_ids": ["step_2AS_denovo__spades", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy", "step_4AN_genes__prokka"],
        "expect_in_context": ["module_draft_genome", "step_2AS_denovo__spades", "step_2AS_mapping__ivar", "step_2AS_mapping__snippy", "step_4AN_genes__prokka"],
        "rejection_reason": (
            "SPAdes (step_2AS_denovo__spades) is a de novo assembler and cannot produce reference-based consensus. "
            "For consensus from mapped reads use iVar (step_2AS_mapping__ivar) or Snippy (step_2AS_mapping__snippy), then annotate with Prokka "
            "(step_4AN_genes__prokka)."
        ),
    },
    {
        "id": "module_denovo_reject_incompatible_spades",
        "level": 4,
        "difficulty": "complex",
        "description": "Reject: Nanopore de novo request forces SPAdes in host-depleted flow",
        "chat_messages": [
            "I have nanopore long reads. I want to deplete the host reads using minimap2. After that I want to do a de novo assembly using spades and find amr genes."
        ],
        "expect_rejection": True,
        "template_ids": ["module_denovo"],
        "component_ids": ["step_1PP_hostdepl__minimap2", "step_2AS_denovo__spades", "step_2AS_denovo__flye", "step_4AN_AMR__abricate"],
        "expect_in_context": ["module_denovo", "step_1PP_hostdepl__minimap2", "step_2AS_denovo__spades", "step_2AS_denovo__flye", "step_4AN_AMR__abricate"],
        "rejection_reason": (
            "For long-read Nanopore assembly in this guardrail setting, SPAdes (step_2AS_denovo__spades) is not the valid choice. "
            "Use Minimap2 host depletion (step_1PP_hostdepl__minimap2), assemble with Flye (step_2AS_denovo__flye), then run AMR analysis such as ABRicate "
            "(step_4AN_AMR__abricate) on the resulting assembly."
        ),
    },
    {
        "id": "module_variant_lineage_reject_wrong_organism",
        "level": 4,
        "difficulty": "complex",
        "description": "Reject: Pangolin requested for Campylobacter",
        "chat_messages": [
            "I have raw reads from campylobacter. I want to map them to a reference genome. Then I want to use pangolin to find the lineage and prokka for genes."
        ],
        "expect_rejection": True,
        "template_ids": ["module_variant_lineage"],
        "component_ids": ["step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_flaA__flaA", "step_4AN_genes__prokka"],
        "expect_in_context": ["module_variant_lineage", "step_4TY_lineage__pangolin", "step_4TY_MLST__mlst", "step_4TY_flaA__flaA", "step_4AN_genes__prokka"],
        "rejection_reason": (
            "Pangolin (step_4TY_lineage__pangolin) is only for SARS-CoV-2 lineage assignment and cannot be used for Campylobacter. "
            "For Campylobacter typing, use bacterial typing tools such as MLST (step_4TY_MLST__mlst) or flaA typing "
            "(step_4TY_flaA__flaA)."
        ),
    },
]

OLD_LEVEL4_TEST_IDS = [s["id"] for s in _LEGACY_LEVEL4_SCENARIOS]
NEW_LEVEL4_TEST_IDS = [s["id"] for s in MODULE_REJECTION_SCENARIOS]


def _env_enabled(var_name: str) -> bool:
    return os.getenv(var_name, "").strip().lower() in {"1", "true", "yes", "on"}


if _env_enabled("ONLY_NEW_SCENARIOS"):
    print("[tests] ONLY_NEW_SCENARIOS is enabled: not testing old Level 4 scenarios.")
    LEVEL4_SCENARIOS = MODULE_REJECTION_SCENARIOS
else:
    LEVEL4_SCENARIOS = _LEGACY_LEVEL4_SCENARIOS + MODULE_REJECTION_SCENARIOS
