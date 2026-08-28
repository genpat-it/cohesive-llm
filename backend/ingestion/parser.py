"""
Nextflow .nf Parser — Extracts process/workflow definitions from Nextflow DSL2 files.

Parses:
  - Process definitions (name, container, input/output channels, script)
  - Workflow definitions (name, take/emit channels, body, includes)
  - Include statements (for dependency graph)
  - Global params.XXX references (variables_needed)
  - Helper function calls (getParam, getSpecies, etc.)
  - Groovy function definitions (for resources catalog)

Usage:
    from ingestion.parser import parse_nf_file
    components = parse_nf_file(Path("steps/step_1PP_trimming__fastp.nf"))
"""

import re
from pathlib import Path


def parse_nf_file(filepath: Path) -> dict:
    """Parse a single .nf file and extract process/workflow definitions.

    Returns:
        {
            "file": str,
            "file_stem": str,
            "processes": [{name, container, inputs, outputs, script}],
            "workflows": [{name, takes, emits, body, includes}],
            "includes": [str],
            "variables_needed": [str],        # params.XXX + helper calls
            "groovy_functions": [{name, body, doc}],  # for resources
            "raw_code": str,
        }
    """
    code = filepath.read_text(encoding="utf-8", errors="replace")

    return {
        "file": str(filepath),
        "file_stem": filepath.stem,
        "processes": _extract_processes(code),
        "workflows": _extract_workflows(code),
        "includes": _extract_includes(code),
        "variables_needed": _extract_variables_needed(code),
        "groovy_functions": _extract_groovy_functions(code),
        "raw_code": code,
    }


# ── Deep extraction ───────────────────────────────────────────────────────────

def _extract_variables_needed(code: str) -> list[str]:
    """Extract all implicit dependencies from the code:
    - params.XXX references
    - getParam('XXX') calls
    - Known named helper invocations (getSpecies, getKingdom, etc.)
    """
    deps: set[str] = set()

    # params.SOMETHING
    for m in re.finditer(r'\bparams\.([a-zA-Z0-9_]+)', code):
        deps.add(f"params.{m.group(1)}")

    # getParam('XXX') or getParam("XXX")
    for m in re.finditer(r"getParam\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", code):
        deps.add(f"getParam('{m.group(1)}')")

    # Known zero-arg helpers that still imply a dependency
    NAMED_HELPERS = [
        "getSpecies", "getKingdom", "getGenus", "getHost",
        "getHostUnkeyed", "getGenusSpecies", "getGenusSpeciesOptional",
        "getBlastDatabase", "getBlastDatabaseUnkeyed",
        "getSingleInput", "getInputOf", "getInputFolders",
        "getRISCD", "getCmp",
    ]
    for helper in NAMED_HELPERS:
        # Match calls like getSpecies() or getSpecies(something)
        if re.search(rf'\b{re.escape(helper)}\s*\(', code):
            deps.add(f"{helper}()")

    return sorted(deps)


def _extract_groovy_functions(code: str) -> list[dict]:
    """Extract Groovy 'def functionName(...)' definitions from a .nf file.

    Returns a list of {name, body, usage, doc} for resources catalog.
    """
    functions = []
    # Match: def funcName(...) { ... }
    for m in re.finditer(r'\bdef\s+([a-zA-Z_]\w*)\s*\([^)]*\)\s*\{', code):
        name = m.group(1)
        start_pos = m.end() - 1  # position of opening {
        body = _extract_balanced_block(code, start_pos)
        if body is not None:
            # Try to get a preceding comment as doc
            pre = code[:m.start()].rstrip()
            lines_before = pre.split('\n')
            doc_lines = []
            for line in reversed(lines_before):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('/*'):
                    doc_lines.insert(0, stripped.lstrip('/*').strip())
                else:
                    break
            doc = ' '.join(doc_lines).strip()

            # Build a usage hint: def funcName(args)
            sig_m = re.match(r'\bdef\s+([a-zA-Z_]\w*)\s*(\([^)]*\))', m.group(0))
            usage = f"def {name}{sig_m.group(2)}" if sig_m else f"def {name}()"
            
            # Extract arguments
            arguments = []
            if sig_m:
                args_str = sig_m.group(2)[1:-1].strip()
                if args_str:
                    for arg in args_str.split(','):
                        clean_arg = arg.split('=')[0].strip().split()[-1]
                        if clean_arg:
                            arguments.append(clean_arg)

            functions.append({
                "name": name,
                "body": body.strip(),
                "usage": usage,
                "doc": doc,
                "num_args": len(arguments),
                "arguments": arguments,
            })
    return functions


# ── Process / Workflow extractors ─────────────────────────────────────────────

def _extract_processes(code: str) -> list[dict]:
    """Extract process definitions from Nextflow DSL2 code."""
    processes = []
    proc_starts = list(re.finditer(r'\bprocess\s+(\w+)\s*\{', code))

    for match in proc_starts:
        name = match.group(1)
        start_pos = match.end()
        body = _extract_balanced_block(code, start_pos - 1)

        if not body:
            continue

        proc = {
            "name": name,
            "container": _extract_container(body),
            "inputs": _extract_input_channels(body),
            "outputs": _extract_output_channels(body),
            "script": _extract_script_block(body),
        }
        processes.append(proc)

    return processes


def _extract_workflows(code: str) -> list[dict]:
    """Extract workflow definitions from Nextflow DSL2 code."""
    workflows = []

    # Named workflows: workflow WORKFLOW_NAME { ... }
    wf_starts = list(re.finditer(r'\bworkflow\s+(\w+)\s*\{', code))

    for match in wf_starts:
        name = match.group(1)
        start_pos = match.end()
        body = _extract_balanced_block(code, start_pos - 1)

        if not body:
            continue

        wf = {
            "name": name,
            "takes": _extract_take_channels(body),
            "emits": _extract_emit_channels(body),
            "body": _extract_main_block(body),
            "includes": _extract_workflow_calls(body),
        }
        workflows.append(wf)

    # Unnamed (entry) workflow
    entry_match = re.search(r'(?<!\w)workflow\s*\{', code)
    if entry_match:
        body = _extract_balanced_block(code, entry_match.end() - 1)
        if body:
            workflows.append({
                "name": "__entrypoint__",
                "takes": [],
                "emits": [],
                "body": body,
                "includes": _extract_workflow_calls(body),
            })

    return workflows


def _extract_includes(code: str) -> list[str]:
    """Extract include statements: include { step_xxx } from '...'"""
    includes = []
    for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", code):
        block = match.group(1)
        for item in block.split(';'):
            item = item.strip()
            name = item.split(' as ')[0].strip() if ' as ' in item else item.strip()
            if name and re.match(r'^[a-zA-Z_]\w*$', name):
                includes.append(name)
    return includes


# ── Block extraction helpers ──────────────────────────────────────────────────

def _extract_balanced_block(code: str, open_brace_pos: int) -> str | None:  # noqa: C901
    """Extract content between balanced braces starting at open_brace_pos.
    Ignores braces inside single/double/triple quotes and comments.
    """
    if open_brace_pos >= len(code) or code[open_brace_pos] != '{':
        return None

    depth = 0
    in_single = False
    in_double = False
    in_triple_single = False
    in_triple_double = False
    in_line_comment = False
    in_block_comment = False

    i = open_brace_pos
    while i < len(code):
        char = code[i]

        if in_line_comment:
            if char == '\n':
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if char == '*' and i + 1 < len(code) and code[i+1] == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_triple_single:
            if char == '\\':
                i += 2
                continue
            if code[i:i+3] == "'''":
                in_triple_single = False
                i += 3
                continue
            i += 1
            continue

        if in_triple_double:
            if char == '\\':
                i += 2
                continue
            if code[i:i+3] == '"""':
                in_triple_double = False
                i += 3
                continue
            i += 1
            continue

        if in_single:
            if char == '\\':
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            if char == '\\':
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue

        if code[i:i+2] == '//':
            in_line_comment = True
            i += 2
            continue
        if code[i:i+2] == '/*':
            in_block_comment = True
            i += 2
            continue
        if code[i:i+3] == "'''":
            in_triple_single = True
            i += 3
            continue
        if code[i:i+3] == '"""':
            in_triple_double = True
            i += 3
            continue
        if char == "'":
            in_single = True
            i += 1
            continue
        if char == '"':
            in_double = True
            i += 1
            continue

        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return code[open_brace_pos + 1:i]

        i += 1

    return None


def _extract_container(process_body: str) -> str | None:
    match = re.search(r'container\s+["\']([^"\']+)["\']', process_body)
    return match.group(1) if match else None


def _extract_input_channels(process_body: str) -> list[str]:
    channels = []
    input_match = re.search(r'\binput\s*:(.*?)(?=\boutput\s*:|\bscript\s*:|\bshell\s*:|\bexec\s*:|\Z)',
                            process_body, re.DOTALL)
    if input_match:
        for line in input_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                names = re.findall(r'(?:val|path|file|env|stdin)\s*\(?\s*(\w+)', line)
                if names:
                    channels.extend(names)
                elif line and not line.startswith('*'):
                    channels.append(line.split('//')[0].strip())
    return channels


def _extract_output_channels(process_body: str) -> list[str]:
    channels = []
    output_match = re.search(r'\boutput\s*:(.*?)(?=\bscript\s*:|\bshell\s*:|\bexec\s*:|\bwhen\s*:|\Z)',
                             process_body, re.DOTALL)
    if output_match:
        for line in output_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                emit_match = re.search(r'emit\s*:\s*(\w+)', line)
                if emit_match:
                    channels.append(emit_match.group(1))
                else:
                    names = re.findall(r'(?:val|path|file|env|stdout)\s*\(?\s*(\w+)', line)
                    if names:
                        channels.extend(names)
    return channels


def _extract_script_block(process_body: str) -> str:
    # Match triple-quoted script blocks
    triple_pat = r'(?:script|shell)\s*:\s*\n?\s*(?:"""|\'\'\')\s*\n?(.*?)(?:"""|\'\'\')'
    match = re.search(triple_pat, process_body, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Single-line quoted fallback
    match = re.search(r'(?:script|shell)\s*:\s*\n?\s*["\'](.+?)["\']', process_body)
    return match.group(1).strip() if match else ""





def _extract_take_channels(workflow_body: str) -> list[str]:
    channels = []
    take_match = re.search(r'\btake\s*:(.*?)(?=\bmain\s*:|\bemit\s*:|\Z)',
                           workflow_body, re.DOTALL)
    if take_match:
        for line in take_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                channels.append(line.split('//')[0].strip())
    return channels


def _extract_emit_channels(workflow_body: str) -> list[str]:
    channels = []
    emit_match = re.search(r'\bemit\s*:(.*?)(?=\}|$|\Z)', workflow_body, re.DOTALL)
    if emit_match:
        for line in emit_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//') and not line.startswith('}'):
                lhs = line.split('=')[0].strip() if '=' in line else line.split('//')[0].strip()
                if lhs:
                    channels.append(lhs)
    return channels


def _extract_main_block(workflow_body: str) -> str:
    match = re.search(r'\bmain\s*:(.*?)(?=\bemit\s*:|\Z)', workflow_body, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'^(.*?)(?=\bemit\s*:|\Z)', workflow_body, re.DOTALL)
    return match.group(1).strip() if match else workflow_body.strip()


def _extract_workflow_calls(body: str) -> list[str]:
    calls = re.findall(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(', body)

    built_ins = {
        'file', 'path', 'val', 'env', 'tuple', 'set', 'Channel', 'println',
        'print', 'error', 'exit', 'log', 'workflow', 'process', 'def',
        'if', 'else', 'for', 'while', 'switch', 'case', 'return',
        'String', 'Integer', 'Boolean', 'Map', 'List',
    }

    seen = set()
    ordered_calls = []
    for c in calls:
        if c not in built_ins and c not in seen:
            seen.add(c)
            ordered_calls.append(c)
    return ordered_calls
