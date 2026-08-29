"""
tests/report.py
Pairwise evaluation report generator.

Produces:
  1. Markdown report with Glicko-2 ratings, win/loss/tie counts, position bias
     analysis, three-tier verdicts, and per-example details
  2. CSV export with one row per example × dimension for data analysis

Reports are saved to tests/reports/ with timestamps.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from tests.evaluation.elo import Glicko2Tracker
from tests.evaluation.schemas import BenchmarkResult, MultiTurnResult

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


class PairwiseReport:
    """Collects pairwise evaluation results and generates reports.

    Usage:
        report = PairwiseReport()
        report.add_benchmark_result(result)
        ...
        report.save(elo_tracker)
    """

    def __init__(self):
        self.benchmark_results: list[BenchmarkResult] = []
        self.multi_turn_results: list[MultiTurnResult] = []
        self.ab_significance: list[dict] = []

    def add_benchmark_result(self, result: BenchmarkResult):
        """Add a single-turn benchmark result."""
        self.benchmark_results.append(result)

    def add_multi_turn_result(self, result: MultiTurnResult):
        """Add a multi-turn conversation result."""
        self.multi_turn_results.append(result)

    def add_ab_significance(
        self, label_a: str, label_b: str, significance: dict[str, dict]
    ):
        """Add A/B significance test results."""
        self.ab_significance.append({
            "label_a": label_a,
            "label_b": label_b,
            "results": significance,
        })

    # ──────────────────────────────────────────────────────────

    def save(self, elo_tracker: Glicko2Tracker) -> Path:
        """Generate and save the full report (markdown + CSV).

        Returns the path to the markdown report.
        """
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        md_path = REPORTS_DIR / f"pairwise_report_{timestamp}.md"
        csv_path = REPORTS_DIR / f"pairwise_results_{timestamp}.csv"

        md_content = self._generate_markdown(elo_tracker)
        md_path.write_text(md_content, encoding="utf-8")

        csv_content = self._generate_csv(elo_tracker)
        csv_path.write_text(csv_content, encoding="utf-8")

        # Also save a "latest" symlink-like file
        latest_md = REPORTS_DIR / "pairwise_report_latest.md"
        latest_md.write_text(md_content, encoding="utf-8")

        print(f"\n  📄 Report: {md_path}")
        print(f"  📊 CSV:    {csv_path}")

        return md_path

    # ──────────────────────────────────────────────────────────
    # Markdown report generation
    # ──────────────────────────────────────────────────────────

    def _generate_markdown(self, elo: Glicko2Tracker) -> str:
        """Generate the full markdown report."""
        lines = []
        lines.append("# Pairwise Evaluation Report")
        lines.append("")
        lines.append(
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        lines.append("")

        # ── Summary statistics ──
        lines.append("## Summary")
        lines.append("")
        n_single = len(self.benchmark_results)
        n_multi = len(self.multi_turn_results)
        n_multi_turns = sum(len(m.turn_results) for m in self.multi_turn_results)
        lines.append(f"- **Single-turn examples**: {n_single}")
        lines.append(f"- **Multi-turn conversations**: {n_multi} ({n_multi_turns} total turns)")
        lines.append(f"- **Total pairwise comparisons**: {len(elo.match_log)}")
        lines.append("")

        # ── Glicko-2 ratings table ──
        lines.append("## Glicko-2 Ratings")
        lines.append("")
        lines.append(
            "Ratings start at 1500 (default). Higher = better. "
            "CI = 95% confidence interval."
        )
        lines.append("")

        ratings = elo.get_ratings_table()
        if ratings:
            lines.append("| Player | Dimension | Rating | ±Deviation | CI 95% | Conservative |")
            lines.append("|--------|-----------|--------|------------|--------|-------------|")
            for r in ratings:
                lines.append(
                    f"| {r['player']} | {r['dimension']} | "
                    f"**{r['rating']}** | ±{r['deviation']} | "
                    f"[{r['ci_95_lower']}, {r['ci_95_upper']}] | "
                    f"{r['conservative']} |"
                )
            lines.append("")

        # ── Summary rollup ──
        summary = elo.get_summary()
        if summary:
            lines.append("### Summary Ratings")
            lines.append("")
            lines.append("| Player | Avg Rating | Avg Deviation | Dimensions | Matches |")
            lines.append("|--------|-----------|---------------|-----------|---------|")
            for player, s in summary.items():
                lines.append(
                    f"| {player} | **{s['avg_rating']}** | ±{s['avg_deviation']} | "
                    f"{s['n_dimensions']} | {s['n_matches']} |"
                )
            lines.append("")

        # ── Win/Loss/Tie counts ──
        wlt = elo.get_win_loss_tie_counts()
        if wlt:
            lines.append("## Win / Loss / Tie Breakdown")
            lines.append("")
            for player, dims in sorted(wlt.items()):
                lines.append(f"### {player}")
                lines.append("")
                lines.append("| Dimension | Wins | Losses | Ties | Win % |")
                lines.append("|-----------|------|--------|------|-------|")
                for dim, counts in sorted(dims.items()):
                    total = counts["wins"] + counts["losses"] + counts["ties"]
                    win_pct = (counts["wins"] / total * 100) if total else 0
                    lines.append(
                        f"| {dim} | {counts['wins']} | {counts['losses']} | "
                        f"{counts['ties']} | {win_pct:.1f}% |"
                    )
                lines.append("")

        # ── Three-tier verdict breakdown ──
        lines.append("## Ground-Truth Verdict Breakdown")
        lines.append("")
        tiers = {"MATCH": 0, "EXCEEDS": 0, "DEFICIENT": 0, "N/A": 0}
        for r in self.benchmark_results:
            if r.ground_truth_verdict:
                tiers[r.ground_truth_verdict.tier] += 1
            else:
                tiers["N/A"] += 1
        # Include multi-turn turns
        for mt in self.multi_turn_results:
            for tr in mt.turn_results:
                if tr.ground_truth_verdict:
                    tiers[tr.ground_truth_verdict.tier] += 1
                else:
                    tiers["N/A"] += 1

        total_verdicts = sum(tiers.values())
        lines.append("| Tier | Count | % |")
        lines.append("|------|-------|---|")
        for tier, count in tiers.items():
            pct = (count / total_verdicts * 100) if total_verdicts else 0
            lines.append(f"| {tier} | {count} | {pct:.1f}% |")
        lines.append("")

        # ── Deterministic check pass rates ──
        lines.append("## Deterministic Check Results")
        lines.append("")
        all_results = list(self.benchmark_results)
        for mt in self.multi_turn_results:
            all_results.extend(mt.turn_results)

        if all_results:
            n_has_code = sum(1 for r in all_results if r.deterministic_checks.has_code)
            avg_p = sum(r.deterministic_checks.tool_routing_precision_pct for r in all_results) / len(all_results)
            avg_r = sum(r.deterministic_checks.tool_routing_recall_pct for r in all_results) / len(all_results)
            avg_f1 = sum(r.deterministic_checks.tool_routing_f1_pct for r in all_results) / len(all_results)

            lines.append(f"- **Code generation rate**: {n_has_code}/{len(all_results)} ({n_has_code/len(all_results)*100:.1f}%)")
            lines.append(f"- **Avg tool routing precision**: {avg_p:.1f}%")
            lines.append(f"- **Avg tool routing recall**: {avg_r:.1f}%")
            lines.append(f"- **Avg tool routing F1**: {avg_f1:.1f}%")
            lines.append("")

        # ── Position bias analysis ──
        lines.append("## Position Bias Analysis")
        lines.append("")
        total_comparisons = 0
        consistent_count = 0
        for r in self.benchmark_results:
            for pr in r.pairwise_results:
                total_comparisons += 1
                if pr.consistent:
                    consistent_count += 1
        for mt in self.multi_turn_results:
            for tr in mt.turn_results:
                for pr in tr.pairwise_results:
                    total_comparisons += 1
                    if pr.consistent:
                        consistent_count += 1

        if total_comparisons:
            consistency = consistent_count / total_comparisons * 100
            lines.append(
                f"- **Total pairwise comparisons**: {total_comparisons}"
            )
            lines.append(
                f"- **Position-consistent**: {consistent_count} ({consistency:.1f}%)"
            )
            lines.append(
                f"- **Position-inconsistent → forced tie**: "
                f"{total_comparisons - consistent_count} "
                f"({100 - consistency:.1f}%)"
            )
            if consistency >= 80:
                lines.append("- ✅ Judge consistency is good (≥80%)")
            else:
                lines.append("- ⚠️ Judge consistency is low (<80%) — consider a stronger judge model")
        else:
            lines.append("- No pairwise comparisons recorded.")
        lines.append("")

        # ── A/B significance results ──
        if self.ab_significance:
            lines.append("## A/B Significance Tests")
            lines.append("")
            for ab in self.ab_significance:
                lines.append(f"### {ab['label_a']} vs {ab['label_b']}")
                lines.append("")
                lines.append("| Dimension | Δ Rating | p-value | 95% CI | Significant? |")
                lines.append("|-----------|----------|---------|--------|-------------|")
                for dim, sig in ab["results"].items():
                    status = "✅" if sig["significant_at_005"] else "❌"
                    lines.append(
                        f"| {dim} | {sig['mean_delta']:+.1f} | "
                        f"{sig['p_value']:.4f} | "
                        f"[{sig['ci_95'][0]:+.1f}, {sig['ci_95'][1]:+.1f}] | "
                        f"{status} |"
                    )
                lines.append("")

        # ── Per-example details (single-turn) ──
        if self.benchmark_results:
            lines.append("## Per-Example Results (Single-Turn)")
            lines.append("")
            lines.append("<details>")
            lines.append(f"<summary>Click to expand ({len(self.benchmark_results)}  examples)</summary>")
            lines.append("")
            for r in self.benchmark_results:
                tier = r.ground_truth_verdict.tier if r.ground_truth_verdict else "N/A"
                has_code = "✅" if r.deterministic_checks.has_code else "❌"
                lines.append(f"### {r.example_id}")
                lines.append(f"- Type: {r.test_type} | GT Tier: **{tier}** | Code: {has_code}")
                lines.append(f"- P/R/F1: {r.deterministic_checks.tool_routing_precision_pct}% / "
                           f"{r.deterministic_checks.tool_routing_recall_pct}% / "
                           f"{r.deterministic_checks.tool_routing_f1_pct}%")
                if r.deterministic_checks.extra_steps:
                    lines.append(f"- Extra steps: {', '.join(r.deterministic_checks.extra_steps)}")
                if r.deterministic_checks.missing_steps:
                    lines.append(f"- Missing steps: {', '.join(r.deterministic_checks.missing_steps)}")
                for pr in r.pairwise_results:
                    icon = "🏆" if pr.verdict == "A" else ("🥈" if pr.verdict == "B" else "🤝")
                    cons = "✓" if pr.consistent else "⚡inconsistent"
                    lines.append(f"- {pr.dimension}: {icon} {pr.verdict} ({cons})")
                lines.append("")
            lines.append("</details>")
            lines.append("")

        # ── Multi-turn summary ──
        if self.multi_turn_results:
            lines.append("## Multi-Turn Results")
            lines.append("")
            lines.append("| Conversation | Kind | Turns | All Passed? |")
            lines.append("|-------------|------|-------|------------|")
            for mt in self.multi_turn_results:
                status = "✅" if mt.all_turns_passed else "❌"
                lines.append(
                    f"| {mt.conversation_id} | {mt.modification_kind} | "
                    f"{len(mt.turn_results)} | {status} |"
                )
            lines.append("")

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────
    # CSV export
    # ──────────────────────────────────────────────────────────

    def _generate_csv(self, elo: Glicko2Tracker) -> str:
        """Generate CSV with one row per example x dimension."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow([
            "example_id", "test_type", "dimension", "verdict",
            "consistent", "gt_tier", "has_code",
            "precision_pct", "recall_pct", "f1_pct",
            "extra_steps", "missing_steps",
            "elapsed_s", "source",
        ])

        # Single-turn results
        for r in self.benchmark_results:
            tier = r.ground_truth_verdict.tier if r.ground_truth_verdict else ""
            for pr in r.pairwise_results:
                writer.writerow([
                    r.example_id, r.test_type, pr.dimension, pr.verdict,
                    pr.consistent, tier,
                    r.deterministic_checks.has_code,
                    r.deterministic_checks.tool_routing_precision_pct,
                    r.deterministic_checks.tool_routing_recall_pct,
                    r.deterministic_checks.tool_routing_f1_pct,
                    ";".join(r.deterministic_checks.extra_steps),
                    ";".join(r.deterministic_checks.missing_steps),
                    r.elapsed_s, "single_turn",
                ])

        # Multi-turn results
        for mt in self.multi_turn_results:
            for tr in mt.turn_results:
                tier = tr.ground_truth_verdict.tier if tr.ground_truth_verdict else ""
                for pr in tr.pairwise_results:
                    writer.writerow([
                        tr.example_id, tr.test_type, pr.dimension, pr.verdict,
                        pr.consistent, tier,
                        tr.deterministic_checks.has_code,
                        tr.deterministic_checks.tool_routing_precision_pct,
                        tr.deterministic_checks.tool_routing_recall_pct,
                        tr.deterministic_checks.tool_routing_f1_pct,
                        ";".join(tr.deterministic_checks.extra_steps),
                        ";".join(tr.deterministic_checks.missing_steps),
                        tr.elapsed_s,
                        f"multi_turn:{mt.conversation_id}",
                    ])

        return output.getvalue()
