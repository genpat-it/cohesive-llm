# `ingestion/` - The Knowledge Ingestion Engine

> [!IMPORTANT]
> The AI Agent is born completely amnesiac. This directory contains the **Ingestion Pipeline** that physically parses Nextflow code (`.nf`), structurally maps it into distinct JSON catalog definitions, and mathematically embeds it into a multi-dimensional Vector space (supporting **ChromaDB** or **FAISS**) using dynamically configured HuggingFace models.

## 1. Top-Level Ingestion Architecture

The ingestion pipeline converts physical Nextflow DSL2 source files into the strict plugin format required by the LangGraph executor. It prevents the LLM from hallucinating by acting as the strict source of truth for whichever domain is being ingested.

```mermaid
flowchart TD
    subgraph 1. Raw Source
        NF["Nextflow Module Files (*.nf)"]
    end

    subgraph 2. The Parsing Engine (parser.py)
        ParseAST["Regex/AST Extraction"]
        ParseAST --> ExtProc["Extract Processes (Input/Output Types)"]
        ParseAST --> ExtWF["Extract Workflows (Routing)"]
    end

    subgraph 3. Catalog Builder (catalog_builder.py)
        JSON["Structured JSON Assembly"]
        JSON --> C_JSON["components.json"]
        JSON --> T_JSON["templates.json"]
        JSON --> R_JSON["resources.json"]
        JSON --> CS_JSON["code_store.jsonl"]
    end

    subgraph 4. Pattern Discovery (ingest_patterns.py & dedup_patterns.py)
        PD["Structural AST Fingerprinting"]
        PD --> PD_LLM["LLM (cyankiwi) Pattern Extraction"]
        PD_LLM --> PD_Dedup["Lossless Semantic Deduplication"]
        PD_Dedup --> P_JSON["patterns.json"]
    end

    subgraph 5. The Embedding Engine (embedder.py)
        EmbedModel["HuggingFace Embeddings\n(Dynamically Configured Model)"]
        EmbedModel --> FAISS["Vector Semantic Space\n(chroma_index/faiss_index/patterns_index)"]
    end

    NF --> ParseAST
    ExtProc & ExtWF --> JSON
    ExtWF --> PD
    C_JSON & T_JSON & P_JSON --> EmbedModel
    
    FAISS -.-> PluginVault[(plugin.yaml / Final Vault)]
    CS_JSON -.-> PluginVault
```

## 2. Component Execution Map

### `cli.py` (Command Line Edge)
The command-line interface orchestrates the entire sequential pipeline. 
- Example: `python -m ingestion.cli --source-dir ./nf_files --plugin-dir plugins/synthetic`
- Responsible for recursively locating all `.nf` objects and generating the `plugin.yaml` manifest.

### `parser.py` (Data Miner)
This module opens the raw text of a `.nf` file and pulls apart the domain logic. It understands the structural blocks of `process { ... }` and `workflow { ... }` using an advanced brace-counting state-machine that ignores comment blocks and Groovy string payloads. It infers the input and output channel structures mathematically, allowing the system to track data lineage without AI assistance.

### `catalog_builder.py` (Ontology Constructor)
Takes the raw parse trees and transforms them into relational JSON objects.
- It splits the physical string blocks into `code_store.jsonl` (to keep the embedding matrix lightweight).
- It generates the metadata blocks (e.g. `domain`, `description`, `take_channels`, `emit_channels`) used in `catalog/components.json`.

### `ingest_patterns.py` (Pattern Harvester & Online Filter)
Extracts recurring Nextflow workflow structures from raw `.nf` files via LLMs, utilizing a **Hybrid Preprocess Filter** to avoid LLM token waste.
- **Structural Fingerprint:** Before hitting the LLM, the parser builds an exact token sequence of channel operators (e.g., `["cross", "multiMap"]`).
- **Semantic Gate:** If a structural duplicate is found, it evaluates Dense Embeddings via local models (`Qwen3`). It only skips the LLM call if the logic is both structurally identical *and* semantically similar (`> 92%`).

### `dedup_patterns.py` (Lossless Post-Pass)
Runs a final sweep after LLM extraction to cluster and deduplicate patterns. Instead of discarding overlapping templates, it performs a **Lossless Set Union**, merging different `use_cases`, `caveats`, and `tags` together to preserve all biological context.

### `embedder.py` (Vector Synthesizer)
The Vector DB embedder. Uses HuggingFace models to turn the `catalog` JSON schemas (and newly mined `patterns.json`) into floating-point vectors. Developers can build persistent `FAISS` indices, enabling advanced retrieval techniques like the **Reciprocal Rank Fusion (RRF) Hybrid Search** used by the Consultant tools. This dimension parameter is serialized into the `plugin.yaml`, ensuring the FastAPI app boots using identical model parameters to query the indices.

## 3. The `Plugin.yaml` Specification

The output of this pipeline is a plugin directory containing a `plugin.yaml`. This file governs how the main API routes requests. It defines:
- **Void Tools**: Identifying processes that have no `output_channels` (e.g. terminal output steps like `data_validation_check`).
- **Prompts**: Binding domain-specific context (`idioms.md`, `rejection_rules.md`) to the agent so it knows how to act within the context of the ingested tools.
