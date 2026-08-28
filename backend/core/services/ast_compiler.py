import re
from typing import Any

from core.catalog_registry import get_registry


def _is_void_tool(name: str) -> bool:
    return get_registry().is_void_tool(name)

def heal_workflow_body(body: str) -> tuple[str, list[str]]:
    """
    Cleans and repairs the groovy body of a workflow.
    Returns (cleaned_body, extracted_emit_channels).
    """
    if not isinstance(body, str):
        return body, []

    extracted_emits = []

    # Extract inline emit: blocks
    emit_match = re.search(r'^\s*emit:\s*([\s\S]*)$', body, flags=re.MULTILINE)
    if emit_match:
        emit_block = emit_match.group(1)
        # Match both `key = value` AND `key` standalone
        lines = emit_block.strip().split('\n')
        for line in lines:
            line = line.split('//')[0].strip() # remove comments
            if not line or line == '}':
                continue
            extracted_emits.append(line)

    # Strip workflow/take/main/emit wrappers
    match = re.search(r'^\s*workflow\s+[_a-zA-Z0-9]*\s*\{(.*)\}\s*$', body, re.DOTALL)
    if match:
        body = match.group(1)

    body = re.sub(r'^\s*take:.*?(?=^\s*main:|^\s*emit:|\Z)', '', body, flags=re.MULTILINE | re.DOTALL)
    body = re.sub(r'^\s*emit:[\s\S]*', '', body, flags=re.MULTILINE)
    body = re.sub(r'^\s*main:\s*', '', body, flags=re.MULTILINE)

    # Strip dsl=2 header
    body = re.sub(r'^\s*nextflow\.enable\.dsl\s*=\s*2\s*\n?', '', body, flags=re.MULTILINE)

    # Strip void tool assignments and precisely track variables
    void_vars = set()
    def _strip_void_assignment(m: Any) -> Any:
        full_match = m.group(0)
        var_name = m.group(2)
        proc_name = m.group(3)
        if _is_void_tool(proc_name):
            void_vars.add(var_name)
            return re.sub(r'^\s*[a-zA-Z0-9_]+\s*=\s*', '', full_match)
        return full_match

    body = re.sub(
        r'^(\s*([a-zA-Z0-9_]+)\s*=\s*)([a-zA-Z0-9_]+)\s*\(',
        _strip_void_assignment,
        body,
        flags=re.MULTILINE
    )

    # Deterministic AST Operator Auto-Healing for Collection Cardinality
    try:
        from core.services.knowledge_graph import kg
        call_matches = re.finditer(r'\b([a-zA-Z0-9_]+)\s*\(([^)]+)\)', body)
        for m in list(call_matches):
            proc_name = m.group(1)
            args_str = m.group(2)
            coll_info = kg.detect_collection_cardinality(proc_name)
            if coll_info and ".collect(" not in args_str and ".toList(" not in args_str:
                args = [a.strip() for a in args_str.split(',')]
                healed_args = [f"{a}.collect()" if a and not a.startswith("'") and not a.startswith('"') and not a.endswith(".collect()") else a for a in args]
                old_call = m.group(0)
                new_call = f"{proc_name}({', '.join(healed_args)})"
                body = body.replace(old_call, new_call)
    except Exception:
        pass

    return body.strip(), extracted_emits

def generate_imports_for_code(all_code: str, defined_sws: set) -> dict:
    """
    Parses code for function calls and maps them to their module paths.
    """
    pattern = re.compile(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(')
    used_callables = set(match.group(1) for match in pattern.finditer(all_code))
    used_callables = used_callables - defined_sws

    import_map = {}
    registry = get_registry()

    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        helper_imports = plugin.helper_imports
    except Exception:
        helper_imports = {}

    for func in used_callables:
        if func in helper_imports:
            path = helper_imports[func]
        elif registry.get_function_import_path(func):
            path = registry.get_function_import_path(func)
        else:
            path = registry.get_import_path(func)

        if not path:
            continue

        if path not in import_map:
            import_map[path] = []
        import_map[path].append(func)

    return import_map

def validate_framework_components(all_code: str, defined_sws: set, defined_inline: set) -> list[tuple[str, list[str]]]:
    """
    Ensures referenced tools/processes exist in the catalog.
    Returns a list of invalid components and suggestions.
    """
    registry = get_registry()
    if not registry.is_initialized or not registry.valid_components:
        return []

    # Strip comments and string literals before scanning for process calls
    clean_code = re.sub(r'//.*', '', all_code)
    clean_code = re.sub(r'/\*.*?\*/', '', clean_code, flags=re.DOTALL)
    clean_code = re.sub(r"'[^']*'|\"[^\"]*\"", "''", clean_code)

    pattern = re.compile(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(')
    referenced = set(match.group(1) for match in pattern.finditer(clean_code))

    # Extract any workflows, processes, or variables defined in the code
    code_defined_sws = set(re.findall(r'\bworkflow\s+([a-zA-Z0-9_]+)\s*\{', clean_code))
    code_defined_procs = set(re.findall(r'\bprocess\s+([a-zA-Z0-9_]+)\s*\{', clean_code))
    local_assignments = set(re.findall(r'\b([a-zA-Z0-9_]+)\s*=(?!=)', clean_code))
    sets = set(re.findall(r'\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}', clean_code))
    tuple_assigns = set()
    for group in re.findall(r'(?:def\s+)?\(([^)]+)\)\s*=', clean_code):
        for v in group.split(','):
            tuple_assigns.add(v.strip())

    all_defined = defined_sws | defined_inline | code_defined_sws | code_defined_procs | local_assignments | sets | tuple_assigns

    built_ins = {
        'file', 'tuple', 'val', 'path', 'Channel', 'fromPath', 'of', 'fromFilePairs', 'println',
        'log', 'error', 'exit', 'get', 'set', 'map', 'flatMap', 'cross', 'combine', 'mix',
        'concat', 'join', 'merge', 'branch', 'multiMap', 'groupTuple', 'flatten', 'transpose',
        'collect', 'collectFile', 'buffer', 'collate', 'count', 'distinct', 'unique', 'first',
        'last', 'ifEmpty', 'reduce', 'filter', 'view', 'dump', 'take', 'until',
        'splitCsv', 'splitText', 'splitFasta', 'splitFastq', 'env', 'print',
        'workflow', 'process', 'def', 'if', 'else', 'for', 'while', 'switch',
        'case', 'return', 'String', 'Integer', 'Boolean', 'param', 'params', 'optional',
        'extractKey', 'getSingleInput', 'getEmpty', 'getSchema', 'getFastqPair', 'void', 'none'
    }

    to_check = referenced - all_defined - built_ins

    plugin_helpers = set()
    try:
        from core.plugin_loader import get_active_plugin
        plugin_helpers = set(get_active_plugin().helper_imports.keys())
    except Exception:
        pass

    try:
        from core.services.knowledge_graph import kg
        if hasattr(kg, '_helper_closures'):
            plugin_helpers.update(kg._helper_closures)
    except Exception:
        pass

    invalid = []
    for item in to_check:
        if item in plugin_helpers or registry.get_function_import_path(item):
            continue

        if item.startswith('get') or item.startswith('param') or item == 'my_active_channel':
            continue

        # Only flag if it matches component naming prefixes or fails exact lookup
        if not registry.component_exists(item):
            # If not a component prefix and doesn't look like a component, skip false positive
            if not item.startswith(('step_', 'multi_', 'module_', 'process_')):
                continue
            matches = registry.get_close_matches(item, cutoff=0.8)
            invalid.append((item, matches))

    return invalid

def validate_undefined_variables(body_code: str, defined_vars: set) -> list[str]:
    """
    Scans the body code to ensure that base variables used in expressions were actually defined.
    Returns a list of undefined variables found.
    """
    if not body_code:
        return []

    # Strip single-line comments, block comments, and string literals
    clean_body = re.sub(r'//.*', '', body_code)
    clean_body = re.sub(r'/\*.*?\*/', '', clean_body, flags=re.DOTALL)
    clean_body = re.sub(r"'[^']*'|\"[^\"]*\"", "''", clean_body)

    local_vars = set(defined_vars)

    # Extract assignments: var = ...
    assignments = re.findall(r'\b([a-zA-Z0-9_]+)\s*=(?!=)', clean_body)
    local_vars.update(assignments)

    # Extract .set { var }
    sets = re.findall(r'\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}', clean_body)
    local_vars.update(sets)

    # Extract .branch { name: ...; name: ... } — creates named output channels
    branch_blocks = re.findall(r'\.branch\s*\{([^}]+)\}', clean_body, re.DOTALL)
    for block in branch_blocks:
        branch_names = re.findall(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', block)
        local_vars.update(branch_names)

    # Extract .multiMap { name: ...; name: ... } — creates named output channels
    multimap_blocks = re.findall(r'\.multiMap\s*\{([^}]+)\}', clean_body, re.DOTALL)
    for block in multimap_blocks:
        multimap_names = re.findall(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', block)
        local_vars.update(multimap_names)

    # Extract destructuring tuple assignment: (var1, var2) = ... or def (v1, v2) = ...
    tuple_assigns = re.findall(r'(?:def\s+)?\(([^)]+)\)\s*=', clean_body)
    for group in tuple_assigns:
        for v in group.split(','):
            v = v.strip()
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
                local_vars.update([v])

    # Extract component.out.channel references — the result var is the LHS of the full expression
    # e.g. step_foo(x) creates step_foo.out which is valid
    component_calls = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)', clean_body, re.DOTALL)
    local_vars.update(component_calls)
    for c in component_calls:
        local_vars.add(f"{c}_out")
        local_vars.add(f"{c}.out")
        if '__' in c:
            suffix = c.split('__')[-1]
            local_vars.add(suffix)
            local_vars.add(f"{suffix}_out")
            local_vars.add(f"{suffix}.out")
        if c.startswith(('step_', 'multi_', 'module_')):
            stem = c.split('_', 2)[-1]
            local_vars.add(stem)
            local_vars.add(f"{stem}_out")

    # Dynamically register all declared AST output channels from catalog_db or plugin components
    try:
        from pathlib import Path
        import json
        from core.loader import data_loader
        db = dict(getattr(data_loader, "catalog_db", {}) or {})
        if not db:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            if plugin and getattr(plugin, "catalog_components_path", None) and Path(plugin.catalog_components_path).exists():
                try:
                    raw_data = json.loads(Path(plugin.catalog_components_path).read_text(encoding="utf-8"))
                    raw_comps = raw_data.get("components", raw_data) if isinstance(raw_data, dict) else raw_data
                    if isinstance(raw_comps, list):
                        db.update({c.get("id") or c.get("tool"): c for c in raw_comps if isinstance(c, dict)})
                    elif isinstance(raw_comps, dict):
                        db.update(raw_comps)
                except Exception:
                    pass
        for comp_name, comp_data in db.items():
            out_chs = []
            if isinstance(comp_data, dict):
                out_chs = comp_data.get("output_channels") or comp_data.get("out") or []
            else:
                out_chs = getattr(comp_data, "output_channels", None) or getattr(comp_data, "out", None) or []
            for emit_ch in (out_chs or []):
                if emit_ch and emit_ch not in ("none", "void", ""):
                    local_vars.add(emit_ch)
                    local_vars.add(f"{comp_name}.out.{emit_ch}")
                    if '__' in comp_name:
                        sfx = comp_name.split('__')[-1]
                        local_vars.add(f"{sfx}.out.{emit_ch}")
    except Exception:
        pass

    used_vars = set()

    # Find variables used before a dot: var.method()
    dot_usages = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.', clean_body)
    used_vars.update(dot_usages)

    # Find variables used as arguments in process calls: process(var1, var2.prop)
    arg_usages = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\s*\(([^)]+)\)', clean_body, re.DOTALL)
    for arg_str in arg_usages:
        args = arg_str.split(',')
        for arg in args:
            arg = arg.strip()
            if not arg: continue
            base_var = re.split(r'[\.\[]', arg)[0].strip()
            if base_var.startswith("'") or base_var.startswith('"'):
                continue
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', base_var):
                used_vars.add(base_var)
                
    builtins = {
        'true', 'false', 'null', 'it', 'Channel', 'file', 'param', 'params', 'get', 'def',
        'workflow', 'process', 'log', 'error', 'exit', 'println', 'env', 'out', 'optional',
        'extractKey', 'getSingleInput', 'getSchema', 'getEmpty', 'getFastqPair', 'void', 'none'
    }

    try:
        from core.catalog_registry import get_registry
        reg = get_registry()
        local_vars.update(reg.valid_components)
        for c_id in reg.valid_components:
            # Add short names: step_4AN_genes__prokka -> prokka, step_4AN_genes
            if '__' in c_id:
                local_vars.add(c_id.split('__')[-1])
            if c_id.startswith(('step_', 'multi_', 'module_')):
                local_vars.add(c_id.split('_', 2)[-1])
        local_vars.update(reg.exported_functions.keys())
    except Exception:
        pass

    try:
        from core.plugin_loader import get_active_plugin
        local_vars.update(get_active_plugin().helper_imports.keys())
    except Exception:
        pass

    undefined = []
    for var in used_vars:
        if var not in local_vars and var not in builtins:
            undefined.append(var)

    return sorted(list(set(undefined)))


def validate_ast_with_knowledge_graph(ast_data: dict, store: Any = None) -> list[str]:
    """Consult the Knowledge Graph to validate Nextflow DSL2 operator semantics,
    cardinalities, key extractors, and channel arities across the AST.
    Returns a list of structural logic error messages, or empty list if valid.
    """
    errors = []
    try:
        from core.services.knowledge_graph import kg
        if store and not kg.is_built:
            kg.build_nx_graph(store)

        if not kg.is_built:
            return errors

        all_bodies = []
        ep = ast_data.get('entrypoint', {})
        if ep:
            all_bodies.append(('entrypoint', ep.get('body_code', '') if isinstance(ep, dict) else str(ep)))
        for sw in ast_data.get('sub_workflows', []):
            all_bodies.append((sw.get('name', 'subworkflow'), sw.get('body_code', '')))

        for scope_name, body in all_bodies:
            if not body: continue

            # 1. Validate .cross() key closures
            cross_calls = re.finditer(r'\b([a-zA-Z0-9_]+)\.cross\s*\(([^)]*)\)(?:\s*\{([^}]*)\})?', body)
            for cm in cross_calls:
                key_closure = cm.group(3)
                if not key_closure or not key_closure.strip():
                    errors.append(f"In '{scope_name}': .cross() call on '{cm.group(1)}' is missing key selector closure (e.g. '{{ extractKey(it) }}').")

            # 2. Validate .multiMap{} named outputs
            multimap_matches = re.finditer(r'([a-zA-Z0-9_]+)\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}', body)
            for mm in multimap_matches:
                var_name = mm.group(2)
                # If assigned from multiMap, check that downstream uses valid properties
                if '.multiMap' in body:
                    downstream_props = re.findall(rf'\b{re.escape(var_name)}\.([a-zA-Z0-9_]+)', body)
                    for prop in downstream_props:
                        if prop not in ('assembly', 'species', 'reads', 'data', 'raw', 'trimmed', 'reference'):
                            # check if declared in multimap block
                            if f"{prop}:" not in body:
                                errors.append(f"In '{scope_name}': Property '{var_name}.{prop}' used but not declared in .multiMap block.")

            # 3. Validate multi-sample collection cardinality
            proc_calls = re.findall(r'\b([a-zA-Z0-9_]+)\s*\(([^)]*)\)', body)
            for p_name, args_str in proc_calls:
                coll_info = kg.detect_collection_cardinality(p_name, store=store)
                if coll_info and ".collect" not in args_str and ".toList" not in args_str:
                    errors.append(f"In '{scope_name}': Multi-sample component '{p_name}' requires collection aggregation (.collect()) on input.")

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Error in validate_ast_with_knowledge_graph: {e}")

    return errors
