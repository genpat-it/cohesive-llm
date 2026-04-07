#!/usr/bin/env python3
"""
NEXTFLOW PIPELINE VALIDATOR

Validates generated Nextflow code by:
1. Syntax check (nextflow -preview)
2. Stub run (nextflow run -stub) - tests workflow logic without execution
3. Full run (nextflow run) - requires test data

Usage:
    # From API response
    python validate_pipeline.py --code "nextflow code here"

    # From file
    python validate_pipeline.py --file generated_pipeline.nf

    # Interactive test with API
    python validate_pipeline.py --prompt "I want to do MLST on bacterial samples"

    # Stub run (test workflow logic)
    python validate_pipeline.py --file pipeline.nf --stub
"""

import argparse
import subprocess
import tempfile
import os
import sys
import json
import requests
from pathlib import Path

# Framework directory where includes are resolved
FRAMEWORK_DIR = Path(os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager")).resolve()
API_URL = "http://localhost:8080"

def check_syntax(code: str) -> dict:
    """Level 1: Syntax validation only"""

    # Create temp file directly in pipelines/ so ../functions/ works
    test_dir = FRAMEWORK_DIR / "pipelines"
    test_file = test_dir / "_llm_test_pipeline.nf"

    try:
        test_file.write_text(code)

        # Run nextflow syntax check
        result = subprocess.run(
            ["nextflow", "run", str(test_file), "-preview"],
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=30
        )

        success = result.returncode == 0

        return {
            "level": "syntax",
            "success": success,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "errors": extract_errors(result.stderr) if not success else []
        }

    except subprocess.TimeoutExpired:
        return {"level": "syntax", "success": False, "errors": ["Timeout after 30s"]}
    except Exception as e:
        return {"level": "syntax", "success": False, "errors": [str(e)]}
    finally:
        if test_file.exists():
            test_file.unlink()


def check_stub(code: str) -> dict:
    """Level 2: Stub run - tests workflow logic without executing processes"""

    test_dir = FRAMEWORK_DIR / "pipelines"
    test_file = test_dir / "_llm_test_pipeline.nf"
    work_dir = FRAMEWORK_DIR / "_llm_test_work"

    try:
        test_file.write_text(code)

        # Run with -stub flag
        result = subprocess.run(
            [
                "nextflow", "run", str(test_file),
                "-stub",
                "-work-dir", str(work_dir),
                "--outdir", str(test_dir / "output")
            ],
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=60
        )

        success = result.returncode == 0

        return {
            "level": "stub",
            "success": success,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "errors": extract_errors(result.stderr) if not success else []
        }

    except subprocess.TimeoutExpired:
        return {"level": "stub", "success": False, "errors": ["Timeout after 60s"]}
    except Exception as e:
        return {"level": "stub", "success": False, "errors": [str(e)]}
    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()
        if work_dir.exists():
            subprocess.run(["rm", "-rf", str(work_dir)], capture_output=True)


def extract_errors(stderr: str) -> list:
    """Extract meaningful error messages from Nextflow stderr"""
    errors = []
    lines = stderr.split('\n')

    capture_next = False
    for line in lines:
        if 'ERROR' in line or 'Error' in line:
            errors.append(line.strip())
            capture_next = True
        elif capture_next and line.strip():
            errors.append(line.strip())
            capture_next = False
        elif 'No such file' in line or 'not found' in line.lower():
            errors.append(line.strip())
        elif 'Unable to' in line:
            errors.append(line.strip())

    return errors[:10]  # Limit to 10 errors


def get_code_from_api(prompt: str, verbose: bool = False) -> str:
    """Get generated code from API"""
    import time

    session_id = f"validate_{os.getpid()}_{int(time.time())}"

    # Send initial prompt
    response = requests.post(
        f"{API_URL}/chat",
        json={"session_id": session_id, "message": prompt}
    )

    if response.status_code != 200:
        raise Exception(f"API error: {response.status_code} - {response.text}")

    data = response.json()
    if verbose:
        print(f"  [Turn 1] Status: {data.get('status')}, Code: {'Yes' if data.get('nextflow_code') else 'No'}")

    # Keep chatting until we get code or give up
    max_turns = 5
    for i in range(2, max_turns + 1):
        if data.get("nextflow_code"):
            return data["nextflow_code"]

        status = data.get("status", "")

        if status == "APPROVED":
            # Plan approved, code should be generated
            # Sometimes needs a follow-up to trigger generation
            response = requests.post(
                f"{API_URL}/chat",
                json={"session_id": session_id, "message": "generate the pipeline"}
            )
        elif status == "CHATTING":
            # Approve the plan
            response = requests.post(
                f"{API_URL}/chat",
                json={"session_id": session_id, "message": "yes, that looks good, please proceed"}
            )
        else:
            break

        if response.status_code != 200:
            raise Exception(f"API error on turn {i}: {response.status_code}")

        data = response.json()
        if verbose:
            print(f"  [Turn {i}] Status: {data.get('status')}, Code: {'Yes' if data.get('nextflow_code') else 'No'}")

    if data.get("nextflow_code"):
        return data["nextflow_code"]

    raise Exception(f"No code generated after {max_turns} turns. Last status: {data.get('status')}, Reply: {data.get('reply', '')[:200]}")


def print_result(result: dict, verbose: bool = False):
    """Pretty print validation result"""

    level = result.get("level", "unknown")
    success = result.get("success", False)

    status = "✅ PASS" if success else "❌ FAIL"
    print(f"\n{'='*60}")
    print(f"  {level.upper()} VALIDATION: {status}")
    print(f"{'='*60}")

    if result.get("errors"):
        print("\nErrors:")
        for err in result["errors"]:
            print(f"  - {err}")

    if verbose and result.get("stderr"):
        print("\nStderr:")
        print(result["stderr"])

    if verbose and result.get("stdout"):
        print("\nStdout:")
        print(result["stdout"])


def main():
    parser = argparse.ArgumentParser(description="Validate Nextflow pipelines")

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--code", help="Nextflow code string")
    input_group.add_argument("--file", help="Path to .nf file")
    input_group.add_argument("--prompt", help="Generate code from API prompt")

    parser.add_argument("--stub", action="store_true", help="Run stub test (level 2)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Get code
    if args.code:
        code = args.code
    elif args.file:
        code = Path(args.file).read_text()
    elif args.prompt:
        print(f"Generating code for: {args.prompt}")
        try:
            code = get_code_from_api(args.prompt, verbose=args.verbose)
            print(f"Generated {len(code)} chars of code")
        except Exception as e:
            print(f"Failed to get code from API: {e}")
            sys.exit(1)

    # Validate
    results = []

    # Level 1: Syntax
    print("\n[1/2] Checking syntax...")
    syntax_result = check_syntax(code)
    results.append(syntax_result)

    if not args.json:
        print_result(syntax_result, args.verbose)

    # Level 2: Stub (optional)
    if args.stub and syntax_result["success"]:
        print("\n[2/2] Running stub test...")
        stub_result = check_stub(code)
        results.append(stub_result)

        if not args.json:
            print_result(stub_result, args.verbose)

    # Output
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n" + "="*60)
        all_passed = all(r["success"] for r in results)
        print(f"  OVERALL: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
        print("="*60)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
