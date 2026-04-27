"""
tests/nf_validation.py
Nextflow syntax and stub-run validation helpers.

These functions write generated Nextflow code to a temporary file inside the
framework directory and run `nextflow -preview` (syntax) or `nextflow -stub`
(logic) against it. Results are returned as structured dicts.

Set the NF_FRAMEWORK_DIR environment variable to point to your Nextflow framework.
If unset, validation is gracefully skipped.
"""
import os
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
FRAMEWORK_DIR = Path(os.environ.get("NF_FRAMEWORK_DIR", PROJECT_DIR / "nf_framework"))
E2E_PARAMS_CONFIG = FRAMEWORK_DIR / "conf" / "e2e_params.config"

from tests.error_patterns import parse_nextflow_output

def _format_parsed_errors(stdout: str, stderr: str) -> list[str]:
    parsed = parse_nextflow_output(stdout, stderr)
    if not parsed["fatal_errors"]:
        return []
    return [f"{e.get('label')}: {e.get('raw', '')}" for e in parsed["fatal_errors"]]

def check_syntax(code: str) -> dict:
    """
    Level 1: Syntax validation only (nextflow -preview).
    Returns {"level": "syntax", "success": bool, ...}
    """
    if not FRAMEWORK_DIR.exists():
        return {
            "level": "syntax",
            "success": None,
            "skipped": True,
            "reason": f"FRAMEWORK_DIR not found: {FRAMEWORK_DIR}",
        }

    test_dir = FRAMEWORK_DIR / "pipelines"
    test_file = test_dir / "_llm_test_pipeline.nf"

    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file.write_text(code)

        args = ["nextflow", "run", str(test_file), "-preview"]
        if E2E_PARAMS_CONFIG.exists():
            args += ["-c", str(E2E_PARAMS_CONFIG)]

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=30,
        )

        return {
            "level": "syntax",
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "parsed_errors": _format_parsed_errors(result.stdout, result.stderr)
        }

    except subprocess.TimeoutExpired:
        return {"level": "syntax", "success": False, "errors": ["Timeout after 30s"]}
    except Exception as e:
        return {"level": "syntax", "success": False, "errors": [str(e)]}
    finally:
        if test_file.exists():
            test_file.unlink()


def check_stub(code: str) -> dict:
    """
    Level 2: Stub run — tests workflow logic without executing real processes.
    Returns {"level": "stub", "success": bool, ...}
    """
    if not FRAMEWORK_DIR.exists():
        return {
            "level": "stub",
            "success": None,
            "skipped": True,
            "reason": f"FRAMEWORK_DIR not found: {FRAMEWORK_DIR}",
        }

    test_dir = FRAMEWORK_DIR / "pipelines"
    test_file = test_dir / "_llm_test_pipeline.nf"
    work_dir = FRAMEWORK_DIR / "_llm_test_work"

    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file.write_text(code)

        result = subprocess.run(
            [
                "nextflow", "run", str(test_file),
                "-stub",
                "-work-dir", str(work_dir),
                "--outdir", str(test_dir / "output"),
            ],
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=60,
        )

        return {
            "level": "stub",
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "parsed_errors": _format_parsed_errors(result.stdout, result.stderr)
        }

    except subprocess.TimeoutExpired:
        return {"level": "stub", "success": False, "errors": ["Timeout after 60s"]}
    except Exception as e:
        return {"level": "stub", "success": False, "errors": [str(e)]}
    finally:
        if test_file.exists():
            test_file.unlink()
        if work_dir.exists():
            subprocess.run(["rm", "-rf", str(work_dir)], capture_output=True)


# ──────────────────────────────────────────────────────────────
# Public helper — wraps the full validation flow for test files
# ──────────────────────────────────────────────────────────────

def validate_nextflow(code: str, run_stub: bool = False) -> dict:
    """
    Run Nextflow compiler validation on generated code.

    Returns a dict with keys ready to merge into test ``details``:
        nf_syntax_passed : bool | None (None = skipped)
        nf_syntax_error  : str  (only on failure)
        nf_stub_passed   : bool | None
        nf_stub_error    : str  (only on failure)

    Parameters
    ----------
    code : str
        The generated Nextflow DSL2 code to validate.
    run_stub : bool
        If True, also run ``check_stub`` when syntax passes.
        Recommended for L3/L5 (complex pipelines) but not L1/L2
        (single-tool, where stub adds latency for little value).
    """
    out = {}

    syntax_res = check_syntax(code)
    if syntax_res.get("skipped"):
        return out  # NF not available — return empty (tests still pass)

    out["nf_syntax_passed"] = syntax_res.get("success")

    if not syntax_res.get("success"):
        if syntax_res.get("parsed_errors"):
            out["nf_syntax_error"] = "; ".join(syntax_res["parsed_errors"])[:300]
        else:
            out["nf_syntax_error"] = str(
                syntax_res.get("errors", syntax_res.get("stderr", ""))
            )[:300]
        return out  # syntax failed — skip stub

    if run_stub:
        stub_res = check_stub(code)
        if not stub_res.get("skipped"):
            out["nf_stub_passed"] = stub_res.get("success")
            if not stub_res.get("success"):
                if stub_res.get("parsed_errors"):
                    out["nf_stub_error"] = "; ".join(stub_res["parsed_errors"])[:300]
                else:
                    out["nf_stub_error"] = str(
                        stub_res.get("errors", stub_res.get("stderr", ""))
                    )[:300]

    return out
