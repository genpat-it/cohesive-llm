"""
Catalog Builder — Converts parsed .nf definitions into structured catalog JSON.

Takes the output of parser.py and produces:
  - components.json: {components: [{id, tool, domain, description, input_channels, output_channels, container,
                                    keywords, use_cases, compatible_seq_types, variables_needed}]}
  - templates.json:  {templates: [{id, description, components_used, input_channels, output_channels,
                                   keywords, use_cases, compatible_seq_types, variables_needed}]}
  - resources.json:  {resources: {helper_functions: [{name, description, usage, path, keywords, use_cases, aliases}],
                                   containers: [...]}}
  - tool_graph.json: {nodes: [{id}], edges: [{source, target, via}]}
  - code_store.jsonl: One JSON object per line: {id, content}

Usage:
    from ingestion.catalog_builder import build_catalog
    build_catalog(parsed_files, output_dir=Path("plugins/synthetic/catalog"))
"""

import json
import fnmatch
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# ── Pydantic output schemas ───────────────────────────────────────────────────

DOMAIN_OPTIONS = Literal[
    "Assembly & Mapping",
    "Annotation & AMR",
    "Metagenomics",
    "Typing",
    "Quality Control",
    "Preprocessing",
    "Taxonomy",
]

SEQ_TYPE_OPTIONS = Literal[
    "illumina_paired",
    "illumina_single",
    "ion",
    "nanopore",
    "pacbio",
    "any",
]


class ComponentEnrichment(BaseModel):
    domain: DOMAIN_OPTIONS = Field(
        description="Strict category. Choose the closest match from the allowed list."
    )
    description: str = Field(
        description=(
            "Extremely token efficient description. Max 10 words. "
            "E.g.: 'Adapter trimming & quality filtering with fastp.'"
        )
    )
    keywords: list[str] = Field(
        description="5-10 semantic tags, acronyms, and synonyms. Lowercase."
    )
    use_cases: list[str] = Field(
        description="2-3 specific, practical use cases. Each under 15 words."
    )
    compatible_seq_types: list[SEQ_TYPE_OPTIONS] = Field(
        description=(
            "Sequencing platforms this tool supports. "
            "Infer from parameters, documentation knowledge, and process logic. "
            "E.g.: if it checks params.seq_type == 'ion', include 'ion'."
        )
    )


class TemplateEnrichment(BaseModel):
    summary: str = Field(
        description="A 3-5 word summary. Highly token efficient."
    )
    steps: list[str] = Field(
        description="Ordered list of highly concise step actions (e.g., ['Fastp Trim', 'Spades Assembly']). Token efficient."
    )
    keywords: list[str] = Field(description="5-10 semantic tags. Lowercase.")
    use_cases: list[str] = Field(description="2-3 specific use cases. Max 15 words each.")
    compatible_seq_types: list[SEQ_TYPE_OPTIONS] = Field(
        description="Infer from component summaries injected into context."
    )


class ResourceEnrichment(BaseModel):
    description: str = Field(
        description="Extremely token efficient 3-5 word description."
    )
    keywords: list[str] = Field(description="3-6 semantic tags. Lowercase.")
    use_cases: list[str] = Field(description="1-2 practical use cases. Max 15 words each.")
    aliases: list[str] = Field(
        description=(
            "ONLY include explicit alias function names found in the source file "
            "(e.g., a wrapper that calls this function). Return empty list if none found."
        )
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def build_catalog(
    parsed_files: list[dict],
    output_dir: Path,
    source_dir: Path,
    resources_dir: Path | None = None,
    enrich_llm: bool = False,
    plugin_config: dict | None = None,
) -> dict[str, int]:
    """Build catalog files from parsed .nf definitions.

    Args:
        parsed_files: List of dicts from parser.parse_nf_file()
        output_dir: Directory to write catalog files into
        source_dir: Root directory of the .nf source files
        resources_dir: Directory containing helper functions (defaults to source_dir/functions)
        enrich_llm: If True, use the LLM to enrich metadata
        plugin_config: Dictionary containing ingestion config (e.g. template_patterns, domain_mapping)

    Returns:
        Summary dict with counts: {components, templates, resources, code_entries}
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    components: list[dict] = []
    templates: list[dict] = []
    resources: list[dict] = []
    code_entries: list[dict] = []
    containers: set[str] = set()

    plugin_config = plugin_config or {}
    template_patterns = plugin_config.get("template_patterns", ["pipeline_*"])
    component_patterns = plugin_config.get("component_patterns", ["*"]) # default accept all

    for parsed in parsed_files:
        file_stem = parsed["file_stem"]
        vars_needed = parsed.get("variables_needed", [])
        
        # 1. Determine if Template or Component based on agnostic patterns
        rel_path = _derive_rel_path(parsed["file"], source_dir, file_stem)
        
        is_template = any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(file_stem, pat) for pat in template_patterns)
        is_component = any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(file_stem, pat) for pat in component_patterns)
        
        # Extract Groovy helper functions → resources (Do this for all files)
        for fn in parsed.get("groovy_functions", []):
            resources.append(_function_to_resource(fn, parsed["file"], source_dir))

        if not is_template and not is_component:
            continue # Skip files like etc/bowtie2.nf that aren't components or templates
            
        # 2. Extract Interface: Look for a workflow matching the filename, fallback to first workflow, fallback to first process
        named_wfs = [wf for wf in parsed["workflows"] if wf["name"] != "__entrypoint__"]
        matching_wfs = [wf for wf in named_wfs if wf["name"] == file_stem]
        
        entry_wf = matching_wfs[0] if matching_wfs else (named_wfs[0] if named_wfs else None)
        entry_proc = parsed["processes"][0] if parsed["processes"] else None
        
        if not entry_wf and not entry_proc:
            pass # No exportable entity
        elif is_template:
            components_used = entry_wf["includes"] if entry_wf else []
            input_channels = entry_wf["takes"] if entry_wf else entry_proc.get("inputs", [])
            output_channels = entry_wf["emits"] if entry_wf else entry_proc.get("outputs", [])
            
            tmpl = {
                "id": file_stem,
                "description": f"Workflow '{entry_wf['name']}'" if entry_wf else f"Process '{entry_proc['name']}'",
                "keywords": [],
                "use_cases": [],
                "compatible_seq_types": [],
                "variables_needed": vars_needed,
                "components_used": components_used,
                "input_channels": input_channels,
                "output_channels": output_channels,
                "file_path": _derive_rel_path(parsed["file"], source_dir, file_stem)
            }
            templates.append(tmpl)
        else:
            input_channels = entry_wf["takes"] if entry_wf else entry_proc.get("inputs", [])
            output_channels = entry_wf["emits"] if entry_wf else entry_proc.get("outputs", [])
            
            tool_name = file_stem
            container = None
            if parsed["processes"]:
                first_proc = parsed["processes"][0]
                if first_proc.get("container"):
                    container = first_proc["container"]
                    containers.add(container)
            
            comp = {
                "id": file_stem,
                "tool": tool_name,
                "domain": "General",
                "description": f"{tool_name} — '{file_stem}'.",
                "keywords": [tool_name],
                "use_cases": [],
                "compatible_seq_types": [],
                "variables_needed": vars_needed,
                "input_channels": input_channels,
                "output_channels": output_channels,
                "container": container,
                "file_path": _derive_rel_path(parsed["file"], source_dir, file_stem)
            }
            components.append(comp)

        # Raw code for code store
        code_id = file_stem
        code_entries.append({
            "id": code_id, 
            "content": parsed["raw_code"],
            "file_path": parsed.get("file", "")
        })

    # Phase 2: Build deterministic tool graph (needed for context)
    graph = _build_tool_graph(components, templates)

    # Phase 3: LLM enrichment in dependency order (Graph-Aware)
    if enrich_llm:
        _enrich_all(components, templates, resources, code_entries, graph, source_dir, resources_dir, plugin_config)

    # ── Write outputs ──────────────────────────────────────────────────────────
    comp_path = output_dir / "components.json"
    with open(comp_path, "w", encoding="utf-8") as f:
        json.dump({"components": components}, f, indent=2)
    print(f"  [CATALOG] Wrote {len(components)} components → {comp_path}")

    tmpl_path = output_dir / "templates.json"
    with open(tmpl_path, "w", encoding="utf-8") as f:
        json.dump({"templates": templates}, f, indent=2)
    print(f"  [CATALOG] Wrote {len(templates)} templates → {tmpl_path}")

    res_path = output_dir / "resources.json"
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {"version": "2.0"},
            "resources": {
                "helper_functions": resources,
                "containers": sorted(containers),
            }
        }, f, indent=2)
    print(f"  [CATALOG] Wrote {len(resources)} resources → {res_path}")

    graph_path = output_dir / "tool_graph.json"
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    print(f"  [CATALOG] Wrote tool_graph → {graph_path} "
          f"({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")

    code_path = output_dir.parent / "code_store.jsonl"
    with open(code_path, "w", encoding="utf-8") as f:
        for entry in code_entries:
            f.write(json.dumps(entry) + "\n")
    print(f"  [CATALOG] Wrote {len(code_entries)} code entries → {code_path}")

    return {
        "components": len(components),
        "templates": len(templates),
        "resources": len(resources),
        "code_entries": len(code_entries),
    }


# ── Struct builders ───────────────────────────────────────────────────────────

def _derive_rel_path(filepath: str, source_dir: Path, file_stem: str) -> str:
    try:
        return str(Path(filepath).relative_to(source_dir).with_suffix(""))
    except ValueError:
        return file_stem


def _process_to_component(
    proc: dict, file_stem: str, filepath: str, source_dir: Path, vars_needed: list[str]
) -> dict:
    name = proc["name"]
    tool_name = name.lower()
    if proc.get("container"):
        tool_name = proc["container"].split("/")[-1].split(":")[0]

    return {
        "id": file_stem,
        "tool": tool_name,
        "domain": "General",
        "description": f"{tool_name} — process '{name}'.",
        "keywords": [tool_name],
        "use_cases": [],
        "compatible_seq_types": [],
        "variables_needed": vars_needed,
        "input_channels": proc.get("inputs") or [],
        "output_channels": proc.get("outputs") or [],
        "container": proc.get("container"),
        "file_path": _derive_rel_path(filepath, source_dir, file_stem),
    }


def _workflow_to_template(
    wf: dict, file_stem: str, filepath: str, source_dir: Path, vars_needed: list[str]
) -> dict:
    return {
        "id": file_stem,
        "description": f"Workflow '{wf['name']}'.",
        "keywords": [],
        "use_cases": [],
        "compatible_seq_types": [],
        "variables_needed": vars_needed,
        "components_used": wf.get("includes") or [],
        "input_channels": wf.get("takes") or [],
        "output_channels": wf.get("emits") or [],
        "file_path": _derive_rel_path(filepath, source_dir, file_stem),
    }


def _function_to_resource(fn: dict, filepath: str, source_dir: Path) -> dict:
    rel = _derive_rel_path(filepath, source_dir, fn["name"])
    return {
        "name": fn["name"],
        "description": fn.get("doc") or f"Groovy helper function '{fn['name']}'.",
        "usage": fn.get("usage", f"def result = {fn['name']}()"),
        "path": str(rel),
        "keywords": [],
        "use_cases": [],
        "aliases": [],
        "num_args": fn.get("num_args", 0),
        "arguments": fn.get("arguments", []),
    }


# ── Phase 4: Deterministic Tool Graph ────────────────────────────────────────

def _build_tool_graph(components: list[dict], templates: list[dict]) -> dict:
    """Build a deterministic directed acyclic graph based on data-flow edges.

    A directed edge A → B exists when:
      - B is `include`d by a template that also includes A (structural co-use), OR
      - A's output_channels overlap with B's input_channels (data flow match).

    Nodes: all component and template IDs.
    Edges: {source, target, via} where 'via' is the channel or 'co-template'.
    """
    nodes = [{"id": c["id"], "type": "component"} for c in components]
    nodes += [{"id": t["id"], "type": "template"} for t in templates]

    edges: list[dict] = []
    seen_edges: set[tuple] = set()

    # Build lookup maps
    comp_by_id = {c["id"]: c for c in components}
    comp_outputs: dict[str, set[str]] = {
        c["id"]: {ch.lower() for ch in (c.get("output_channels") or [])}
        for c in components
    }
    comp_inputs: dict[str, set[str]] = {
        c["id"]: {ch.lower() for ch in (c.get("input_channels") or [])}
        for c in components
    }

    # Edge type 1: Data-flow edges (channel overlap)
    for src_id, src_outs in comp_outputs.items():
        if not src_outs:
            continue
        for tgt_id, tgt_ins in comp_inputs.items():
            if src_id == tgt_id or not tgt_ins:
                continue
            overlap = src_outs & tgt_ins
            if overlap:
                key = (src_id, tgt_id)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({
                        "source": src_id,
                        "target": tgt_id,
                        "via": sorted(overlap),
                        "edge_type": "data_flow",
                    })

    # Edge type 2: Template-component inclusion edges
    for tmpl in templates:
        for comp_id in (tmpl.get("components_used") or []):
            if comp_id in comp_by_id:
                key = (tmpl["id"], comp_id)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({
                        "source": tmpl["id"],
                        "target": comp_id,
                        "via": ["include"],
                        "edge_type": "template_include",
                    })

        # Edge type 3: Co-inclusion (sequential ordering within template)
        used = tmpl.get("components_used") or []
        for i in range(len(used) - 1):
            src = used[i]
            tgt = used[i + 1]
            if src in comp_by_id and tgt in comp_by_id:
                key = (src, tgt)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({
                        "source": src,
                        "target": tgt,
                        "via": [f"co-use in {tmpl['id']}"],
                        "edge_type": "co_template",
                    })

    return {"nodes": nodes, "edges": edges}


# ── Phase 3: LLM Enrichment ───────────────────────────────────────────────────

def _enrich_all(
    components: list[dict],
    templates: list[dict],
    resources: list[dict],
    code_entries: list[dict],
    graph: dict,
    source_dir: Path,
    resources_dir: Path | None = None,
    plugin_config: dict | None = None,
) -> None:
    """Run LLM enrichment in topological order:
    1. Resources first (smallest, self-contained)
    2. Components second
    3. Templates third (inject component summaries into context)
    """
    from core.services.llm import get_llm
    from langchain_core.prompts import ChatPromptTemplate

    llm = get_llm()
    code_map = {e["id"]: e["content"] for e in code_entries}

    # 1. Enrich resources
    print("  [LLM] Enriching resources...")
    res_llm = llm.with_structured_output(ResourceEnrichment)
    res_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a bioinformatics expert writing catalog metadata. "
         "Be telegraphic and precise. No filler words."),
        ("human",
         "Analyze this Groovy helper function and return ResourceEnrichment metadata.\n"
         "Current description (may be empty or just from doc comment): {current_desc}\n\n"
         "Source code:\n```groovy\n{code}\n```"),
    ])
    res_chain = res_prompt | res_llm

    for res in resources:
        try:
            result = res_chain.invoke({
                "current_desc": res.get("description", ""),
                "code": _get_function_code(res["name"], code_entries, resources_dir),
            })
            res.update({
                "description": result.description,
                "keywords": result.keywords,
                "use_cases": result.use_cases,
                "aliases": result.aliases,
            })
            print(f"    ✓ {res['name']}")
        except Exception as e:
            print(f"    ✗ {res['name']}: {e}")

    # 2. Enrich components
    print("  [LLM] Enriching components...")
    comp_llm = llm.with_structured_output(ComponentEnrichment)
    comp_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a bioinformatics expert writing catalog metadata for an LLM agent. "
         "Be telegraphic: dense detail, zero fluff. "
         "Domain must be EXACTLY ONE of: 'Assembly & Mapping', 'Annotation & AMR', "
         "'Metagenomics', 'Typing', 'Quality Control', 'Preprocessing', 'Taxonomy'."),
        ("human",
         "Analyze this Nextflow process component.\n\n"
         "Component ID: {comp_id}\n"
         "Variables needed (from AST): {vars_needed}\n"
         "Graph Context (How it connects to other tools):\n{graph_context}\n\n"
         "Source code (truncated):\n```nextflow\n{code}\n```\n\n"
         "Return ComponentEnrichment metadata."),
    ])
    comp_chain = comp_prompt | comp_llm

    for comp in components:
        # Build topological context for the component
        comp_id = comp["id"]
        used_in = [e["source"] for e in graph["edges"] if e["target"] == comp_id and e["edge_type"] == "template_include"]
        upstream = [e["source"] for e in graph["edges"] if e["target"] == comp_id and e["edge_type"] == "data_flow"]
        downstream = [e["target"] for e in graph["edges"] if e["source"] == comp_id and e["edge_type"] == "data_flow"]
        
        ctx_lines = []
        if used_in: ctx_lines.append(f"- Used in templates: {', '.join(used_in)}")
        if upstream: ctx_lines.append(f"- Receives data from: {', '.join(upstream)}")
        if downstream: ctx_lines.append(f"- Feeds data to: {', '.join(downstream)}")
        graph_context = "\n".join(ctx_lines) if ctx_lines else "No known graph connections."

        try:
            result = comp_chain.invoke({
                "comp_id": comp_id,
                "vars_needed": comp.get("variables_needed", []),
                "graph_context": graph_context,
                "code": code_map.get(comp_id, "")[:3500],
            })
            
            # Deterministic override for domain based on plugin.yaml
            domain = result.domain
            domain_mapping = (plugin_config or {}).get("domain_mapping", {})
            for prefix, mapped_domain in domain_mapping.items():
                if comp_id.startswith(prefix):
                    domain = mapped_domain
                    break
            
            comp.update({
                "domain": domain,
                "description": result.description,
                "keywords": result.keywords,
                "use_cases": result.use_cases,
                "compatible_seq_types": list(result.compatible_seq_types),
            })
            print(f"    ✓ {comp['id']}")
        except Exception as e:
            print(f"    ✗ {comp['id']}: {e}")

    # 3. Enrich templates — inject component summaries (GraphRAG)
    print("  [LLM] Enriching templates (with GraphRAG component context)...")
    tmpl_llm = llm.with_structured_output(TemplateEnrichment)
    comp_summary_map = {
        c["id"]: f"{c['tool']} ({c['domain']}): {c['description']}"
        for c in components
    }
    tmpl_prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a bioinformatics expert writing catalog metadata for an LLM agent. "
         "`steps` must be a token-efficient JSON array of actions without any extra text or fluff."),
        ("human",
         "Analyze this Nextflow workflow template.\n\n"
         "Template ID: {tmpl_id}\n"
         "Variables needed (from AST): {vars_needed}\n\n"
         "Component summaries (from graph context):\n{comp_context}\n\n"
         "Workflow source code (truncated):\n```nextflow\n{code}\n```\n\n"
         "Return TemplateEnrichment metadata."),
    ])
    tmpl_chain = tmpl_prompt | tmpl_llm

    for tmpl in templates:
        # Build GraphRAG context: inject summaries of all included components
        included_ids = tmpl.get("components_used") or []
        comp_context_lines = [
            f"- {cid}: {comp_summary_map.get(cid, '(not in catalog)')}"
            for cid in included_ids
        ]
        comp_context = "\n".join(comp_context_lines) if comp_context_lines else "(no components listed)"

        try:
            result = tmpl_chain.invoke({
                "tmpl_id": tmpl["id"],
                "vars_needed": tmpl.get("variables_needed", []),
                "comp_context": comp_context,
                "code": code_map.get(tmpl["id"], "")[:3500],
            })
            tmpl.update({
                "summary": result.summary,
                "steps": result.steps,
                "keywords": result.keywords,
                "use_cases": result.use_cases,
                "compatible_seq_types": list(result.compatible_seq_types),
            })
            print(f"    ✓ {tmpl['id']}")
        except Exception as e:
            print(f"    ✗ {tmpl['id']}: {e}")


def _get_function_code(func_name: str, code_entries: list[dict], resources_dir: Path | None) -> str:
    """Find the source code of a specific Groovy function directly from resource files."""
    import re
    if resources_dir and resources_dir.exists():
        for nf_file in resources_dir.glob("*.nf"):
            try:
                content = nf_file.read_text()
                # Find start of function
                m = re.search(rf'\bdef\s+{re.escape(func_name)}\s*\(', content)
                if m:
                    start_idx = m.start()
                    # Grab the next 2000 chars, the LLM can handle trailing garbage
                    code_chunk = content[start_idx:start_idx + 2000]
                    return f"Usage hint: def result = {func_name}(...)\n\nCode:\n{code_chunk}"
            except Exception:
                continue
            
    # Fallback to code_entries if not found in functions/ (e.g. local overrides)
    for entry in code_entries:
        content = entry.get("content", "")
        m = re.search(rf'\bdef\s+{re.escape(func_name)}\s*\(', content)
        if m:
            start_idx = m.start()
            code_chunk = content[start_idx:start_idx + 2000]
            return f"Usage hint: def result = {func_name}(...)\n\nCode:\n{code_chunk}"
            
    return f"// Function '{func_name}' not found in source files"
