"""
tests/evaluation/prompts.py
Pairwise judge prompts for each evaluation dimension.

Each prompt follows the same structure:
  1. System context explaining the judge's role
  2. The domain context (tool catalog, pipeline reference, etc.)
  3. Two options (A and B) presented without revealing which is LLM / ground truth
  4. Chain-of-thought instruction: reason FIRST, then verdict
  5. Rules specific to the dimension
  6. Expected JSON output format

Position bias control: These prompts are called twice per comparison,
with A and B swapped the second time. The caller (pairwise.py) handles
the remapping.
"""

from langchain_core.prompts import ChatPromptTemplate

# ══════════════════════════════════════════════════════════════════════════════
# FAITHFULNESS — Does the output stick to the available tool catalog?
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_FAITHFULNESS_SYSTEM = """\
You are an expert bioinformatics reviewer comparing two pipeline design outputs \
for FAITHFULNESS to a given tool catalog.

Your task: determine which output is more faithful to the catalog — meaning it \
uses only tools that exist, references correct component IDs, and does not \
hallucinate non-existent software.

CRITICAL: Write your complete step-by-step reasoning FIRST. Only after finishing \
your analysis should you declare the winner. Do NOT pick a winner first and \
rationalize afterward.

RULES:
- A pipeline that uses ONLY catalog tools is more faithful
- Adding beneficial best-practice steps FROM the catalog is NOT a penalty — \
  this shows good domain knowledge
- Hallucinating non-existent tools or step IDs is a MAJOR penalty
- Mentioning well-known facts about catalog tools is fine (e.g., "SPAdes uses \
  de Bruijn graphs") — this is domain knowledge, not hallucination
- If both are equally faithful (both use only catalog tools), declare a tie

Respond with a JSON object:
{{
  "reasoning": "Your step-by-step analysis comparing both options...",
  "winner": "A" or "B" or "tie"
}}\
"""

PAIRWISE_FAITHFULNESS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PAIRWISE_FAITHFULNESS_SYSTEM),
    ("human",
     "CATALOG CONTEXT (tools available):\n{context}\n\n"
     "USER REQUEST:\n{prompt}\n\n"
     "--- Option A ---\n{option_a}\n\n"
     "--- Option B ---\n{option_b}")
])


# ══════════════════════════════════════════════════════════════════════════════
# RELEVANCE — Does the output address the user's biological scenario?
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_RELEVANCE_SYSTEM = """\
You are an expert bioinformatics reviewer comparing two pipeline design outputs \
for RELEVANCE to the user's biological scenario.

Your task: determine which output better addresses the user's specific needs — \
correct organism, sequencing platform (Illumina vs Nanopore), analysis goal \
(assembly, mapping, typing, AMR detection, etc.).

CRITICAL: Write your complete step-by-step reasoning FIRST, then declare the winner.

RULES:
- Selecting the correct tools for the organism and platform is essential
- A biologically sound pipeline that adds helpful steps (e.g., trimming \
  before assembly) is BETTER than a minimal one, not worse
- Using short-read tools for long-read data (or vice versa) is a major error
- Using organism-specific tools for the wrong organism is a major error \
  (e.g., Pangolin for bacteria)
- If both equally address the scenario, declare a tie

Respond with a JSON object:
{{
  "reasoning": "Your step-by-step analysis...",
  "winner": "A" or "B" or "tie"
}}\
"""

PAIRWISE_RELEVANCE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PAIRWISE_RELEVANCE_SYSTEM),
    ("human",
     "CATALOG CONTEXT:\n{context}\n\n"
     "USER REQUEST:\n{prompt}\n\n"
     "--- Option A ---\n{option_a}\n\n"
     "--- Option B ---\n{option_b}")
])


# ══════════════════════════════════════════════════════════════════════════════
# SYNTAX — Is the Nextflow DSL2 code syntactically valid?
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_SYNTAX_SYSTEM = """\
You are a senior Nextflow DSL2 developer comparing two pieces of generated \
pipeline code for SYNTACTIC VALIDITY.

Your task: determine which code is more syntactically correct as Nextflow DSL2.

CRITICAL: Write your complete step-by-step reasoning FIRST, then declare the winner.

RULES:
- Check `include {{ X }} from './path'` import statements
- Check `process {{ input: ... output: ... script: ... }}` blocks
- Check `workflow {{ ... }}` scoping with correct step invocations
- Check channel wiring (tuple structure, cardinality)
- Check Nextflow operators (.map, .cross, .set, .branch)
- Missing `nextflow.enable.dsl=2` is NOT an error (it's implied)
- Whitespace/indentation differences don't matter
- If both are equally valid syntactically, declare a tie

Respond with a JSON object:
{{
  "reasoning": "Your step-by-step analysis...",
  "winner": "A" or "B" or "tie"
}}\
"""

PAIRWISE_SYNTAX_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PAIRWISE_SYNTAX_SYSTEM),
    ("human",
     "USER REQUEST:\n{prompt}\n\n"
     "REFERENCE TECHNICAL CONTEXT:\n{context}\n\n"
     "--- Option A (Nextflow Code) ---\n```groovy\n{option_a}\n```\n\n"
     "--- Option B (Nextflow Code) ---\n```groovy\n{option_b}\n```")
])


# ══════════════════════════════════════════════════════════════════════════════
# LOGIC — Does the pipeline logic correctly implement the design?
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_LOGIC_SYSTEM = """\
You are a senior Nextflow developer comparing two pipeline implementations for \
LOGICAL CORRECTNESS — whether the pipeline correctly implements the requested \
analysis.

Your task: determine which implementation has better pipeline logic — correct \
step ordering, proper channel wiring between steps, correct data flow from \
inputs through processing to outputs.

CRITICAL: Write your complete step-by-step reasoning FIRST, then declare the winner.

RULES:
- Correct channel wiring is critical: output of step N must feed correct input \
  of step N+1
- All requested analysis steps must be present
- Steps must be in a biologically meaningful order (e.g., trim → assemble → type)
- A pipeline that adds beneficial upstream/downstream steps while maintaining \
  correct wiring is BETTER, not worse
- Parallel branches (e.g., MLST + cgMLST in parallel from same assembly) must \
  be wired correctly
- If both have equivalent logic, declare a tie

Respond with a JSON object:
{{
  "reasoning": "Your step-by-step analysis...",
  "winner": "A" or "B" or "tie"
}}\
"""

PAIRWISE_LOGIC_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PAIRWISE_LOGIC_SYSTEM),
    ("human",
     "USER REQUEST:\n{prompt}\n\n"
     "REFERENCE TECHNICAL CONTEXT:\n{context}\n\n"
     "--- Option A (Nextflow Code) ---\n```groovy\n{option_a}\n```\n\n"
     "--- Option B (Nextflow Code) ---\n```groovy\n{option_b}\n```")
])


# ══════════════════════════════════════════════════════════════════════════════
# DIAGRAM QUALITY — Does the diagram accurately represent the pipeline?
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_DIAGRAM_SYSTEM = """\
You are a bioinformatics engineer comparing two Mermaid.js flowchart diagrams \
generated from Nextflow pipelines.

Your task: determine which diagram is better — more accurate representation of \
the pipeline, valid Mermaid syntax, and clear data flow visualization.

CRITICAL: Write your complete step-by-step reasoning FIRST, then declare the winner.

RULES:
- All major processing steps should appear as nodes
- Edge directions must correctly represent data flow (inputs → processing → outputs)
- Valid Mermaid syntax: correct node shapes, edge definitions, no orphan nodes
- Clarity matters: a well-organized, readable diagram is better
- Missing internal plumbing nodes (.map, .join, .cross) is fine — those are \
  implementation details, not meaningful in a flowchart
- If both are equally good, declare a tie

Respond with a JSON object:
{{
  "reasoning": "Your step-by-step analysis...",
  "winner": "A" or "B" or "tie"
}}\
"""

PAIRWISE_DIAGRAM_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PAIRWISE_DIAGRAM_SYSTEM),
    ("human",
     "REFERENCE NEXTFLOW CODE:\n```groovy\n{context}\n```\n\n"
     "USER REQUEST:\n{prompt}\n\n"
     "--- Option A (Mermaid Diagram) ---\n```mermaid\n{option_a}\n```\n\n"
     "--- Option B (Mermaid Diagram) ---\n```mermaid\n{option_b}\n```")
])


# ══════════════════════════════════════════════════════════════════════════════
# COMMUNICATION — Is the natural language response clear and helpful?
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_COMMUNICATION_SYSTEM = """\
You are a laboratory scientist evaluating two AI responses for COMMUNICATION \
QUALITY — how clear, informative, and helpful the natural language explanation is.

Your task: determine which response better communicates the pipeline design, \
explains tool choices, and helps the user understand what was built and why.

CRITICAL: Write your complete step-by-step reasoning FIRST, then declare the winner.

RULES:
- Clear explanation of what tools were selected and why
- Acknowledgment of the user's specific organism, platform, and goals
- Helpful context about the pipeline workflow (what each step does)
- Warning about important caveats or assumptions
- Professional and accessible tone (not overly jargon-heavy for non-experts)
- If both communicate equally well, declare a tie

Respond with a JSON object:
{{
  "reasoning": "Your step-by-step analysis...",
  "winner": "A" or "B" or "tie"
}}\
"""

PAIRWISE_COMMUNICATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PAIRWISE_COMMUNICATION_SYSTEM),
    ("human",
     "USER REQUEST:\n{prompt}\n\n"
     "--- Option A (AI Response) ---\n{option_a}\n\n"
     "--- Option B (AI Response) ---\n{option_b}")
])


# ══════════════════════════════════════════════════════════════════════════════
# Ground-Truth Three-Tier Classification Prompt
# ══════════════════════════════════════════════════════════════════════════════

GROUND_TRUTH_CLASSIFICATION_SYSTEM = """\
You are a bioinformatics expert classifying an LLM-generated Nextflow pipeline \
against a verified ground-truth reference.

Your task: classify the LLM output into one of three tiers:

MATCH — The LLM output is functionally equivalent to the ground truth. It \
includes the same steps, wired the same way, producing equivalent results. \
Minor differences in variable naming, import ordering, or whitespace don't matter.

EXCEEDS — The LLM output includes ALL ground-truth elements PLUS additional \
beneficial steps that improve the pipeline. For example, adding a trimming step \
before assembly, or adding species identification in parallel. The extras must \
be valid catalog tools used correctly — hallucinated or incorrect extras still \
count as DEFICIENT.

DEFICIENT — The LLM output is MISSING one or more required elements from the \
ground truth, OR includes hallucinated/incorrect tools. Even if it has extras, \
if required steps are missing, it's DEFICIENT.

CRITICAL: Write your reasoning FIRST, then classify.

Respond with a JSON object:
{{
  "reasoning": "Your analysis...",
  "tier": "MATCH" or "EXCEEDS" or "DEFICIENT",
  "extra_steps": ["list", "of", "extra", "step_ids"],
  "missing_steps": ["list", "of", "missing", "step_ids"]
}}\
"""

GROUND_TRUTH_CLASSIFICATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", GROUND_TRUTH_CLASSIFICATION_SYSTEM),
    ("human",
     "USER REQUEST:\n{prompt}\n\n"
     "GROUND TRUTH (verified, compiles, passes stub-run):\n"
     "```groovy\n{ground_truth}\n```\n\n"
     "LLM OUTPUT:\n```groovy\n{llm_output}\n```")
])


# ══════════════════════════════════════════════════════════════════════════════
# Prompt Registry — maps dimension name to prompt template
# ══════════════════════════════════════════════════════════════════════════════

PAIRWISE_PROMPTS: dict[str, ChatPromptTemplate] = {
    "faithfulness":    PAIRWISE_FAITHFULNESS_PROMPT,
    "relevance":       PAIRWISE_RELEVANCE_PROMPT,
    "syntax":          PAIRWISE_SYNTAX_PROMPT,
    "logic":           PAIRWISE_LOGIC_PROMPT,
    "diagram_quality": PAIRWISE_DIAGRAM_PROMPT,
    "communication":   PAIRWISE_COMMUNICATION_PROMPT,
}
