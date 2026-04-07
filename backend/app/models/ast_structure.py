from pydantic import BaseModel, Field, field_validator, model_validator
import re
from pathlib import Path
from typing import List, Optional

# ──────────────────────────────────────────────────────────────────────────────
# FRAMEWORK CONSTRAINT: Load valid component names directly from the filesystem
# ──────────────────────────────────────────────────────────────────────────────
from app.core.config import settings
FRAMEWORK_DIR = settings.FRAMEWORK_DIR

FRAMEWORK_MODULES = {f.stem for f in (FRAMEWORK_DIR / "modules").glob("*.nf")} if (FRAMEWORK_DIR / "modules").exists() else set()
FRAMEWORK_STEPS = {f.stem for f in (FRAMEWORK_DIR / "steps").glob("*.nf")} if (FRAMEWORK_DIR / "steps").exists() else set()
FRAMEWORK_COMPONENTS = FRAMEWORK_MODULES | FRAMEWORK_STEPS

class ImportItem(BaseModel):
    module_path: str = Field(
        description="Path to the module. MUST start with '../steps/' or '../functions/'. NEVER use 'nf-core'."
    )
    functions: List[str] = Field(description="List of process names to import.")

    @field_validator('functions')
    def validate_aliases(cls, v):
        """Enforce correct 'as' alias formatting."""
        cleaned = []
        for func in v:
            if ' as ' in func:
                parts = func.split(' as ')
                if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                    raise ValueError(f"Invalid alias format: '{func}'. Use 'OriginalName as AliasName'")
            cleaned.append(func)
        return cleaned

    @field_validator('module_path')
    def forbid_nf_core(cls, v):
        if 'nf-core' in v:
            raise ValueError(
                f"HALLUCINATION DETECTED: 'nf-core' paths are strictly forbidden. "
                f"You MUST use local paths based on the tool prefix (e.g., '../steps/...' or '../modules/...'). Got: {v}"
            )
        return v
        
    @model_validator(mode='after')
    def auto_fix_module_paths(self):
        """Automatically correct paths based on the function prefix."""
        if "../functions/" in self.module_path:
            return self

        for func in self.functions:
            base_name = func.split(' as ')[0].strip()
            if base_name.startswith('multi_'):
                self.module_path = f"../multi/{base_name}"
            elif base_name.startswith('step_'):
                self.module_path = f"../steps/{base_name}"
            elif base_name.startswith('module_'):
                self.module_path = f"../modules/{base_name}"
        return self
    
class GlobalDef(BaseModel):
    type: str = Field(description="The definition keyword, usually 'def'.")
    name: str = Field(description="The variable name.")
    value: str = Field(description="The string value.")

    @field_validator('value')
    def forbid_active_channels(cls, v):
        """Blocks the LLM from putting getSingleInput() or getReference() in the globals block."""
        if '(' in v and ')' in v and any(kw in v for kw in ['get', 'param', 'Channel']):
            raise ValueError(
                f"\n=======================================================\n"
                f"GLOBAL SCOPE ERROR: You placed an active function '{v}' in the `globals` list.\n"
                f"Active data channels WILL CRASH Nextflow if defined globally.\n"
                f"CRITICAL REPAIR INSTRUCTION: \n"
                f"1. DELETE this variable from the `globals` array.\n"
                f"2. Move '{v}' down into the `entrypoint` body_code!\n"
                f"=======================================================\n"
            )
        return v

class InlineProcess(BaseModel):
    name: str = Field(description="The name of the custom process.")
    container: Optional[str] = None
    input_declarations: List[str] = []
    output_declarations: List[str] = []
    script_block: str = Field(description="The raw bash script.")

    @field_validator('script_block')
    def validate_no_dsl(cls, v):
        """Forbid DSL2 logic inside bash scripts."""
        forbidden = ['workflow', '.cross(', '.join(', '.multiMap', '.map{', '.mix(']
        for kw in forbidden:
            if kw in v:
                raise ValueError(
                    f"INVALID PROCESS CONTENT: Found DSL2 keyword '{kw}' inside a Process script.\n"
                    f"Processes are for BASH/SHELL commands only. If you need logic, define a 'sub_workflow'."
                )
        return v
    
    @field_validator('name')
    def validate_name(cls, v):
        """Forbid RAG names or UPPERCASE names in inline processes."""
        if v.startswith("step_") or v.startswith("multi_"):
            raise ValueError(f"Process name '{v}' starts with a reserved prefix. Standard tools MUST be imported, not defined inline.")
        if v.isupper():
            raise ValueError(f"Process '{v}' is UPPERCASE. It should likely be a Global Constant, not a Process.")
        return v

# Void tool suffixes (double-underscore prefix ensures exact matching).
# e.g. '__abricate' matches step_4AN_AMR__abricate but NOT step_3TX_species__vdabricate.
# NOTE: __filtering removed — step_1PP_filtering__bowtie/minimap2 are EMITTING tools.
VOID_TOOL_SUFFIXES = [
    '__pangolin', '__prokka', '__abricate', '__staramr', '__resfinder',
    '__mlst', '__flaa', '__chewbbaca', '__centrifuge',
    '__confindr', '__mash', '__fastq', '__snippy',
]
# Exact void tool/module names (for tools that can't be matched by suffix)
VOID_EXACT_NAMES = [
    'module_qc_fastqc', 'module_qc_nanoplot', 'module_qc_quast',
    'step_4an_amr__filtering',  # Only THIS filtering tool is void (stored lowercase for comparison)
]

def _is_void_tool(name: str) -> bool:
    """Check if a process/module name is a void tool (no emit channels)."""
    lower = name.lower().strip()
    if lower in VOID_EXACT_NAMES:
        return True
    for suffix in VOID_TOOL_SUFFIXES:
        if lower.endswith(suffix):
            return True
    return False

def _is_void_reference(text: str) -> bool:
    """Check if a string references a void tool (for emit filtering)."""
    lower = text.lower()
    if any(name in lower for name in VOID_EXACT_NAMES):
        return True
    for suffix in VOID_TOOL_SUFFIXES:
        if suffix.lstrip('_') in lower:
            # Guard: vdabricate is emitting, not void
            if suffix == '__abricate' and 'vdabricate' in lower:
                continue
            return True
    return False


class WorkflowBlock(BaseModel):
    name: str = Field(description="The name of the workflow.")
    take_channels: List[str] = Field(default=[], description="List of input channel names.")
    emit_channels: List[str] = Field(default=[], description="List of output channel names.")
    body_code: str = Field(description="The raw Groovy logic.")

    @model_validator(mode='before')
    @classmethod
    def rescue_and_heal_body(cls, data: dict) -> dict:
        if not isinstance(data, dict): return data
        
        body = data.get('body_code', '')
        if not isinstance(body, str): return data
        
        # --- Extract inline emit: blocks into emit_channels ---
        emit_match = re.search(r'^\s*emit:\s*([\s\S]*)$', body, flags=re.MULTILINE)
        if emit_match:
            emit_block = emit_match.group(1)
            assignments = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_.\-\[\]]+)', emit_block)
            if assignments:
                existing = data.get('emit_channels', [])
                for k, v in assignments:
                    emit_str = f"{k} = {v}"
                    if emit_str not in existing:
                        existing.append(emit_str)
                data['emit_channels'] = existing

        # --- Strip workflow/take/main/emit wrappers ---
        match = re.search(r'^\s*workflow\s+[_a-zA-Z0-9]*\s*\{(.*)\}\s*$', body, re.DOTALL)
        if match: body = match.group(1)

        body = re.sub(r'^\s*take:.*?(?=^\s*main:|^\s*emit:|\Z)', '', body, flags=re.MULTILINE | re.DOTALL)
        body = re.sub(r'^\s*emit:[\s\S]*', '', body, flags=re.MULTILINE)
        body = re.sub(r'^\s*main:\s*', '', body, flags=re.MULTILINE)

        # --- DETERMINISTIC HEAL: Strip dsl=2 header if LLM included it ---
        body = re.sub(r'^\s*nextflow\.enable\.dsl\s*=\s*2\s*\n?', '', body, flags=re.MULTILINE)

        # --- DETERMINISTIC HEAL #4: Strip void tool assignments ---
        # Converts `var = step_4TY_lineage__pangolin(...)` → `step_4TY_lineage__pangolin(...)`
        def _strip_void_assignment(m):
            full_match = m.group(0)
            proc_name = m.group(2)
            if _is_void_tool(proc_name):
                # Remove the `varname = ` prefix
                return re.sub(r'^\s*[a-zA-Z0-9_]+\s*=\s*', '', full_match)
            return full_match
        
        body = re.sub(
            r'^(\s*[a-zA-Z0-9_]+\s*=\s*)((?:step_|multi_|module_)[a-zA-Z0-9_]+)\s*\(',
            _strip_void_assignment,
            body,
            flags=re.MULTILINE
        )

        # --- DETERMINISTIC HEAL #6: Auto-inject [1..3] reference slice ---
        # Only in mapping tool contexts: if step_2AS_mapping__ is in the body
        if 'step_2AS_mapping__' in body and '.multiMap' in body:
            body = re.sub(
                r'(refs?\s*:\s*it\[1\])(?!\s*\[)',
                r'\1[1..3]',
                body
            )

        data['body_code'] = body.strip()

        # --- DETERMINISTIC HEAL #5: Remove void tool entries from emit_channels ---
        emits = data.get('emit_channels', [])
        if emits:
            cleaned_emits = [e for e in emits if not _is_void_reference(e)]
            if len(cleaned_emits) != len(emits):
                removed = set(emits) - set(cleaned_emits)
                print(f"  [AUTO-HEAL] Removed void tool references from emit_channels: {removed}")
            data['emit_channels'] = cleaned_emits

        return data

    @field_validator('emit_channels')
    def validate_emit_format(cls, v):
        for emit_str in v:
            if '(' in emit_str or ')' in emit_str:
                raise ValueError(
                    f"STRICT EMIT FORMAT ERROR: '{emit_str}' contains parenthesis. "
                    f"DO NOT put function calls in the emit channels.\n"
                    f"Use 'name = variable.property' or a bare variable name."
                )
        return v

    @field_validator('emit_channels')
    def validate_emit_identifiers(cls, v):
        """Ensures emit LHS is a valid Groovy identifier."""
        for emit_str in v:
            if '=' in emit_str:
                lhs = emit_str.split('=')[0].strip()
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', lhs):
                    raise ValueError(
                        f"EMIT NAME ERROR: '{lhs}' is not a valid identifier. "
                        f"Use format: 'name = variable.property' (e.g., 'consensus = ivar_out.consensus')."
                    )
            else:
                cleaned = emit_str.strip()
                if cleaned and not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cleaned):
                    raise ValueError(
                        f"EMIT FORMAT ERROR: '{cleaned}' is not a valid identifier. "
                        f"Use a bare variable name or 'name = value' format."
                    )
        return v

    @field_validator('emit_channels')
    def forbid_void_emits(cls, v):
        """Blocks void tool references that survived deterministic healing."""
        for emit_str in v:
            if _is_void_reference(emit_str):
                raise ValueError(
                    f"VOID TOOL ERROR: You are trying to emit '{emit_str}'. "
                    f"This tool has NO outputs. Remove it from emit_channels."
                )
        return v

    @model_validator(mode='after')
    def enforce_take_channel_usage(self):
        if not self.take_channels:
            return self
            
        combined_text = self.body_code + " " + " ".join(self.emit_channels)
        
        for ch in self.take_channels:
            pattern = rf"\b{re.escape(ch)}\b"
            if not re.search(pattern, combined_text):
                raise ValueError(
                    f"LOGIC ERROR in workflow '{self.name}'. You defined '{ch}' in take_channels "
                    f"but you never used it in the body_code and never emitted it. "
                    f"Either use it, emit it directly, or remove it from take_channels."
                )
        return self

    @field_validator('name')
    def validate_workflow_name_against_framework(cls, v):
        """Block sub-workflow names that use reserved prefixes but don't exist in the framework."""
        if v.startswith('module_') and v not in FRAMEWORK_MODULES:
            raise ValueError(
                f"\n=======================================================\n"
                f"HALLUCINATION DETECTED: Sub-workflow name '{v}' uses the 'module_' prefix "
                f"but does NOT exist in the cohesive-ngsmanager framework.\n"
                f"CRITICAL REPAIR INSTRUCTIONS:\n"
                f"1. If this is a custom sub-workflow, rename it with a 'wf_' prefix (e.g., 'wf_{v[7:]}').\n"
                f"2. If this wraps a single tool, DELETE the sub-workflow and call the tool directly in the entrypoint.\n"
                f"3. Only use 'module_' names for EXISTING framework templates: {sorted(FRAMEWORK_MODULES)}\n"
                f"=======================================================\n"
            )
        if v.startswith('step_') and v not in FRAMEWORK_STEPS:
            raise ValueError(
                f"HALLUCINATION DETECTED: '{v}' is not a valid step in the framework. "
                f"Sub-workflows cannot use 'step_' prefix — steps are imported, not defined."
            )
        return v

    @model_validator(mode='after')
    def forbid_recursion(self):
        if self.name and self.body_code:
            pattern = rf"\b{self.name}\b\s*\("
            if re.search(pattern, self.body_code):
                raise ValueError(f"RECURSION ERROR: Workflow '{self.name}' is trying to call itself. This is forbidden.")
        return self

    @model_validator(mode='after')
    def enforce_strict_data_shaping(self):
        """Strictly enforces that the LLM manually shapes data and never uses inline channel joins."""
        if not self.body_code:
            return self
            
        process_calls = re.finditer(r'\b(?:step_|multi_|module_|medaka|samtools|coverage|aggregate|staramr)[a-zA-Z0-9_]*\s*\(([^)]+)\)', self.body_code)
        for match in process_calls:
            args = match.group(1)
            # Allowed: | groupTuple. Forbidden: .cross, .combine
            if '.cross' in args or '.combine' in args:
                raise ValueError(
                    f"SYNTAX ERROR in '{self.name}': Inline channel joins are forbidden.\n"
                    f"Found: '{match.group(0)}'\n"
                    f"You MUST perform .cross() or .combine() on a separate line, "
                    f"flatten it with .map or .multiMap, assign it to a variable, and pass ONLY the variable."
                )

        ops_matches = re.finditer(r'\.(cross|combine)\s*\([^)]*\)', self.body_code)
        for match in ops_matches:
            post_op_text = self.body_code[match.end():]
            chain_pattern = re.compile(r'^\s*(?:\{[^}]*\}\s*)?\.(?:map|multiMap|set|branch|cross|combine)\b')
            if not chain_pattern.search(post_op_text):
                raise ValueError(
                    f"DATA SHAPING ERROR in '{self.name}': A '.{match.group(1)}()' operation was found without being flattened.\n"
                    f"In cohesive-ngsmanager, you MUST chain '.map {{ ... }}', '.multiMap {{ ... }}', or '.set {{ ... }}' "
                    f"after channel joins to ensure the tuple structure is correct."
                )

        return self

    @model_validator(mode='after')
    def enforce_variable_existence(self):
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

        process_calls = re.findall(r'\b((?:step_|multi_|module_)[a-zA-Z0-9_]+)\s*\(', self.body_code)
        valid_vars.update(process_calls)

        for emit_str in self.emit_channels:
            rhs = emit_str.split('=')[-1].strip()
            
            base_var = re.split(r'[\.\[]', rhs)[0].strip()
            
            if not base_var or base_var.startswith("'") or base_var.startswith('"') or base_var in ['true', 'false', 'null']:
                continue
                
            if base_var not in valid_vars:
                raise ValueError(
                    f"\n=======================================================\n"
                    f"HALLUCINATION DETECTED in workflow '{self.name}'.\n"
                    f"You are trying to emit '{emit_str}' but the base variable '{base_var}' was NEVER DEFINED in the body_code.\n\n"
                    f"CRITICAL REPAIR INSTRUCTIONS:\n"
                    f"1. Did you forget to extract the channel? (e.g., If you assigned `kraken_out = step_3TX(...)`, you CANNOT just emit `genus_report`. You MUST emit `kraken_out.genus_report` or define `genus_report = kraken_out.genus_report` first).\n"
                    f"2. Did you misspell the variable name from your `.set {{}}` or assignment block?\n"
                    f"3. If this tool is a VOID tool (publishDir only), it DOES NOT emit anything! Delete this emit entirely.\n"
                    f"Fix the emit channel or the body_code so the variables match exactly.\n"
                    f"=======================================================\n"
                )
        return self

    @model_validator(mode='after')
    def enforce_host_depletion_shape(self):
        """Traces the specific variable passed to Host Depletion to ensure it uses .map"""
        if not self.body_code:
            return self

        host_calls = re.findall(r'step_1PP_hostdepl__[a-zA-Z0-9_]+\s*\(([a-zA-Z0-9_]+)\)', self.body_code)
        
        for var_name in host_calls:
            bad_pattern = rf'\.multiMap\s*\{{[^}}]*\}}\s*\.set\s*\{{\s*{var_name}\s*\}}'
            
            if re.search(bad_pattern, self.body_code):
                raise ValueError(
                    f"\n=======================================================\n"
                    f"DATA SHAPING ERROR in '{self.name}': You used `.multiMap` to prepare the '{var_name}' channel for Host Depletion.\n"
                    f"Host depletion tools (`step_1PP_hostdepl__*`) require a SINGLE FLAT TUPLE.\n"
                    f"CRITICAL REPAIR INSTRUCTION:\n"
                    f"Change the preparation of '{var_name}' to use `.map` instead of `.multiMap`:\n"
                    f"`.map {{ [ it[0][0], it[0][1], it[1][1] ] }}.set {{ {var_name} }}`\n"
                    f"=======================================================\n"
                )
        return self

    @model_validator(mode='after')
    def forbid_set_on_processes(self):
        if not self.body_code:
            return self
            
        if re.search(r'\b(?:step_|multi_|module_)[a-zA-Z0-9_]+\s*\([^)]*\)\s*\.set\s*\{', self.body_code):
            raise ValueError(
                f"\n=======================================================\n"
                f"SYNTAX ERROR in '{self.name}': You appended `.set {{...}}` to a process call.\n"
                f"In Nextflow, `.set` is ONLY for channel shaping (like `.map`).\n"
                f"CRITICAL REPAIR INSTRUCTION:\n"
                f"1. If this is a standard process, use direct assignment: `var_name = process(...)`\n"
                f"2. If this is a VOID tool (like Pangolin or Prokka), DO NOT assign it to a variable at all. Just call `process(...)`.\n"
                f"=======================================================\n"
            )
        return self
    
    @model_validator(mode='after')
    def enforce_reference_slice(self):
        """Safety net: catches missing [1..3] that survived deterministic healer #6."""
        if not self.body_code:
            return self

        if 'step_2AS_mapping__' in self.body_code and '.multiMap' in self.body_code:
            bad_pattern = r'\b[a-zA-Z0-9_]+\s*:\s*it\[1\](?!\s*\[)'
            if re.search(bad_pattern, self.body_code):
                raise ValueError(
                    f"DATA SHAPING ERROR in '{self.name}': Missing `[1..3]` slice for reference. "
                    f"Change `it[1]` to `it[1][1..3]` inside the `.multiMap` block."
                )
        return self

    @model_validator(mode='after')
    def forbid_void_tool_assignment(self):
        """Safety net: catches void tool assignments that survived deterministic healing."""
        if not self.body_code:
            return self

        # Find all assignments to process calls
        assignment_matches = re.finditer(
            r'\b[a-zA-Z0-9_]+\s*=\s*((?:step_|multi_|module_)[a-zA-Z0-9_]+)\s*\(',
            self.body_code
        )
        for m in assignment_matches:
            proc_name = m.group(1)
            if _is_void_tool(proc_name):
                raise ValueError(
                    f"VOID TOOL ERROR in '{self.name}': Assigned void tool '{proc_name}' to a variable. "
                    f"Remove the assignment and call it directly."
                )
        return self

    @model_validator(mode='after')
    def forbid_active_channels_in_subworkflows(self):
        if not self.body_code:
            return self

        active_channel_funcs = [
            'getSingleInput', 'getInput', 'getReference', 'getReferences',
            'getReferenceOptional', 'getReferenceUnkeyed',
            'getHost', 'getHostOptional', 'getHostUnkeyed', 'getHostReference',
            'getAssembly', 'getTrimmedReads', 'getDepletedReads',
            'getDS', 'getVCFs', 'getLongReads', 'getKrakenResults',
            'getKingdom', 'getGenusSpecies', 'getGenusSpeciesOptional',
            'getGenusSpeciesOptionalUnkeyed', 'getInputFolders', 'getInputOf',
        ]

        for func in active_channel_funcs:
            if re.search(rf'\b{func}\s*\(', self.body_code):
                raise ValueError(
                    f"\n=======================================================\n"
                    f"ARCHITECTURE ERROR in sub-workflow '{self.name}':\n"
                    f"You called `{func}()` inside a sub-workflow.\n"
                    f"ALL active data channels MUST be instantiated in the `entrypoint` workflow and passed into your modules via `take:` channels.\n"
                    f"CRITICAL REPAIR INSTRUCTION:\n"
                    f"1. Delete `{func}()` from '{self.name}'.\n"
                    f"2. Add a `take:` parameter to '{self.name}' to receive the data.\n"
                    f"3. Move `{func}()` into the `entrypoint` workflow and pass it as an argument.\n"
                    f"=======================================================\n"
                )
        return self

class Entrypoint(BaseModel):
    body_code: str = Field(
        description="The code inside the main unnamed workflow. Do not write 'workflow {{ }}'."
    )

    @field_validator('body_code', mode='before')
    def auto_heal_entrypoint(cls, v):
        """Silently cleans up the entrypoint logic."""
        if not isinstance(v, str): return v

        # Strip dsl=2 header
        v = re.sub(r'^\s*nextflow\.enable\.dsl\s*=\s*2\s*\n?', '', v, flags=re.MULTILINE)

        # Unwrap workflow { } wrapper
        match = re.search(r'^\s*workflow\s*\{(.*)\}\s*$', v, re.DOTALL)
        if match: v = match.group(1)

        # Strip take/main/emit keywords (entrypoints don't have take: or emit:)
        v = re.sub(r'^\s*take:.*?(?=^\s*main:|^\s*emit:|\Z)', '', v, flags=re.MULTILINE | re.DOTALL)
        v = re.sub(r'^\s*main:\s*', '', v, flags=re.MULTILINE)
        v = re.sub(r'^\s*emit:[\s\S]*', '', v, flags=re.MULTILINE)

        return v.strip()

class NextflowPipelineAST(BaseModel):
    imports: List[ImportItem] = []
    globals: List[GlobalDef] = []
    inline_processes: List[InlineProcess] = []
    sub_workflows: List[WorkflowBlock] = []
    entrypoint: Entrypoint

    @model_validator(mode='before')
    @classmethod
    def auto_relocate_active_globals(cls, data: dict) -> dict:
        """Deterministically moves active channel calls from globals to entrypoint."""
        if not isinstance(data, dict): return data
        globals_list = data.get('globals', [])
        if not globals_list: return data

        active_keywords = ['get', 'param', 'Channel', 'getSingleInput', 'getInput',
                           'getReference', 'getHost', 'getTrimmedReads', 'getAssembly']
        
        safe_globals = []
        relocated_lines = []
        for g in globals_list:
            if not isinstance(g, dict):
                safe_globals.append(g)
                continue
            val = g.get('value', '')
            # Check if value contains active function calls
            if '(' in val and ')' in val and any(kw in val for kw in active_keywords):
                name = g.get('name', 'unknown')
                relocated_lines.append(f"{name} = {val}")
                print(f"  [AUTO-HEAL] Relocated active global '{name} = {val}' to entrypoint")
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
        
        return data

    @model_validator(mode='after')
    def auto_generate_imports(self):
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code
        for ip in self.inline_processes:
            all_code += "\n" + ip.script_block
        for g in self.globals:
            all_code += "\n" + g.value

        pattern = re.compile(r'(?<!\.)\b((?:step_|multi_|module_|prepare_|get[A-Z]|extract[A-Z]|is[A-Z]|has[A-Z]|parse[A-Z]|execution[A-Z]|task[A-Z]|check[A-Z]|param|optional)[a-zA-Z0-9_]*)\s*\(')
        used_callables = set(match.group(1) for match in pattern.finditer(all_code))
        
        defined_sws = {sw.name for sw in self.sub_workflows}
        used_callables = used_callables - defined_sws

        common_funcs = {
            'parseMetadataFromFileName', 'executionMetadata', 'taskMemory', 'taskTime',
            'getRisCd', 'extractKey', 'stepInputs', 'extractDsRef', 'getBaseName',
            'getGB', 'getEmpty', 'parseRISCD', 'isFastqRiscd', 'isFastaRiscd',
            'isSpeciesSupported', 'csv2map', 'flattenPath', 'logHeader'
        }

        sampletype_funcs = {
            'isBacterium', 'isVirus', 'isFungus', 'isSarsCov2',
            'isPositiveControlSarsCov2', 'isNegativeControlSarsCov2', 'isNGSMG16S'
        }

        param_funcs = {
            'getTrimmedReads', 'getAssembly', 'getDepletedReads', 'getReferenceCodes',
            'getNCBICodes', 'getReferences', 'getReference', 'getReferenceUnkeyed',
            'getReferenceOptional', 'getHost', 'getGenusSpecies', 'getModule',
            'getAbricateDatabase', 'getBlastDatabase', 'getParamTaxaId', '_getParamAsValue',
            '_getParam', 'getDS', 'isFullOutput', 'getResult', 'getKrakenResults',
            'getInput', 'getVCFs', 'hasEnoughFastqData', 'hasFastqData', 'param',
            'optional', 'optionalOrDefault', 'isIonTorrent', 'isNanopore', 'isIlluminaPaired',
            'isCompatibleWithSeqType', 'isSegmentedMapping', 'checkEnum', 'getHostReference',
            'getLongReads', 'paramWrap', 'optWrap', '_getReferences', '_getSingleReference',
            'getHostOptional', 'getHostUnkeyed', 'getHostReference', 'getGenusSpeciesOptionalUnkeyed',
            'getGenusSpeciesOptional', 'getSpecies', 'getBlastDatabaseUnkeyed', 'getKingdom',
            'getTaxIdsUnkeyed', 'getParamIncludeParents', 'getParamIncludeChildren',
            '_getAlleles', 'getParam', 'getInputOf', 'getInputFolders', 'getSingleInput', 'param'
        }
        
        import_map = {}
        for func in used_callables:
            if func.startswith('step_'):
                path = f"../steps/{func}"
            elif func.startswith('multi_'):
                path = f"../multi/{func}"
            elif func.startswith('module_'):
                path = f"../modules/{func}"
            elif func in common_funcs:
                path = "../functions/common.nf"
            elif func in param_funcs:
                path = "../functions/parameters.nf"
            elif func in sampletype_funcs:
                path = "../functions/sampletypes.nf"
            else:
                continue
                
            if path not in import_map:
                import_map[path] = []
            import_map[path].append(func)

        new_imports = []
        for path, funcs in import_map.items():
            new_imports.append(ImportItem(module_path=path, functions=sorted(funcs)))
            
        self.imports = new_imports
        return self

    @model_validator(mode='after')
    def enforce_framework_components(self):
        """Ensures ALL step_*/module_* references in generated code exist in the framework."""
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code

        # Find all step_* and module_* calls in the code
        referenced = set(re.findall(r'\b((?:step_|module_)[a-zA-Z0-9_]+)\s*\(', all_code))

        # Exclude sub-workflows defined in this AST (they're local, not framework imports)
        defined_sws = {sw.name for sw in self.sub_workflows}
        referenced -= defined_sws

        invalid = referenced - FRAMEWORK_COMPONENTS
        if invalid:
            raise ValueError(
                f"\n=======================================================\n"
                f"FRAMEWORK CONSTRAINT VIOLATION: The following components do NOT exist "
                f"in cohesive-ngsmanager and cannot be used:\n"
                f"  {sorted(invalid)}\n\n"
                f"REPAIR: Replace them with valid framework components or remove them.\n"
                f"=======================================================\n"
            )
        return self

    @model_validator(mode='after')
    def enforce_workflow_usage(self):
        """If you define a sub_workflow, you must actually use it."""
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code
            
        for sw in self.sub_workflows:
            pattern = rf"\b{sw.name}\b\s*\("
            if not re.search(pattern, all_code):
                raise ValueError(
                    f"VALIDATION ERROR: The sub_workflow '{sw.name}' is defined but NEVER CALLED in the pipeline. "
                    f"Either call it in the entrypoint or subworkflow or remove it."
                )
        return self