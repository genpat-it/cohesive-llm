"""
tests/error_patterns.py
Nextflow output error patterns for parsing stdout/stderr from -preview and -stub runs.

Reference: https://training.nextflow.io/2.7.0/side_quests/debugging

Provides:
  - LINE_PATTERNS: combined list of all regex patterns (fatal + non-fatal)
  - LOCATION_TRAILER, FILE_LOCATION, NF_LOG_HINT: multi-line trailer patterns
  - extract_text_blocks(): splits output into logical blocks
  - parse_nextflow_output(): structured error dict from raw stdout/stderr
"""
import re

ErrorPattern = dict

# ============================================================
# CATEGORY 1 — SYNTAX / COMPILATION ERRORS
# ============================================================

SYNTAX_PATTERNS: list[ErrorPattern] = [
    # 1a. Script compilation error header
    {
        "pattern": re.compile(r"ERROR\s*~\s*Script compilation (?:error|failed)"),
        "category": "script_compilation_error",
        "label": "Script compilation error",
        "is_fatal": True,
        "groups": [],
        "source": "ERROR ~ Script compilation error",
    },
    # 1b. Cause line inside compilation block
    {
        "pattern": re.compile(r"-\s*cause:\s*(.+)"),
        "category": "compilation_cause",
        "label": "Compilation error cause",
        "is_fatal": True,
        "groups": ["cause_text"],
        "source": "- cause: Unexpected input: '{' @ line 3, column 23.",
    },
    # 1c. Unexpected parser input
    {
        "pattern": re.compile(
            r"Unexpected input:\s*['\"]?(.+?)['\"]?(?:\s*@\s*line\s*(\d+),?\s*column\s*(\d+))?"
        ),
        "category": "syntax_unexpected_input",
        "label": "Unexpected input token",
        "is_fatal": True,
        "groups": ["token", "line_number", "column_number"],
        "source": "Unexpected input: '{' @ line 3, column 23",
    },
    # 1d. Invalid process keyword
    {
        "pattern": re.compile(
            r"Invalid process definition\s*--\s*Unknown keyword\s*[`'\"]?(\w+)[`'\"]?"
        ),
        "category": "syntax_invalid_process_keyword",
        "label": "Invalid process keyword",
        "is_fatal": True,
        "groups": ["keyword"],
        "source": "Invalid process definition -- Unknown keyword `inputs`",
    },
    # 1e. Undefined variable
    {
        "pattern": re.compile(r"No such variable:\s*(\w+)"),
        "category": "undefined_variable",
        "label": "Undefined variable",
        "is_fatal": True,
        "groups": ["variable_name"],
        "source": "No such variable: prefix",
    },
    # 1f. Compilation error count
    {
        "pattern": re.compile(r"\b(\d+)\s+errors?\b"),
        "category": "compilation_error_count",
        "label": "Compilation error count",
        "is_fatal": True,
        "groups": ["count"],
        "source": "1 error",
    },
    # 1g. Syntax NOTE hint
    {
        "pattern": re.compile(
            r"NOTE:\s*If this is the beginning of a process or workflow"
        ),
        "category": "syntax_note_body_error",
        "label": "Possible process/workflow body syntax issue",
        "is_fatal": False,
        "groups": [],
        "source": "NOTE: If this is the beginning of a process or workflow...",
    },
    # 1h. Missing component in module
    {
        "pattern": re.compile(
            r"Cannot find a component with name\s+[`'\"]?([^`'\"]+)[`'\"]?\s+in module:\s+([^\n]+)"
        ),
        "category": "module_missing_component",
        "label": "Component not found in module",
        "is_fatal": True,
        "groups": ["component_name", "module_path"],
        "source": "Cannot find a component with name 'extractKe' in module: /path/module.nf",
    },
    # 1i. Module include file not found
    {
        "pattern": re.compile(
            r"Can't find a matching module file for include:\s+([^\n]+)"
        ),
        "category": "module_include_not_found",
        "label": "Module include failed: file not found or path incorrect",
        "is_fatal": True,
        "groups": ["include_path"],
        "source": "Can't find a matching module file for include: ../functions/commn.nf",
    },
]


# ============================================================
# CATEGORY 2 — CHANNEL STRUCTURE ERRORS
# ============================================================

CHANNEL_PATTERNS: list[ErrorPattern] = [
    # 2a. Wrong number of input channels
    {
        "pattern": re.compile(
            r"Process\s+[`'\"]?(\w+)[`'\"]?\s+declares\s+(\d+)\s+input channels?\s+but\s+(\d+)\s+were specified"
        ),
        "category": "channel_count_mismatch",
        "label": "Incorrect number of input channels",
        "is_fatal": True,
        "groups": ["process_name", "expected_count", "provided_count"],
        "source": "Process `PROCESS_FILES` declares 1 input channel but 2 were specified",
    },
    # 2b. Missing output file due to tuple mis-shape
    {
        "pattern": re.compile(
            r"Missing output file\(s\)\s+`(\[[^\]]+\][^`]*)`\s+expected by process\s+[`'\"]?(.+?)[`'\"]?"
        ),
        "category": "channel_shape_mismatch",
        "label": "Channel structure mismatch",
        "is_fatal": True,
        "groups": ["expected_file", "process_name"],
        "source": "Missing output file(s) `[sample1, file1.txt]_output.txt` expected by process `PROCESS_FILES (1)`",
    },
    # 2c. Null path emitted by channel
    {
        "pattern": re.compile(r"Path value cannot be null"),
        "category": "channel_null_value",
        "label": "Channel emitted null path value",
        "is_fatal": True,
        "groups": [],
        "source": "Path value cannot be null",
    },
]


# ============================================================
# CATEGORY 3 — PROCESS EXECUTION ERRORS
# ============================================================

PROCESS_PATTERNS: list[ErrorPattern] = [
    # 3a. Process execution failure header
    {
        "pattern": re.compile(
            r"ERROR\s*~\s*Error executing process\s*>\s*[`'\"]?(.+?)[`'\"]?"
        ),
        "category": "process_execution_error",
        "label": "Process execution error",
        "is_fatal": True,
        "groups": ["process_name"],
        "source": "ERROR ~ Error executing process > 'PROCESS_FILES (3)'",
    },
    # 3b. Missing expected output file
    {
        "pattern": re.compile(
            r"Missing output file\(s\)\s+`([^`]+)`\s+expected by process\s+[`'\"]?(.+?)[`'\"]?"
        ),
        "category": "process_missing_output",
        "label": "Missing expected output file",
        "is_fatal": True,
        "groups": ["expected_file", "process_name"],
        "source": "Missing output file(s) `sample3.txt` expected by process `PROCESS_FILES (3)`",
    },
    # 3c. Non-zero exit code
    {
        "pattern": re.compile(
            r"Process\s+[`'\"]?(.+?)[`'\"]?\s+terminated with an error exit status\s+\((\d+)\)"
        ),
        "category": "process_nonzero_exit",
        "label": "Process exited with non-zero status",
        "is_fatal": True,
        "groups": ["process_name", "exit_code"],
        "source": "Process `PROCESS_FILES (3)` terminated with an error exit status (127)",
    },
    # 3d. Command not found inside process script
    {
        "pattern": re.compile(
            r"\.command\.sh:\s*line\s*(\d+):\s*(\w+):\s*command not found"
        ),
        "category": "process_command_not_found",
        "label": "Command not found in process script",
        "is_fatal": True,
        "groups": ["line_number", "command"],
        "source": ".command.sh: line 2: cowpy: command not found",
    },
    # 3e. Generic command not found
    {
        "pattern": re.compile(r"(\w+):\s*command not found"),
        "category": "process_command_not_found",
        "label": "Command not found",
        "is_fatal": True,
        "groups": ["command"],
        "source": "cowpy: command not found",
    },
    # 3f. Process exceeded runtime limit
    {
        "pattern": re.compile(r"Process exceeded running time limit\s*\(([^)]+)\)"),
        "category": "process_time_limit_exceeded",
        "label": "Process exceeded time limit",
        "is_fatal": True,
        "groups": ["limit"],
        "source": "Process exceeded running time limit (1ms)",
    },
    # 3g. Out-of-memory termination
    {
        "pattern": re.compile(
            r"(?:Process|Task)\s+(?:killed|terminated).*?(?:out of memory|OOM|exit.*?137)"
        ),
        "category": "process_oom",
        "label": "Process killed due to out-of-memory",
        "is_fatal": True,
        "groups": [],
        "source": "Process killed: out of memory (exit 137)",
    },
    # 3h. Caused by block
    {
        "pattern": re.compile(r"Caused by:\s*(.+)"),
        "category": "process_caused_by",
        "label": "Process error cause",
        "is_fatal": True,
        "groups": ["cause_text"],
        "source": "Caused by: Missing output file(s)...",
    },
]


# ============================================================
# CATEGORY 4 — NON-FATAL / EXPECTED NOISE  (is_fatal=False)
# ============================================================

NOISE_PATTERNS: list[ErrorPattern] = [
    # 4a. WARN: file not found
    {
        "pattern": re.compile(
            r"(?:WARN|WARNING)[:\s]+file not found[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "warn_file_not_found",
        "label": "File not found warning (expected in -preview with no real data)",
        "is_fatal": False,
        "groups": ["path_glob"],
        "source": "WARN: file not found: '/home/zeynull/.../result/*.fastq*'",
    },
    # 4b. Channel defined outside workflow block
    {
        "pattern": re.compile(
            r"(?:WARN|WARNING)[:\s]+.*?channel.*?(?:outside|defined outside).*?workflow(.*)$",
            re.IGNORECASE,
        ),
        "category": "warn_channel_outside_workflow",
        "label": "Channel defined outside workflow block (bad practice, non-fatal today)",
        "is_fatal": False,
        "groups": ["detail"],
        "source": "WARN: channel defined outside workflow block",
    },
    # 4c. Missing required parameter
    {
        "pattern": re.compile(
            r"[Mm]issing\s+(?:required\s+)?(?:param(?:eter)?)[:\s]+[-\s]*['\"]?(\w+)['\"]?"
        ),
        "category": "missing_param",
        "label": "Missing pipeline parameter (expected in -preview)",
        "is_fatal": False,
        "groups": ["param_name"],
        "source": "Missing required parameter: --genome",
    },
    # 4d. Param should be provided
    {
        "pattern": re.compile(
            r"param(?:eter)?\s+should be provided[:\s]+['\"]?(\w+)['\"]?"
        ),
        "category": "missing_param",
        "label": "Parameter should be provided (expected in -preview)",
        "is_fatal": False,
        "groups": ["param_name"],
        "source": "param should be provided: genome",
    },
    # 4e. Could not find reference
    {
        "pattern": re.compile(
            r"[Cc]ould not find\s+(?:reference|genome)[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "missing_reference",
        "label": "Reference genome not found (expected in -preview)",
        "is_fatal": False,
        "groups": ["reference_name"],
        "source": "Could not find reference genome: hg38",
    },
    # 4f. No reference provided
    {
        "pattern": re.compile(r"No reference provided(.*)"),
        "category": "missing_reference",
        "label": "No reference genome provided (expected in -preview)",
        "is_fatal": False,
        "groups": ["detail"],
        "source": "No reference provided",
    },
    # 4g. Generic input file not found
    {
        "pattern": re.compile(
            r"[Ii]nput file not found[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "missing_input_file",
        "label": "Input data file not found (expected in -preview)",
        "is_fatal": False,
        "groups": ["path"],
        "source": "Input file not found: /data/sample.fastq",
    },
    # 4h. nf-core style "not found: <item>"
    {
        "pattern": re.compile(r"\bnot found:\s*['\"]?(\S+)['\"]?\s*$"),
        "category": "missing_input_file",
        "label": "Generic 'not found' (likely input data, expected in -preview)",
        "is_fatal": False,
        "groups": ["item"],
        "source": "not found: samplesheet.csv",
    },
]


# ============================================================
# MULTI-LINE / TRAILER PATTERNS
# ============================================================

LOCATION_TRAILER = re.compile(
    r"--\s+Check script\s+['\"]([^'\"]+)['\"]\s+at line[:\s]+(\d+)",
    re.MULTILINE,
)

FILE_LOCATION = re.compile(
    r"-\s*file\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)

NF_LOG_HINT = re.compile(
    r"see\s+['\"]?\.nextflow\.log['\"]?\s+file for (?:more )?details",
    re.MULTILINE | re.IGNORECASE,
)

# ============================================================
# CATEGORY 5 — DSL2-SPECIFIC ERRORS
# ============================================================

DSL2_PATTERNS: list[ErrorPattern] = [
    # 5a. Duplicate process definition
    {
        "pattern": re.compile(
            r"Duplicate process (?:definition|invocation)[:\s]+[`'\"]?(\w+)[`'\"]?"
        ),
        "category": "dsl2_duplicate_process",
        "label": "Duplicate process definition",
        "is_fatal": True,
        "groups": ["process_name"],
        "source": "Duplicate process definition: FASTP_TRIM",
    },
    # 5b. Missing 'take:' declaration in subworkflow
    {
        "pattern": re.compile(
            r"Missing ['`\"]?take['`\"]?\s+declaration"
        ),
        "category": "dsl2_missing_take",
        "label": "Missing 'take:' declaration in subworkflow",
        "is_fatal": True,
        "groups": [],
        "source": "Missing 'take' declaration in workflow 'MY_WF'",
    },
    # 5c. Missing 'emit:' declaration in subworkflow
    {
        "pattern": re.compile(
            r"Missing ['`\"]?emit['`\"]?\s+declaration"
        ),
        "category": "dsl2_missing_emit",
        "label": "Missing 'emit:' declaration in subworkflow",
        "is_fatal": True,
        "groups": [],
        "source": "Missing 'emit' declaration in workflow 'MY_WF'",
    },
    # 5d. Invalid emit name
    {
        "pattern": re.compile(
            r"Invalid emit name[:\s]+[`'\"]?(\w+)[`'\"]?"
        ),
        "category": "dsl2_invalid_emit",
        "label": "Invalid emit name",
        "is_fatal": True,
        "groups": ["emit_name"],
        "source": "Invalid emit name: 'output'",
    },
    # 5e. Deprecated operator
    {
        "pattern": re.compile(
            r"(?:WARN|WARNING)[:\s]+.*?operator\s+[`'\"]?\.?(\w+)[`'\"]?\s+(?:has been|is)\s+deprecated",
            re.IGNORECASE,
        ),
        "category": "dsl2_deprecated_operator",
        "label": "Nextflow operator deprecated",
        "is_fatal": False,
        "groups": ["operator_name"],
        "source": "WARN: operator .flatten has been deprecated",
    },
    # 5f. Not a valid module / include error
    {
        "pattern": re.compile(
            r"Not a valid (?:module|component) (?:file|path)[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "dsl2_invalid_module",
        "label": "Invalid module file or path",
        "is_fatal": True,
        "groups": ["path"],
        "source": "Not a valid module file: ./steps/bad_path.nf",
    },
    # 5g. Process already used / multiple invocations without alias
    {
        "pattern": re.compile(
            r"Process\s+[`'\"]?(\w+)[`'\"]?\s+has already been (?:invoked|used)"
        ),
        "category": "dsl2_process_reused",
        "label": "Process invoked multiple times without alias",
        "is_fatal": True,
        "groups": ["process_name"],
        "source": "Process 'BOWTIE2' has already been invoked",
    },
    # 5h. DSL version mismatch / enableDsl error
    {
        "pattern": re.compile(
            r"(?:Cannot|Unable to)\s+(?:enable|switch|use)\s+DSL\s*(\d)",
            re.IGNORECASE,
        ),
        "category": "dsl2_version_error",
        "label": "DSL version conflict",
        "is_fatal": True,
        "groups": ["dsl_version"],
        "source": "Cannot enable DSL 2 -- DSL 1 was already activated",
    },
]


# ============================================================
# COMBINED PATTERN LIST
# ============================================================

LINE_PATTERNS: list[ErrorPattern] = (
    SYNTAX_PATTERNS
    + CHANNEL_PATTERNS
    + PROCESS_PATTERNS
    + DSL2_PATTERNS
    + NOISE_PATTERNS
)


# ============================================================
# PARSING FUNCTIONS
# ============================================================

def extract_text_blocks(lines):
    """
    Extract all text blocks separated by at least one empty line.
    Returns a list of strings (blocks).
    """
    blocks = []
    current_block = []

    for line in lines:
        if line.strip() == "":
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
        else:
            current_block.append(line)

    if current_block:
        blocks.append("\n".join(current_block))

    return blocks


def parse_nextflow_output(stdout: str, stderr: str) -> dict:
    """
    Parse Nextflow stdout + stderr and return structured error info.

    Returns
    -------
    {
      "fatal_errors"    : list of matched fatal error dicts,
      "noise_errors"    : list of matched non-fatal / expected dicts,
      "unmatched_errors": list of raw lines that looked like errors but were not matched,
      "script_location" : {"file": str, "line": int} | None,
      "file_location"   : str | None,
      "output_blocks"   : list of text blocks,
      "suggestions"     : list of "Did you mean" blocks | None,
      "nf_log_hint"     : bool,
    }
    """
    combined = stderr + "\n" + stdout
    fatal_errors: list[dict] = []
    noise_errors: list[dict] = []
    unmatched_errors: list[str] = []

    for raw_line in combined.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matched = False
        for ep in LINE_PATTERNS:
            m = ep["pattern"].search(line)
            if m:
                entry = {
                    "category": ep["category"],
                    "label": ep["label"],
                    "is_fatal": ep["is_fatal"],
                    "raw": line[:400],
                    "captures": dict(zip(ep["groups"], m.groups())),
                }
                if ep["is_fatal"]:
                    fatal_errors.append(entry)
                else:
                    noise_errors.append(entry)
                matched = True
                break

        if not matched:
            # Skip bare section headers
            if line in ("Caused by:", "Command error:", "Command output:", "Command executed:"):
                continue
            if any(kw in line for kw in (
                "ERROR", "Error:", "error:", "Exception",
                "FAILED", "Caused by", "terminated",
            )):
                unmatched_errors.append(line[:400])

    loc_m = LOCATION_TRAILER.search(combined)
    file_m = FILE_LOCATION.search(combined)

    script_location = (
        {"file": loc_m.group(1), "line": int(loc_m.group(2))}
        if loc_m else None
    )
    file_location = file_m.group(1).strip() if file_m else None

    lines_list = combined.splitlines()
    output_blocks = extract_text_blocks(lines_list)
    suggestions = [block for block in output_blocks if "Did you mean" in block]
    suggestions = suggestions if suggestions else None

    nf_log_hint = bool(NF_LOG_HINT.search(combined))

    return {
        "fatal_errors": fatal_errors,
        "noise_errors": noise_errors,
        "unmatched_errors": unmatched_errors,
        "script_location": script_location,
        "file_location": file_location,
        "output_blocks": output_blocks,
        "suggestions": suggestions,
        "nf_log_hint": nf_log_hint,
    }
