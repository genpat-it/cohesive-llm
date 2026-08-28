"""
Architect Tools — tools for the architect to verify code logic during generation.

These tools let the architect look up component source code, validate
channel connections, and check AST structure against DSL2 rules.
Token-efficient: only used on retries, not on first attempt.
"""

import json
import re

from langchain.tools import tool, ToolRuntime

from core.services.consultant_tools import _parse_nextflow_channels
from core.catalog_registry import get_registry


@tool
def validate_body_code(code_snippet: str, workflow_name: str) -> str:  # noqa: C901
    """Validate a body_code snippet for common Nextflow DSL2 errors.
    Use this to check a piece of body_code BEFORE including it in the AST.

    Checks for:
    - Forbidden keywords (take:, main:, emit:, workflow wrapper)
    - Active channels in sub-workflows (get*() functions)
    - Void tool assignment errors
    - Inline channel join errors (.cross/.combine inside process args)
    - Framework component existence

    Args:
        code_snippet: The body_code string to validate
        workflow_name: Name of the workflow this code belongs to (use 'entrypoint' for the main workflow)
    """
    issues = []
    warnings = []
    _is_entrypoint = (workflow_name == "entrypoint")

    # Check for forbidden keywords that the template handles
    forbidden_kw_list = [
        ("take:", "Remove it from body_code and put channels in take_channels list."),
        ("main:", "Remove it from body_code. The rendering template adds it automatically."),
        ("emit:", "Remove it from body_code and put channels in emit_channels list."),
    ]
    for kw, fix in forbidden_kw_list:
        if re.search(rf'^\s*{re.escape(kw)}\s*$', code_snippet, re.MULTILINE):
            issues.append("FORBIDDEN KEYWORD '" + kw + "' found in body_code. " + fix)

    # Check for workflow wrapper
    if re.search(r'^\s*workflow\s+\w+\s*\{', code_snippet, re.MULTILINE):
        issues.append(
            "body_code contains a 'workflow name' wrapper. "
            "The template handles this. body_code should only contain the logic INSIDE the workflow block."
        )

    # Check for active channels in sub-workflows
    from core.catalog_registry import get_registry
    registry = get_registry()
    
    # Check if .out.X property accesses match the actual component signature
    out_accesses = re.findall(r'\b([a-zA-Z0-9_]+)\.out\.([a-zA-Z0-9_]+)', code_snippet)
    for comp, channel in out_accesses:
        if registry.component_exists(comp):
            comp_info = _get_component_channels_internal(comp)
            if "error" not in comp_info:
                valid_emits = comp_info.get("emits", [])
                if channel not in valid_emits:
                    issues.append(f"HALLUCINATION: Component '{comp}' does not emit '{channel}'. Valid emits are: {valid_emits}")

    if not _is_entrypoint and registry.is_initialized:
        exported_functions = registry.function_exports
        active_calls = []
        for match in re.finditer(r'\b([a-zA-Z0-9_]+)\s*\(', code_snippet):
            func_name = match.group(1)
            if func_name in exported_functions:
                active_calls.append(func_name)
        if active_calls:
            issues.append(
                f"Active channel instantiation {active_calls} found inside sub-workflow '{workflow_name}'. "
                "Active channels must ONLY be instantiated in the entrypoint."
            )

    # Check for void tool assignments
    from core.models.ast_structure import _is_void_tool
    void_assignments = re.finditer(
        r'\b[a-zA-Z0-9_]+\s*=\s*([a-zA-Z0-9_]+)\s*\(',
        code_snippet
    )
    for m in void_assignments:
        proc_name = m.group(1)
        if _is_void_tool(proc_name):
            issues.append(
                "Void tool '" + proc_name + "' is assigned to a variable. "
                "Void tools have no output. Call it directly without assignment."
            )

    # Check for inline channel joins in process arguments
    proc_calls = re.finditer(
        r'\b[a-zA-Z0-9_]+\s*\(([^)]+)\)',
        code_snippet
    )
    for m in proc_calls:
        args = m.group(1)
        if '.cross' in args or '.combine' in args:
            issues.append(
                "Inline channel join in process arguments: '" + m.group(0) + "'. "
                "Perform .cross()/.combine() on a separate line, shape with .map/.multiMap, "
                "assign to a variable, then pass the variable."
            )

    # Check for .set on process calls
    if re.search(r'\b[a-zA-Z0-9_]+\s*\([^)]*\)\s*\.set\s*\{', code_snippet):
        issues.append(
            "'.set' appended to a process call. "
            "Use direct assignment instead: 'var = process(...)'"
        )

    # Check for framework components existence (via CatalogRegistry)
    from core.catalog_registry import get_registry
    registry = get_registry()
    referenced = set(match.group(1) for match in re.finditer(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(', code_snippet))

    if registry.is_initialized and registry.valid_components:
        builtins = {
            'file', 'tuple', 'val', 'path', 'Channel', 'fromPath', 'of', 'println',
            'log', 'error', 'exit', 'get', 'set', 'map', 'cross', 'combine', 'mix',
            'join', 'branch', 'multiMap', 'groupTuple', 'flatten', 'collect',
            'splitCsv', 'splitText', 'splitFasta', 'splitFastq', 'env', 'print',
            'workflow', 'process', 'def', 'if', 'else', 'for', 'while', 'switch',
            'case', 'return', 'String', 'Integer', 'Boolean'
        }

        plugin_helpers = set()
        exported_functions = set()
        try:
            from core.plugin_loader import get_active_plugin
            plugin_helpers = set(get_active_plugin().helper_imports.keys())
            exported_functions = registry.function_exports
        except Exception:
            pass

        filtered_referenced = {
            r for r in referenced 
            if not (r in builtins or r in plugin_helpers or r in exported_functions)
        }

        invalid = filtered_referenced - registry.valid_components
        if invalid:
            issues.append("Unknown components or functions not in catalog: " + str(sorted(invalid)))

    return json.dumps({
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "checked_workflow": workflow_name,
    }, indent=2)


@tool
def verify_dataflow_plan(entrypoint_instantiations: list[str], sub_workflows: list[dict]) -> str:
    """Use this tool to incrementally test your dataflow plan before finalizing your reasoning.
    
    Args:
        entrypoint_instantiations: List of variable instantiations (e.g., ["input = my_active_channel()"])
        sub_workflows: List of dictionaries with 'name', 'takes' (list of strings), and 'emits' (list of strings).
    """
    warnings = []
    
    # Extract defined variables from entrypoint instantiations
    defined_vars = set()
    for inst in entrypoint_instantiations:
        if "=" in inst:
            lhs = inst.split("=")[0].strip()
            # Handle tuple unpacking: val1, val2 = ...
            if "," in lhs:
                for v in lhs.split(","):
                    defined_vars.add(v.strip())
            else:
                defined_vars.add(lhs)

    # Check subworkflow takes against defined vars (warning if not defined)
    for sw in sub_workflows:
        sw_name = sw.get("name", "unknown")
        takes = sw.get("takes", [])
        for t in takes:
            if t not in defined_vars and not t.startswith("params."):
                warnings.append(
                    f"Warning: Subworkflow '{sw_name}' takes '{t}', but '{t}' is never instantiated in the entrypoint. "
                    "Did you forget to add 'var = getSingleInput()' or similar?"
                )

    if not warnings:
        return json.dumps({"status": "SUCCESS", "message": "DataFlow Plan looks logically consistent."}, indent=2)
    return json.dumps({"status": "WARNINGS FOUND", "warnings": warnings}, indent=2)


def _get_component_channels_internal(component_name: str) -> dict:
    """Internal (non-tool) helper: read take/emit channels from the registry.
    Used by validate_body_code to avoid a tool-inside-tool call.
    """
    from core.loader import data_loader
    from core.catalog_registry import get_registry

    registry = get_registry()
    if component_name not in registry.valid_components:
        return {"error": f"Component '{component_name}' not found in catalog."}

    code = data_loader.code_db.get(component_name, "")
    parsed = _parse_nextflow_channels(code)

    if not parsed["takes"]:
        meta = data_loader.comp_db.get(component_name) or data_loader.tmpl_db.get(component_name)
        if meta:
            parsed["takes"] = meta.get("input_channels", meta.get("input_types", [])) or []

    if not parsed["emits"]:
        meta = data_loader.comp_db.get(component_name) or data_loader.tmpl_db.get(component_name)
        if meta:
            parsed["emits"] = meta.get("output_channels", meta.get("out", [])) or []

    return {"takes": parsed["takes"], "emits": parsed["emits"]}


@tool
def check_component_channels(component_name: str, runtime: ToolRuntime) -> str:
    """Check exactly what channels a specific component takes and emits.
    Use this to pull specific constraints into your working memory to avoid hallucinations.

    Args:
        component_name: The name of the component (e.g., 'step_process_name')
    """
    res = _get_component_channels_internal(component_name)
    if "error" in res:
        return json.dumps(res, indent=2)
        
    emits = res.get("emits", [])

    from core.services.ast_compiler import _is_void_tool
    if _is_void_tool(component_name):
        return json.dumps({
            "component": component_name,
            "takes": res.get("takes", []) if res.get("takes") else ["(NONE)"],
            "emits": ["(VOID TOOL - DOES NOT EMIT)"]
        }, indent=2)

    return json.dumps({
        "component": component_name,
        "takes": res.get("takes", []) if res.get("takes") else ["(NONE)"],
        "emits": [f"{component_name}.out.{e}" for e in emits] if emits else ["(NONE)"]
    }, indent=2)


from core.services.consultant_tools import (
    search_helper_functions,
    search_design_patterns,
    auto_complete_pipeline_dag,
    search_components,
    find_dataflow_path,
    get_component_neighbors,
    explain_component,
)

ARCHITECT_TOOLS = [
    search_components,
    auto_complete_pipeline_dag,
    validate_body_code,
    verify_dataflow_plan,
    check_component_channels,
    search_helper_functions,
    search_design_patterns,
    find_dataflow_path,
    get_component_neighbors,
    explain_component,
]

