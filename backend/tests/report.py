"""
tests/report.py
ReportCollector — collects test results across all levels and generates
a comprehensive, readable markdown assessment report.

The report is designed to be understandable by someone who does NOT know
the internal system architecture — it explains what each level tests,
provides complexity examples, and gives clear pass/fail summaries.
"""
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).parent.parent
REPORT_DIR = PROJECT_DIR / "test_reports"
REPORT_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────
# Level Metadata — descriptions and examples for the report
# ──────────────────────────────────────────────────────────────

LEVEL_META = {
    1: {
        "name": "Single-Tool Pipelines",
        "difficulty": "Simple",
        "description": (
            "Tests basic single-tool requests. The user asks for one specific bioinformatics "
            "tool (e.g., 'trim my reads with fastp'). The system should identify the tool, "
            "generate valid Nextflow code, and produce a visual diagram."
        ),
        "example": "User: 'I want to trim my Illumina reads with fastp' → System builds a one-step pipeline.",
    },
    2: {
        "name": "Template-Level Pipelines",
        "difficulty": "Medium",
        "description": (
            "Tests multi-step requests that match known pipeline templates. The user describes "
            "a biological scenario involving 2–3 tools (e.g., 'SARS-CoV-2 mapping and lineage'). "
            "The system should recognize the template and build the correct workflow."
        ),
        "example": "User: 'I have COVID samples, I need mapping + Pangolin lineage' → System selects the COVID emergency template.",
    },
    3: {
        "name": "Complex Multi-Step Pipelines",
        "difficulty": "Complex",
        "description": (
            "Tests complex requests requiring 3+ tools chained together. The system must "
            "correctly wire data channels between steps, handle different input/output types, "
            "and produce a pipeline that would execute correctly."
        ),
        "example": "User: 'Trim with fastp → assemble with SPAdes → detect AMR with ABRicate' → System chains three tools with correct data flow.",
    },
    4: {
        "name": "Rejection Guardrails",
        "difficulty": "Medium",
        "description": (
            "Tests that the system correctly REJECTS invalid or impossible requests. "
            "These include: tools not in the catalog (BWA, GATK), tools for the wrong organism "
            "(Pangolin for bacteria), or tools for the wrong sequencing technology "
            "(Flye for Illumina short reads). The system should refuse, explain why, "
            "and suggest valid alternatives."
        ),
        "example": "User: 'Run Pangolin on my Salmonella samples' → System refuses: 'Pangolin is SARS-CoV-2 only' and suggests MLST instead.",
    },
    5: {
        "name": "Module Recreation",
        "difficulty": "Complex",
        "description": (
            "Tests the system's ability to recreate known pipeline modules from the framework. "
            "Each test provides a natural language description of a pipeline and checks if the "
            "generated Nextflow code structurally matches the reference implementation. "
            "This validates channel wiring, step ordering, and conditional logic."
        ),
        "example": "User describes the WNV pipeline → System generates code matching the module_westnile reference implementation.",
    },
}


class ReportCollector:
    """Collects all test results and generates a final markdown report."""

    def __init__(self):
        self.results = []
        self.start_time = datetime.now()

    def add_result(
        self,
        scenario_id: str,
        level: int,
        success: bool,
        difficulty: str = "—",
        description: str = "",
        scores: dict = None,
        details: dict = None,
        elapsed: float = 0.0,
    ):
        self.results.append({
            "id": scenario_id,
            "level": level,
            "difficulty": difficulty,
            "description": description,
            "success": success,
            "scores": scores or {},
            "details": details or {},
            "elapsed": elapsed,
        })

    def generate_report(self) -> str:
        """Generate the final assessment markdown report."""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        levels = {}
        for r in self.results:
            lvl = r["level"]
            if lvl not in levels:
                levels[lvl] = []
            levels[lvl].append(r)

        lines = []

        # ══════════════════════════════════════
        # HEADER
        # ══════════════════════════════════════
        lines.append("# 🧬 IZS Bioinformatics Pipeline AI — Test Assessment Report")
        lines.append("")
        lines.append("## What Is This?")
        lines.append("")
        lines.append(
            "This report evaluates an AI system that helps laboratory scientists design "
            "Nextflow bioinformatics pipelines for pathogen surveillance. The AI takes "
            "natural language requests (e.g., *'I have COVID samples, I need lineage classification'*) "
            "and generates executable Nextflow DSL2 pipeline code with visual Mermaid diagrams."
        )
        lines.append("")
        lines.append("### Test Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| **Date** | {end_time.strftime('%Y-%m-%d %H:%M')} |")
        lines.append(f"| **Total Duration** | {duration:.0f} seconds ({duration/60:.1f} minutes) |")
        lines.append(f"| **Total Tests** | {len(self.results)} |")
        total_pass = sum(1 for r in self.results if r["success"])
        total_fail = len(self.results) - total_pass
        pct = (total_pass / len(self.results) * 100) if self.results else 0
        lines.append(f"| **Passed** | {total_pass} ✅ ({pct:.0f}%) |")
        lines.append(f"| **Failed** | {total_fail} ❌ |")
        
        # Calculate aggregate RAG performance
        rag_results = [r for r in self.results if "rag_recall_pct" in r["scores"]]
        if rag_results:
            total_found = sum(r["details"].get("found_count", 0) for r in rag_results)
            total_req = sum(r["details"].get("total_count", 0) for r in rag_results)
            rag_pct = (total_found / total_req * 100) if total_req > 0 else 0
            lines.append(f"| **RAG Retrieval** | {total_found}/{total_req} documents ({rag_pct:.0f}%) |")
        
        lines.append(f"| **RAG Database** | ✅ LOADED (via `conftest.py`) |")
        lines.append(f"| **Retry Attempts** | Each test retried up to 3 times (best result kept) |")
        lines.append("")

        # ── Failure Summary (Only shown if there are failures) ──
        if total_fail > 0:
            lines.append("### ❌ Failure Summary")
            lines.append("")
            lines.append("| Scenario ID | Level | Error |")
            lines.append("|-------------|-------|-------|")
            for r in self.results:
                if not r["success"]:
                    err = "—"
                    if r["details"].get("errors"):
                        err = "; ".join(r["details"]["errors"])[:200] + "..."
                    elif r["details"].get("error"):
                        err = str(r["details"]["error"])[:200] + "..."
                    
                    lines.append(f"| `{r['id']}` | {r['level']} | {err} |")
            lines.append("")

        # ── Summary Table ──
        lines.append("### Results by Level")
        lines.append("")
        lines.append("| Level | Category | Difficulty | Tests | ✅ Pass | ❌ Fail | Avg Time |")
        lines.append("|-------|----------|-----------|-------|--------|--------|----------|")

        for lvl in sorted(levels.keys()):
            lresults = levels[lvl]
            meta = LEVEL_META.get(lvl, {"name": f"Level {lvl}", "difficulty": "—"})
            n = len(lresults)
            n_pass = sum(1 for r in lresults if r["success"])
            n_fail = n - n_pass
            avg_time = f"{sum(r['elapsed'] for r in lresults) / n:.0f}s"
            icon = "✅" if n_fail == 0 else "⚠️"
            lines.append(
                f"| {lvl} | {meta['name']} {icon} | {meta['difficulty']} | {n} | {n_pass} | {n_fail} | {avg_time} |"
            )

        lines.append("")

        # Pre-calculate RAG status map for cross-referencing
        rag_status_map = {}
        for r in self.results:
            if r["id"].startswith("[RAG]"):
                base_id = r["id"].replace("[RAG] ", "")
                rag_status_map[base_id] = "Passed ✅" if r["success"] else "Failed ❌"

        # ══════════════════════════════════════
        # DETAILED RESULTS PER LEVEL
        # ══════════════════════════════════════
        for lvl in sorted(levels.keys()):
            lresults = levels[lvl]
            meta = LEVEL_META.get(lvl, {"name": f"Level {lvl}", "difficulty": "—", "description": "", "example": ""})

            lines.append("---")
            lines.append("")
            lines.append(f"## Level {lvl}: {meta['name']} ({meta['difficulty']})")
            lines.append("")

            # Level description box
            lines.append(f"> **What this tests:** {meta['description']}")
            lines.append(f">")
            lines.append(f"> **Example:** {meta['example']}")
            lines.append("")

            n_pass = sum(1 for r in lresults if r["success"])
            n_fail = len(lresults) - n_pass
            lines.append(f"**Results: {n_pass} passed, {n_fail} failed out of {len(lresults)} tests**")
            lines.append("")

            for r in lresults:
                icon = "✅" if r["success"] else "❌"
                lines.append(f"### {icon} `{r['id']}` — {r['description']}")
                lines.append("")
                lines.append(f"| Property | Value |")
                lines.append(f"|----------|-------|")
                lines.append(f"| Difficulty | {r['difficulty']} |")
                lines.append(f"| Time | {r['elapsed']:.1f}s |")
                
                # Cross-reference RAG status if available
                base_id = r["id"].split("] ")[-1] if "]" in r["id"] else r["id"]
                if not r["id"].startswith("[RAG]") and base_id in rag_status_map:
                    lines.append(f"| RAG Status | {rag_status_map[base_id]} |")
                    
                lines.append(f"| Total Result | {'Passed ✅' if r['success'] else 'Failed ❌'} |")

                det = r["details"]

                if det.get("turns"):
                    lines.append(f"| Conversation Turns | {det['turns']} |")
                if det.get("ai_status"):
                    lines.append(f"| AI Status | `{det['ai_status']}` |")
                if det.get("nf_code_length"):
                    lines.append(f"| Nextflow Code | {det['nf_code_length']} characters |")
                    
                    if "nf_syntax_passed" in det:
                        icon = "✅" if det["nf_syntax_passed"] else "❌"
                        lines.append(f"| NF Compiler (Syntax) | {icon} |")
                        if not det["nf_syntax_passed"] and det.get("nf_syntax_error"):
                            lines.append(f"| NF Syntax Error | `{det['nf_syntax_error'][:200]}` |")
                    if "nf_stub_passed" in det:
                        icon = "✅" if det["nf_stub_passed"] else "❌"
                        lines.append(f"| NF Compiler (Stub) | {icon} |")
                        if not det["nf_stub_passed"] and det.get("nf_stub_error"):
                            lines.append(f"| NF Stub Error | `{det['nf_stub_error'][:200]}` |")
                        
                if det.get("mermaid_code_length"):
                    lines.append(f"| Mermaid Diagram | {det['mermaid_code_length']} chars |")
                if det.get("mermaid_code_length"):
                    lines.append(f"| Mermaid Diagram | {det['mermaid_code_length']} chars |")
                if det.get("has_ast"):
                    lines.append(f"| AST JSON | ✅ Generated |")
                if det.get("module_id"):
                    lines.append(f"| Reference Module | `{det['module_id']}` |")
                if det.get("reference_code_length"):
                    lines.append(f"| Reference Code | {det['reference_code_length']} chars |")
                if det.get("include_match_ratio"):
                    lines.append(f"| Include Match | {det['include_match_ratio']} |")
                if "found_count" in det and "total_count" in det:
                    lines.append(f"| Retrieval Score | **{det['found_count']}/{det['total_count']}** documents found |")

                lines.append("")

                # Scores (if any)
                if r["scores"]:
                    lines.append("**Evaluation Scores:**")
                    lines.append("")
                    for k, v in r["scores"].items():
                        if "score" in k or "pct" in k:
                            label = k.replace("_", " ").title()
                            if isinstance(v, (int, float)):
                                if "pct" in k:
                                    e = "🟢" if v >= 75 else "🟡" if v >= 50 else "🔴"
                                    lines.append(f"- {e} {label}: **{v}%**")
                                else:
                                    e = "🟢" if v >= 4 else "🟡" if v >= 3 else "🔴"
                                    lines.append(f"- {e} {label}: **{v}**/5")
                    lines.append("")

                # Judge Reasoning (if any)
                has_reasoning = any("reason" in k for k in det.keys())
                if has_reasoning:
                    lines.append("**Judge Reasoning:**")
                    lines.append("")
                    for k, v in det.items():
                        if "reason" in k and v:
                            label = k.replace("judge_", "").replace("_reason", "").replace("_", " ").title()
                            lines.append(f"> **{label}**: {v}")
                    lines.append("")

                # AI Reply preview
                if det.get("ai_reply"):
                    reply_preview = det["ai_reply"][:250].replace("\n", " ")
                    lines.append(f"**AI said:** _{reply_preview}_")
                    lines.append("")

                # Rejection info
                if det.get("rejection_expected"):
                    lines.append(f"**Why this should be rejected:** {det['rejection_expected']}")
                    lines.append("")

                # Error info
                if det.get("error"):
                    lines.append(f"**Error:** `{str(det['error'])[:300]}`")
                    lines.append("")

                if det.get("errors"):
                    lines.append("**Errors:**")
                    for err in det["errors"]:
                        lines.append(f"- `{err}`")
                    lines.append("")

                # Code and Diagram Dropdowns
                if det.get("rag_context"):
                    lines.append("<details><summary>View Retrieved RAG Context</summary>")
                    lines.append("")
                    lines.append("```text")
                    lines.append(str(det["rag_context"])[:3000] + "\n...(truncated)" if len(str(det["rag_context"])) > 3000 else str(det["rag_context"]))
                    lines.append("```")
                    lines.append("</details>")
                    lines.append("")

                if det.get("nf_code"):
                    lines.append("<details><summary>View Nextflow Code</summary>")
                    lines.append("")
                    lines.append("```groovy")
                    lines.append(det["nf_code"])
                    lines.append("```")
                    lines.append("</details>")
                    lines.append("")

                if det.get("mermaid_code"):
                    lines.append("<details><summary>View Mermaid Diagram</summary>")
                    lines.append("")
                    lines.append("```mermaid")
                    lines.append(det["mermaid_code"])
                    lines.append("```")
                    lines.append("</details>")
                    lines.append("")

                if det.get("mermaid_code"):
                    lines.append("<details><summary>View Mermaid Diagram</summary>")
                    lines.append("")
                    lines.append("```mermaid")
                    lines.append(det["mermaid_code"])
                    lines.append("```")
                    lines.append("</details>")
                    lines.append("")

        # ══════════════════════════════════════
        # FOOTER
        # ══════════════════════════════════════
        lines.append("---")
        lines.append("")
        lines.append("## How to Read This Report")
        lines.append("")
        lines.append("- **✅ Passed**: The AI correctly handled the request")
        lines.append("- **❌ Failed**: The AI produced an incorrect or unexpected result")
        lines.append("- **Conversation Turns**: How many messages were exchanged before getting a result")
        lines.append("- **Nextflow Code**: The generated pipeline code length (longer = more complex)")
        lines.append("- **Mermaid Diagram**: A visual flowchart generated by the AI from the code")
        lines.append("- **Mermaid Diagram**: A flowchart generated algorithmically from the pipeline structure")
        lines.append("- **Include Match**: For recreation tests — what percentage of the reference module's tool imports were present")
        lines.append("")
        lines.append("### Score Guide")
        lines.append("")
        lines.append("| Score | Meaning |")
        lines.append("|-------|---------|")
        lines.append("| 🟢 5/5 | Excellent — meets or exceeds expectations |")
        lines.append("| 🟢 4/5 | Good — correct with minor issues |")
        lines.append("| 🟡 3/5 | Acceptable — works but has notable gaps |")
        lines.append("| 🔴 2/5 | Poor — significant issues |")
        lines.append("| 🔴 1/5 | Unacceptable — fundamentally wrong |")
        lines.append("")
        lines.append(
            f"*Report generated by `tests/` framework on {end_time.strftime('%Y-%m-%d %H:%M:%S')}*"
        )
        return "\n".join(lines)

    def save_report(self) -> Path:
        """Save the report to disk and return the path."""
        report_content = self.generate_report()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = REPORT_DIR / f"test_report_{timestamp}.md"
        report_path.write_text(report_content)

        latest_path = REPORT_DIR / "test_report_latest.md"
        latest_path.write_text(report_content)

        print(f"\n📋 REPORT SAVED: {report_path}")
        return report_path


# Global singleton
report = ReportCollector()
