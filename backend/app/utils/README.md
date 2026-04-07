# `app/utils/` Templates

Contains purely algorithmic rendering engines to translate data arrays back into structural Groovy formatting.

## Process Layout

```mermaid
flowchart LR
    JSON(AST JSON Objects) --> J2[Jinja2 Engine]
    Template(rendering.py\nNF_TEMPLATE_AST) --> J2
    
    J2 --> imports(1. Generate imports)
    J2 --> globals(2. Output Global params)
    J2 --> inline(3. Output Inline bash Processes)
    J2 --> sub(4. Loop / Indent Sub-Workflows)
    J2 --> main(5. Populate entry point)
    
    imports & globals & inline & sub & main --> Out((main.nf String))
```

## Files

### `rendering.py`
* **`NF_TEMPLATE_AST`**: Defines a 100+ line Jinja2 templating block mapping the structural list outputs (e.g. `ast_json.imports`, `ast_json.globals`, `ast_json.sub_workflows`) into correctly formatted `.nf` modules. It carefully spaces code using standard DSL2 conventions.
