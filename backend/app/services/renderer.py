from jinja2 import Template
from langchain_core.messages import AIMessage
from app.services.graph_state import GraphState
from app.utils.rendering import NF_TEMPLATE_AST

def render_nextflow_code(ast) -> str:
    if hasattr(ast, 'model_dump'):
        data = ast.model_dump()
    elif hasattr(ast, 'dict'):
        data = ast.dict()
    else:
        data = ast

    # Ensure keys are present to prevent Jinja2 crashes on incomplete generated data
    data.setdefault('imports', [])
    data.setdefault('globals', [])
    data.setdefault('inline_processes', [])
    data.setdefault('sub_workflows', [])
    if 'entrypoint' not in data:
        data['entrypoint'] = {'body_code': '// Missing entrypoint in generated AST'}
    elif not isinstance(data['entrypoint'], dict):
        data['entrypoint'] = {'body_code': str(data['entrypoint'])}

    # Render Template
    t = Template(NF_TEMPLATE_AST)
    rendered = t.render(**data)
    
    # Clean up excess whitespace
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
        
    return rendered.strip()

def renderer_node(state: GraphState):
    print("--- [NODE] RENDERER ---")

    if state.get("error"): return {}

    raw_ast = state.get('ast_json', {})
    messages_update = []
    
    try:
        nf_code = render_nextflow_code(raw_ast)
        
        # Inject simple warning comment at the bottom if validation error persisted
        validation_error = state.get('validation_error')
        if validation_error:
            warning = f"// ⚠️ WARNING: Pipeline generation failed strict DSL2 validation.\n// The code above is potentially broken or incomplete and was output as a best-effort draft.\n\n"
            nf_code = warning + nf_code
            messages_update.append(AIMessage(content="⚠️ **Generation Warning**: I reached the maximum number of attempts trying to generate a perfectly valid pipeline. I have output the current draft as a **best effort**, but please note that the generated code **might have errors or missing components** based on the strict DSL2 rules."))
            
    except Exception as e:
        print(f"💥 NEXTFLOW RENDERER CRASH: {e}")
        return {"error": f"Nextflow Code Generation Failed: {str(e)}"}

    result = {
        "nextflow_code": nf_code
    }
    if messages_update:
        result["messages"] = messages_update
    return result

def render_mermaid_from_json(data) -> str:
    lines = ["flowchart TD"]
    
    subgraphs = {}
    unassigned = []
    
    for node in data.nodes:
        if node.subgraph:
            sg = node.subgraph.strip()
            if sg not in subgraphs: 
                subgraphs[sg] = []
            subgraphs[sg].append(node)
        else:
            unassigned.append(node)

    def draw_node(n):
        label = n.label 
        if n.shape == 'input': return f'    {n.id}(["{label}"])'
        elif n.shape == 'operator': return f'    {n.id}{{"{label}"}}'
        elif n.shape == 'output': return f'    {n.id}[("{label}")]'
        elif n.shape == 'global': return f'    {n.id}("{label}")'
        else: return f'    {n.id}["{label}"]' 

    for sg_name, nodes in subgraphs.items():
        clean_sg = sg_name.replace(" ", "_").replace(".", "_")
        
        lines.append(f'    subgraph sg_{clean_sg} ["{sg_name}"]')
        
        for n in nodes:
            lines.append(draw_node(n))
        lines.append("    end")

    for n in unassigned:
        lines.append(draw_node(n))

    for e in data.edges:
        if e.label and e.label.strip():
            lines.append(f'    {e.source} -->|"{e.label}"| {e.target}')
        else:
            lines.append(f'    {e.source} --> {e.target}')

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# DETERMINISTIC MERMAID RENDERER (from AST, no LLM)
# ──────────────────────────────────────────────────────────────────────────────
import re

def render_mermaid_from_ast(ast_json: dict) -> str:
    """
    Generate a Mermaid flowchart directly from the AST JSON.
    Deterministic — same AST always produces the same diagram.
    """
    lines = ["flowchart TD"]
    nodes = []   # (id, label, shape, subgraph)
    edges = []   # (source, target, label)
    node_ids = set()

    # Maps: scope -> {var_name: node_id} for tracking data flow
    scope_vars = {}
    # Maps: sub-workflow name -> list of take_channel node_ids (in order)
    sw_take_ids = {}
    # Maps: sub-workflow name -> first process node_id
    sw_first_proc = {}

    def _add_node(nid, label, shape, subgraph=None):
        if nid not in node_ids:
            nodes.append((nid, label, shape, subgraph))
            node_ids.add(nid)

    def _safe_id(name):
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    def _resolve_var(scope, var_name):
        """Find the node_id for a variable, checking scope then globals."""
        v = scope_vars.get(scope, {}).get(var_name)
        if v:
            return v
        # Check if it's a known node_id directly
        if var_name in node_ids:
            return var_name
        return None

    def _connect_arg(scope, arg_str, target_id):
        """Parse an argument and create an edge from the source to target."""
        arg_str = arg_str.strip()
        if not arg_str:
            return

        # Check if arg is a get* function call (e.g., getSingleInput())
        get_match = re.match(r'(get\w+)\(', arg_str)
        if get_match:
            func_id = _safe_id(f"{scope}_{get_match.group(1)}")
            if func_id not in node_ids:
                display = arg_str.split(')')[0] + ')'
                _add_node(func_id, display, "input", scope)
            edges.append((func_id, target_id, ""))
            return

        # Get the base variable name (before . or [ or ()
        base_var = re.split(r'[.\[\(]', arg_str)[0].strip()
        source = _resolve_var(scope, base_var)
        if source:
            # Use the property access as label if present
            label = arg_str if '.' in arg_str else ""
            edges.append((source, target_id, label))

    def _parse_body(body_code, scope_name):
        """Parse body_code to extract process calls and data flow."""
        if not body_code:
            return

        scope_vars.setdefault(scope_name, {})
        first_proc_set = False

        for line in body_code.split('\n'):
            line = line.strip()
            if not line or line.startswith('//'):
                continue

            # Match: var = step_xxx(args) or module_xxx(args) or wf_xxx(args)
            assign_call = re.match(
                r'([a-zA-Z_]\w*)\s*=\s*((?:step_|module_|wf_)[a-zA-Z0-9_]+)\s*\(([^)]*)\)',
                line
            )
            if assign_call:
                var_name = assign_call.group(1)
                proc_name = assign_call.group(2)
                args = assign_call.group(3).strip()
                proc_id = _safe_id(f"{scope_name}_{proc_name}")

                _add_node(proc_id, proc_name, "process", scope_name)
                scope_vars[scope_name][var_name] = proc_id

                if not first_proc_set:
                    sw_first_proc[scope_name] = proc_id
                    first_proc_set = True

                # Connect arguments
                for arg in _split_args(args):
                    _connect_arg(scope_name, arg, proc_id)
                continue

            # Match: step_xxx(args) — void call, no assignment
            void_call = re.match(
                r'((?:step_|module_|wf_)[a-zA-Z0-9_]+)\s*\(([^)]*)\)',
                line
            )
            if void_call:
                proc_name = void_call.group(1)
                args = void_call.group(2).strip()
                proc_id = _safe_id(f"{scope_name}_{proc_name}")

                _add_node(proc_id, proc_name, "process", scope_name)

                if not first_proc_set:
                    sw_first_proc[scope_name] = proc_id
                    first_proc_set = True

                for arg in _split_args(args):
                    _connect_arg(scope_name, arg, proc_id)
                continue

            # Match: var = expression (channel operations, .map, etc.)
            channel_assign = re.match(r'([a-zA-Z_]\w*)\s*=\s*(.+)', line)
            if channel_assign:
                var_name = channel_assign.group(1)
                rhs = channel_assign.group(2).strip()
                # Try to trace source
                base_var = re.split(r'[.\[\(]', rhs)[0].strip()
                source = _resolve_var(scope_name, base_var)
                var_id = _safe_id(f"{scope_name}_{var_name}")
                scope_vars[scope_name][var_name] = source if source else var_id

    def _split_args(args_str):
        """Split function arguments, respecting nested braces/parens."""
        if not args_str.strip():
            return []
        depth = 0
        current = []
        parts = []
        for ch in args_str:
            if ch in '({[':
                depth += 1
                current.append(ch)
            elif ch in ')}]':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
        return [p for p in parts if p]

    # --- Globals ---
    for g in ast_json.get('globals', []):
        gid = _safe_id(f"global_{g.get('name', 'unknown')}")
        _add_node(gid, f"{g.get('name', '?')}", "global")

    # --- Sub-workflows: register take channels first ---
    for sw in ast_json.get('sub_workflows', []):
        sw_name = sw.get('name', 'unknown')
        scope_vars.setdefault(sw_name, {})
        take_ids = []
        for ch in sw.get('take_channels', []):
            ch_id = _safe_id(f"{sw_name}_in_{ch}")
            _add_node(ch_id, ch, "input", sw_name)
            scope_vars[sw_name][ch] = ch_id
            take_ids.append(ch_id)
        sw_take_ids[sw_name] = take_ids

    # --- Sub-workflows: parse bodies ---
    for sw in ast_json.get('sub_workflows', []):
        sw_name = sw.get('name', 'unknown')
        _parse_body(sw.get('body_code', ''), sw_name)

        # Connect take inputs to first process if no edges from them yet
        connected_sources = {e[0] for e in edges}
        first_proc = sw_first_proc.get(sw_name)
        if first_proc:
            for take_id in sw_take_ids.get(sw_name, []):
                if take_id not in connected_sources:
                    edges.append((take_id, first_proc, ""))

        # Output channels
        for em in sw.get('emit_channels', []):
            em_name = em.split('=')[0].strip() if '=' in em else em.strip()
            em_id = _safe_id(f"{sw_name}_out_{em_name}")
            _add_node(em_id, em_name, "output", sw_name)
            # Connect last assignment to output
            rhs = em.split('=')[1].strip() if '=' in em else em_name
            base = re.split(r'[.\[]', rhs)[0].strip()
            source = _resolve_var(sw_name, base)
            if source:
                edges.append((source, em_id, ""))

    # --- Entrypoint ---
    ep = ast_json.get('entrypoint', {})
    ep_body = ep.get('body_code', '')

    # Add input function nodes (getSingleInput, getReference, etc.)
    input_funcs = re.findall(r'(\w+)\s*=\s*(get\w+)\(([^)]*)\)', ep_body)
    for var_name, func_name, func_args in input_funcs:
        func_id = _safe_id(f"entrypoint_{func_name}")
        display = f"{func_name}({func_args})" if func_args else f"{func_name}()"
        _add_node(func_id, display, "input", "entrypoint")
        scope_vars.setdefault("entrypoint", {})[var_name] = func_id

    # Also catch inline getSingleInput() etc. in sub-workflow calls
    inline_inputs = re.findall(r'(get\w+)\(([^)]*)\)', ep_body)
    for func_name, func_args in inline_inputs:
        func_id = _safe_id(f"entrypoint_{func_name}")
        if func_id not in node_ids:
            display = f"{func_name}({func_args})" if func_args else f"{func_name}()"
            _add_node(func_id, display, "input", "entrypoint")

    _parse_body(ep_body, 'entrypoint')

    # --- Connect entrypoint calls to sub-workflow take channels ---
    for sw in ast_json.get('sub_workflows', []):
        sw_name = sw.get('name', 'unknown')
        # Find the call in entrypoint: sw_name(arg1, arg2, ...)
        call_match = re.search(
            rf'{re.escape(sw_name)}\s*\(([^)]*)\)',
            ep_body
        )
        if call_match:
            args = _split_args(call_match.group(1))
            take_ids = sw_take_ids.get(sw_name, [])
            for i, arg in enumerate(args):
                if i < len(take_ids):
                    # Find source node for the argument
                    base_var = re.split(r'[.\[\(]', arg.strip())[0].strip()
                    # Check if it's a get* function
                    get_match = re.match(r'(get\w+)\(', arg.strip())
                    if get_match:
                        source = _safe_id(f"entrypoint_{get_match.group(1)}")
                    else:
                        source = _resolve_var('entrypoint', base_var)
                    if source:
                        edges.append((source, take_ids[i], arg.strip()))

    # --- Render ---
    by_subgraph = {}
    no_subgraph = []
    for nid, label, shape, sg in nodes:
        if sg:
            by_subgraph.setdefault(sg, []).append((nid, label, shape))
        else:
            no_subgraph.append((nid, label, shape))

    def _draw(nid, label, shape):
        label = label.replace('"', "'")
        if shape == 'input':
            return f'    {nid}(["{label}"])'
        elif shape == 'output':
            return f'    {nid}[("{label}")]'
        elif shape == 'global':
            return f'    {nid}("{label}")'
        else:
            return f'    {nid}["{label}"]'

    # Entrypoint first, then sub-workflows
    if 'entrypoint' in by_subgraph:
        lines.append('    subgraph sg_entrypoint ["entrypoint"]')
        for nid, label, shape in by_subgraph.pop('entrypoint'):
            lines.append(_draw(nid, label, shape))
        lines.append('    end')

    for sg_name, sg_nodes in by_subgraph.items():
        clean = _safe_id(sg_name)
        lines.append(f'    subgraph sg_{clean} ["{sg_name}"]')
        for nid, label, shape in sg_nodes:
            lines.append(_draw(nid, label, shape))
        lines.append('    end')

    for nid, label, shape in no_subgraph:
        lines.append(_draw(nid, label, shape))

    # Edges — deduplicated
    seen_edges = set()
    for src, tgt, label in edges:
        if src == tgt:
            continue
        key = (src, tgt)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        label = label.replace('"', "'")
        if label:
            lines.append(f'    {src} -->|"{label}"| {tgt}')
        else:
            lines.append(f'    {src} --> {tgt}')

    return "\n".join(lines)