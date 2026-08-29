import os
import sys
import json
import time
import asyncio
import argparse
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

os.environ["AUTH_BYPASS"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./benchmark_eval.db"
os.environ["NF_AGENT_PLUGIN"] = os.getenv("NF_AGENT_PLUGIN", "izs")

from langchain_core.messages import HumanMessage, AIMessage
from core.loader import data_loader
from core.services.graph import app_graph, global_store
from core.utils.logger import logger


def evaluate_case_structure(nf_code: str, ast_json: dict, expected_emits: list | None = None, gt_components: list | None = None) -> tuple[bool, list[str]]:
    """Verify that the generated Nextflow code has modular subworkflows with take:, main:, emit: and entrypoint."""
    errors = []
    if not nf_code:
        return False, ["Missing Nextflow code in output"]

    # When no components are expected (rejection of adversarial/incompatible requests), valid entrypoint is sufficient
    if gt_components is not None and len(gt_components) == 0:
        if "workflow {" not in nf_code and "\nworkflow\n{" not in nf_code:
            errors.append("Missing entrypoint 'workflow { ... }' in rendered code")
        return len(errors) == 0, errors

    sub_workflows = ast_json.get("sub_workflows", []) if isinstance(ast_json, dict) else []

    # 1. Sub-workflow check
    if not sub_workflows:
        errors.append("No sub_workflows defined in AST")

    # 2. Check for keywords in rendered Nextflow code
    if "workflow " not in nf_code:
        errors.append("Missing named workflow definition in code")
    if "take:" not in nf_code:
        errors.append("Missing 'take:' block in rendered code")
    if "main:" not in nf_code:
        errors.append("Missing 'main:' block in rendered code")

    # emit: is required when expected_emits is non-empty or when sub_workflow defines emit_channels
    has_subwf_emits = any(sw.get("emit_channels") for sw in sub_workflows if isinstance(sw, dict))
    if (expected_emits and len(expected_emits) > 0) or has_subwf_emits:
        if "emit:" not in nf_code:
            errors.append("Missing 'emit:' block in rendered code when outputs are expected")

    if "workflow {" not in nf_code and "\nworkflow\n{" not in nf_code:
        errors.append("Missing entrypoint 'workflow { ... }' in rendered code")

    return len(errors) == 0, errors


def evaluate_case_logic(selected_components: list[str], gt_components: list[str]) -> tuple[float, float, float]:
    """Calculate recall, precision, and F1."""
    if not gt_components:
        return 1.0, 1.0, 1.0

    sel_set = set(selected_components or [])
    gt_set = set(gt_components)

    # Allow mapping prefix differences if base tool matches
    matched = 0
    for gt in gt_set:
        gt_base = gt.split('__')[-1]
        if gt in sel_set or any(gt_base in s for s in sel_set):
            matched += 1

    recall = matched / len(gt_set) if gt_set else 1.0
    precision = matched / len(sel_set) if sel_set else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return recall, precision, f1


async def run_single_edge_case(case: dict, halt_on_failure: bool = True) -> dict:
    case_id = case.get("id", "unknown")
    title = case.get("title", case_id)
    category = case.get("category", "general")
    prompt = case.get("prompt", "")
    gt_components = case.get("gt_components", [])

    print(f"\n=======================================================")
    print(f"▶ RUNNING EDGE CASE: [{case_id}] {title}")
    print(f"  Category: {category}")
    print(f"  Prompt: \"{prompt}\"")
    print(f"  GT Components: {gt_components}")
    print(f"=======================================================")

    t0 = time.time()
    thread_id = f"eval_{case_id}_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    # Step 1: Execute directly in automated mode (execution_mode="direct")
    state_update = {
        "user_query": prompt,
        "messages": [HumanMessage(content=prompt)],
        "generate_diagrams": True,
        "execution_mode": "direct",
    }
    final_result = await app_graph.ainvoke(state_update, config=config)

    elapsed = time.time() - t0
    nf_code = final_result.get("nextflow_code", "")
    ast_json = final_result.get("ast_json", {})
    selected_components = final_result.get("selected_component_ids", []) or []

    # Evaluate logic (precision, recall)
    recall, precision, f1 = evaluate_case_logic(selected_components, gt_components)

    # Evaluate structure (take:, main:, emit:, sub_workflows, entrypoint)
    expected_emits = case.get("expected_emit_channels", [])
    struct_ok, struct_errors = evaluate_case_structure(nf_code, ast_json, expected_emits=expected_emits, gt_components=gt_components)

    logic_pass = recall >= 0.75  # 75%+ recall pass threshold for complex routing
    overall_pass = struct_ok and logic_pass

    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Selected Components: {selected_components}")
    print(f"  Recall: {recall:.2f}, Precision: {precision:.2f}, F1: {f1:.2f}")
    print(f"  Structure Check: {'PASS' if struct_ok else 'FAIL ' + str(struct_errors)}")
    print(f"  Verdict: {'[PASS] ✅' if overall_pass else '[FAIL] ❌'}")

    if not overall_pass and halt_on_failure:
        print("\n⛔ FAIL-FAST TRIGGERED! Edge case failed acceptance criteria.")
        print(f"Details: struct_errors={struct_errors}, recall={recall:.2f}, precision={precision:.2f}")
        if nf_code:
            print("\n--- Rendered Nextflow Code ---")
            print(nf_code[:1000])
            print("------------------------------")
        sys.exit(1)

    return {
        "id": case_id,
        "category": category,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "struct_ok": struct_ok,
        "struct_errors": struct_errors,
        "overall_pass": overall_pass,
        "elapsed": elapsed,
    }


async def main():
    parser = argparse.ArgumentParser(description="Run 51 Edge Cases Benchmark with Fail-Fast Gate")
    parser.add_argument("--first", type=int, default=None, help="Run only first N cases")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated case IDs to run")
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    parser.add_argument("--no-halt", action="store_true", help="Do not halt on failure")
    args = parser.parse_args()

    # Load data store
    print("Loading data store and knowledge graph...")
    data_loader.load_all(store=global_store)
    print("Data loader ready!")

    dataset_path = backend_dir / "tests" / "benchmark_50_edge_cases.jsonl"
    with open(dataset_path, "r", encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]

    if args.ids:
        target_ids = set(args.ids.split(","))
        cases = [c for c in cases if c.get("id") in target_ids]
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if args.first:
        cases = cases[:args.first]

    print(f"Loaded {len(cases)} edge cases to evaluate.")

    results = []
    for case in cases:
        res = await run_single_edge_case(case, halt_on_failure=not args.no_halt)
        results.append(res)

    print("\n=======================================================")
    print("              BENCHMARK SUMMARY REPORT                 ")
    print("=======================================================")
    total = len(results)
    passed = sum(1 for r in results if r["overall_pass"])
    avg_recall = sum(r["recall"] for r in results) / total if total else 0
    avg_precision = sum(r["precision"] for r in results) / total if total else 0
    avg_f1 = sum(r["f1"] for r in results) / total if total else 0
    struct_pass_rate = sum(1 for r in results if r["struct_ok"]) / total if total else 0

    print(f"Total Cases Evaluated: {total}")
    print(f"Cases Passed: {passed} / {total} ({passed/total*100:.1f}%)")
    print(f"Structure Compliance (take/main/emit): {struct_pass_rate*100:.1f}%")
    print(f"Average Recall: {avg_recall:.3f}")
    print(f"Average Precision: {avg_precision:.3f}")
    print(f"Average F1: {avg_f1:.3f}")
    print("=======================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
