import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ──────────────────────────────────────────────────────────────────────────────
# CATALOG REGISTRY & COMPILER
# ──────────────────────────────────────────────────────────────────────────────
from core.catalog_registry import get_registry
from core.services.ast_compiler import (
    _is_void_tool,
    generate_imports_for_code,
    heal_workflow_body,
    validate_framework_components,
)


class ImportItem(BaseModel):
    module_path: str = Field(
        description="Path to the module or local file."
    )
    functions: list[str] = Field(description="List of process names to import.")

    @field_validator('functions', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator('functions')
    @classmethod
    def validate_aliases(cls, v: Any) -> Any:
        """Enforce correct 'as' alias formatting."""
        cleaned = []
        for func in v:
            if ' as ' in func:
                parts = func.split(' as ')
                if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                    raise ValueError(f"Invalid alias format: '{func}'. Use 'OriginalName as AliasName'")
            cleaned.append(func)
        return cleaned


class GlobalDef(BaseModel):
    type: str = Field(description="The definition keyword, usually 'def'.")
    name: str = Field(description="The variable name.")
    value: str = Field(description="The string value.")

    @field_validator('value')
    @classmethod
    def forbid_active_channels(cls, v: Any) -> Any:
        """Blocks the LLM from putting active channel instantiations in the globals block."""
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            plugin_helpers = list(plugin.helper_imports.keys())
        except Exception:
            plugin_helpers = ['get', 'param']

        if '(' in v and ')' in v and (any(kw in v for kw in plugin_helpers) or 'Channel' in v):
            raise ValueError(f"GLOBAL SCOPE ERROR: Active func '{v}' in globals. Move to entrypoint body_code.")
        return v


class InlineProcess(BaseModel):
    name: str = Field(description="The name of the custom process.")
    container: str | None = None
    input_declarations: list[str] = Field(default_factory=list)
    output_declarations: list[str] = Field(default_factory=list)
    script_block: str = Field(description="The raw bash script.")

    @field_validator('input_declarations', 'output_declarations', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator('script_block')
    @classmethod
    def validate_no_dsl(cls, v: Any) -> Any:
        """Forbid DSL2 logic inside bash scripts."""
        forbidden = ['workflow', '.cross(', '.join(', '.multiMap', '.map{', '.mix(']
        for kw in forbidden:
            if kw in v:
                raise ValueError(f"DSL2 keyword '{kw}' inside Process. Use sub_workflow for logic.")
        return v

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Forbid RAG names or UPPERCASE names in inline processes."""
        try:
            exists = get_registry().component_exists(v)
        except Exception:
            exists = False

        if exists:
            raise ValueError(f"Process name '{v}' exists in the component catalog. Standard tools MUST be imported, not defined inline.")

        if v.isupper():
            raise ValueError(f"Process '{v}' is UPPERCASE. It should likely be a Global Constant, not a Process.")
        return v


class WorkflowBlock(BaseModel):
    name: str = Field(description="The name of the workflow.")
    take_channels: list[str] = Field(default_factory=list, description="List of input channel names.")
    emit_channels: list[str] = Field(default_factory=list, description="List of output channel names.")
    body_code: str = Field(description="The raw Groovy logic.")

    @field_validator('take_channels', 'emit_channels', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @model_validator(mode='before')
    @classmethod
    def rescue_and_heal_body(cls, data: dict) -> dict:
        if not isinstance(data, dict): return data

        body = data.get('body_code', '')
        if not isinstance(body, str): return data

        cleaned_body, extracted_emits = heal_workflow_body(body)

        existing_emits = data.get('emit_channels', []) or []
        for em in extracted_emits:
            if em not in existing_emits:
                existing_emits.append(em)

        data['body_code'] = cleaned_body
        data['emit_channels'] = existing_emits

        return data

    @field_validator('take_channels')
    @classmethod
    def validate_take_identifiers(cls, v: Any) -> Any:
        """Ensures take LHS is a valid Groovy identifier."""
        for ch in v:
            cleaned = ch.strip()
            if cleaned and not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cleaned):
                raise ValueError(f"TAKE ERROR: '{cleaned}' is invalid identifier.")
        return [ch.strip() for ch in v if ch.strip()]

    @field_validator('emit_channels')
    @classmethod
    def validate_emit_format(cls, v: Any) -> Any:
        for emit_str in v:
            if '(' in emit_str or ')' in emit_str:
                raise ValueError(f"EMIT ERROR: '{emit_str}' invalid. No logic allowed, only assignments like 'res = proc.res'.")
        return v

    @field_validator('emit_channels')
    @classmethod
    def validate_emit_identifiers(cls, v: Any) -> Any:
        """Ensures emit LHS is a valid Groovy identifier and auto-heals unassigned component output channels."""
        normalized_emits = []
        for emit_str in v:
            if '=' in emit_str:
                lhs, rhs = emit_str.split('=', 1)
                lhs = lhs.strip()
                rhs = rhs.strip()
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', lhs):
                    sanitized_lhs = re.sub(r'[^a-zA-Z0-9_]', '_', lhs).strip('_') or "out_channel"
                    normalized_emits.append(f"{sanitized_lhs} = {rhs}")
                else:
                    normalized_emits.append(f"{lhs} = {rhs}")
            else:
                cleaned = emit_str.strip()
                if cleaned:
                    if '.' in cleaned:
                        # Auto-heal: proc.out.consensus -> consensus = proc.out.consensus
                        name = cleaned.split('.')[-1]
                        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
                            name = "out_channel"
                        normalized_emits.append(f"{name} = {cleaned}")
                    elif re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cleaned):
                        normalized_emits.append(cleaned)
                    else:
                        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', cleaned).strip('_') or "out_channel"
                        normalized_emits.append(sanitized)
        return normalized_emits

    @model_validator(mode='after')
    def enforce_take_channel_usage(self) -> Any:
        if not self.take_channels:
            return self

        combined_text = self.body_code + " " + " ".join(self.emit_channels)
        self.take_channels = [ch for ch in self.take_channels if re.search(rf"\b{re.escape(ch)}\b", combined_text)]
        return self

    @model_validator(mode='after')
    def forbid_recursion(self) -> Any:
        if self.name and self.body_code:
            pattern = rf"\b{self.name}\b\s*\("
            if re.search(pattern, self.body_code):
                raise ValueError(f"RECURSION ERROR: Workflow '{self.name}' is trying to call itself. This is forbidden.")
        return self

    @model_validator(mode='after')
    def enforce_variable_existence(self) -> Any:
        """Ensures that any variable emitted actually exists in the take_channels or body_code."""
        if not self.body_code:
            return self

        valid_vars = set(self.take_channels)

        # Catch assignments (e.g., my_var = ... or Channel my_var = ...)
        assignments = re.findall(r'\b([a-zA-Z0-9_]+)\s*=(?!=)', self.body_code)
        valid_vars.update(assignments)

        # Catch .set { my_var }
        sets = re.findall(r'\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}', self.body_code)
        valid_vars.update(sets)

        # Catch .branch { name: ... } — creates named output channels
        branch_blocks = re.findall(r'\.branch\s*\{([^}]+)\}', self.body_code, re.DOTALL)
        for block in branch_blocks:
            branch_names = re.findall(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', block)
            valid_vars.update(branch_names)

        # Catch .multiMap { name: ... } — creates named output channels
        multimap_blocks = re.findall(r'\.multiMap\s*\{([^}]+)\}', self.body_code, re.DOTALL)
        for block in multimap_blocks:
            multimap_names = re.findall(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', block)
            valid_vars.update(multimap_names)

        # Catch destructuring tuple assignment: (var1, var2) = ...
        tuple_assigns = re.findall(r'(?:def\s+)?\(([^)]+)\)\s*=', self.body_code)
        for group in tuple_assigns:
            for v in group.split(','):
                v = v.strip()
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
                    valid_vars.add(v)

        # Catch regular assignments: var = ...
        regular_assigns = re.findall(r'(?:def\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=', self.body_code)
        valid_vars.update(regular_assigns)

        process_calls = re.findall(r'\b([a-zA-Z0-9_]+)\s*\(', self.body_code)
        valid_vars.update(process_calls)
        for p in process_calls:
            if '__' in p:
                suffix = p.split('__')[-1]
                valid_vars.add(suffix)
                valid_vars.add(f"{suffix}_out")
            valid_vars.add(f"{p}_out")

        filtered_emits = []
        for emit_str in self.emit_channels:
            rhs = emit_str.split('=')[-1].strip()
            base_var = re.split(r'[\.\[]', rhs)[0].strip()

            if not base_var or base_var.startswith("'") or base_var.startswith('"') or base_var in ['true', 'false', 'null', 'Channel', 'get', 'param']:
                filtered_emits.append(emit_str)
                continue

            if base_var in valid_vars:
                filtered_emits.append(emit_str)

        self.emit_channels = filtered_emits
        return self

    @model_validator(mode='after')
    def auto_emit_terminal_assignments(self) -> Any:
        """If emit_channels is empty, auto-detect non-void process assignments in body_code and emit them."""
        if not self.emit_channels and self.body_code:
            assignments = re.findall(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', self.body_code, re.MULTILINE)
            valid_emits = [a for a in assignments if not _is_void_tool(a)]
            if valid_emits:
                self.emit_channels = [valid_emits[-1]]
        return self

    @model_validator(mode='after')
    def forbid_set_on_processes(self) -> Any:
        if not self.body_code:
            return self

        if re.search(r'\b[a-zA-Z0-9_]+\s*\([^)]*\)\s*\.set\s*\{', self.body_code):
            raise ValueError(f"SYNTAX ERROR in '{self.name}': Do not use .set on processes. Use assignment 'var = process(...)' or call directly if void tool.")
        return self

    @model_validator(mode='after')
    def forbid_void_tool_assignment(self) -> Any:
        """Safety net: catches void tool assignments that survived deterministic healing."""
        if not self.body_code:
            return self

        assignment_matches = re.finditer(
            r'\b[a-zA-Z0-9_]+\s*=\s*([a-zA-Z0-9_]+)\s*\(',
            self.body_code
        )
        for m in assignment_matches:
            proc_name = m.group(1)
            if _is_void_tool(proc_name):
                raise ValueError(f"VOID TOOL ERROR in '{self.name}': Assigned void tool '{proc_name}' to a variable. Call it directly.")
        return self


class Entrypoint(BaseModel):
    body_code: str = Field(
        description="The code inside the main unnamed workflow. Do not write 'workflow {{ }}'."
    )

    @field_validator('body_code', mode='before')
    @classmethod
    def auto_heal_entrypoint(cls, v: Any) -> Any:
        """Silently cleans up the entrypoint logic."""
        if not isinstance(v, str): return v
        cleaned_body, _ = heal_workflow_body(v)
        return cleaned_body


class DataFlowSubWorkflow(BaseModel):
    name: str = Field(description="Name of the sub-workflow")
    takes: list[str] = Field(description="Exact 'take' parameters needed")
    emits: list[str] = Field(description="Exact 'emit' parameters produced")


class DataFlowPlan(BaseModel):
    nodes: list[str] = Field(
        description="The EXACT list of catalog component IDs and helper functions you will use in this pipeline. This is the One Source of Truth for the diagram."
    )
    entrypoint_instantiations: list[str] = Field(
        description="List exactly how you will instantiate variables in the entrypoint (e.g., 'trimmed = getSingleInput()'). DO NOT skip this if you need input data!"
    )
    sub_workflows: list[DataFlowSubWorkflow] = Field(
        description="List the sub-workflows you plan to create, and their take/emit channels."
    )


class NextflowPipelineAST(BaseModel):
    reasoning: str | None = Field(None, description="Explain your thought process, what you are fixing, and how you addressed any validation errors. Do NOT place conversational text in the code fields.")
    data_flow_plan: DataFlowPlan = Field(description="Deterministic planning step: explicitly map out all inputs, takes, and emits BEFORE generating the code.")
    imports: list[ImportItem] = Field(default_factory=list)
    globals: list[GlobalDef] = Field(default_factory=list)
    inline_processes: list[InlineProcess] = Field(default_factory=list)
    sub_workflows: list[WorkflowBlock] = Field(default_factory=list)
    entrypoint: Entrypoint

    @field_validator('imports', 'globals', 'inline_processes', 'sub_workflows', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        if v is None:
            return []
        return v

    @model_validator(mode='before')
    @classmethod
    def auto_modularize_and_repair_ast(cls, data: dict) -> dict:
        """Deterministically normalizes AST, relocates active globals, and modularizes flat entrypoints into named sub-workflows."""
        if not isinstance(data, dict): return data

        # 1. Normalize entrypoint
        if 'entrypoint' not in data or data['entrypoint'] is None:
            data['entrypoint'] = {'body_code': '// Generated entrypoint'}
        elif isinstance(data['entrypoint'], str):
            data['entrypoint'] = {'body_code': data['entrypoint']}

        # 2. Normalize data_flow_plan
        if 'data_flow_plan' not in data or data['data_flow_plan'] is None:
            data['data_flow_plan'] = {
                'nodes': [],
                'entrypoint_instantiations': [],
                'sub_workflows': []
            }

        # 3. Relocate active channel instantiations from globals into entrypoint
        globals_list = data.get('globals', []) or []
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            active_keywords = ['Channel', *list(plugin.helper_imports.keys())]
        except Exception:
            active_keywords = ['Channel', 'get', 'param']

        safe_globals = []
        relocated_lines = []
        for g in globals_list:
            if not isinstance(g, dict):
                safe_globals.append(g)
                continue
            val = g.get('value', '')
            if '(' in val and ')' in val and any(kw in val for kw in active_keywords):
                name = g.get('name', 'unknown')
                relocated_lines.append(f"{name} = {val}")
            else:
                safe_globals.append(g)

        if relocated_lines:
            data['globals'] = safe_globals
            ep = data.get('entrypoint', {})
            if isinstance(ep, dict):
                existing_body = ep.get('body_code', '')
                prefix = '\n'.join(relocated_lines)
                ep['body_code'] = f"{prefix}\n{existing_body}" if existing_body else prefix
                data['entrypoint'] = ep

        # 4. Modular Sub-Workflow Auto-Encapsulation
        # Fire when: (a) sub_workflows is empty, OR (b) sub_workflows exists but first entry has empty body_code
        sub_wf_list = data.get('sub_workflows', []) or []
        ep_dict = data.get('entrypoint', {})
        ep_body = ep_dict.get('body_code', '') if isinstance(ep_dict, dict) else str(ep_dict)

        needs_modularization = False
        if not sub_wf_list and ep_body:
            needs_modularization = True
        elif sub_wf_list and ep_body:
            # Check if first sub-workflow has empty body_code — move entrypoint logic into it
            first_sw = sub_wf_list[0] if isinstance(sub_wf_list[0], dict) else {}
            if not first_sw.get('body_code', '').strip():
                needs_modularization = True

        if needs_modularization and ep_body:
            lines = [l.rstrip() for l in ep_body.splitlines() if l.strip()]
            input_instantiations = []
            process_lines = []
            defined_input_vars = []

            for line in lines:
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('/*'):
                    continue

                # Check if this line is an input channel/parameter instantiation
                is_input_line = False
                if any(kw in stripped for kw in active_keywords) or 'Channel.from' in stripped or 'params.' in stripped:
                    # e.g., rawreads = getSingleInput() or def ref = param('ref')
                    assign_match = re.match(r'^(?:def\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*=', stripped)
                    if assign_match:
                        defined_input_vars.append(assign_match.group(1))
                        input_instantiations.append(line)
                        is_input_line = True

                if not is_input_line:
                    process_lines.append(line)

            # If we found process calls in entrypoint and have input vars or steps:
            if process_lines and any('(' in l for l in process_lines):
                combined_proc_code = '\n'.join(process_lines)

                # Auto-instantiate common unassigned input channel parameters used in calls
                assigned_in_proc = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=', combined_proc_code))
                for used_var in ("rawreads", "reads", "data", "input"):
                    if re.search(rf'\b{re.escape(used_var)}\b', combined_proc_code):
                        if used_var not in assigned_in_proc and used_var not in defined_input_vars:
                            from core.catalog_registry import registry
                            default_in_fn = registry.get_default_input_function()
                            defined_input_vars.append(used_var)
                            input_instantiations.append(f"    {used_var} = {default_in_fn}()")

                # Discover take channels from defined input variables used in process_lines
                take_channels = []
                for in_var in defined_input_vars:
                    if re.search(rf'\b{re.escape(in_var)}\b', combined_proc_code):
                        take_channels.append(in_var)

                # Discover emit channels from process assignments
                emit_channels = []
                assigned_vars = re.findall(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', combined_proc_code, re.MULTILINE)
                for av in assigned_vars:
                    if not _is_void_tool(av):
                        emit_channels.append(av)

                # Derive meaningful name from process call categories
                sub_wf_name = "PIPELINE"
                if sub_wf_list and isinstance(sub_wf_list[0], dict) and sub_wf_list[0].get('name', '').strip():
                    sub_wf_name = sub_wf_list[0]['name']
                else:
                    proc_calls = re.findall(r'\b([a-zA-Z0-9_]+)\s*\(', combined_proc_code)
                    categories = set()
                    cat_map = {
                        "PP": "PREPROCESSING", "QC": "QC", "AS": "ASSEMBLY",
                        "TY": "TYPING", "SC": "SCREENING", "AN": "ANNOTATION",
                        "CL": "CLUSTERING", "MP": "MAPPING"
                    }
                    for pc in proc_calls:
                        cat_m = re.match(r'(?:step_\d+|multi_)([A-Z]{2,3})_', pc)
                        if cat_m:
                            categories.add(cat_m.group(1))
                    if categories:
                        named_cats = [cat_map.get(c, c) for c in sorted(categories)]
                        sub_wf_name = "_AND_".join(named_cats[:3]) if len(named_cats) <= 3 else "WGS_ANALYSIS"

                if sub_wf_list and isinstance(sub_wf_list[0], dict):
                    # Fill empty body into existing sub-workflow
                    sub_wf_list[0]['body_code'] = combined_proc_code.strip()
                    if not sub_wf_list[0].get('take_channels'):
                        sub_wf_list[0]['take_channels'] = take_channels
                    if not sub_wf_list[0].get('emit_channels'):
                        sub_wf_list[0]['emit_channels'] = emit_channels[:3]
                    data['sub_workflows'] = sub_wf_list
                else:
                    new_sub_wf = {
                        "name": sub_wf_name,
                        "take_channels": take_channels,
                        "emit_channels": emit_channels[:3],  # emit primary outputs
                        "body_code": combined_proc_code.strip()
                    }
                    data['sub_workflows'] = [new_sub_wf]

                # Update entrypoint body to call the subworkflow
                call_args = ", ".join(take_channels)
                ep_call = f"{sub_wf_name}({call_args})" if take_channels else f"{sub_wf_name}()"
                new_ep_body = "\n".join(input_instantiations) + ("\n\n" if input_instantiations else "") + ep_call
                data['entrypoint'] = {"body_code": new_ep_body.strip()}

        return data

    @model_validator(mode='after')
    def auto_generate_imports(self) -> Any:
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code
        for ip in self.inline_processes:
            all_code += "\n" + ip.script_block
        for g in self.globals:
            all_code += "\n" + g.value

        defined_sws = {sw.name for sw in self.sub_workflows}
        import_map = generate_imports_for_code(all_code, defined_sws)

        new_imports = []
        for path, funcs in import_map.items():
            new_imports.append(ImportItem(module_path=path, functions=sorted(funcs)))

        self.imports = new_imports
        return self

    @model_validator(mode='after')
    def enforce_framework_components(self) -> Any:
        """Ensures referenced tools/processes exist in the catalog.
        Auto-heals un-prefixed tool calls using Knowledge Graph projection.
        """
        try:
            from core.services.knowledge_graph import kg
        except Exception:
            kg = None

        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code

        defined_sws = {sw.name for sw in self.sub_workflows}
        defined_inline = {ip.name for ip in self.inline_processes}

        invalid = validate_framework_components(all_code, defined_sws, defined_inline)

        # Attempt auto-healing via KnowledgeGraph projection
        if invalid and kg and kg.is_built:
            healed_count = 0
            for item, _matches in list(invalid):
                proj = kg.project_vertex(item)
                if proj:
                    pattern = rf"\b{re.escape(item)}\b\s*\("
                    self.entrypoint.body_code = re.sub(pattern, f"{proj}(", self.entrypoint.body_code)
                    for sw in self.sub_workflows:
                        sw.body_code = re.sub(pattern, f"{proj}(", sw.body_code)
                    healed_count += 1

            if healed_count > 0:
                all_code = self.entrypoint.body_code
                for sw in self.sub_workflows:
                    all_code += "\n" + sw.body_code
                invalid = validate_framework_components(all_code, defined_sws, defined_inline)

        if invalid:
            error_details = []
            for item, matches in sorted(invalid, key=lambda x: x[0]):
                suggestion = f" (Did you mean: {', '.join(matches)}?)" if matches else ""
                error_details.append(f"  - {item}{suggestion}")
            details_str = "\n".join(error_details)

            raise ValueError(f"CATALOG ERROR: Missing components/processes:\n{details_str}\nReplace with valid catalog components.")
        return self

    @model_validator(mode='after')
    def enforce_workflow_usage(self) -> Any:
        """Ensures sub-workflows are linked to entrypoint rather than silently discarded."""
        if not self.sub_workflows:
            return self

        used_sws = []
        for sw in self.sub_workflows:
            pattern = rf"\b{re.escape(sw.name)}\b\s*\("
            other_code = self.entrypoint.body_code + "\n" + "\n".join(
                other.body_code for other in self.sub_workflows if other.name != sw.name
            )
            if re.search(pattern, other_code):
                used_sws.append(sw)

        # If none were explicitly called in entrypoint, keep all sub-workflows and auto-wire primary invocation
        if not used_sws:
            primary_sw = self.sub_workflows[0]
            call_args = ", ".join(primary_sw.take_channels)
            invocation = f"{primary_sw.name}({call_args})" if call_args else f"{primary_sw.name}()"
            if primary_sw.name not in self.entrypoint.body_code:
                self.entrypoint.body_code = (self.entrypoint.body_code.rstrip() + f"\n\n{invocation}").strip()
            used_sws = self.sub_workflows

        self.sub_workflows = used_sws
        return self
