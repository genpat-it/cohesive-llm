# Core Engine (`backend/core/`) - Architecture & Strict Agnosticism Invariant

The `backend/core/` package is the **domain-agnostic reasoning and compilation engine** of the platform. It implements the LangGraph state machine, topological Knowledge Graph traversals, Pydantic AST validation, and deterministic Nextflow DSL2 source compilation.

---

## 1. The Strict Plugin-Agnostic Core Invariant (Mandatory)

The core engine contains **zero hardcoded tool names, organism types, or biological keywords** (such as `fastp`, `spades`, `bowtie`, `kraken2`, `prokka`, `abricate`, `resfinder`, `staramr`, etc.).

### How Agnosticism is Preserved at Runtime:
1. **Dynamic Catalog Reflection**:
   - Component takes, emits, void tool rules, and parameter types are read at boot time from the active plugin (`backend/plugins/<plugin_name>/catalog/`).
   - `catalog_registry.py` and `loader.py` dynamically build component registries and FAISS indexes from plugin files.
2. **Generic Domain Roles**:
   - System prompts and AST directives use abstract domain roles (`QC`, `Trimming`, `Assembly`, `Screening`, `Typing`, `Annotation`, `Clustering`, `Mapping`) and generic placeholders.
3. **Pure Semantic State Evaluation**:
   - The State Machine evaluates semantic approval intent (`_detect_approval`) and graph connectivity without checking for specific tool strings.

---

## 2. Directory Structure & Module Responsibilities

```
backend/core/
├── adapters/                        Provider abstraction layer
│   ├── llm_provider.py              Unified LLM client wrapping OpenAI / vLLM chat completions
│   └── vector_store.py              FAISS vector database adapter
├── models/                          Strictly-typed Pydantic schemas
│   ├── ast_structure.py             NextflowPipelineAST, SubWorkflow, and EntrypointWorkflow schemas
│   └── consultant_structure.py      ConsultantStructuredOutput and PipelineIntentClassification schemas
├── nodes/                           LangGraph discrete executable agent nodes
│   ├── architect.py                 Architect Precheck & Direct AST Generation nodes
│   ├── consultant.py                Consultant ReAct agent, message sanitization, and structured extraction
│   └── drawer_enricher.py           Visual Canvas graph enricher with KG path reflection and operator synthesis
├── prompts/                         Base prompt templates (domain-agnostic)
│   ├── architect.md                 Architect AST synthesis instructions
│   ├── consultant_base.md           Consultant ReAct agent system instructions
│   ├── drawer_enricher_base.md      Visual canvas operator synthesis and gap-filling instructions
│   └── extractor.md                 Consultant structured plan extractor prompt
├── services/                        Engine services and execution graphs
│   ├── ast_compiler.py              Compiles NextflowPipelineAST into valid Nextflow DSL2 Groovy code
│   ├── consultant_tools.py          LangGraph @tool functions (KG query, batch lookup, plan checking)
│   ├── graph.py                     Hierarchical LangGraph StateGraph definition and routing
│   ├── graph_state.py               GraphState TypedDict representing the global workflow state
│   ├── knowledge_graph.py           Topological Knowledge Graph with 3 edge confidence tiers
│   ├── llm.py                       LLM singleton factory with exponential retry backoff
│   ├── prompt_loader.py             Assembles base prompts and merges active plugin domain overlays
│   ├── query_normalizer.py          Conversational noise filtering and semantic query formulation
│   └── renderer.py                  Deterministic AST-to-Mermaid flowchart generator
├── utils/                           Cross-cutting utilities
│   ├── logger.py                    JSON-structured logging with context attributes
│   └── retry.py                     Exponential backoff and rate-limit retry decorators
├── catalog_registry.py              Central registry for valid component IDs, void tools, and exports
├── config.py                        Pydantic BaseSettings environment configuration
├── loader.py                        Central DataLoader initializing FAISS, KG, and in-memory stores
├── plugin_loader.py                 Plugin discovery, manifest parsing, and lifecycle initialization
└── tool_registry.py                 Tool discoverer merging core and plugin tools for LangGraph
```

---

## 3. Subsystem Deep-Dives

### 3.1 Hierarchical LangGraph State Machine (`services/graph.py`)
- **Planner Subgraph**:
  - `consultant` $\leftrightarrow$ `tools` ReAct loop with multi-hop Knowledge Graph queries.
  - `drawer_enrich` analyzes visual topologies from `/drawer` and bridges gaps using Knowledge Graph paths.
  - `sanitize` injects stub `ToolMessage` instances for orphaned tool calls if the safety limit is hit.
  - `compact_memory` performs lossless memory compaction by extracting tool facts before trimming old messages.
- **Execution Subgraph**:
  - `architect_precheck` algorithmically determines void tools, retrieves helper functions for unmet takes, and injects template bases.
  - `architect_generate` directly synthesizes `NextflowPipelineAST`.
  - `renderer` converts AST into production Nextflow DSL2 code and Mermaid flowchart.

### 3.2 Topological Knowledge Graph (`services/knowledge_graph.py`)
- Traverses AST-derived dataflow connections using NetworkX.
- Resolves upstream and downstream pipelines, detects communities, and calculates shortest dataflow paths across `EXTRACTED`, `INFERRED`, and `AMBIGUOUS` confidence tiers.

### 3.3 Dynamic Prompt Assembly (`services/prompt_loader.py`)
- Merges base prompts from `core/prompts/` with domain-specific overlays from `plugins/<active_plugin>/prompts/domain_context.md`.
- Injects dynamic component tables (`%%void_tools%%`, `%%emitting_tools_table%%`) populated directly from `catalog_registry.py`.
