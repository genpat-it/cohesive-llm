"""
tests/evaluation/dimensions.py
Evaluation dimension definitions and test-type mapping.

6 core dimensions evaluated via pairwise comparison, mapped to test types
so only relevant dimensions are judged for each scenario category.
"""

# ══════════════════════════════════════════════════════════════════════════════
# 6 Core Evaluation Dimensions
# ══════════════════════════════════════════════════════════════════════════════

DIMENSIONS = {
    "faithfulness": (
        "Is the output faithful to the available tool catalog? "
        "Does it only use tools that actually exist in the framework?"
    ),
    "relevance": (
        "Does the output address the user's biological scenario? "
        "Correct organism, sequencing platform, and analysis goal?"
    ),
    "syntax": (
        "Is the generated Nextflow DSL2 code syntactically valid? "
        "Correct include statements, process blocks, workflow scoping?"
    ),
    "logic": (
        "Does the pipeline logic correctly implement the design? "
        "Channels wired correctly, steps in proper order, conditional branches correct?"
    ),
    "diagram_quality": (
        "Does the Mermaid diagram accurately represent the pipeline? "
        "All steps present, edges correctly directed, no orphan nodes?"
    ),
    "communication": (
        "Is the natural language response clear, helpful, and informative? "
        "Does it explain the pipeline design and any decisions made?"
    ),
}

# ══════════════════════════════════════════════════════════════════════════════
# Dimension → Test Type Mapping
# ══════════════════════════════════════════════════════════════════════════════
# Each test type gets a subset of dimensions for pairwise comparison.
# This reduces cognitive load on the judge and avoids N/A evaluations.

DIMENSION_MAP: dict[str, list[str]] = {
    "consultant":      ["faithfulness", "relevance", "communication"],
    "code_generation": ["syntax", "logic", "faithfulness", "communication"],
    "modification":    ["syntax", "logic", "faithfulness", "communication"],
    "rejection":       ["faithfulness", "communication"],
    "recreation":      ["syntax", "logic", "communication"],
    "diagram":         ["diagram_quality", "communication"],
    "level_unified_turn1": ["faithfulness", "relevance", "communication"],
    "level_unified_turn2": ["syntax", "logic", "faithfulness", "diagram_quality", "communication"],
}

# ══════════════════════════════════════════════════════════════════════════════
# 4 Summary Roll-up Dimensions (for paper reporting)
# ══════════════════════════════════════════════════════════════════════════════
# Aggregate fine-grained dimensions into broader categories suitable for
# publication tables and cross-model comparison.

SUMMARY_DIMENSIONS: dict[str, list[str]] = {
    "correctness":    ["faithfulness", "syntax"],
    "completeness":   ["relevance", "logic"],
    "channel_wiring": ["logic"],
    "communication":  ["communication"],
}


def get_dimensions_for_test_type(test_type: str) -> list[str]:
    """Return the list of pairwise dimensions applicable to a test type.

    Raises KeyError if the test type is not recognized.
    """
    if test_type not in DIMENSION_MAP:
        raise KeyError(
            f"Unknown test type '{test_type}'. "
            f"Valid types: {list(DIMENSION_MAP.keys())}"
        )
    return DIMENSION_MAP[test_type]


def get_summary_for_dimensions(dimensions: list[str]) -> dict[str, list[str]]:
    """Given a list of evaluated dimensions, return which summary categories
    can be computed."""
    result = {}
    for summary_name, required_dims in SUMMARY_DIMENSIONS.items():
        applicable = [d for d in required_dims if d in dimensions]
        if applicable:
            result[summary_name] = applicable
    return result
