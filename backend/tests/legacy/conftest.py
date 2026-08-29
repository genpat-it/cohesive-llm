"""
tests/conftest.py
Session-scoped fixtures for the IZS test suite.

Performs essential preflight checks before running tests:
  - OPENAI_API_KEY is set (required for the agent)
  - JUDGE_BASE_URL is set (required by default for the judge, unless --judge false)
  - FAISS index exists (required for RAG retrieval)

Provides two complementary fixture paths:
  - api_client: Full API integration (L1–L5 tests via /chat endpoint)
  - store + llm + judge_llm: Isolated direct invocation (bypasses API)
"""
import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from core.api import app
from core.loader import data_loader
from core.services.llm import get_llm, get_judge_llm
from tests.report import report


def _parse_bool(val: str) -> bool:
    return str(val).lower() in ("true", "1", "yes", "y", "on")


def pytest_addoption(parser):
    parser.addoption(
        "--judge",
        action="store",
        default="true",
        help="Enable judge scoring (true/false, default true)"
    )


def pytest_configure(config):
    """Run preflight checks after options are parsed."""
    judge_enabled = _parse_bool(config.getoption("--judge"))
    config.judge_enabled = judge_enabled
    
    errors = []
    from core.config import settings

    # --- OPENAI_API_KEY (required — powers the agent) ---
    if not os.environ.get("OPENAI_API_KEY"):
        errors.append(
            "OPENAI_API_KEY is not set.\n"
            "  → Add it to .env or export it: export OPENAI_API_KEY=your_key"
        )

    # --- JUDGE_BASE_URL (required by default) ---
    if not os.environ.get("JUDGE_BASE_URL"):
        if judge_enabled:
            errors.append(
                "JUDGE_BASE_URL is not set.\n"
                "  → LLM judge is required for evaluation. Set it in .env or export it, or use --judge false."
            )
        else:
            print("WARNING: JUDGE_BASE_URL is not set. Judge is disabled (--judge false) so skipping.", file=sys.stderr)

    # --- Vector DB index (required — RAG retrieval) ---
    if settings.VECTOR_DB_TYPE == "chroma":
        chroma_path = Path(settings.CHROMA_INDEX_PATH)
        # Check if plugin overrides the path
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            if hasattr(plugin, "chroma_index_path") and plugin.chroma_index_path:
                chroma_path = Path(plugin.chroma_index_path)
            elif plugin.faiss_index_path:
                chroma_path = Path(str(plugin.faiss_index_path).replace("faiss_index", "chroma_index"))
        except Exception:
            pass
            
        if not chroma_path.exists():
            errors.append(
                f"ChromaDB index not found at: {chroma_path}\n"
                f"  → Run the indexing script first (--vector-db chroma)"
            )
    else:
        faiss_path = Path(settings.FAISS_INDEX_PATH)
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            if plugin.faiss_index_path:
                faiss_path = Path(plugin.faiss_index_path)
        except Exception:
            pass
            
        if not faiss_path.exists():
            errors.append(
                f"FAISS index not found at: {faiss_path}\n"
                f"  → Run the indexing script first (--vector-db faiss)"
            )

    if errors:
        print("\n" + "=" * 60)
        print("--- [TEST] ERROR preflight check failed so we cannot run tests")
        print("=" * 60)
        for e in errors:
            print(f"\n  • {e}")
        print()
        sys.exit(1)
        
    print(f"--- [TEST] preflight checks passed (judge_enabled={judge_enabled})")


# ──────────────────────────────────────────────────────────────
# 2. Now safe to import the app and test utilities
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# 3. Isolated testing fixtures (direct invocation, no API)
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def store():
    """Session-scoped InMemoryStore with the full catalog loaded.

    Mirrors the exact loading path used by the production API lifespan
    (data_loader.load_all), but into a standalone store that tests can
    pass directly to agents, hydrators, and helper functions.
    """
    _store = InMemoryStore()
    print("\n--- [TEST] loading the real vector store and catalog for testing")
    data_loader.load_all(store=_store)
    print("--- [TEST] database loaded successfully")
    return _store


@pytest.fixture(scope="session")
def llm():
    """Session-scoped LLM instance — same factory as production."""
    return get_llm()


@pytest.fixture(scope="session")
def judge_llm(request):
    """Session-scoped judge LLM, or None if judge is disabled or JUDGE_BASE_URL is missing."""
    judge_enabled = getattr(request.config, "judge_enabled", True)
    if not judge_enabled or not os.environ.get("JUDGE_BASE_URL"):
        return None
    return get_judge_llm(temperature=0.0)


@pytest.fixture(scope="session", autouse=True)
def setup_database(store):
    """Automatically loads the real vector store and catalog for the test session.

    This is autouse — it runs once per session before any test, ensuring
    the store is populated even if no test explicitly requests it.
    """
    print("--- [TEST] database ready for testing")


# ──────────────────────────────────────────────────────────────
# 4. API integration fixtures (existing)
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_client():
    """In-memory API client with lifespan (loads RAG catalog)."""
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def finalize_report(request):
    """Save the markdown report after all tests complete."""
    yield
    report_path = report.save_report()
    print(f"\n--- [TEST] final report saved to {report_path}")
