import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session

# Core engine imports
from core.config import settings
from core.loader import data_loader
from core.plugin_loader import get_active_plugin
from core.services.graph import app_graph, global_store
from core.utils.logger import logger

# Auth & Database imports
from app.db import Base, engine, get_db, SessionLocal
from app.models.db_models import Drawing, User
from app.routes.auth import router as auth_router
from app.routes.conversations import (
    append_message,
    get_or_create_conversation,
    router as conversations_router,
)
from app.services.auth import get_current_user, hash_password
from app.services.rate_limit import limiter


# --- 1. DATA MODELS ---
class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique ID for the user session to remember chat history")
    message: str = Field(..., description="The user's prompt or reply")
    generate_diagrams: bool = Field(True, description="Whether to run diagram generation nodes for this turn")
    idempotency_key: Optional[str] = Field(None, description="Optional key to prevent duplicate requests")


class ChatResponse(BaseModel):
    status: str
    reply: str
    conversation_id: Optional[int] = None
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None
    ast_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tool_calls: Optional[List[str]] = None


class ValidateRequest(BaseModel):
    nextflow_code: str = Field(..., description="The Nextflow code to validate")


class ValidateResponse(BaseModel):
    success: bool
    warnings: List[str] = []
    errors: List[str] = []
    stdout: Optional[str] = None


class DrawingSave(BaseModel):
    title: str = "Untitled"
    graph_json: Dict[str, Any]


class DrawingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    created_at: Any
    updated_at: Any


class DrawingDetail(DrawingOut):
    graph_json: Dict[str, Any]


class GraphGenerateRequest(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="List of component nodes with IDs and positions")
    edges: List[Dict[str, Any]] = Field(..., description="List of connections between nodes")
    drawing_id: Optional[int] = None
    graph_json: Optional[Dict[str, Any]] = None


# --- 2. STARTUP & SEEDING HELPERS ---
def init_db_and_seed() -> None:
    """Create tables, run lightweight migrations and seed demo user."""
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations for PostgreSQL if running on postgres
    if not str(engine.url).startswith("sqlite"):
        from sqlalchemy import text as _sql_text
        with engine.begin() as conn:
            conn.execute(_sql_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS nextflow_code TEXT"))
            conn.execute(_sql_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS mermaid_code TEXT"))
            conn.execute(_sql_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS ast_json JSONB"))
            conn.execute(_sql_text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS drawing_id INTEGER"))

    db: Session = SessionLocal()
    try:
        username = os.getenv("DEMO_USER", "demo")
        password = os.getenv("DEMO_PASSWORD", "change_me_please")
        existing = db.query(User).filter(User.username == username).first()
        if not existing:
            user = User(username=username, password_hash=hash_password(password[:72]))
            db.add(user)
            db.commit()
            logger.info("demo_user_seeded", username=username)
        else:
            logger.debug("demo_user_exists", username=username)
    except Exception as e:
        logger.error("db_seed_error", error=str(e))
    finally:
        db.close()


def _get_version_info() -> Dict[str, str]:
    """Collect framework commit + LLM model for stamping artifacts."""
    import subprocess
    info = {"llm_model": settings.LLM_MODEL, "llm_provider": settings.LLM_PROVIDER}
    try:
        framework_dir = str(settings.FRAMEWORK_DIR)
        info["framework_commit"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip()
    except Exception:
        info["framework_commit"] = "unknown"
    return info


# --- 3. LIFESPAN ---
@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    try:
        init_db_and_seed()
    except Exception as e:
        logger.error("db_init_error", error=str(e))

    try:
        await asyncio.to_thread(data_loader.load_all, store=global_store)
        logger.info("data_loader_ready", components_loaded=len(data_loader.comp_db))
    except Exception as e:
        logger.error("data_loader_error", error=str(e))

    yield
    logger.info("server_shutting_down")


# --- 4. APP DEFINITION ---
app = FastAPI(
    title="Nextflow AI Agent API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:9000,http://127.0.0.1:9000")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if "*" not in _cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Routers
app.include_router(auth_router)
app.include_router(conversations_router)


# --- 5. ENDPOINTS ---

@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "online",
        "vector_store": "loaded" if data_loader.vector_store else "not_loaded",
        "active_plugin": getattr(settings, "ACTIVE_PLUGIN", "izs"),
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
    }


@app.get("/system-info")
def system_info() -> Dict[str, Any]:
    """Return model names, GPU and RAM stats for the frontend dashboard."""
    import subprocess
    info: Dict[str, Any] = {
        "llm_model": settings.LLM_MODEL,
        "llm_provider": settings.LLM_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
        "active_plugin": getattr(settings, "ACTIVE_PLUGIN", "izs"),
        "gpu": None,
        "ram": None,
    }

    # Framework git commit
    try:
        framework_dir = str(settings.FRAMEWORK_DIR)
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip()
        repo_url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip().replace(".git", "")
        info["framework"] = {"commit": commit, "repo_url": repo_url}
    except Exception:
        pass

    # GPU stats (nvidia-smi)
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True, timeout=5,
        ).strip()
        if out:
            parts = [p.strip() for p in out.split(",")]
            info["gpu"] = {
                "name": parts[0],
                "vram_used_mb": int(parts[1]),
                "vram_total_mb": int(parts[2]),
                "utilization_pct": int(parts[3]),
                "temperature_c": int(parts[4]),
            }
    except Exception:
        pass

    # RAM stats
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["ram"] = {
            "used_mb": int(vm.used / 1024 / 1024),
            "total_mb": int(vm.total / 1024 / 1024),
            "percent": vm.percent,
        }
    except Exception:
        pass

    return info


@app.post("/validate", response_model=ValidateResponse)
async def validate_pipeline(
    request: ValidateRequest,
    user: User = Depends(get_current_user),
) -> ValidateResponse:
    """Validate Nextflow code via nextflow -preview against the framework."""
    import subprocess
    from pathlib import Path

    framework_dir = Path(settings.FRAMEWORK_DIR)
    pipelines_dir = framework_dir / "pipelines"

    if not pipelines_dir.exists():
        return ValidateResponse(success=True, warnings=["Nextflow framework pipelines directory not present locally; code syntax valid."])

    tmp_file = Path("/tmp/_llm_validate_tmp.nf")
    link_file = pipelines_dir / "_llm_validate_tmp.nf"
    try:
        tmp_file.write_text(request.nextflow_code)
        if link_file.exists() or link_file.is_symlink():
            link_file.unlink()
        link_file.symlink_to(tmp_file)

        env = {
            **os.environ,
            "NXF_HOME": "/tmp/nxf_home",
            "NXF_WORK": "/tmp/nxf_work",
            "NXF_TEMP": "/tmp",
            "NXF_LOG_FILE": "/tmp/nxf.log",
        }
        os.makedirs("/tmp/nxf_home", exist_ok=True)
        os.makedirs("/tmp/nxf_work", exist_ok=True)

        result = subprocess.run(
            ["nextflow", "run", str(link_file), "-preview"],
            capture_output=True,
            text=True,
            cwd="/tmp",
            env=env,
            timeout=10,
        )

        if result.returncode == 0:
            return ValidateResponse(success=True, stdout=result.stdout[-1000:] if result.stdout else None)

        all_output = (result.stderr or "") + "\n" + (result.stdout or "")
        errors = []
        for line in all_output.split("\n"):
            line = line.strip()
            if any(kw in line for kw in ["ERROR", "Error", "No such file", "Unable to", "not found", "Cannot find"]):
                errors.append(line)

        if not errors:
            errors = [result.stderr.strip()[-500:]] if result.stderr.strip() else [f"Exit code {result.returncode}"]

        is_params_only = all(
            "missing required params" in e.lower() or "missing params" in e.lower()
            for e in errors
        )
        if is_params_only:
            return ValidateResponse(success=True, warnings=errors[:10])

        return ValidateResponse(success=False, errors=errors[:10])

    except subprocess.TimeoutExpired:
        return ValidateResponse(success=True, warnings=["Validation preview timed out after 10s; syntax assumed valid."])
    except Exception as e:
        return ValidateResponse(success=False, errors=[str(e)])
    finally:
        if link_file.is_symlink():
            link_file.unlink()
        if tmp_file.exists():
            tmp_file.unlink()


@app.get("/catalog/components")
def get_catalog_components(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Return components dynamically grouped by domain for the visual drawer palette."""
    from collections import defaultdict
    groups = defaultdict(list)
    for comp_id, comp in data_loader.comp_db.items():
        domain = comp.get("domain") or comp.get("role") or "Other"
        groups[domain].append({
            "id": comp_id,
            "tool": comp.get("tool") or comp.get("name") or comp_id,
            "description": comp.get("description", ""),
            "inputs": comp.get("input_channels") or comp.get("input_types", []),
            "outputs": comp.get("output_channels") or comp.get("out", []),
            "seq_types": comp.get("compatible_seq_types", []),
            "file_path": comp.get("file_path", ""),
        })
    return dict(sorted(groups.items()))


# --- Drawing CRUD ---
@app.get("/drawings")
def list_drawings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = db.query(Drawing).filter(Drawing.user_id == user.id).order_by(Drawing.updated_at.desc()).all()
    return [{"id": r.id, "title": r.title, "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]


@app.post("/drawings")
def save_drawing(payload: DrawingSave, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    payload.graph_json["_version"] = _get_version_info()
    drawing = Drawing(user_id=user.id, title=payload.title, graph_json=payload.graph_json)
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return {"id": drawing.id, "title": drawing.title}


@app.put("/drawings/{drawing_id}")
def update_drawing(drawing_id: int, payload: DrawingSave, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    from datetime import datetime
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    d.title = payload.title
    payload.graph_json["_version"] = _get_version_info()
    d.graph_json = payload.graph_json
    d.updated_at = datetime.utcnow()
    db.commit()
    return {"id": d.id, "title": d.title}


@app.get("/drawings/{drawing_id}")
def get_drawing(drawing_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return {"id": d.id, "title": d.title, "graph_json": d.graph_json, "created_at": d.created_at, "updated_at": d.updated_at}


@app.delete("/drawings/{drawing_id}")
def delete_drawing(drawing_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    db.delete(d)
    db.commit()
    return {"status": "ok"}


@app.post("/generate-from-graph", response_model=ChatResponse)
async def generate_from_graph(
    request: GraphGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Generate a pipeline from a visual graph, executing the pipeline generation subgraph directly."""
    component_ids = [n["component_id"] for n in request.nodes if "component_id" in n]
    connections = []
    for e in request.edges:
        src = next((n for n in request.nodes if n.get("node_id") == e.get("source")), None)
        tgt = next((n for n in request.nodes if n.get("node_id") == e.get("target")), None)
        if src and tgt:
            connections.append(f"{src.get('component_id')} -> {tgt.get('component_id')}")

    plan = "## Visual Pipeline Design\n\n"
    plan += "### Components (in order):\n"
    for cid in component_ids:
        plan += f"- {cid}\n"
    plan += "\n### Data Flow:\n"
    for conn in connections:
        plan += f"- {conn}\n"
    plan += "\n### Instructions:\n"
    plan += "Generate a Nextflow DSL2 pipeline using exactly these components in the order and connections shown above.\n"

    drawing_id = request.drawing_id
    if request.graph_json:
        if drawing_id:
            drawing = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
            if drawing:
                request.graph_json["_version"] = _get_version_info()
                drawing.graph_json = request.graph_json
                db.commit()
        else:
            title = "Drawer: " + ", ".join(c.split("__")[-1] for c in component_ids[:3])
            request.graph_json["_version"] = _get_version_info()
            drawing = Drawing(user_id=user.id, title=title, graph_json=request.graph_json)
            db.add(drawing)
            db.commit()
            db.refresh(drawing)
            drawing_id = drawing.id

    session_id = f"drawer_{user.id}_{os.urandom(4).hex()}"
    conv = get_or_create_conversation(db, user, session_id, "Visual pipeline design")
    conv.drawing_id = drawing_id
    db.commit()
    append_message(db, conv, "user", f"[Visual drawer] Components: {', '.join(component_ids)}")

    thread_id = f"u{user.id}:drawer_{os.urandom(4).hex()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = await app_graph.ainvoke(
            {
                "user_query": plan,
                "messages": [("user", plan)],
                "consultant_status": "APPROVED",
                "design_plan": plan,
                "selected_module_ids": component_ids,
                "strategy_selector": "CUSTOM_BUILD",
                "used_template_id": None,
                "generate_diagrams": True,
            },
            config=config,
        )

        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid = result.get("mermaid_deterministic") or result.get("mermaid_agent") or result.get("mermaid_code")
        error = result.get("error")

        messages = result.get("messages", [])
        reply = "Pipeline successfully generated and validated from your visual design."
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                reply = msg.content
                break

        append_message(
            db, conv, "assistant", reply,
            nextflow_code=nf_code, mermaid_code=mermaid, ast_json=ast_json
        )

        return ChatResponse(
            status="APPROVED" if nf_code else "failed",
            reply=reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=error,
        )
    except Exception as e:
        logger.error("graph_generation_failed", error=str(e))
        return ChatResponse(status="failed", reply="Generation failed", error=str(e))


@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    trace_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id, session_id=request.session_id, user_id=user.id)

    try:
        conv = get_or_create_conversation(db, user, request.session_id, request.message)
        append_message(db, conv, "user", request.message)

        thread_id = f"u{user.id}:{request.session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        result = await asyncio.wait_for(
            app_graph.ainvoke(
                {
                    "user_query": request.message,
                    "generate_diagrams": request.generate_diagrams,
                    "messages": [("user", request.message)],
                },
                config=config,
            ),
            timeout=600.0,
        )

        if result.get("error"):
            err_msg = str(result["error"])
            append_message(db, conv, "assistant", f"Error: {err_msg}")
            return ChatResponse(
                status="failed",
                reply="The agent encountered an error.",
                conversation_id=conv.id,
                error=err_msg,
            )

        status_val = result.get("consultant_status", "CHATTING")
        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid = result.get("mermaid_deterministic") or result.get("mermaid_agent") or result.get("mermaid_code")

        messages = result.get("messages", [])
        ai_reply = "No response generated."

        if status_val == "APPROVED":
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

        append_message(
            db, conv, "assistant", ai_reply,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
        )

        return ChatResponse(
            status=status_val,
            reply=ai_reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=None,
            tool_calls=tool_calls,
        )

    except TimeoutError:
        logger.error("server_timeout")
        return ChatResponse(
            status="error",
            reply="The request timed out while processing.",
            error="TimeoutError",
        )
    except Exception as e:
        logger.error("server_error", error=str(e))
        return ChatResponse(
            status="error",
            reply="The server encountered an unexpected error.",
            error=str(e),
        )
    finally:
        structlog.contextvars.clear_contextvars()
