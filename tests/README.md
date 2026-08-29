# IZS-LLM Pairwise Evaluation Test Suite

> **Architecture (Topo@k Evaluation)**: Pairwise comparison with position bias control,
> Chain-of-Thought reasoning, Glicko-2 ratings, and incremental checkpoint/resume.

## Quick Start

```bash
# Set required environment variables
export OPENAI_API_KEY="your_key"
export JUDGE_BASE_URL="your_judge_endpoint"

# Run ALL single-turn tests (205 examples)
python3 tests/test_benchmark_single_turn.py

# Run ALL multi-turn tests (159 conversations, 330 turns)
python3 tests/test_benchmark_multi_turn.py
```

## Running Subsets

The system makes it easy to run any slice of the benchmark:

### By count (quick smoke tests)

```bash
# First 5 examples — fast validation that everything works
python3 tests/test_benchmark_single_turn.py --first 5

# First 10 multi-turn conversations
python3 tests/test_benchmark_multi_turn.py --first 10
```

### By pipeline complexity (1–5)

```bash
# Level 1 only: mono-step pipelines (trimming, assembly, typing, etc.)
python3 tests/test_benchmark_single_turn.py --complexity 1

# Level 2 only: two-step (trim→assemble)
python3 tests/test_benchmark_single_turn.py --complexity 2

# Levels 1–3 (everything up to 3-step)
python3 tests/test_benchmark_single_turn.py --max-complexity 3
```

#### Complexity Levels

| Level | Categories | Count | Description |
|-------|-----------|-------|-------------|
| 1 | mono-taskA, mono-taskB, mono-taskC, ... | ~57 | Single-step pipelines |
| 2 | 2step-process-A, 2step-process-B | 7 | Two-step pipelines |
| 3 | 3step, 3step.K, 3step-process-C, 3step-parallel.Q | ~104 | Three-step pipelines |
| 4 | 4step | 24 | Four-step pipelines |
| 5 | 5step, multi.aggregation, multi.analysis | 15 | Five-step + multi-analysis |

### By category

```bash
# Only mono-taskA examples (8 examples)
python3 tests/test_benchmark_single_turn.py --category mono-taskA

# Combine multiple categories
python3 tests/test_benchmark_single_turn.py --category mono-taskA --category mono-taskB

# Three-step parallel branching variants
python3 tests/test_benchmark_single_turn.py --category 3step-parallel.Q
```

### By example ID

```bash
# Specific examples
python3 tests/test_benchmark_single_turn.py --ids A01_domain_task_1,B01_domain_task_2

# Multiple comma-separated IDs
python3 tests/test_benchmark_single_turn.py --ids A01,B01
```

### Multi-turn: by modification kind

```bash
# Only "add" modifications (49 conversations)
python3 tests/test_benchmark_multi_turn.py --mod-kind add

# Multiple kinds
python3 tests/test_benchmark_multi_turn.py --mod-kind add --mod-kind drop
```

### By test type (Variant Datasets)

```bash
# Code generation (default) - evaluating generated Nextflow code
python3 tests/test_benchmark_single_turn.py --test-type code_generation

# Consultant variants - evaluating tool recommendations based on active plugin
python3 tests/test_benchmark_single_turn.py --test-type consultant

# Diagram variants - evaluating generated Mermaid diagrams
python3 tests/test_benchmark_single_turn.py --test-type diagram

# Rejection scenarios - evaluating appropriate system refusals
python3 tests/test_benchmark_single_turn.py --test-type rejection

# Recreation scenarios - multi-turn reference module reproduction
python3 tests/test_benchmark_single_turn.py --test-type recreation
```

### Combining filters

```bash
# First 5 mono-step examples
python3 tests/test_benchmark_single_turn.py --complexity 1 --first 5

# First 5 consultant variants
python3 tests/test_benchmark_single_turn.py --test-type consultant --first 5

# First 3 "add" modifications
python3 tests/test_benchmark_multi_turn.py --mod-kind add --first 3
```


## A/B Model Comparison

Compare outputs from two model runs on the same prompts:

```bash
# Run benchmark twice, saving outputs
# (both runs save to tests/reports/runs_*.jsonl)

# Compare
export RUN_A_PATH="tests/reports/runs_model_a.jsonl"
export RUN_B_PATH="tests/reports/runs_model_b.jsonl"
export RUN_A_LABEL="mistral-large"
export RUN_B_LABEL="qwen3-30b"
pytest tests/test_pairwise_ab.py -v
```

## How It Works

### Evaluation Flow

```
User Prompt → /chat API (full agent pipeline) → LLM Output
                                                     ↓
                                        ┌────────────┴────────────┐
                                        │                         │
                                 Deterministic Checks      Pairwise Comparison
                                 (step matching, P/R/F1)   (LLM judge × 2 orderings)
                                        │                         │
                                        └────────────┬────────────┘
                                                     ↓
                                              Glicko-2 Rating
                                              + Report (MD + CSV)
```

### Pairwise Comparison (vs Likert scoring)

Instead of asking the judge to score an output on a 1–5 scale (cognitively
demanding, high variance), we present **two options** and ask the judge to
pick the better one (A, B, or tie).

**Position bias control**: Each comparison runs **twice** with A and B swapped.
If both orderings agree on the winner, that's the verdict. If they disagree,
it's a conservative tie.

**Chain-of-Thought**: The judge must explain its reasoning step-by-step
BEFORE declaring a winner, improving reliability.

### Evaluation Dimensions

Each test type gets a relevant subset of 6 dimensions:

| Dimension | Description | Test Types |
|-----------|-------------|------------|
| `faithfulness` | Uses only catalog tools? | consultant, code_gen, rejection |
| `relevance` | Correct context parameters? | consultant |
| `syntax` | Valid Nextflow DSL2? | code_gen, recreation |
| `logic` | Correct pipeline wiring? | code_gen, recreation |
| `diagram_quality` | Accurate Mermaid diagram? | diagram |
| `communication` | Clear, helpful response? | all |

### Ground Truth Verdicts

For benchmark examples with verified ground truth:

| Tier | Meaning |
|------|---------|
| **MATCH** | LLM output ≡ ground truth (same steps, same wiring) |
| **EXCEEDS** | LLM includes all GT steps + beneficial extras |
| **DEFICIENT** | LLM is missing required GT steps |

### Pairwise Glicko-2 Evaluation (Topo@k)

Advanced rating system (evolution of Elo) that provides:
- **Rating (μ)**: player strength (starts at 1500)
- **Rating Deviation (φ)**: uncertainty (shrinks with more matches)
- **95% Confidence Intervals**: for paper reporting
- **Bootstrap significance tests**: p-values for A vs B comparisons

## Incremental / Resumable Runs

The system writes results to JSONL checkpoint files as it goes.
You can stop mid-run (Ctrl+C) and resume later — it skips already-completed
examples automatically.

```bash
# Run first batch
python3 tests/test_benchmark_single_turn.py --complexity 1

# ... take a break ...

# Resume the rest — automatically skips already-completed examples
python3 tests/test_benchmark_single_turn.py

# Force re-run by deleting checkpoint
rm tests/reports/eval_checkpoint_single.jsonl
```

Checkpoint files: `tests/reports/eval_checkpoint_*.jsonl`

## Directory Structure

```
tests/
├── benchmark/                     # Benchmark dataset + loading
│   ├── data/                      # Dataset JSONL files (local copies)
│   │   ├── dataset_205.jsonl      # 205 single-turn examples
│   │   ├── dataset_modifications_full.jsonl  # 159 multi-turn conversations
│   │   └── README.md              # Dataset documentation
│   ├── loader.py                  # Dataset loading + subset filtering
│   └── enrichment.py              # Adds chat_messages, component_ids
│
├── evaluation/                    # Pairwise evaluation engine
│   ├── dimensions.py              # 6 dimensions + test-type mapping
│   ├── schemas.py                 # PairwiseVerdict, BenchmarkResult, etc.
│   ├── prompts.py                 # Judge prompts per dimension
│   ├── pairwise.py                # Core comparison engine + checkpoint
│   └── elo.py                     # Glicko-2 rating system
│
├── legacy/                        # Old Likert-scale test suite (preserved)
│   ├── evaluation/                # Old judge prompts + schemas
│   ├── scenarios/                 # Old L1–L5 scenario definitions
│   ├── test_consultant.py         # Old consultant tests
│   ├── test_execution.py          # Old execution tests
│   └── ...
│
├── test_benchmark_single_turn.py  # 205 single-turn tests
├── test_benchmark_multi_turn.py   # 159 multi-turn tests
├── test_pairwise_ab.py            # A/B model comparison
│
├── report.py                      # Markdown + CSV report generator
├── helpers.py                     # Shared utilities
├── conftest.py                    # Fixtures + CLI subset options
├── error_patterns.py              # Error categorization (reused)
└── nf_validation.py               # Nextflow validation (reused)
```

## Reports

Reports are saved to `tests/reports/`:
- `pairwise_report_YYYYMMDD_HHMMSS.md` — Full markdown report
- `pairwise_results_YYYYMMDD_HHMMSS.csv` — Per-example × dimension data
- `pairwise_report_latest.md` — Latest report (overwritten each run)
- `eval_checkpoint_*.jsonl` — Checkpoint files for resume

## Configuration

| Env Variable | Purpose | Default |
|-------------|---------|---------|
| `OPENAI_API_KEY` | Powers the agent | **required** |
| `JUDGE_BASE_URL` | Judge LLM endpoint | **required** |
| `JUDGE_RATE_LIMIT` | Enable judge rate limiting | `false` |
| `RUN_A_PATH` | A/B mode: model A outputs | — |
| `RUN_B_PATH` | A/B mode: model B outputs | — |
| `RUN_A_LABEL` | A/B mode: model A label | `model_a` |
| `RUN_B_LABEL` | A/B mode: model B label | `model_b` |

## CLI Options Reference

| Option | Applies To | Description |
|--------|-----------|-------------|
| `--first N` | all | Only run the first N examples |
| `--complexity L` | single-turn | Only run complexity level L (1-5) |
| `--max-complexity L` | single-turn | Only run up to complexity level L |
| `--category CAT` | single-turn | Only run category CAT |
| `--ids ID1,ID2` | all | Only run specific example IDs |
| `--mod-kind KIND` | multi-turn | Only run modification kind (repeatable) |

## Legacy Tests

The old Likert-scale evaluation suite is preserved in `tests/legacy/`:

```bash
pytest tests/legacy/ -v
```

---

## IZS-LLM Validation, Benchmark, and Testing Report

The testing and validation architecture for the `izs-llm` framework consists of two main pillars: a comprehensive **Pairwise Evaluation Benchmark Suite (Topo@k)** for comparative model testing, and a suite of **Component-Specific Validation Subprocesses** that assess distinct features of the pipeline.

### 1. Current Benchmark Framework: Pairwise Glicko-2 (Topo@k) Evaluation

The current primary evaluation mechanism compares two LLM executions head-to-head. Instead of scoring outputs on a 1–5 scale (which can have high variance), it uses a **Pairwise LLM Judge** with position bias control and Chain-of-Thought reasoning. Models are then ranked using the **Glicko-2 Rating System**.

#### Datasets and Complexity
The benchmark runs against curated datasets organized by pipeline complexity:
- **Single-Turn Benchmark:** 205 examples spanning complexity levels 1 to 5 (from simple mono-step pipelines like trimming/assembly to complex 5-step parallel aggregations).
- **Multi-Turn Benchmark:** 159 conversations (330 turns) focusing on iterative pipeline modifications (e.g., adding or dropping steps).

#### The Evaluation Flow
For each benchmark run, the system executes the following flow:
1. **Agent Pipeline Execution:** The user request is passed to the `/chat` API, and the full pipeline generates the output (Nextflow code, diagrams, tool recommendations, etc.).
2. **Deterministic Checks:** Validates structure via exact step matching and calculates Precision/Recall/F1 scores based on verified ground truth.
3. **Pairwise Comparison:** An LLM Judge compares Model A and Model B across 6 dimensions.
    - *Position bias control:* Both A vs B and B vs A orderings are evaluated.
    - *Chain-of-Thought:* The judge explicitly explains reasoning step-by-step before deciding the winner.
4. **Rating Calculation:** Glicko-2 calculates Player Strength (μ) and Rating Deviation (φ).

#### Evaluation Dimensions
The pairwise judge evaluates specific test types based on up to 6 dimensions:
- **Faithfulness:** Does the AI only use actual tools from the catalog?
- **Relevance:** Are context parameters correct for the biological problem?
- **Syntax:** Is the generated Nextflow DSL2 code valid?
- **Logic:** Is the pipeline wired together correctly?
- **Diagram Quality:** Are the generated Mermaid diagrams accurate?
- **Communication:** Is the system's textual response clear and helpful?

---

### 2. Detailed Validation Subprocesses (Component Level)

The framework breaks down validation into specialized test subprocesses (originally designed as the Likert-scale test suite and now integrated into variant benchmark datasets). These tests validate discrete components of the AI's behavior:

#### a. Retrieval Testing Feature (RAG Validation)
- **Goal:** Check if the AI correctly locates the required tools from the catalog.
- **Process:** The system searches the Qdrant vector database based on the request.
- **Validation:** **Deterministic**. There is no LLM judge here. The system simply checks if the expected `tool_id` from the catalog matches the results returned by the agent. If this fails, the system knows the search/retrieval mechanism is broken, not the code generation.
- **File Reference:** [test_rag.py](file:///Users/grady/Documents/DIE/cloud/izs-llm/tests/legacy/test_rag.py)

#### b. Planning and Consultation Feature
- **Goal:** Test how the AI plans the biological work before writing code.
- **Process:** The user request is processed by the Consultant Node to plan the pipeline.
- **Validation:** The system first checks if the plan achieves an "approved" status. A specialized **Consultant Judge** then assesses the plan.
    - *Faithfulness:* Checks if the plan hallucinated tools.
    - *Relevance:* Checks if the plan actually solves the underlying bioinformatics problem.
- **File Reference:** [test_consultant.py](file:///Users/grady/Documents/DIE/cloud/izs-llm/tests/legacy/test_consultant.py)

#### c. Code Generation and Execution Feature
- **Goal:** Ensure the generated Nextflow DSL2 code is structurally and logically correct.
- **Process:** The Architect Node generates Nextflow code and a flowchart.
- **Validation:**
    1. **Existence Check:** Verifies the code was output.
    2. **Nextflow Stub Run:** The system runs the generated Nextflow code in a stub execution to catch any compiler or basic connectivity errors.
    3. **Architect Judge:** Validates the syntax and ensures the code directly follows the consultant's plan.
    4. **Diagram Judge:** Evaluates the Mermaid flowchart to ensure it aligns perfectly with the generated code.
- **File Reference:** [test_execution.py](file:///Users/grady/Documents/DIE/cloud/izs-llm/tests/legacy/test_execution.py)

#### d. Guardrails and Safety Feature
- **Goal:** Verify that the AI refuses bad, unsafe, or impossible laboratory requests.
- **Process:** The agent receives a malicious or out-of-scope prompt.
- **Validation:** The test asserts that the AI terminates the workflow generation and remains in a conversational state. The **Rejection Judge** then evaluates:
    - *Refusal Quality:* Did the AI safely and politely refuse the request?
    - *Alternatives:* Did the AI offer a valid alternative solution instead of wasting lab resources?

#### e. Module Recreation Feature
- **Goal:** Ensure the AI generated code strictly adheres to the lab's manual coding standards.
- **Process:** The AI is asked to recreate existing standard pipelines.
- **Validation:** A **Recreation Judge** performs a comparative analysis against a human-written reference codebase. It checks:
    - *Structure:* Does the code look human-readable and standard?
    - *Data Logic:* Are the data connections mathematically and logically identical to the reference?

#### f. Report Generation
- **Process:** Once all validation and benchmark tests finish, the system automatically collects all deterministic scores and LLM Judge explanations.
- **Validation:** It aggregates this data to create markdown (`.md`) and spreadsheet (`.csv`) reports (e.g., `pairwise_report_latest.md`), giving developers actionable debugging information and detailed judge reasoning.
