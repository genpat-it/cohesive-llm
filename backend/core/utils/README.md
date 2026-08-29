# Core Utilities (`backend/core/utils/`) - Cross-Cutting Tools & Helpers

The `backend/core/utils/` directory provides foundational, deterministic utilities including structured JSON logging, exponential retry decorators, Jinja2 AST formatting, and debug trace extractors.

---

## 1. Modules Overview

```mermaid
flowchart LR
    Logger["logger.py\n(Structured JSON Logging)"]
    Retry["retry.py\n(Exponential Backoff & Rate-Limit Retries)"]
    Rendering["rendering.py\n(AST Jinja2 Template Definition)"]
    TraceDumper["trace_dumper.py\n(LangGraph State Trace Extractor)"]
```

---

## 2. Utility Specifications

### 2.1 `logger.py` — Structured JSON Logger
- Configures Python standard logging with `structlog` / JSON formatting.
- Automatically outputs structured key-value context attributes (`node`, `event`, `level`, `timestamp`, `extracted_ids`, `plugin_name`) for downstream observability and log aggregation.

### 2.2 `retry.py` — Resilience & Exponential Backoff
- Implements intelligent retry decorators with exponential backoff and jitter (`tenacity`-backed).
- Intercepts transient HTTP 429 (rate limits), connection resets, and LLM server timeouts, protecting agentic execution loops from transient network drops.

### 2.3 `rendering.py` — Nextflow DSL2 Jinja2 AST Template
- Defines `NF_TEMPLATE_AST`: the master Jinja2 template utilized by the AST compiler to serialize `NextflowPipelineAST` objects into production-grade Nextflow DSL2 source code.
- Enforces strict indentation (8 spaces for subworkflow main blocks, 4 spaces for entrypoints) and clean separation between imports, globals, inline processes, subworkflows, and entrypoint blocks.

### 2.4 `trace_dumper.py` — LangGraph State Trace Extractor
- Utility to extract and format internal LangGraph state transitions, system prompts, tool invocations, and agent thoughts into human-readable Markdown traces for debugging and benchmarking.
