import re
from typing import Any

from jinja2 import Template
from langchain_core.messages import AIMessage

from core.services.graph_state import GraphState
from core.utils.logger import logger
from core.utils.rendering import NF_TEMPLATE_AST


def render_nextflow_code(ast: Any) -> str:
    if hasattr(ast, 'model_dump'):
        data = ast.model_dump()
    elif hasattr(ast, 'dict'):
        data = ast.dict()
    else:
        # Prevent in-place mutation of the GraphState object
        data = dict(ast)

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

def renderer_node(state: GraphState) -> Any:
    logger.info("--- [NODE] RENDERER ---")

    if state.get("error"): return {}

    raw_ast = state.get('ast_json', {})
    messages_update = []

    try:
        nf_code = render_nextflow_code(raw_ast)

        # Inject simple warning comment at the bottom if validation error persisted
        validation_error = state.get('validation_error')
        if validation_error:
            warning = "// ⚠️ WARNING: Pipeline generation failed strict DSL2 validation.\n// The code above is potentially broken or incomplete and was output as a best-effort draft.\n\n"
            nf_code = warning + nf_code
            messages_update.append(AIMessage(content="⚠️ **Generation Warning**: I reached the maximum number of attempts trying to generate a perfectly valid pipeline. I have output the current draft as a **best effort**, but please note that the generated code **might have errors or missing components** based on the strict DSL2 rules."))

    except Exception as e:
        logger.error(f"💥 NEXTFLOW RENDERER CRASH: {e}")
        return {"error": f"Nextflow Code Generation Failed: {e!s}"}

    result = {
        "nextflow_code": nf_code
    }
    
    if raw_ast or state.get("selected_component_ids"):
        try:
            component_ids = state.get("selected_component_ids", [])
            mermaid_code = ""
            if raw_ast and isinstance(raw_ast, dict):
                mermaid_code = render_mermaid_from_ast(raw_ast)
            if (not mermaid_code or "-->" not in mermaid_code) and component_ids:
                from core.services.knowledge_graph import kg
                if kg.is_built:
                    mermaid_code = render_mermaid_from_graph(component_ids, kg)
            result["mermaid_deterministic"] = mermaid_code
        except Exception as e:
            logger.warning(f"Failed to render deterministic mermaid: {e}")

    if messages_update:
        result["messages"] = messages_update
    return result

def render_mermaid_from_json(data: Any) -> str:  # noqa: C901
    lines = [
        "flowchart TD",
        "    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef input fill:#50E3C2,stroke:#36A68D,stroke-width:2px,color:#111,rx:5px,ry:5px;",
        "    classDef output fill:#F5A623,stroke:#C28114,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef operator fill:#9013FE,stroke:#6608B8,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef global fill:#9B9B9B,stroke:#656565,stroke-width:2px,color:#fff,rx:5px,ry:5px;"
    ]

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

    def draw_node(n: Any) -> str:
        label = n.label.replace('"', "'")
        if n.shape == 'input': return f'    {n.id}(["{label}"]):::input'
        if n.shape == 'operator': return f'    {n.id}{{{{"{label}"}}}}:::operator'
        if n.shape == 'output': return f'    {n.id}[("{label}")]:::output'
        if n.shape == 'global': return f'    {n.id}("{label}"):::global'
        return f'    {n.id}["{label}"]:::process'

    for sg_name, nodes in subgraphs.items():
        clean_sg = sg_name.replace(" ", "_").replace(".", "_")
        sg_label = sg_name.replace('"', "'")

        lines.append(f'    subgraph sg_{clean_sg} ["{sg_label}"]')
        lines.append(f'        style sg_{clean_sg} fill:#fdfbfb,stroke:#ebedee,stroke-width:2px,stroke-dasharray: 5 5')

        for n in nodes:
            lines.append(draw_node(n))
        lines.append("    end")

    for n in unassigned:
        lines.append(draw_node(n))

    for e in data.edges:
        if e.label and e.label.strip():
            lbl = e.label.replace('"', "'")
            lines.append(f'    {e.source} -->|"{lbl}"| {e.target}')
        else:
            lines.append(f'    {e.source} --> {e.target}')

    return "\n".join(lines)

class MermaidRenderer:
    """High-fidelity Nextflow DSL2 AST to Mermaid diagram compiler.
    Deconstructs workflows, subworkflows, helper functions, and all operator chains
    (.cross, .multiMap, .branch, .map, .mix, .collect, .combine, .join, .filter, .flatten)
    into clean, visual flowcharts.
    """

    def __init__(self, ast_json: dict) -> None:
        self.ast = ast_json
        self.lines = [
            "flowchart TD",
            "    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
            "    classDef input fill:#50E3C2,stroke:#36A68D,stroke-width:2px,color:#111,rx:5px,ry:5px;",
            "    classDef output fill:#F5A623,stroke:#C28114,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
            "    classDef operator fill:#9013FE,stroke:#6608B8,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
            "    classDef global fill:#9B9B9B,stroke:#656565,stroke-width:2px,color:#fff,rx:5px,ry:5px;"
        ]
        self.nodes = []
        self.edges = []
        self.node_ids = set()
        self.instance_counts = {}
        self.scope_vars = {}        # scope -> {var_name: (node_id, label)}
        self.sw_take_ids = {}       # sw_name -> [(node_id, ch_name)]
        self.known_procs = set()

        for imp in self.ast.get('imports', []):
            for func in imp.get('functions', []):
                self.known_procs.add(func.split(' as ')[0].strip())
        for ip in self.ast.get('inline_processes', []):
            self.known_procs.add(ip.get('name', ''))
        for sw in self.ast.get('sub_workflows', []):
            self.known_procs.add(sw.get('name', 'unknown'))

    def _is_process_call(self, name: str) -> bool:
        if name.startswith(('get', 'param', 'optional', 'extractKey', 'parseMetadata')):
            return False
        if name.startswith(('step_', 'multi_', 'module_', 'process_')):
            return True
        try:
            from core.catalog_registry import get_registry
            reg = get_registry()
            if reg.get_function_import_path(name):
                return False
            if reg.component_exists(name):
                return True
        except Exception:
            pass
        if name in self.known_procs:
            return True
        return False

    def _add_node(self, nid: str, label: str, shape: str, subgraph: str | None = None) -> None:
        if nid not in self.node_ids:
            self.nodes.append((nid, label, shape, subgraph))
            self.node_ids.add(nid)

    def _safe_id(self, name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    def _get_unique_id(self, name: str, scope: str) -> str:
        clean_name = self._safe_id(name)
        key = f"{scope}_{clean_name}"
        count = self.instance_counts.get(key, 0)
        self.instance_counts[key] = count + 1
        return self._safe_id(f"n_{scope}_{clean_name}_{count}")

    def _resolve_var(self, scope: str, var_name: str) -> tuple[str | None, str]:
        var_name = var_name.strip()
        scope_map = self.scope_vars.get(scope, {})
        if var_name in scope_map:
            return scope_map[var_name]

        if '.' in var_name:
            obj_name, prop = var_name.split('.', 1)
            if obj_name in scope_map:
                obj_node, _ = scope_map[obj_name]
                return obj_node, prop
            if var_name in scope_map:
                return scope_map[var_name]

        if var_name in self.node_ids:
            return var_name, ""
        return None, ""

    def _split_args(self, args_str: str) -> list[str]:
        if not args_str.strip():
            return []
        depth, current, parts = 0, [], []
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

    def _tokenize_chain(self, expr: str) -> list[str]:
        tokens, curr, depth = [], [], 0
        for ch in expr:
            if ch in '({[':
                depth += 1
                curr.append(ch)
            elif ch in ')}]':
                depth -= 1
                curr.append(ch)
            elif ch == '.' and depth == 0:
                if curr:
                    tokens.append(''.join(curr).strip())
                    curr = []
            else:
                curr.append(ch)
        if curr:
            tokens.append(''.join(curr).strip())
        return tokens

    def _parse_expression(self, scope: str, expr: str) -> tuple[str | None, str]:  # noqa: C901
        expr = expr.strip()
        if not expr:
            return None, ""

        # Match helper getters like getSingleInput(), getSchema(), param('x'), getEmpty()
        get_match = re.match(r'(get\w+|param|optional)\(([^)]*)\)', expr)
        if get_match:
            f_name, f_args = get_match.groups()
            label = f"{f_name}({f_args})" if f_args else f"{f_name}()"
            f_id = self._get_unique_id(f_name, scope)
            self._add_node(f_id, label, "input", scope)
            return f_id, ""

        # Match process output access like proc(args).out.ch or proc(args).ch
        proc_call_m = re.match(r'^([a-zA-Z0-9_]+)\s*\((.*?)\)(?:\.(?:out\.)?([a-zA-Z0-9_]+))?$', expr, flags=re.DOTALL)
        if proc_call_m and self._is_process_call(proc_call_m.group(1)):
            p_name = proc_call_m.group(1)
            p_args = proc_call_m.group(2)
            out_ch = proc_call_m.group(3) or ""
            p_id = self._get_unique_id(p_name, scope)
            self._add_node(p_id, p_name, "process", scope)
            for arg in self._split_args(p_args):
                a_node, a_lbl = self._parse_expression(scope, arg)
                if a_node:
                    self.edges.append((a_node, p_id, a_lbl))
            return p_id, out_ch

        tokens = self._tokenize_chain(expr)
        if not tokens:
            return None, ""

        base = tokens[0]
        if base.startswith('Channel.'):
            c_id = self._get_unique_id('Channel', scope)
            self._add_node(c_id, base, "input", scope)
            curr_id = c_id
            curr_label = ""
        else:
            resolved_node, resolved_lbl = self._resolve_var(scope, base)
            if resolved_node:
                curr_id = resolved_node
                curr_label = resolved_lbl
            else:
                var_id = self._safe_id(f"var_{scope}_{self._safe_id(base)}")
                self._add_node(var_id, base, "input", scope)
                curr_id = var_id
                curr_label = ""

        for t in tokens[1:]:
            # 1. cross(...) { extractKey(it) }
            cross_m = re.match(r'cross\s*\(([^)]*)\)(?:\s*\{\s*([^}]*)\s*\})?', t)
            if cross_m:
                other_ch = cross_m.group(1).strip()
                key_func = cross_m.group(2) or ""
                op_lbl = f".cross({key_func.strip()})" if key_func else ".cross()"
                op_id = self._get_unique_id('cross', scope)
                self._add_node(op_id, op_lbl, "operator", scope)
                self.edges.append((curr_id, op_id, curr_label))
                if other_ch:
                    for o_arg in self._split_args(other_ch):
                        o_node, o_lbl = self._parse_expression(scope, o_arg)
                        if o_node:
                            self.edges.append((o_node, op_id, o_lbl))
                curr_id = op_id
                curr_label = ""
                continue

            # 2. multiMap { branch1: ... branch2: ... }
            multi_m = re.match(r'multiMap\s*\{\s*(.*?)\s*\}', t, flags=re.DOTALL)
            if multi_m:
                body = multi_m.group(1).strip()
                branches = [b.split(':')[0].strip() for b in body.splitlines() if ':' in b]
                lbl_branches = ', '.join(branches) if branches else '...'
                op_lbl = f".multiMap{{{lbl_branches}}}"
                op_id = self._get_unique_id('multiMap', scope)
                self._add_node(op_id, op_lbl, "operator", scope)
                self.edges.append((curr_id, op_id, curr_label))
                curr_id = op_id
                curr_label = ""
                for b in branches:
                    self.scope_vars.setdefault(scope, {})[f"{op_id}.{b}"] = (op_id, b)
                continue

            # 3. branch { ... }
            branch_m = re.match(r'branch\s*\{\s*(.*?)\s*\}', t, flags=re.DOTALL)
            if branch_m:
                b_body = branch_m.group(1).strip()
                b_names = [b.split(':')[0].strip() for b in b_body.splitlines() if ':' in b]
                lbl_b = ', '.join(b_names) if b_names else '...'
                op_lbl = f".branch{{{lbl_b}}}"
                op_id = self._get_unique_id('branch', scope)
                self._add_node(op_id, op_lbl, "operator", scope)
                self.edges.append((curr_id, op_id, curr_label))
                curr_id = op_id
                curr_label = ""
                continue

            # 4. map { ... } / flatMap { ... }
            map_m = re.match(r'(map|flatMap)\s*\{\s*(.*?)\s*\}', t, flags=re.DOTALL)
            if map_m:
                op_name = map_m.group(1)
                m_body = map_m.group(2).strip()
                clean_body = re.sub(r'\s+', ' ', m_body)[:30]
                op_lbl = f".{op_name}{{{clean_body}}}"
                op_id = self._get_unique_id(op_name, scope)
                self._add_node(op_id, op_lbl, "operator", scope)
                self.edges.append((curr_id, op_id, curr_label))
                for h_match in re.finditer(r'(get\w+|param|optional)\(([^)]*)\)', m_body):
                    h_name, _ = h_match.groups()
                    h_id = self._get_unique_id(h_name, scope)
                    self._add_node(h_id, f"{h_name}()", "input", scope)
                    self.edges.append((h_id, op_id, ""))
                curr_id = op_id
                curr_label = ""
                continue

            # 5. collect() / toList() / mix(...) / combine(...) / join(...) / filter(...) / flatten()
            std_op_m = re.match(r'([a-zA-Z0-9_]+)(?:\s*\((.*?)\))?(?:\s*\{\s*(.*?)\s*\})?', t, flags=re.DOTALL)
            if std_op_m:
                op_name = std_op_m.group(1)
                op_args = std_op_m.group(2)
                if op_name in ('collect', 'toList', 'mix', 'concat', 'combine', 'join', 'filter', 'flatten', 'first', 'unique'):
                    op_lbl = f".{op_name}()"
                    if op_args and op_args.strip():
                        op_lbl = f".{op_name}({op_args.strip()})"
                    op_id = self._get_unique_id(op_name, scope)
                    self._add_node(op_id, op_lbl, "operator", scope)
                    self.edges.append((curr_id, op_id, curr_label))
                    if op_args:
                        for arg in self._split_args(op_args):
                            a_node, a_lbl = self._parse_expression(scope, arg)
                            if a_node:
                                self.edges.append((a_node, op_id, a_lbl))
                    curr_id = op_id
                    curr_label = ""
                    continue
                elif op_name == 'out':
                    continue
                else:
                    curr_label = op_name

        return curr_id, curr_label

    def _parse_body(self, body_code: str, scope_name: str) -> None:
        if not body_code:
            return
        self.scope_vars.setdefault(scope_name, {})

        clean_code = re.sub(r'//.*', '', body_code)
        stmts = []
        curr, depth = [], 0
        for ch in clean_code:
            if ch in '({[': depth += 1
            elif ch in ')}]': depth -= 1
            if depth == 0 and ch in ('\n', ';'):
                if curr:
                    s = ''.join(curr).strip()
                    if s: stmts.append(s)
                    curr = []
            else:
                curr.append(ch)
        if curr:
            s = ''.join(curr).strip()
            if s:
                stmts.append(s)
        for stmt in stmts:
            stmt = stmt.strip()
            if not stmt: continue

            # Unwrap control flow: if (...) { inner } -> inner
            if_m = re.match(r'^(?:if\s*\(.*?\)|else)\s*\{\s*(.*?)\s*\}$', stmt, flags=re.DOTALL)
            if if_m:
                inner = if_m.group(1).strip()
                if inner:
                    self._parse_body(inner, scope_name)
                continue

            if stmt.startswith(('if ', 'if(', 'else', 'switch', 'for', 'while', 'return', 'log.', 'println', 'error')):
                continue

            set_m = re.search(r'(.*?)\.set\s*\{\s*([a-zA-Z_]\w*)\s*\}', stmt, flags=re.DOTALL)
            if set_m:
                chain_expr = set_m.group(1).strip()
                var_target = set_m.group(2).strip()
                chain_expr = re.sub(r'^(?:def\s+)?[a-zA-Z_]\w*\s*=\s*', '', chain_expr).strip()
                e_node, e_lbl = self._parse_expression(scope_name, chain_expr)
                if e_node:
                    self.scope_vars[scope_name][var_target] = (e_node, e_lbl)
                    for k in list(self.scope_vars.get(scope_name, {}).keys()):
                        if k.startswith(f"{e_node}."):
                            prop = k.split('.', 1)[1]
                            self.scope_vars[scope_name][f"{var_target}.{prop}"] = (e_node, prop)
                continue

            helper_m = re.match(r'(?:([a-zA-Z_]\w*)\s*=\s*)?(get\w+|param|optional)\(([^)]*)\)', stmt)
            if helper_m:
                assigned_var = helper_m.group(1)
                f_name = helper_m.group(2)
                f_args = helper_m.group(3)
                label = f"{f_name}({f_args})" if f_args else f"{f_name}()"
                f_id = self._get_unique_id(f_name, scope_name)
                self._add_node(f_id, label, "input", scope_name)
                if assigned_var:
                    self.scope_vars[scope_name][assigned_var] = (f_id, assigned_var)
                continue

            proc_m = re.match(r'(?:([a-zA-Z_]\w*)\s*=\s*)?([a-zA-Z0-9_]+)\s*\((.*?)\)(?:\.(?:out\.)?([a-zA-Z0-9_]+))?$', stmt, flags=re.DOTALL)
            if proc_m and self._is_process_call(proc_m.group(2)):
                assigned_var = proc_m.group(1)
                p_name = proc_m.group(2)
                p_args = proc_m.group(3)
                out_ch = proc_m.group(4) or ""
                p_id = self._get_unique_id(p_name, scope_name)
                self._add_node(p_id, p_name, "process", scope_name)
                if assigned_var:
                    self.scope_vars[scope_name][assigned_var] = (p_id, out_ch or assigned_var)
                for arg in self._split_args(p_args):
                    s_id, s_lbl = self._parse_expression(scope_name, arg)
                    if s_id:
                        self.edges.append((s_id, p_id, s_lbl))
                continue

            assign_m = re.match(r'([a-zA-Z_]\w*)\s*=\s*(.*)', stmt, flags=re.DOTALL)
            if assign_m:
                var_name = assign_m.group(1).strip()
                expr = assign_m.group(2).strip()
                e_node, e_lbl = self._parse_expression(scope_name, expr)
                if e_node:
                    self.scope_vars[scope_name][var_name] = (e_node, e_lbl or var_name)
                continue

            self._parse_expression(scope_name, stmt)

    def _draw(self, nid: str, label: str, shape: str) -> str:
        _l = label.replace('"', "'")
        if shape == 'input':
            return f'    {nid}(["{_l}"]):::input'
        if shape == 'output':
            return f'    {nid}[("{_l}")]:::output'
        if shape == 'global':
            return f'    {nid}("{_l}"):::global'
        if shape == 'operator':
            return f'    {nid}{{{{"{_l}"}}}}:::operator'
        return f'    {nid}["{_l}"]:::process'

    def _render_globals(self) -> None:
        for g in self.ast.get('globals', []):
            self._add_node(self._safe_id(f"global_{g.get('name', 'unknown')}"), f"{g.get('name', '?')}", "global")

    def _render_subworkflows_takes(self) -> None:
        for sw in self.ast.get('sub_workflows', []):
            sw_name = sw.get('name', 'unknown')
            self.scope_vars.setdefault(sw_name, {})
            t_ids = []
            for ch in sw.get('take_channels', []):
                c_id = self._safe_id(f"in_{sw_name}_{ch}")
                self._add_node(c_id, ch, "input", sw_name)
                self.scope_vars[sw_name][ch] = (c_id, "")
                t_ids.append((c_id, ch))
            self.sw_take_ids[sw_name] = t_ids

    def _render_subworkflows_bodies(self) -> None:
        for sw in self.ast.get('sub_workflows', []):
            sw_name = sw.get('name', 'unknown')
            self._parse_body(sw.get('body_code', ''), sw_name)
            for em in sw.get('emit_channels', []):
                em_name = em.split('=')[0].strip() if '=' in em else em.strip()
                em_id = self._safe_id(f"out_{sw_name}_{em_name}")
                self._add_node(em_id, em_name, "output", sw_name)
                rhs = em.split('=')[1].strip() if '=' in em else em_name
                source_node, s_lbl = self._resolve_var(sw_name, re.split(r'[.\[]', rhs)[0].strip())
                if source_node:
                    self.edges.append((source_node, em_id, s_lbl or ""))

    def _render_entrypoint(self) -> None:
        ep = self.ast.get('entrypoint', {})
        if ep:
            ep_body = ep.get('body_code', '')
            self._parse_body(ep_body, 'entrypoint')
            for sw in self.ast.get('sub_workflows', []):
                sw_name = sw.get('name', 'unknown')
                for m in re.finditer(rf'{re.escape(sw_name)}\s*\(([^)]*)\)', ep_body):
                    args = self._split_args(m.group(1))
                    t_ids = self.sw_take_ids.get(sw_name, [])
                    for i, arg in enumerate(args):
                        if i < len(t_ids):
                            t_node, t_ch_name = t_ids[i]
                            s_node, s_lbl = self._parse_expression('entrypoint', arg.strip())
                            if s_node:
                                self.edges.append((s_node, t_node, s_lbl or t_ch_name))

    def _build_lines(self) -> None:
        by_sg, no_sg = {}, []
        for nid, lbl, shp, sg in self.nodes:
            if sg:
                by_sg.setdefault(sg, []).append((nid, lbl, shp))
            else:
                no_sg.append((nid, lbl, shp))
        if 'entrypoint' in by_sg:
            self.lines.extend(['    subgraph sg_entrypoint ["entrypoint"]', '        style sg_entrypoint fill:#eef2f3,stroke:#8e9eab,stroke-width:2px,stroke-dasharray: 5 5'])
            for nid, lbl, shp in by_sg.pop('entrypoint'):
                self.lines.append(self._draw(nid, lbl, shp))
            self.lines.append('    end')
        for sg_name, sg_nodes in by_sg.items():
            clean = self._safe_id(sg_name)
            self.lines.extend([f'    subgraph sg_{clean} ["{sg_name}"]', f'        style sg_{clean} fill:#fdfbfb,stroke:#ebedee,stroke-width:2px,stroke-dasharray: 5 5'])
            for nid, lbl, shp in sg_nodes:
                self.lines.append(self._draw(nid, lbl, shp))
            self.lines.append('    end')
        for nid, lbl, shp in no_sg:
            self.lines.append(self._draw(nid, lbl, shp))
        seen = set()
        for src, tgt, lbl in self.edges:
            if src == tgt: continue
            k = (src, tgt, lbl)
            if k in seen: continue
            seen.add(k)
            lbl = lbl.replace('"', "'")
            if lbl:
                self.lines.append(f'    {src} -->|"{lbl}"| {tgt}')
            else:
                self.lines.append(f'    {src} --> {tgt}')

    def render(self) -> str:
        self._render_globals()
        self._render_subworkflows_takes()
        self._render_subworkflows_bodies()
        self._render_entrypoint()
        self._build_lines()
        return "\n".join(self.lines)

def render_mermaid_from_ast(ast_json: dict) -> str:
    """
    Generate a Mermaid flowchart directly from the AST JSON.
    Deterministic. Same AST always produces the same diagram.
    """
    renderer = MermaidRenderer(ast_json)
    return renderer.render()


def render_mermaid_from_graph(component_ids: list[str], kg: Any) -> str:
    """
    Generate a Mermaid flowchart directly from the Knowledge Graph's verified edges and community clusters.
    Deterministic and fast (<1ms).
    """
    if not component_ids or not hasattr(kg, "G") or not kg.is_built:
        return ""

    lines = [
        "flowchart TD",
        "    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef input fill:#50E3C2,stroke:#36A68D,stroke-width:2px,color:#111,rx:5px,ry:5px;",
        "    classDef output fill:#F5A623,stroke:#C28114,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef operator fill:#9013FE,stroke:#6608B8,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef global fill:#9B9B9B,stroke:#656565,stroke-width:2px,color:#fff,rx:5px,ry:5px;"
    ]

    valid_nodes = [cid for cid in component_ids if cid in kg.G]
    if not valid_nodes:
        return ""

    sub_g = kg.G.subgraph(valid_nodes)

    for nid in sub_g.nodes():
        safe_nid = re.sub(r'[^a-zA-Z0-9_]', '_', nid)
        lines.append(f'    {safe_nid}["{nid}"]:::process')

    edge_count = 0
    for src, tgt, data in sub_g.edges(data=True):
        safe_src = re.sub(r'[^a-zA-Z0-9_]', '_', src)
        safe_tgt = re.sub(r'[^a-zA-Z0-9_]', '_', tgt)
        ch = data.get("channel", "")
        conf = data.get("confidence", "")
        lbl = ch or conf
        lbl_clean = lbl.replace('"', "'")
        if lbl_clean:
            lines.append(f'    {safe_src} -->|"{lbl_clean}"| {safe_tgt}')
        else:
            lines.append(f'    {safe_src} --> {safe_tgt}')
        edge_count += 1

    if edge_count == 0 and len(valid_nodes) > 1:
        for i in range(len(valid_nodes) - 1):
            s = re.sub(r'[^a-zA-Z0-9_]', '_', valid_nodes[i])
            t = re.sub(r'[^a-zA-Z0-9_]', '_', valid_nodes[i + 1])
            lines.append(f'    {s} --> {t}')

    return "\n".join(lines)
