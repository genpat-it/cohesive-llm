import os
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any

# Import your custom modules
from app.core.loader import data_loader
from app.services.graph import app_graph, global_store

# Auth / DB
from app.db import Base, engine, get_db
from app.models.db_models import User
from app.routes.auth import router as auth_router
from app.routes.conversations import (
    router as conversations_router,
    get_or_create_conversation,
    append_message,
)
from app.services.auth import get_current_user, hash_password
from app.services.rate_limit import limiter


# --- 1. DATA MODELS (Request/Response) ---
class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique ID for the user session to remember chat history")
    message: str = Field(..., description="The user's prompt or reply")

class ChatResponse(BaseModel):
    status: str
    reply: str
    conversation_id: Optional[int] = None
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None
    ast_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# --- 2. STARTUP HELPERS ---
def init_db_and_seed():
    """Create tables, run small in-place migrations and seed the demo user."""
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations (idempotent). Used instead of Alembic because the
    # schema is small and only ever grows by adding nullable columns.
    from sqlalchemy import text as _sql_text
    with engine.begin() as conn:
        conn.execute(_sql_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS nextflow_code TEXT"
        ))
        conn.execute(_sql_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS mermaid_code TEXT"
        ))
        conn.execute(_sql_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS ast_json JSONB"
        ))

    from app.db import SessionLocal
    db: Session = SessionLocal()
    try:
        username = os.getenv("DEMO_USER", "demo")
        password = os.getenv("DEMO_PASSWORD", "change_me_please")
        existing = db.query(User).filter(User.username == username).first()
        if not existing:
            user = User(username=username, password_hash=hash_password(password))
            db.add(user)
            db.commit()
            print(f"[startup] Seeded user '{username}'")
        else:
            print(f"[startup] User '{username}' already exists")
    finally:
        db.close()


# --- 3. LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db_and_seed()
    except Exception as e:
        print(f"DB INIT ERROR: {e}")
    try:
        data_loader.load_all(store=global_store)
    except Exception as e:
        print(f"CRITICAL STARTUP ERROR {e}")
    yield
    print("Server shutting down...")


# --- 4. APP DEFINITION ---
app = FastAPI(
    title="Nextflow AI Agent API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS origins driven by env. Comma-separated list.
# Dev default keeps localhost so the dev workflow on http://localhost:9000 works.
# Production: set CORS_ORIGINS=https://cohesive.izs.it
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:9000,http://127.0.0.1:9000")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (slowapi). The actual per-route limits are declared on the
# routes themselves with @limiter.limit("…"). When the limit is exceeded the
# default handler returns 429 with a JSON body.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Routers
app.include_router(auth_router)
app.include_router(conversations_router)


# --- 5. ENDPOINTS ---
@app.get("/health")
def health_check():
    return {
        "status": "online",
        "vector_store": "loaded" if data_loader.vector_store else "not_loaded",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        # Get or create the conversation row for this user/session
        conv = get_or_create_conversation(db, user, request.session_id, request.message)
        append_message(db, conv, "user", request.message)

        # Per-user thread id so different users never share LangGraph state
        thread_id = f"u{user.id}:{request.session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        result = await app_graph.ainvoke(
            {
                "user_query": request.message,
                "messages": [("user", request.message)],
            },
            config=config,
        )

        if result.get("error"):
            return ChatResponse(
                status="failed",
                reply="The agent encountered an error.",
                conversation_id=conv.id,
                error=result["error"],
            )

        messages = result.get("messages", [])
        ai_reply = "No response generated."
        for msg in reversed(messages):
            if msg.type == "ai" and msg.content:
                ai_reply = msg.content
                break

        status = result.get("consultant_status", "CHATTING")
        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid = result.get("mermaid_code")

        # Persist assistant reply (with pipeline artifacts when available)
        append_message(
            db, conv, "assistant", ai_reply,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
        )

        return ChatResponse(
            status=status,
            reply=ai_reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Server Error {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
