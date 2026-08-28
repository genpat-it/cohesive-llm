import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Import your custom modules
from core.loader import data_loader
from core.utils.logger import logger


# --- 1. DATA MODELS (Request/Response) ---
class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique ID for the user session to remember chat history")
    message: str = Field(..., description="The user's prompt or reply")
    generate_diagrams: bool = Field(True, description="Whether to run diagram generation nodes for this turn")
    idempotency_key: str | None = Field(None, description="Optional key to prevent duplicate requests")

class ChatResponse(BaseModel):
    status: str
    reply: str
    nextflow_code: str | None = None
    mermaid_agent: str | None = None
    mermaid_deterministic: str | None = None
    ast_json: dict[str, Any] | None = None
    error: str | None = None
    tool_calls: list[str] | None = None

# --- 1.5 DEPENDENCIES ---
def get_graph() -> Any:
    from core.services.graph import app_graph
    return app_graph

# --- 2. LIFESPAN (Startup/Shutdown Logic) ---
@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    try:
        from core.services.graph import global_store
        await asyncio.to_thread(data_loader.load_all, store=global_store)
    except Exception as e:
        logger.error("startup_error", error=str(e))
    yield
    logger.info("server_shutting_down")

# --- 3. API APP DEFINITION ---
app = FastAPI(
    title="Nextflow AI Agent API",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 4. ENDPOINTS ---

@app.get("/health")
def health_check() -> Any:
    return {
        "status": "online",
        "vector_store": "loaded" if data_loader.vector_store else "not_loaded"
    }

import time
from collections import OrderedDict

from langchain_core.messages import AIMessage, HumanMessage


class IdempotencyCache:
    def __init__(self, maxsize: int = 1000, ttl: int = 3600) -> None:
        self.cache: OrderedDict = OrderedDict()
        self.maxsize = maxsize
        self.ttl = ttl

    def get(self, key: str) -> Any | None:
        if key in self.cache:
            entry_time, value = self.cache[key]
            if time.time() - entry_time <= self.ttl:
                self.cache.move_to_end(key)
                return value
            del self.cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self.cache:
            del self.cache[key]
        self.cache[key] = (time.time(), value)
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

_idempotency_cache = IdempotencyCache(maxsize=1000, ttl=3600)

from fastapi import Depends


@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(request: ChatRequest, graph: Any=Depends(get_graph)) -> Any:  # noqa: C901
    trace_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id, session_id=request.session_id)
    try:
        if request.idempotency_key:
            cached_resp = _idempotency_cache.get(request.idempotency_key)
            if cached_resp:
                logger.info("idempotency_cache_hit", key=request.idempotency_key)
                return cached_resp

        # Set up the thread ID so the agent remembers the chat
        config = {"configurable": {"thread_id": request.session_id}}
        # Run the graph with async, applying a 10-minute timeout
        result = await asyncio.wait_for(
            graph.ainvoke(
                {
                    "user_query": request.message,
                    "generate_diagrams": request.generate_diagrams,
                    "messages": [("user", request.message)]
                },
                config=config
            ),
            timeout=600.0
        )

        # Check for agent errors
        if result.get("error"):
            return ChatResponse(
                status="failed",
                reply="The agent encountered an error.",
                error=str(result["error"])
            )

        # Get the status and final codes
        status = result.get("consultant_status", "CHATTING")
        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid_agent = result.get("mermaid_agent")
        mermaid_deterministic = result.get("mermaid_deterministic") or mermaid_agent

        # Get the AI reply from the messages list safely
        messages = result.get("messages", [])
        
        import os
        log_dir = os.environ.get("IZS_LOG_DIR")
        if log_dir:
            try:
                from core.utils.trace_dumper import dump_trace
                dump_trace(log_dir, request.session_id, messages)
            except Exception as e:
                logger.error("trace_dump_error", error=str(e))
        
        ai_reply = "No response generated."
        
        if status == "APPROVED":
            if result.get("error"):
                ai_reply = f"I encountered an error while building the pipeline: {result.get('error')}"
            elif result.get("validation_error"):
                ai_reply = f"I could not fix the pipeline validation errors after multiple attempts. The last error was:\n\n{result.get('validation_error')}"
            else:
                ai_reply = "I have successfully generated and validated the Nextflow pipeline based on your approved plan."
        else:
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    if msg.additional_kwargs.get("internal_agent"):
                        continue
                    ai_reply = msg.content
                    break
        # Collect tool calls from the most recent turn (since last human message)
        tool_calls = []
        seen = set()
        last_human_idx = None
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
                last_human_idx = idx
                break

        start_idx = last_human_idx + 1 if last_human_idx is not None else 0
        for msg in messages[start_idx:]:
            for tc in getattr(msg, "tool_calls", []) or []:
                name = tc.get("name")
                if name and name not in seen:
                    tool_calls.append(name)
                    seen.add(name)

        response = ChatResponse(
            status=status,
            reply=ai_reply,
            nextflow_code=nf_code,
            mermaid_agent=mermaid_agent,
            mermaid_deterministic=mermaid_deterministic,
            ast_json=ast_json,
            error=None,
            tool_calls=tool_calls
        )

        if request.idempotency_key:
            _idempotency_cache.set(request.idempotency_key, response)

        return response

    except TimeoutError:
        logger.error("server_timeout")
        return ChatResponse(
            status="error",
            reply="The request timed out while processing.",
            error="TimeoutError"
        )
    except Exception as e:
        logger.error("server_error", error=str(e))
        return ChatResponse(
            status="error",
            reply="The server encountered an unexpected error.",
            error=str(e)
        )
    finally:
        structlog.contextvars.clear_contextvars()
