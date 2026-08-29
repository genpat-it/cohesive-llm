# IZS Bioinformatics Plugin (`backend/plugins/izs/`)

The **IZS Bioinformatics Plugin** is the production domain plugin for genomic surveillance and pathogen characterization at the Istituto Zooprofilattico Sperimentale (IZS).

---

## 1. Domain Coverage & Workflow Categories

The plugin catalogs over 70 production Nextflow DSL2 steps covering:
- **Preprocessing & QC**: FastQC (`step_0SQ_rawreads__fastq`), Trimmomatic (`step_1PP_trimming__trimmomatic`), fastp (`step_1PP_trimming__fastp`), Bowtie2 host depletion (`step_1PP_hostdepl__bowtie`).
- **Assembly & Consensus**: SPAdes (`step_2AS_denovo__spades`), Shovill (`step_2AS_denovo__shovill`), Unicycler (`step_2AS_denovo__unicycler`), iVar consensus (`step_2AS_mapping__ivar`), Bowtie2 mapping (`step_2AS_mapping__bowtie`).
- **Taxonomy & Species ID**: Kraken2 (`step_3TX_class__kraken`), KmerFinder (`step_3TX_species__kmerfinder`), VirDabricate (`step_3TX_species__vdabricate`).
- **Typing & Annotation**: MLST (`step_4TY_MLST__mlst`), cgMLST / chewBBACA (`step_4TY_cgMLST__chewbbaca`), flaA (`step_4TY_flaA__flaA`), MOB-suite (`step_4TY_plasmid__mobsuite`), Pangolin (`step_4TY_lineage__pangolin`), Prokka (`step_4AN_genes__prokka`), ABRicate (`step_4AN_AMR__abricate`), StarAMR (`step_4AN_AMR__staramr`), BLAST (`step_4AN_AMR__blast`).

---

## 2. Directory Layout

```
backend/plugins/izs/
├── plugin.yaml                      Plugin manifest with void tool definitions and FAISS index pointers
├── catalog/
│   ├── components.json              78 component definitions with typed takes and emits
│   ├── templates.json               Production pipeline blueprints (WNV, COVID, bacterial WGS, de novo)
│   └── resources.json               Helper function schemas (getSingleInput, getReference, param, extractKey)
├── faiss_index/                     Qwen3-Embedding-0.6B vector database index of component cards
├── patterns_index/                  FAISS vector index of Nextflow DSL2 data-shaping channel idioms
├── prompts/
│   └── domain_context.md            IZS laboratory conventions, naming standards, and channel types
├── code_store.jsonl                 Verbatim Nextflow Groovy code for each module
└── benchmark_data/                  Golden benchmark suites (L1-L5 + recreation test suites)
```
