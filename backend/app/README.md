# `app/` Directory API Internals

This is the primary application directory for the **Nextflow AI Agent API** (`izs-llm`). It defines the FastAPI application endpoints and connects the server layer to the deeply nested LangGraph AI logic.

## Application Architecture

```mermaid
sequenceDiagram
    participant User
    participant FastAPI as app/api.py
    participant Core as app/core/loader.py
    participant Graph as app/services/graph.py
    
    Note over FastAPI,Core: Application Lifespan Startup
    FastAPI->>Core: loader.load_all()
    Core-->>FastAPI: FAISS & Metadata Initialized
    
    Note over User,Graph: API Request Flow
    User->>FastAPI: POST /chat (session_id, message)
    FastAPI->>Graph: app_graph.ainvoke(user_query)
    Graph-->>FastAPI: Returns GraphState (status, reply, ast, code)
    FastAPI-->>User: ChatResponse JSON
```

## Structure Overview

* **`api.py`**: The central FastAPI application. 
  * Uses a Python `@asynccontextmanager` called `lifespan` to preload the vector databases into memory upon Uvicorn boot.
  * Exposes the `POST /chat` endpoint. This routes incoming user text and their `session_id` into the LangGraph state machine (`app_graph`). Once the graph finishes traversing all of its active nodes, it returns the final `GraphState`, which the API packages into a `ChatResponse` model.
  * Includes a `GET /health` endpoint for basic readiness probes (useful for Docker orchestrators like Kubernetes).
* **`core/`**: Initialization loaders and application-wide configurations (like model selection and paths).
* **`models/`**: Strict Pydantic classes governing LLM output constraints.
* **`services/`**: The massive AI component. Contains the node definitions (Consultant, Architect, Diagram) and the actual StateGraph that links them.
* **`utils/`**: Utilities for rendering ASTs into Groovy strings using Jinja2 templates.
