# `core/services/` - The Cognitive Engine & LangGraph AI Orchestration

> [!CAUTION]
> This directory houses the primary LangGraph topologies. It dictates *how* the Large Language Models think, what tools they can use, and how they recover from failure. Modifications to `graph.py` or the `core/nodes/` modules can drastically alter the behavioral safety of the entire agentic pipeline.

## 1. High-Level Orchestration Flow

The service layer translates a simple web request into a massive, multi-agent processing pipeline. It enforces strict separation of concerns through **Execution Graph Modularity**—splitting the cognitive workload between Planning (Consultant) and Generation (Architect). This decoupling allows us to inject deterministic Python validation directly into the LLM's thought loop without losing conversation state.

### 1.1 The Master State Routing Matrix

```mermaid
flowchart TD
    subgraph FastAPI Boundary
        API[Incoming Request]
    end

    subgraph The Consultant Subgraph (Planning)
        C_Start([Entry]) --> CNode[Consultant LLM]
        CNode -->|Requires Information| CTools[[Catalog Tools]]
        CTools --> CNode
        
        CNode -->|No tools requested| Sanitize[Sanitize Drift]
        Sanitize --> Extract[Extract JSON Plan]
        Extract --> Compact[Lossless Memory Prune]
    end

    subgraph The Execution Subgraph (Generation)
        HNode[Hydrator: Fetch Groovy] --> Precheck[Math/Channel Validation]
        Precheck --> AGen[Architect Generation]
        
        AGen --> Validate{Pydantic Valid?}
        
        Validate -->|Fail| Repair[Repair Logic Trigger]
        Repair --> AReason[Architect Reason]
        AReason -->|Needs Info| ATools[[Investigative Tools]]
        ATools --> AReason
        AReason -->|Max Retries / Done| ASanitize[Sanitize Drift]
        ASanitize --> AGen
        
        Validate -->|Success or Fail Max| Render[Jinja2 Rendering]
        Render --> DetDia[Deterministic Mermaid]
        DetDia --> ProbDia[Agentic Diagram]
    end

    API --> C_Start
    Compact --> CheckState{Status?}
    CheckState -->|CHATTING| Return_Early((Return Chat Reply))
    CheckState -->|APPROVED| HNode
    ProbDia --> Return_Success((Return Payload))
```

## 2. Core Service Modules

### 2.1 The Topologies (`graph.py` & `core/nodes/*.py`)
These files define the actual LangGraph nodes and their wiring. They utilize `LLMs` strictly bound to the domain-agnostic tools defined in the active plugin's tool registries.
- **Lossless Tool-Trajectory Compaction (`compact_memory_node`)**: LLMs crash if their context window overflows (attention collapse). This node implements a surgical memory pruning algorithm that extracts concrete semantic facts from a tool's output into a structured `tool_memory` buffer, completely deleting raw tool call tokens from the chat history. The LLM retains the "knowledge" without the token bloat.
- **Deterministic Approval Short-Circuiting**: Bypasses the LLM for terminal routing in the Consultant Subgraph. It uses an internal heuristic alongside structured output to forcibly sever the planning loop and route directly to execution the moment human intent is satisfied, preventing endless planning loops.
- **`sanitize_orphaned_tool_calls`**: In highly restricted loops (where `MAX_TOOL_ITERATIONS` hits a ceiling), LLMs may leave dangling tool invocations. This node surgically injects mock `ToolMessage` stubs to satisfy the provider's API constraints and prevent HTTP 400 crashes.

### 2.2 The Epistemic Toolkits (`consultant_tools.py` & `architect_tools.py`)
Tools are the only way the AI interacts with reality. The AI cannot "guess" code; it must retrieve it.
- **Consultant Tools**: Capable of hybrid vector semantic search and catalog exploration (`search_components`, `lookup_catalog_item`). Used to dynamically explore the catalog and build the high-level `draft_plan`.
- **Architect Tools**: Heavily restricted to exact ID lookups and channel compatibility matching. Used strictly during repair loops to investigate *why* a generation failed (e.g., verifying if a component actually emits a `.results` channel).

### 2.3 The Multi-Turn Recovery Engine (`repair.py`)
If the Pydantic models in `core/models/ast_structure.py` catch a fatal logic error, the execution state routes here. 
- This module builds a highly aggressive "Correction Prompt" that injects the exact Python `ValueError` traceback back into the LLM. 
- It actively forces the Architect LLM to debug its own hallucinated Nextflow channels in a retry loop (bounded by `MAX_REPAIR_RETRIES`).
- If it fails beyond the retry maximum, it proceeds to the renderer but explicitly flags the pipeline as a best-effort draft with a strict warning injected into the final code and payload, preventing the pipeline failure from silently crashing the UI.

### 2.4 Model Agnosticism (`llm.py` & `prompt_loader.py`)
- **LLM Provider Agnosticism**: Uses a `get_llm()` factory pattern to dynamically bind standard LLM providers (OpenAI, Anthropic, Google, local Mistral/Llama instances) via the `LLM_PROVIDER` environment configuration. This ensures that no logic nodes are locked into a specific AI vendor.
- **Cloud Resilience**: Implements rigorous exponential backoff wrappers (`with_exponential_backoff`) to survive `HTTP 429 Too Many Requests` or `HTTP 502 Bad Gateway` errors from cloud AI providers during heavy concurrent load.
- **Dynamic Prompt Injection**: Continuously pulls component channel compatibility tables and syntax definitions from the active plugin catalogs, injecting them into the base prompts at runtime.
