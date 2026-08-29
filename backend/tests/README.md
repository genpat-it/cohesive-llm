# Testing & Evaluation Framework (`backend/tests/`)

The `backend/tests/` directory provides a multi-tiered verification framework:
1. **Offline Unit & Competency Tests**: 33 fast, deterministic unit tests (~0.5s execution) verifying agent logic, topological routing, AST compilation, and Mermaid rendering with zero LLM API dependency.
2. **Pairwise Benchmark Evaluation Harness**: Comprehensive LLM-as-a-judge evaluation system with position bias control, Chain-of-Thought scoring, and Glicko-2 competitive rating calculations across pipeline complexity levels 1–5.

---

## 1. Quickstart: Running Offline Unit Tests

```bash
# Run the complete offline unit test suite (33 tests)
PYTHONPATH=backend python3 backend/tests/run_unit_tests.py
```

### Unit Test Modules (`backend/tests/unit/`):
- **`test_drawer_enricher.py`**: Validates dynamic Knowledge Graph hydration, schema injection, and operator synthesis.
- **`test_drawer_topology.py`**: Validates Drawflow visual node/port wiring resolution.
- **`test_error_patterns.py`**: Validates Nextflow error pattern classification and channel arity mismatch parsing.
- **`test_glicko2_math.py`**: Validates Glicko-2 rating updates, rating deviations, and tie handling.
- **`test_mermaid_renderer.py`**: Validates deterministic AST Mermaid rendering, subworkflow boundaries, port connections, and phantom node prevention.
- **`test_metrics_helpers.py`**: Validates step inclusion parsing, step precision, recall, and F1 scoring.
- **`test_pairwise_evaluator_unit.py`**: Validates LLM-as-a-judge decision parsing, position swap consistency, and tie evaluation.

---

## 2. Running the Live Benchmark & Pairwise Evaluation

```bash
# Set required environment variables
export OPENAI_API_KEY="your_api_key"
export LOCAL_LLM_URL="http://localhost:8000/v1"

# Run single-turn benchmark tests (Levels 1-5)
python3 backend/tests/test_benchmark_single_turn.py

# Run multi-turn conversational benchmark tests
python3 backend/tests/test_benchmark_multi_turn.py

# Run 50 edge cases evaluation
python3 backend/tests/run_edge_cases_evaluation.py
```

---

## 3. Evaluation Metrics & Rating System

### 3.1 Glicko-2 Rating Algorithm
The evaluation harness treats the pipeline generator as a competitor playing matches against baseline references. Each match updates:
- **Rating ($R$)**: The skill rating of the model (default: 1500).
- **Rating Deviation ($RD$)**: The measurement uncertainty (default: 350).
- **Volatility ($\sigma$)**: The degree of expected rating fluctuation (default: 0.06).

### 3.2 Position Bias Control
To eliminate positional bias in LLM-as-a-judge evaluations, every pair `(A, B)` is evaluated twice:
1. Pass 1: Candidate as Model A, Baseline as Model B.
2. Pass 2: Baseline as Model A, Candidate as Model B.
- **Consistent Winner**: If candidate wins in both configurations $\rightarrow$ Candidate Wins.
- **Inconsistent Disagreement**: If judge flips decision based on position $\rightarrow$ Scored as a **Tie**.
