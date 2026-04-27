"""
tests/test_rag.py
Isolated RAG Retrieval Tests

Tests that retrieve_rag_context() returns the expected catalog items for
various user queries across all scenario complexities (Simple, Medium, Complex).

Calls the RAG function DIRECTLY against the loaded store.
Bypasses the Agent, the LLM, and the API.
"""
import pytest

from app.services.tools import retrieve_rag_context
from tests.scenarios.level1_simple import LEVEL1_SCENARIOS
from tests.scenarios.level2_medium import LEVEL2_SCENARIOS
from tests.scenarios.level3_complex import LEVEL3_SCENARIOS
from tests.scenarios.level4_guardrails import LEVEL4_SCENARIOS
from tests.scenarios.level5_recreation import LEVEL5_SCENARIOS
from tests.report import report

ALL_SCENARIOS = [
    s
    for s in (LEVEL1_SCENARIOS + LEVEL2_SCENARIOS + LEVEL3_SCENARIOS + LEVEL4_SCENARIOS + LEVEL5_SCENARIOS)
    if "RECREATION_REV" not in s.get("id", "")
]

@pytest.mark.parametrize(
    "scenario",
    ALL_SCENARIOS,
    ids=[s["id"] for s in ALL_SCENARIOS],
)
def test_rag_retrieval(scenario, store):
    """Verify that RAG retrieves the expected catalog components and templates."""
    # RAG uses the first message in the chat history as the query
    query = scenario["chat_messages"][0]
    expected_ids = scenario.get("expect_in_context", [])

    if not expected_ids:
        pytest.skip("No expect_in_context defined for this scenario.")

    # ── Direct RAG call (no API, no agent) ──
    context = retrieve_rag_context(query, store, embed_code=False)

    # ── Deterministic assertions ──
    missing = [eid for eid in expected_ids if eid not in context]

    import re
    # Extract IDs that were actually found in the context blocks
    actual_found_ids = re.findall(r'--- (?:COMPONENT|TEMPLATE): ([\w\_\d]+) ---', context)
    
    found_count = len(expected_ids) - len(missing)
    total_count = len(expected_ids)
    recall_pct = (found_count / total_count * 100) if total_count > 0 else 0
    
    passed = len(missing) == 0
    scores = {
        "rag_precision": 1.0 if passed else 0.0,
        "rag_recall_pct": recall_pct
    }

    if missing:
        print(f"\n[FAIL] {scenario['id']} test_rag failed!")
        print(f"  Expected: {expected_ids}")
        print(f"  Got:      {actual_found_ids}")
        print(f"  Missing:  {missing}")
        print(f"  Score:    {found_count}/{total_count} ({recall_pct:.0f}%)")
    else:
        print(f"\n[OK] {scenario['id']} test_rag succeeded! Found expected IDs: {expected_ids}")
        print(f"  Score:    {found_count}/{total_count} (100%)")

    report.add_result(
        scenario_id=f"[RAG] {scenario['id']}",
        level=scenario["level"],
        success=passed,
        difficulty=scenario.get("difficulty", "—"),
        description=scenario.get("description", ""),
        scores=scores,
        details={
            "query": query,
            "expected": expected_ids,
            "actual": actual_found_ids,
            "missing": missing,
            "found_count": found_count,
            "total_count": total_count,
            "context_length": len(context),
            "rag_context": context,
            "error": f"RAG Missing: {missing} (Found {found_count}/{total_count})" if missing else None,
        }
    )

    assert not missing, (
        f"RAG missed expected IDs: {missing}\n"
        f"Query: {query}\n"
        f"Context snippet: {context[:500]}..."
    )
