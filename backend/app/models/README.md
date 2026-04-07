# `app/models/` Pydantic Guardrails

This directory contains strict **Pydantic Data Models**. It is the primary defense system against LLM hallucinations. Rather than asking an LLM to "write code", it forces the LLM to populate the objects below, allowing Python decorators to police the logic before it compiles.

## Comprehensive Data Model Architecture

The diagram below illustrates the hierarchical and interconnected nature of the Pydantic models across the architectural components (Architect, Consultant, and Diagram Generator).

```mermaid
classDiagram
    %% Consultant Models
    class ConsultantOutput {
        +String response_to_user
        +String status ["CHATTING", "APPROVED"]
        +String draft_plan
        +String strategy_selector
        +String used_template_id
        +List~String~ selected_module_ids
    }
    
    %% Architect AST Models
    NextflowPipelineAST *-- ImportItem
    NextflowPipelineAST *-- GlobalDef
    NextflowPipelineAST *-- InlineProcess
    NextflowPipelineAST *-- WorkflowBlock
    NextflowPipelineAST *-- Entrypoint
    
    class NextflowPipelineAST {
        +List~ImportItem~ imports
        +List~GlobalDef~ globals
        +List~InlineProcess~ inline_processes
        +List~WorkflowBlock~ sub_workflows
        +Entrypoint entrypoint
        +auto_generate_imports()
        +enforce_workflow_usage()
    }
    class WorkflowBlock {
        +String name
        +List~String~ take_channels
        +List~String~ emit_channels
        +String body_code
        +rescue_and_heal_body()
        +validate_emit_format()
        +forbid_void_emits()
        +enforce_strict_data_shaping()
        +enforce_variable_existence()
        +forbid_set_on_processes()
    }
    class ImportItem {
        +String module_path
        +List~String~ functions
        +validate_aliases()
        +forbid_nf_core()
        +auto_fix_module_paths()
    }
    class GlobalDef {
        +String type
        +String name
        +String value
        +forbid_active_channels()
    }
    class InlineProcess {
        +String name
        +String container
        +List~String~ input_declarations
        +List~String~ output_declarations
        +String script_block
        +validate_no_dsl()
        +validate_name()
    }
    class Entrypoint {
        +String body_code
        +auto_heal_entrypoint()
    }

    %% Diagram Models
    DiagramData *-- Node
    DiagramData *-- Edge

    class DiagramData {
        +List~Node~ nodes
        +List~Edge~ edges
        +validate_graph_integrity()
    }
    class Node {
        +String id
        +String label
        +String shape
        +String subgraph
        +validate_id()
        +sanitize_label()
    }
    class Edge {
        +String source
        +String target
        +String label
        +sanitize_edge_label()
    }
```

## Defensive Modeling Files

### `ast_structure.py`
The most complex validation engine in the system. When the Architect Agent returns its pipeline suggestion, this file intercepts it and runs rigorous heuristic checks:
* **Validation Rules**: Uses `@field_validator` and `@model_validator` closures to parse the `body_code` using RegEx.
* **Self-Healing Triggers**: It detects if the LLM wrongly appends `.set` directly onto a process call (which is invalid in Groovy), or if it utilizes inline `.cross` statements without immediately flattening the tuple via `.map`. If it detects an error, it manually raises a `ValueError` injecting a highly-specific, scolding prompt instructing the LLM on exactly how to fix its own mistake.
* **Void Tool Blocking**: Specifically hardcodes rules preventing the LLM from trying to capture standard output channels from reporting/QC tools that utilize `publishDir` (Void tools). It forces deletion of hallucinated emit parameters.
* **Auto-Resolution Framework**: Automatically fixes common LLM mistakes, like deducing the exact import directory path (`../steps/`, `../modules/`, etc.) based entirely on the process prefix name.

### `consultant_structure.py`
Forces the Consultant Agent into its strict mode:
* Ensures `used_template_id` and `selected_module_ids` perfectly match strings retrieved from the RAG context.
* Restricts the agent to binary `status` decisions (`CHATTING` vs `APPROVED`).
* Requires the LLM to justify its pipeline strategy via `strategy_selector` (`EXACT_MATCH`, `ADAPTED_MATCH`, or `CUSTOM_BUILD`).

### `diagram_structure.py`
Maps Nextflow logic to Mermaid `.js` elements safely:
* Validates Graph `Node` and `Edge` schemas, catching duplicate IDs or strings that utilize reserved terminology that would crash the Mermaid runtime engine.
* Enforces `shape` typings mapping physical logic to visual markers (`input`, `process`, `operator`, `output`, `global`).
* Actively sanitizes labels to remove internal formatting quotes that break JavaScript rendering systems.
