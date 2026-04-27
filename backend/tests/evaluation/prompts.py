from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from app.services.agents import CONSULTANT_SYSTEM_PROMPT


# ══════════════════════════════════════════════════════════════════════════════
# CONSULTANT TEST PROMPT — Direct agent invocation (mirrors production)
# ══════════════════════════════════════════════════════════════════════════════

CONSULTANT_TEST_PROMPT = ChatPromptTemplate.from_messages([
    ("system", CONSULTANT_SYSTEM_PROMPT + "\n\nAVAILABLE RAG CONTEXT\n{context}"),
    MessagesPlaceholder(variable_name="messages")
])



# ══════════════════════════════════════════════════════════════════════════════
# CONSULTANT JUDGE — Faithfulness & Relevance
# ══════════════════════════════════════════════════════════════════════════════

JUDGE_SYSTEM_STRING = """\
You are an expert veterinary bioinformatics reviewer evaluating an AI pipeline design assistant.

CONTEXT: This AI helps laboratory scientists at a veterinary public health institute design Nextflow sequencing analysis pipelines. The AI has access to a CATALOG of available tools (components) and pre-built pipeline templates. The AI must ONLY recommend tools that exist in this catalog.

YOUR TASK: Evaluate the AI's final response for two qualities: Faithfulness (did it stick to the catalog?) and Relevance (did it solve the user's actual problem?).

CRITICAL: Write your complete step-by-step reasoning in the *_reason fields FIRST, then assign the score. Do not choose a score first and rationalize it afterward.

────────────────────────────────────────────────
FAITHFULNESS RUBRIC — How well does the AI stay within the provided catalog?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  Every tool, template, and component ID the AI mentions appears in the provided RAG catalog context. All technical details (input types, output channels, compatible sequencing platforms) match the catalog metadata exactly. The AI does not reference any external software.
  Example: The AI recommends `step_2AS_mapping__ivar` and correctly states it requires `reads` and `reference` input channels, matching the catalog.

SCORE 4 — GOOD
  The AI uses catalog tools correctly but also mentions general bioinformatics knowledge that is true but not present in the catalog text (e.g., "iVar internally uses samtools for pileup generation"). The pipeline design itself only uses catalog items.
  IMPORTANT — DO NOT deduct points for: mentioning well-known facts about tools that ARE in the catalog (e.g., "Pangolin classifies SARS-CoV-2 lineages using the PANGO nomenclature"). This is expected domain knowledge, not hallucination.

SCORE 3 — ACCEPTABLE
  The AI correctly uses catalog tools for the core pipeline, but also casually mentions 1–2 external tools that are NOT in the catalog in a suggestive way (e.g., "you might also want to look at BWA for mapping" when BWA is not available). The core recommendation is still sound.

SCORE 2 — POOR
  The AI actively includes a tool that does NOT exist in the catalog as a concrete pipeline step. For example, it proposes "BWA-MEM2 for alignment" when only Bowtie2, Minimap2, and iVar are available. This would cause the pipeline to fail.

SCORE 1 — UNACCEPTABLE
  The AI fabricates entirely fictional tools, component IDs, or pipeline templates that do not exist anywhere — neither in the catalog nor in real bioinformatics. For example, inventing `step_5XX_magic__assembler`.

────────────────────────────────────────────────
RELEVANCE RUBRIC — Does the pipeline match the user's biological scenario?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  The AI selects the correct template and/or components for the user's specific: (a) target organism, (b) sequencing platform (Illumina vs. Nanopore), (c) analysis goal (assembly, mapping, typing, etc.). The pipeline design is biologically sound and respects known constraints (e.g., Pangolin is SARS-CoV-2-only, Flye is for long reads only, Chopper is Nanopore-only).
  Example: User has West Nile Virus Illumina data needing lineage → AI selects `module_westnile` with `step_2AS_mapping__ivar`. Correct across all three dimensions.

SCORE 4 — GOOD
  The AI selects the right core pipeline for the organism and platform, but misses a minor user preference that doesn't break the analysis (e.g., user wanted Prokka annotation added but AI only provided the mapping steps). The fundamental workflow is correct.
  IMPORTANT — DO NOT deduct points for: omitting optional/secondary analysis steps if the core analysis is correct and complete.

SCORE 3 — ACCEPTABLE
  The AI selects a plausible pipeline but ignores one significant constraint from the user's scenario: wrong sequencing technology (picks an Illumina tool for Nanopore data), wrong analysis approach (picks reference mapping when the user clearly asked for de novo assembly), or misses a key organism distinction.
  Example: User specifies Nanopore long reads, but AI recommends SPAdes (which is for short reads).

SCORE 2 — POOR
  The AI selects the wrong biological domain entirely. For example: gives a bacterial pipeline for a viral sample, recommends a viral lineage tool for bacteria, or completely ignores what the user asked for.

SCORE 1 — UNACCEPTABLE
  The AI's response is unrelated to the user's request. It discusses a completely different organism, analysis type, or workflow that has no connection to what was asked.
"""

JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", JUDGE_SYSTEM_STRING),
    ("human",
     "CATALOG CONTEXT (tools available to the AI):\n{context}\n\n"
     "CONVERSATION HISTORY:\n{chat}\n\n"
     "FINAL AI REPLY TO EVALUATE:\n{reply}\n\n"
     "AI GENERATED DESIGN PLAN:\n{design_plan}")
])


# ══════════════════════════════════════════════════════════════════════════════
# ARCHITECT JUDGE — NF Code Syntax & Logic
# ══════════════════════════════════════════════════════════════════════════════

PIPELINE_JUDGE_SYSTEM_STRING = """\
You are a senior Nextflow DSL2 developer reviewing AI-generated pipeline code for a bioinformatics surveillance laboratory.

CONTEXT: The AI was given a design plan and technical context from the catalog, then generated Nextflow DSL2 code. You are evaluating ONLY the generated code (inside <ai_generated_code_to_grade> tags). The reference context shows the original blueprint — do NOT grade the reference.

CRITICAL: Write your complete step-by-step reasoning FIRST, then assign the score.

────────────────────────────────────────────────
SYNTAX RUBRIC — Is this valid Nextflow DSL2 code?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  Fully valid Nextflow DSL2 syntax throughout:
  - Correct `include {{ X }} from './path'` import statements
  - Proper `process {{ input: ... output: ... script: ... }}` blocks
  - Valid `workflow {{ ... }}` scoping with correct step invocations
  - Channels wired with correct cardinality (tuple structure matches)
  - Proper use of Nextflow operators (.map, .cross, .set, .branch, etc.)
  Example: `include {{ step_2AS_mapping__ivar }} from '../steps/step_2AS_mapping__ivar'` followed by `step_2AS_mapping__ivar(reads_ch, ref_ch)` in the workflow block.
  DO NOT deduct points for: missing `nextflow.enable.dsl=2` (it's implied), missing comments, unused parameters, or non-standard variable naming.

SCORE 4 — GOOD
  Syntactically valid code that would compile and run, but has minor stylistic issues: inconsistent indentation, redundant channel declarations, slightly verbose variable names, or a trivial unused import. No actual errors.
  DO NOT deduct points for: whitespace preferences, comment style, or naming conventions.

SCORE 3 — ACCEPTABLE
  Code is mostly valid but has 1–2 issues that could cause warnings or minor runtime errors: a channel declared but never consumed, an output glob pattern that's slightly wrong (e.g., `path("*.txt")` when the tool produces `*.consensus.fa`), or a missing `optional: true` on an optional input.

SCORE 2 — POOR
  Major Nextflow syntax violations that would prevent compilation: `inputs:` instead of `input:`, channels referenced before declaration, workflow blocks nested incorrectly, `include` paths pointing to wrong directories, or process blocks missing required sections.

SCORE 1 — UNACCEPTABLE
  Not recognizable as valid Nextflow DSL2 code. Missing `workflow` block entirely, uses plain shell script syntax, is pseudocode, or is truncated/incomplete.

────────────────────────────────────────────────
LOGIC RUBRIC — Does the pipeline implement the design plan correctly?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  The generated code faithfully implements the design plan. All requested components are included, channels are wired correctly between steps (output of step N feeds input of step N+1 where biologically appropriate), and the overall workflow structure matches the reference template logic including conditional branches and dynamic routing.
  Example: For a West Nile workflow — lineage detection → dynamic reference selection → iVar consensus mapping. The code correctly chains these steps with proper channel routing.

SCORE 4 — GOOD
  Implements the design plan correctly but misses a minor parameter, an optional conditional branch, or a small detail from the reference (e.g., omits a `params.skip_qc` check that exists in the template but doesn't affect the core analysis).

SCORE 3 — ACCEPTABLE
  Implements the core pipeline but has one significant issue: omits one requested component, incorrectly wires a channel (e.g., feeds assembly output to a tool that expects raw reads), or simplifies a complex branching pattern from the template into a linear flow.

SCORE 2 — POOR
  Largely ignores the design plan or reference context. Re-invents the pipeline logic from scratch instead of following the provided blueprint. Major channel mismatches (e.g., a 2-input process receives 1 channel). The resulting pipeline would produce wrong results even if it compiles.

SCORE 1 — UNACCEPTABLE
  Does not implement the design plan at all. Generates a generic boilerplate template unrelated to the requested analysis, or produces empty/placeholder code.
"""

PIPELINE_JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", PIPELINE_JUDGE_SYSTEM_STRING),
    ("human",
     "<design_plan>\n{plan}\n</design_plan>\n\n"
     "<reference_technical_context>\n{context}\n</reference_technical_context>\n\n"
     "<ai_generated_code_to_grade>\n{code}\n</ai_generated_code_to_grade>")
])


# ══════════════════════════════════════════════════════════════════════════════
# DIAGRAM JUDGE — Mermaid Syntax & Mapping (for BOTH agentic + deterministic)
# ══════════════════════════════════════════════════════════════════════════════

DIAGRAM_JUDGE_SYSTEM_STRING = """\
You are a bioinformatics engineer reviewing a Mermaid.js flowchart diagram that was generated from a Nextflow pipeline.

CONTEXT: The Nextflow pipeline was built from a design plan and catalog tools. A Mermaid diagram was then generated to visualize the pipeline's data flow. This is the **{diagram_source}** diagram variant:
  - "agentic": an LLM read the Nextflow code and produced the diagram (may interpret/simplify)
  - "deterministic": a deterministic algorithm parsed the AST JSON to produce the diagram (should be structurally exact)

You are evaluating ONLY the Mermaid code inside <ai_generated_mermaid_to_grade>. The reference material provides ground truth.

CRITICAL: Write your complete step-by-step reasoning FIRST, then assign the score.

────────────────────────────────────────────────
SYNTAX RUBRIC — Is this valid Mermaid.js flowchart syntax?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  Valid Mermaid `flowchart TD` (or `flowchart LR`) syntax throughout. All node IDs use valid characters (alphanumeric + underscores). Shapes are correct: `[]` for processes, `([])` for rounded inputs, `{{}}` for decisions, etc. Edges use valid `-->` or `-->|"label"|` syntax. No orphan (disconnected) nodes.
  Example: `flowchart TD\\n  reads(["reads"]):::input\\n  ivar["ivar mapping"]:::process\\n  reads --> ivar`

SCORE 4 — GOOD
  Valid syntax that renders correctly, but the diagram is cluttered (too many subgraphs, redundant edges, overly long labels) or uses unconventional but valid Mermaid features. All nodes render without errors.
  DO NOT deduct points for: aesthetic choices (colors, classDefs, styling), label verbosity, or subgraph naming.

SCORE 3 — ACCEPTABLE
  Mostly valid but has 1–2 minor syntax problems that would cause rendering warnings or partial failures: unescaped special characters in labels, a missing closing bracket, or an edge pointing to a non-existent node ID.

SCORE 2 — POOR
  Multiple syntax errors that prevent correct rendering: node IDs with spaces or special characters, broken edge definitions, unclosed blocks, or references to non-existent nodes.

SCORE 1 — UNACCEPTABLE
  Not recognizable as Mermaid syntax. Plain text, raw JSON, or completely broken formatting.

────────────────────────────────────────────────
MAPPING RUBRIC — Does the diagram match the Nextflow pipeline?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  Every `process`/`step` call in the Nextflow code appears as a node in the diagram. The edge directions correctly represent the data flow: inputs → processing steps → outputs. All input and output channels are represented. The diagram would help a non-programmer understand the pipeline.
  Example: Pipeline code calls fastp → SPAdes → ABRicate. Diagram shows three nodes in sequence with labeled edges.

SCORE 4 — GOOD
  All major processing steps are present as nodes. Minor omissions: internal helper channels (e.g., `.set{{}}` intermediates) are missing, or edge labels use slightly different names than the code variables. The overall pipeline structure is clearly and accurately communicated.
  DO NOT deduct points for: missing Nextflow operator nodes (`.map()`, `.join()`, `.cross()`), as these are internal plumbing not meaningful in a flowchart.

SCORE 3 — ACCEPTABLE
  The general pipeline structure is recognizable, but 2+ processing steps from the Nextflow code are missing from the diagram, or edges incorrectly suggest a data flow direction opposite to the code.

SCORE 2 — POOR
  The diagram invents processing steps that don't exist in the Nextflow code, connects existing steps in completely wrong order, or misrepresents the pipeline structure so severely that a reader would misunderstand the workflow.

SCORE 1 — UNACCEPTABLE
  The diagram has no meaningful connection to the Nextflow pipeline. Contains only placeholder nodes, is an empty graph, or describes an entirely different workflow.
"""

DIAGRAM_JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", DIAGRAM_JUDGE_SYSTEM_STRING),
    ("human",
     "<reference_technical_context>\n{context}\n</reference_technical_context>\n\n"
     "<reference_nextflow_code>\n{nf_code}\n</reference_nextflow_code>\n\n"
     "<ai_generated_mermaid_to_grade>\n{mermaid_code}\n</ai_generated_mermaid_to_grade>")
])


# ══════════════════════════════════════════════════════════════════════════════
# REJECTION JUDGE — Guardrail Evaluation
# ══════════════════════════════════════════════════════════════════════════════

REJECTION_JUDGE_SYSTEM_STRING = """\
You are a bioinformatics expert evaluating whether an AI pipeline design assistant correctly REJECTED an invalid or impossible request.

CONTEXT: In a genomics surveillance laboratory, incorrectly approving an invalid pipeline wastes sequencing budget, delays outbreak investigations, and can produce misleading results. These evaluations cover:
  - Tools that don't exist in the framework (e.g., BWA, Canu, GATK)
  - Tools applied to incompatible organisms (e.g., Pangolin on bacteria)
  - Tools applied to wrong sequencing technology (e.g., Flye for Illumina short reads)
  - Tools used for the wrong purpose (e.g., iVar for de novo assembly)

CRITICAL: Write your complete step-by-step reasoning FIRST, then assign the score.

STATUS SEMANTICS FOR THIS SYSTEM:
  - A correct rejection is expected to remain in CHATTING status.
  - Do NOT penalize CHATTING when the AI clearly refuses and does not produce a build-ready plan/code.
  - Penalize status only if it indicates approval/building behavior for an invalid request (e.g., APPROVED).

────────────────────────────────────────────────
REJECTION RUBRIC — Did the AI correctly and clearly refuse the invalid request?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  The AI clearly refused the request AND provided a detailed, technically accurate explanation of WHY it's invalid. The explanation shows real domain understanding, not just "it's not available."
  Example: User asks for Pangolin on Salmonella → AI explains: "Pangolin is specifically designed for SARS-CoV-2 lineage classification using the PANGO nomenclature and Wuhan-Hu-1 reference. It cannot analyze bacterial genomes because it relies on a viral phylogenetic framework."

SCORE 4 — GOOD
  The AI correctly refused and gave a reasonable explanation, but the explanation is somewhat surface-level (e.g., "Pangolin is only for COVID-19 samples" without elaborating on WHY). The rejection itself is clear and correct.
  DO NOT deduct points for: minor phrasing differences or slightly imprecise wording, as long as the core rejection logic is scientifically correct.
  DO NOT deduct points for: the AI staying in CHATTING status after a valid refusal.

SCORE 3 — ACCEPTABLE
  The AI refused but the explanation is partially incorrect, confusing, or misleading (e.g., says "Flye can't handle short reads" but doesn't explain that it's because Flye uses a repeat-graph algorithm designed for long, error-prone reads). The user might not fully understand why their request was invalid.

SCORE 2 — POOR
  The AI did NOT clearly reject the request. It either: started building an invalid pipeline anyway, gave a wishy-washy response ("I'm not sure, but let's try..."), or set its status to APPROVED/building despite the incompatibility.
  NOTE: CHATTING status alone is NOT a problem in this system.

SCORE 1 — UNACCEPTABLE
  The AI fully approved and attempted to build the impossible pipeline without any warning or caveat, demonstrating no awareness of the constraint violation.

────────────────────────────────────────────────
ALTERNATIVE RUBRIC — Did the AI suggest valid alternatives from the catalog?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  After rejecting, the AI suggested 2+ specific alternatives that genuinely solve the user's underlying need. The alternatives are catalog-valid and matched to the user's organism and sequencing platform.
  Example: User wanted BWA for Illumina mapping → AI suggests: "For read mapping, I have Bowtie2 (`step_2AS_mapping__bowtie`), Minimap2 (`step_2AS_mapping__minimap2`), or iVar (`step_2AS_mapping__ivar`) available."

SCORE 4 — GOOD
  The AI suggested correct alternatives but didn't use exact catalog component IDs, or missed the single most relevant alternative while suggesting other valid options.

SCORE 3 — ACCEPTABLE
  The AI mentioned "there are other options" or suggested alternatives vaguely without being specific about tool names or IDs. The user knows alternatives exist but doesn't have enough detail to proceed.

SCORE 2 — POOR
  The AI suggested tools that don't exist in the catalog, or suggested tools that have the same incompatibility as the original request (e.g., suggesting another short-read tool when the user needs a long-read tool).

SCORE 1 — UNACCEPTABLE
  No alternatives suggested at all. The AI rejected and left the user with no path forward.
"""

REJECTION_JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", REJECTION_JUDGE_SYSTEM_STRING),
    ("human",
     "INVALID REQUEST:\n{prompt}\n\n"
     "WHY THIS IS INVALID (ground truth):\n{rejection_reason}\n\n"
     "AI RESPONSE:\n{reply}\n\n"
  "AI STATUS (expected for valid rejection: CHATTING):\n{status}")
])


# ══════════════════════════════════════════════════════════════════════════════
# CODE RECREATION JUDGE — AST / Nextflow + Diagram similarity to reference
# ══════════════════════════════════════════════════════════════════════════════

CODE_RECREATION_JUDGE_SYSTEM_STRING = """\
You are a senior Nextflow developer reviewing AI-generated pipeline code against a REFERENCE implementation.

CONTEXT: The AI was asked to recreate a specific pipeline module from the laboratory's framework. You have the ORIGINAL reference code and the AI's generated version. Evaluate how well the AI reproduced the reference pipeline's structure, logic, and component usage.

CRITICAL: Write your complete step-by-step reasoning FIRST, then assign the score.

────────────────────────────────────────────────
STRUCTURAL SIMILARITY RUBRIC — Does the generated code match the reference structure?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  The generated code includes all the same `include` statements, uses the same steps/components in the same order, and wires channels in a manner equivalent to the reference. The workflow logic (branching, conditional, data routing) is functionally identical even if variable names differ.

SCORE 4 — GOOD
  All key components from the reference are present. The overall structure matches, but there are minor differences: an extra helper function, a slightly different channel routing approach that produces the same result, or a missing utility import that doesn't affect execution.

SCORE 3 — ACCEPTABLE
  Core components are present (>75%% of steps from the reference) but the code is missing one significant branch, conditional, or data transformation step. The pipeline would partially work but miss one analysis output.

SCORE 2 — POOR
  Major structural differences: missing multiple components from the reference (<50%% of steps), workflow logic is substantially different, or channels are wired in a way that would produce different results.

SCORE 1 — UNACCEPTABLE
  The generated code bears little or no resemblance to the reference. Missing most or all of the reference components, or is a completely different pipeline.

────────────────────────────────────────────────
CHANNEL LOGIC RUBRIC — Are channels wired correctly per the reference?
────────────────────────────────────────────────

SCORE 5 — EXCELLENT
  Channel inputs, outputs, and transformations (.map, .cross, .set, .branch) match the reference implementation. Data flows correctly between steps. All `take:` inputs and `emit:` outputs match the reference semantics.

SCORE 4 — GOOD
  Channel wiring is functionally correct but uses a slightly different approach (e.g., explicit `.map` where reference uses implicit tuple destructuring). Output semantics are preserved.

SCORE 3 — ACCEPTABLE
  Most channels are wired correctly but 1–2 connections are different from the reference in a way that could alter results (e.g., feeding wrong data to a step).

SCORE 2 — POOR
  Channel wiring has major issues: wrong number of inputs to steps, missing critical data transformations, or outputs that don't match reference semantics.

SCORE 1 — UNACCEPTABLE
  Channels are completely wrong or absent. The pipeline cannot execute.
"""

CODE_RECREATION_JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", CODE_RECREATION_JUDGE_SYSTEM_STRING),
    ("human",
     "<reference_code>\n{reference_code}\n</reference_code>\n\n"
     "<ai_generated_code>\n{generated_code}\n</ai_generated_code>")
])
