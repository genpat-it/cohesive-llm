#!/usr/bin/env python3
"""
END-TO-END PIPELINE VALIDATION

Simulates a bioinformatician prompting the LLM and validates the generated
Nextflow code against the real cohesive-ngsmanager framework.

Flow: Prompt → /chat API → Nextflow code → Nextflow -preview validation

Usage:
    python test_e2e.py                          # Run all tests
    python test_e2e.py --levels 1 2             # Run only L1 and L2
    python test_e2e.py --prompt "I want to..."  # Single custom prompt
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
API_URL = os.getenv("API_URL", "http://localhost:8080")
FRAMEWORK_DIR = Path(os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager")).resolve()
PIPELINES_DIR = FRAMEWORK_DIR / "pipelines"
TEST_FILE = PIPELINES_DIR / "_llm_e2e_test.nf"

# Config file with all dummy params for -preview validation
E2E_PARAMS_CONFIG = Path(__file__).parent / "test_e2e_params.config"

# ──────────────────────────────────────────────────────────────────────────────
# VALID COMPONENTS (loaded from framework filesystem)
# ──────────────────────────────────────────────────────────────────────────────
VALID_STEPS = {f.stem for f in (FRAMEWORK_DIR / "steps").glob("*.nf")}
VALID_MODULES = {f.stem for f in (FRAMEWORK_DIR / "modules").glob("*.nf")}
ALL_VALID = VALID_STEPS | VALID_MODULES

# ──────────────────────────────────────────────────────────────────────────────
# TEST SCENARIOS
# ──────────────────────────────────────────────────────────────────────────────
SCENARIOS = [
    # L1: Simple - single tool
    {
        "id": "L1_trim_fastp",
        "level": 1,
        "prompt": "I want to trim my Illumina paired-end reads using fastp",
        "must_include": ["step_1PP_trimming__fastp"],
        "must_reject": False,
    },
    {
        "id": "L1_assembly_spades",
        "level": 1,
        "prompt": "I have trimmed reads and want to do de novo assembly with SPAdes",
        "must_include": ["step_2AS_denovo__spades"],
        "must_reject": False,
    },
    {
        "id": "L1_qc_fastqc",
        "level": 1,
        "prompt": "I want to check the quality of my reads with FastQC",
        "must_include": ["module_qc_fastqc"],
        "must_reject": False,
    },

    # L2: Medium - template-level
    {
        "id": "L2_covid",
        "level": 2,
        "prompt": "I have SARS-CoV-2 samples and want to do mapping and lineage assignment",
        "must_include": ["step_2AS_mapping__ivar", "step_4TY_lineage__pangolin"],
        "must_reject": False,
    },
    {
        "id": "L2_denovo_hostdepl",
        "level": 2,
        "prompt": "I want to do de novo assembly but first remove human host reads",
        "must_include": ["step_1PP_hostdepl__bowtie", "step_2AS_denovo__spades"],
        "must_reject": False,
    },
    {
        "id": "L2_westnile",
        "level": 2,
        "prompt": "I have West Nile Virus samples and want to determine lineage and do mapping",
        "must_include": ["step_4TY_lineage__westnile", "step_2AS_mapping__ivar"],
        "must_reject": False,
    },

    # L3: Complex - multi-step custom
    {
        "id": "L3_bacteria_typing",
        "level": 3,
        "prompt": "I have bacterial Illumina isolates. I want to identify species, do MLST, and find resistance genes",
        "must_include": ["step_3TX_species__kmerfinder", "step_4TY_MLST__mlst"],
        "must_reject": False,
    },
    {
        "id": "L3_viral_reconstruction",
        "level": 3,
        "prompt": "I want to reconstruct a viral genome: reference mapping, consensus and Prokka annotation",
        "must_include": ["step_2AS_mapping__ivar", "step_4AN_genes__prokka"],
        "must_reject": False,
    },
    {
        "id": "L3_filter_assemble",
        "level": 3,
        "prompt": "I have a mixed clinical sample. I want to extract only reads that map to a specific reference and then assemble only those",
        "must_include": ["step_1PP_filtering__bowtie", "step_2AS_denovo__spades"],
        "must_reject": False,
    },

    # L4: Negative - must reject
    {
        "id": "L4_bwa_not_exists",
        "level": 4,
        "prompt": "I want to do mapping with BWA",
        "must_include": [],
        "must_reject": True,
        "reject_reason": "BWA not in framework",
    },
    {
        "id": "L4_canu_not_exists",
        "level": 4,
        "prompt": "I want to assemble my nanopore long reads with Canu",
        "must_include": [],
        "must_reject": True,
        "reject_reason": "Canu not in framework",
    },
    {
        "id": "L4_pangolin_salmonella",
        "level": 4,
        "prompt": "I have Salmonella samples and want to determine lineage with Pangolin",
        "must_include": [],
        "must_reject": True,
        "reject_reason": "Pangolin is SARS-CoV-2 only",
    },
    {
        "id": "L4_denovo_ivar",
        "level": 4,
        "prompt": "I want to do de novo assembly with iVar",
        "must_include": [],
        "must_reject": True,
        "reject_reason": "iVar is for mapping, not assembly",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# CHAT CLIENT
# ──────────────────────────────────────────────────────────────────────────────
def chat_until_code(prompt: str, max_turns=5, expect_rejection=False) -> dict:
    """Send prompt, auto-approve, return result with code or rejection."""
    session_id = f"e2e_{int(time.time())}_{os.getpid()}"
    message = prompt

    for turn in range(max_turns):
        start = time.time()
        try:
            resp = requests.post(
                f"{API_URL}/chat",
                json={"session_id": session_id, "message": message},
                timeout=120,
            )
        except Exception as e:
            return {"ok": False, "error": str(e), "time": time.time() - start}

        elapsed = time.time() - start

        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}", "time": elapsed}

        data = resp.json()
        status = data.get("status", "CHATTING")
        code = data.get("nextflow_code")
        reply = data.get("reply", "")

        # Rejection test: return after first response
        if expect_rejection:
            return {"ok": True, "status": status, "code": code, "reply": reply, "time": elapsed, "turns": 1}

        if status == "APPROVED" and code:
            return {"ok": True, "status": status, "code": code, "reply": reply, "time": elapsed, "turns": turn + 1}

        # Auto-approve
        message = "Yes, approve it. Proceed with exactly what you suggested."

    return {"ok": False, "error": f"No code after {max_turns} turns", "reply": reply, "time": 0, "turns": max_turns}


# ──────────────────────────────────────────────────────────────────────────────
# NEXTFLOW VALIDATION
# ──────────────────────────────────────────────────────────────────────────────
def validate_nextflow(code: str) -> dict:
    """Run generated code through Nextflow -preview inside the real framework."""
    try:
        TEST_FILE.write_text(code)

        result = subprocess.run(
            ["nextflow", "run", str(TEST_FILE), "-preview", "-c", str(E2E_PARAMS_CONFIG)],
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=30,
        )

        errors = []
        if result.returncode != 0:
            for line in (result.stderr + result.stdout).split("\n"):
                line_s = line.strip()
                if line_s and any(kw in line_s for kw in ["ERROR", "Error", "error", "not found", "Unable to", "missing required"]):
                    errors.append(line_s[:200])
            if not errors:
                stderr_lines = [l.strip() for l in result.stderr.split("\n") if l.strip()]
                errors = stderr_lines[-3:] if stderr_lines else ["Unknown error (returncode != 0)"]

        # Distinguish code errors from missing-data errors.
        # Missing params/references/files are expected in -preview (no real data).
        # Code structure errors (syntax, missing includes, wrong arity) are real failures.
        param_error_keywords = [
            "missing required param", "could not find reference",
            "file not found", "No reference provided", "not found:",
            "param should be provided",
        ]
        real_errors = [e for e in errors if not any(kw in e for kw in param_error_keywords)]
        param_errors = [e for e in errors if any(kw in e for kw in param_error_keywords)]

        return {
            "valid": result.returncode == 0,
            "valid_structure": len(real_errors) == 0,  # No code errors, only missing data
            "errors": real_errors,
            "param_errors": param_errors,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:
        return {"valid": False, "errors": ["Nextflow timeout (30s)"]}
    except FileNotFoundError:
        return {"valid": False, "errors": ["Nextflow not installed"]}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}
    finally:
        if TEST_FILE.exists():
            TEST_FILE.unlink()


# ──────────────────────────────────────────────────────────────────────────────
# COMPONENT ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────
def extract_components(code: str) -> list:
    """Extract step_* and module_* calls from generated code."""
    found = set()
    found.update(re.findall(r"include\s*{\s*((?:step_|module_)\w+)", code))
    found.update(re.findall(r"\b((?:step_|module_)\w+)\s*\(", code))
    return list(found)


def check_hallucinations(components: list) -> list:
    """Return components that don't exist in the framework."""
    return [c for c in components if c not in ALL_VALID]


def check_rejection(reply: str) -> bool:
    """Check if the LLM correctly rejected the request."""
    keywords = [
        "do not have", "don't have", "not available", "doesn't exist",
        "does not exist", "cannot", "can't", "not supported", "only for",
        "is not", "not in", "instead", "alternative", "i can offer",
        "i can suggest", "not in my", "no tool",
        "non disponibile", "non esiste", "non supportato",
    ]
    reply_lower = (reply or "").lower()
    return any(kw in reply_lower for kw in keywords)


# ──────────────────────────────────────────────────────────────────────────────
# RUNNER
# ──────────────────────────────────────────────────────────────────────────────
def run_scenario(scenario: dict) -> dict:
    """Run a single test scenario end-to-end."""
    sid = scenario["id"]
    is_negative = scenario["must_reject"]

    print(f"\n{'='*60}")
    print(f"  {sid} (L{scenario['level']}){' [NEGATIVE]' if is_negative else ''}")
    print(f"  {scenario['prompt'][:75]}")
    print(f"{'='*60}")

    # Step 1: Chat
    chat = chat_until_code(scenario["prompt"], expect_rejection=is_negative)

    result = {
        "id": sid,
        "level": scenario["level"],
        "prompt": scenario["prompt"],
        "api_ok": chat["ok"],
        "api_time": chat.get("time", 0),
        "turns": chat.get("turns", 0),
        "code": chat.get("code"),
        "reply": chat.get("reply", ""),
        "error": chat.get("error"),
        # Validation
        "nf_valid": False,
        "nf_errors": [],
        "components": [],
        "hallucinations": [],
        "missing": [],
        # Negative
        "correctly_rejected": False,
        # Verdict
        "pass": False,
    }

    # Step 2: Negative test handling
    if is_negative:
        has_code = bool(chat.get("code"))
        rejected = check_rejection(chat.get("reply", ""))
        result["correctly_rejected"] = not has_code and rejected
        result["pass"] = result["correctly_rejected"]

        status = "PASS - correctly rejected" if result["pass"] else "FAIL - should have rejected"
        print(f"  [{status}]")
        return result

    # Step 3: Check API success
    if not chat["ok"]:
        print(f"  [FAIL] API error: {chat.get('error', '?')}")
        return result

    code = chat["code"]
    if not code:
        print(f"  [FAIL] No code generated")
        return result

    print(f"  [OK] Code generated ({len(code)} chars, {chat.get('turns', '?')} turns, {chat.get('time', 0):.1f}s)")

    # Step 4: Component analysis
    result["components"] = extract_components(code)
    result["hallucinations"] = check_hallucinations(result["components"])
    result["missing"] = [c for c in scenario["must_include"] if c not in result["components"]]

    if result["hallucinations"]:
        print(f"  [FAIL] Hallucinated: {result['hallucinations']}")
    if result["missing"]:
        print(f"  [WARN] Missing: {result['missing']}")
    else:
        print(f"  [OK] All expected components found")

    # Step 5: Nextflow validation
    nf = validate_nextflow(code)
    result["nf_valid"] = nf["valid"]
    result["nf_valid_structure"] = nf.get("valid_structure", nf["valid"])
    result["nf_errors"] = nf["errors"]
    result["nf_param_errors"] = nf.get("param_errors", [])

    if nf["valid"]:
        print(f"  [OK] Nextflow -preview PASSED")
    elif nf.get("valid_structure"):
        print(f"  [OK] Nextflow structure valid (param-only errors: {nf.get('param_errors', [])[:1]})")
    else:
        print(f"  [FAIL] Nextflow code error: {nf['errors'][:2]}")

    # Verdict: pass if no hallucinations, no missing components, and no CODE errors
    # (missing params/references are expected without real data)
    result["pass"] = (
        len(result["hallucinations"]) == 0
        and len(result["missing"]) == 0
        and result["nf_valid_structure"]
    )

    verdict = "PASS" if result["pass"] else "FAIL"
    print(f"  [{verdict}]")
    return result


# ──────────────────────────────────────────────────────────────────────────────
# REPORT
# ──────────────────────────────────────────────────────────────────────────────
def generate_report(results: list, output: str):
    """Generate markdown report."""
    lines = [
        "# E2E Pipeline Validation Report",
        f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"API: {API_URL}",
        f"Framework: {FRAMEWORK_DIR}",
        f"Tests: {len(results)}",
    ]

    # Summary
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    hallucinations = sum(len(r["hallucinations"]) for r in results)
    nf_passed = sum(1 for r in results if r["nf_valid"] or r.get("correctly_rejected"))

    lines.append(f"\n## Summary")
    lines.append(f"- **Pass rate**: {passed}/{total} ({100*passed/total:.0f}%)")
    lines.append(f"- **Nextflow valid**: {nf_passed}/{total}")
    lines.append(f"- **Hallucinations**: {hallucinations}")

    # Per level
    lines.append(f"\n| Level | Pass | Total |")
    lines.append(f"|-------|------|-------|")
    for level in [1, 2, 3, 4]:
        lvl_results = [r for r in results if r["level"] == level]
        if lvl_results:
            lvl_pass = sum(1 for r in lvl_results if r["pass"])
            lines.append(f"| L{level} | {lvl_pass}/{len(lvl_results)} | {'OK' if lvl_pass == len(lvl_results) else 'ISSUES'} |")

    # Details
    lines.append(f"\n## Details\n")
    for r in results:
        icon = "PASS" if r["pass"] else "FAIL"
        lines.append(f"### [{icon}] {r['id']}")
        lines.append(f"**Prompt**: {r['prompt']}")

        if r.get("correctly_rejected"):
            lines.append(f"- Correctly rejected (negative test)")
        elif r.get("must_reject") and not r.get("correctly_rejected"):
            lines.append(f"- **FAILED to reject** (negative test)")
        else:
            lines.append(f"- Components: {', '.join(r['components']) or 'None'}")
            if r["hallucinations"]:
                lines.append(f"- **HALLUCINATED**: {', '.join(r['hallucinations'])}")
            if r["missing"]:
                lines.append(f"- Missing: {', '.join(r['missing'])}")
            if r["nf_valid"]:
                lines.append(f"- Nextflow: PASSED")
            elif r.get("nf_valid_structure"):
                lines.append(f"- Nextflow: structure OK (needs real data/params)")
            else:
                lines.append(f"- Nextflow: FAILED")
            if r["nf_errors"]:
                lines.append(f"- Code errors: {'; '.join(r['nf_errors'][:3])}")
            if r.get("nf_param_errors"):
                lines.append(f"- Param errors (expected): {'; '.join(r['nf_param_errors'][:2])}")
            if r["code"]:
                lines.append(f"\n<details><summary>Code ({len(r['code'])} chars)</summary>\n")
                lines.append(f"```nextflow\n{r['code']}\n```\n</details>")

        lines.append("")

    report = "\n".join(lines)
    Path(output).write_text(report)
    print(f"\nReport: {output}")
    return report


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
def main():
    global API_URL

    parser = argparse.ArgumentParser(description="E2E Pipeline Validation")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--levels", "-l", type=int, nargs="+", help="Filter by level")
    parser.add_argument("--prompt", "-p", help="Run a single custom prompt")
    parser.add_argument("--output", "-o", default="e2e_report.md")
    args = parser.parse_args()

    API_URL = args.api_url

    # Health check
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        assert r.status_code == 200
        print(f"API OK ({API_URL})")
    except Exception:
        print(f"API not reachable at {API_URL}")
        sys.exit(1)

    # Check nextflow
    try:
        subprocess.run(["nextflow", "-version"], capture_output=True, timeout=10)
        print(f"Nextflow OK")
    except Exception:
        print("WARNING: Nextflow not found, skipping NF validation")

    # Run
    if args.prompt:
        # Single custom prompt
        scenario = {
            "id": "custom",
            "level": 0,
            "prompt": args.prompt,
            "must_include": [],
            "must_reject": False,
        }
        results = [run_scenario(scenario)]
    else:
        scenarios = SCENARIOS
        if args.levels:
            scenarios = [s for s in scenarios if s["level"] in args.levels]

        print(f"\nRunning {len(scenarios)} scenarios...\n")
        results = []
        for s in scenarios:
            results.append(run_scenario(s))
            time.sleep(2)

    # Report
    report = generate_report(results, args.output)
    print(f"\n{'='*60}")
    print(report)

    # Exit code
    all_pass = all(r["pass"] for r in results)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
