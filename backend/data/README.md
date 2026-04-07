# `data/` Knowledge Base

This parent directory contains the "truth" behind the conversational RAG platform. The LLMs are heavily restricted in what they can perform natively; instead, they retrieve explicit definitions and physical Groovy code lines from this core data cache.

## Database Strategy Execution

```mermaid
flowchart TD
    UserQuery["User: I need to sequence COVID"] --> FAISS
    FAISS[("FAISS Semantic DB")] -.-> |"Matches 'covid' vector"| Tmpl_Metadata
    
    subgraph JSON_Catalogs ["JSON Catalogs"]
        Tmpl_Metadata["Module: Covid Emergency"] --> Comp["Step: Bowtie"]
        Comp --> Resource_Tool["Func: parseRiscd"]
    end
    
    subgraph Raw_Extraction ["Raw Extraction"]
        Tmpl_Metadata & Comp & Resource_Tool --> CodeStore[("code_store_hollow.jsonl")]
        CodeStore --> |"Hydrates actual .nf strings"| LLM_Context(("LLM Context Prompt"))
    end
```

## Internal Storage Methods

### `faiss_index/`
* An auto-generated binary index created at runtime by semantic models (e.g. `HuggingFace`). 
* Capable of high-speed nearest-neighbor sequence comparisons so the graph can process natural language accurately.

### `code_store_hollow.jsonl`
* The physical manifestation of every component.
* **Structure**: Each JSON line contains a dictionary defining an `id` and `content`. 
* **Purpose**: Rather than allowing the LLM Architect to attempt guessing the exact flag configurations of standard bioinformatics tools (like Spades, Prokka, Samtools), the agent locates the ID in its chat and the Hydrator extracts the pre-tested, verified Groovy function string from this file.

### `catalog/`
* A directory of multiple JSON subsets mapping the human-readable definitions (like input strings, compatible filetypes, and descriptions) of the entities hidden inside the `code_store`.
