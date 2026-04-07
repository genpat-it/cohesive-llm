#!/usr/bin/env python3
"""
Generate a comprehensive validation report in Markdown.

For each prompt: sends to API, collects Nextflow code + Mermaid,
validates Nextflow with -preview, checks Mermaid structure.

Usage:
    python generate_report.py --output VALIDATION_REPORT.md
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

API_URL = os.getenv("API_URL", "http://localhost:8080")
FRAMEWORK_DIR = Path(os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager")).resolve()
PARAMS_CONFIG = Path(__file__).parent / "test_e2e_params.config"

# ──────────────────────────────────────────────────────────────────────────────
# 20+ PROMPTS — realistic bioinformatician requests
# ──────────────────────────────────────────────────────────────────────────────
PROMPTS = [
    # L1: Simple single-tool
    {"id": "01", "prompt": "I want to trim my Illumina paired-end reads using fastp", "level": "Simple"},
    {"id": "02", "prompt": "I have trimmed reads and want to do de novo assembly with SPAdes", "level": "Simple"},
    {"id": "03", "prompt": "I want to check the quality of my reads with FastQC", "level": "Simple"},
    {"id": "04", "prompt": "I want to assemble nanopore long reads with Flye", "level": "Simple"},
    {"id": "05", "prompt": "I want to trim my nanopore reads with Chopper", "level": "Simple"},

    # L2: Template-level
    {"id": "06", "prompt": "I have SARS-CoV-2 samples and want to do mapping and lineage assignment", "level": "Medium"},
    {"id": "07", "prompt": "I want to do de novo assembly but first remove human host reads", "level": "Medium"},
    {"id": "08", "prompt": "I have West Nile Virus samples and want to determine lineage and do mapping", "level": "Medium"},
    {"id": "09", "prompt": "I want to map reads to a reference using Bowtie2", "level": "Medium"},
    {"id": "10", "prompt": "I want to do species identification with KmerFinder on my assemblies", "level": "Medium"},

    # L3: Complex multi-step
    {"id": "11", "prompt": "I have bacterial Illumina isolates. I want to identify species, do MLST, and find resistance genes", "level": "Complex"},
    {"id": "12", "prompt": "I want to reconstruct a viral genome: reference mapping, consensus and Prokka annotation", "level": "Complex"},
    {"id": "13", "prompt": "I have a mixed clinical sample. I want to extract only reads that map to a specific reference and then assemble only those", "level": "Complex"},
    {"id": "14", "prompt": "I want to trim Illumina reads with fastp, do de novo assembly with SPAdes, and then run Abricate for AMR detection", "level": "Complex"},
    {"id": "15", "prompt": "I have bacterial isolates. Trim with fastp, assemble with Shovill, identify species with KmerFinder, and run MLST", "level": "Complex"},
    {"id": "16", "prompt": "I want to do host depletion with Bowtie, then assemble the depleted reads with SPAdes, and annotate with Prokka", "level": "Complex"},

    # L4: Negative — must reject
    {"id": "17", "prompt": "I want to do mapping with BWA", "level": "Negative", "expect_reject": True},
    {"id": "18", "prompt": "I want to assemble my nanopore long reads with Canu", "level": "Negative", "expect_reject": True},
    {"id": "19", "prompt": "I have Salmonella samples and want to determine lineage with Pangolin", "level": "Negative", "expect_reject": True},
    {"id": "20", "prompt": "I want to do de novo assembly with iVar", "level": "Negative", "expect_reject": True},
    {"id": "21", "prompt": "I want to run Trimgalore on my reads", "level": "Negative", "expect_reject": True},
    {"id": "22", "prompt": "I want to use GATK for variant calling", "level": "Negative", "expect_reject": True},
]


def chat_until_code(prompt, max_turns=5, expect_reject=False):
    session_id = f"report_{int(time.time())}_{os.getpid()}"
    message = prompt

    for turn in range(max_turns):
        try:
            resp = requests.post(
                f"{API_URL}/chat",
                json={"session_id": session_id, "message": message},
                timeout=120,
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}

        data = resp.json()
        status = data.get("status", "CHATTING")
        code = data.get("nextflow_code")
        mermaid = data.get("mermaid_code")
        reply = data.get("reply", "")

        if expect_reject:
            return {"ok": True, "status": status, "code": code, "mermaid": mermaid, "reply": reply, "turns": 1}

        if status == "APPROVED" and code:
            return {"ok": True, "status": status, "code": code, "mermaid": mermaid, "reply": reply, "turns": turn + 1}

        message = "Yes, approve it. Proceed with exactly what you suggested."

    return {"ok": False, "error": f"No code after {max_turns} turns", "reply": reply}


def validate_nextflow(code):
    test_file = FRAMEWORK_DIR / "pipelines" / "_report_test.nf"
    try:
        test_file.write_text(code)
        result = subprocess.run(
            ["nextflow", "run", str(test_file), "-preview", "-c", str(PARAMS_CONFIG)],
            capture_output=True, text=True, cwd=str(FRAMEWORK_DIR), timeout=30,
        )
        stderr = result.stderr + result.stdout
        param_keywords = ["missing required param", "could not find reference", "file not found", "param should be provided"]
        errors = [l.strip() for l in stderr.split("\n") if "ERROR" in l or "Error" in l]
        real_errors = [e for e in errors if not any(kw in e for kw in param_keywords)]
        return {"runs": result.returncode == 0, "structure_ok": len(real_errors) == 0, "errors": real_errors}
    except Exception as e:
        return {"runs": False, "structure_ok": False, "errors": [str(e)]}
    finally:
        if test_file.exists():
            test_file.unlink()


def validate_mermaid(mermaid):
    if not mermaid:
        return {"valid": False, "reason": "No Mermaid generated"}
    if not mermaid.startswith("flowchart"):
        return {"valid": False, "reason": "Missing flowchart header"}
    has_nodes = bool(re.search(r'\w+\[', mermaid) or re.search(r'\w+\(\[', mermaid))
    if not has_nodes:
        return {"valid": False, "reason": "No nodes found"}
    # Edges are optional for single-step pipelines
    return {"valid": True, "reason": "OK"}


def check_rejection(reply):
    keywords = [
        "do not have", "don't have", "not available", "doesn't exist",
        "does not exist", "cannot", "can't", "not supported", "only for",
        "is not", "not in", "instead", "alternative", "i can offer", "i can suggest",
    ]
    return any(kw in (reply or "").lower() for kw in keywords)


def main():
    global API_URL

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", default="VALIDATION_REPORT.md")
    parser.add_argument("--api-url", default=API_URL)
    args = parser.parse_args()

    API_URL = args.api_url

    # Health check
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        assert r.status_code == 200
    except Exception:
        print(f"API not reachable at {API_URL}")
        sys.exit(1)

    print(f"Running {len(PROMPTS)} prompts...\n")

    results = []
    for p in PROMPTS:
        expect_reject = p.get("expect_reject", False)
        print(f"  [{p['id']}] {p['prompt'][:60]}...", end=" ", flush=True)

        chat = chat_until_code(p["prompt"], expect_reject=expect_reject)

        r = {
            "id": p["id"],
            "level": p["level"],
            "prompt": p["prompt"],
            "expect_reject": expect_reject,
        }

        if expect_reject:
            rejected = not chat.get("code") and check_rejection(chat.get("reply", ""))
            r["rejected"] = rejected
            r["reply"] = chat.get("reply", "")
            print("REJECTED" if rejected else "FAIL (should reject)")
        elif chat.get("ok") and chat.get("code"):
            r["code"] = chat["code"]
            r["mermaid"] = chat.get("mermaid", "")
            r["turns"] = chat.get("turns", 0)

            nf = validate_nextflow(chat["code"])
            r["nf_runs"] = nf["runs"]
            r["nf_structure_ok"] = nf["structure_ok"]
            r["nf_errors"] = nf["errors"]

            mv = validate_mermaid(chat.get("mermaid", ""))
            r["mermaid_valid"] = mv["valid"]
            r["mermaid_reason"] = mv["reason"]

            status = "PASS" if r["nf_structure_ok"] and r["mermaid_valid"] else "ISSUES"
            print(f"{status} (NF:{'ok' if r['nf_structure_ok'] else 'ERR'} Mermaid:{'ok' if r['mermaid_valid'] else 'ERR'})")
        else:
            r["error"] = chat.get("error", "Unknown")
            print(f"FAIL: {r['error'][:50]}")

        results.append(r)
        time.sleep(2)

    # ── Generate Report ──
    lines = [
        "# IZS-LLM Validation Report",
        "",
        f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**API**: {API_URL}",
        f"**Framework**: `{FRAMEWORK_DIR}`",
        f"**Prompts tested**: {len(results)}",
        "",
    ]

    # Summary table
    positive = [r for r in results if not r.get("expect_reject")]
    negative = [r for r in results if r.get("expect_reject")]
    nf_ok = sum(1 for r in positive if r.get("nf_structure_ok"))
    mermaid_ok = sum(1 for r in positive if r.get("mermaid_valid"))
    rejected_ok = sum(1 for r in negative if r.get("rejected"))
    errors = sum(1 for r in positive if r.get("error"))

    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Result |")
    lines.append(f"|--------|--------|")
    lines.append(f"| Positive tests | {len(positive)} |")
    lines.append(f"| Nextflow valid | {nf_ok}/{len(positive)} |")
    lines.append(f"| Mermaid valid | {mermaid_ok}/{len(positive)} |")
    lines.append(f"| API errors | {errors}/{len(positive)} |")
    lines.append(f"| Negative tests (rejection) | {rejected_ok}/{len(negative)} |")
    lines.append("")

    # Per-level breakdown
    for level in ["Simple", "Medium", "Complex", "Negative"]:
        lvl = [r for r in results if r["level"] == level]
        if not lvl:
            continue
        if level == "Negative":
            ok = sum(1 for r in lvl if r.get("rejected"))
        else:
            ok = sum(1 for r in lvl if r.get("nf_structure_ok") and r.get("mermaid_valid"))
        lines.append(f"- **{level}**: {ok}/{len(lvl)}")
    lines.append("")

    # Detailed results
    lines.append("---")
    lines.append("")

    for r in results:
        icon = ""
        if r.get("expect_reject"):
            icon = "pass" if r.get("rejected") else "FAIL"
        elif r.get("nf_structure_ok") and r.get("mermaid_valid"):
            icon = "pass"
        elif r.get("error"):
            icon = "FAIL"
        else:
            icon = "WARN"

        lines.append(f"## [{r['id']}] {r['prompt']}")
        lines.append(f"**Level**: {r['level']} | **Result**: {icon}")
        lines.append("")

        if r.get("expect_reject"):
            lines.append(f"**Expected**: Rejection")
            lines.append(f"**Rejected**: {'Yes' if r.get('rejected') else 'No'}")
            if r.get("reply"):
                lines.append(f"\n> {r['reply'][:300]}")
            lines.append("")
            continue

        if r.get("error"):
            lines.append(f"**Error**: {r['error']}")
            lines.append("")
            continue

        # Nextflow
        nf_status = "Runs" if r.get("nf_runs") else ("Structure OK" if r.get("nf_structure_ok") else "FAILED")
        lines.append(f"**Nextflow**: {nf_status}")
        if r.get("nf_errors"):
            for e in r["nf_errors"][:2]:
                lines.append(f"- `{e[:150]}`")

        # Mermaid
        lines.append(f"**Mermaid**: {'Valid' if r.get('mermaid_valid') else r.get('mermaid_reason', 'Unknown')}")
        lines.append("")

        # Code
        if r.get("code"):
            lines.append("<details><summary>Nextflow Code</summary>")
            lines.append("")
            lines.append("```nextflow")
            lines.append(r["code"])
            lines.append("```")
            lines.append("</details>")
            lines.append("")

        # Mermaid
        if r.get("mermaid"):
            lines.append("<details><summary>Mermaid Diagram</summary>")
            lines.append("")
            lines.append("```mermaid")
            lines.append(r["mermaid"])
            lines.append("```")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")

    report = "\n".join(lines)
    Path(args.output).write_text(report)
    print(f"\nReport written to {args.output}")


if __name__ == "__main__":
    main()
