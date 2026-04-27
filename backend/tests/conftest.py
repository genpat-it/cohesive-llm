"""
tests/conftest.py
Session-scoped fixtures for the IZS test suite.

Performs essential preflight checks before running tests:
  - MISTRAL_API_KEY is set (required for the agent)
  - GROQ_API_KEY is set (optional for the judge)
  - FAISS index exists (required for RAG retrieval)

Provides two complementary fixture paths:
  - api_client: Full API integration (L1–L5 tests via /chat endpoint)
  - store + llm + judge_llm: Isolated direct invocation (bypasses API)
"""
import os
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# 1. Preflight checks
# ──────────────────────────────────────────────────────────────
def _preflight_checks():
    """Fail fast with clear messages instead of cryptic 401s."""
    errors = []

    # FAISS and Keys are validated via app.core.config side-effects mostly,
    # but we do an explicit check here for the test environment.
    from app.core.config import settings
    
    # --- MISTRAL_API_KEY (required — powers the agent) ---
    if not os.environ.get("MISTRAL_API_KEY"):
        errors.append(
            "MISTRAL_API_KEY is not set.\n"
            "  → Add it to .env or export it: export MISTRAL_API_KEY=your_key"
        )

    # --- Judge LLM (JUDGE_BASE_URL for local, or GROQ_API_KEY for cloud) ---
    if not os.environ.get("JUDGE_BASE_URL") and not os.environ.get("GROQ_API_KEY"):
        errors.append(
            "Neither JUDGE_BASE_URL nor GROQ_API_KEY is set.\n"
            "  → Set JUDGE_BASE_URL=http://localhost:8001/v1 for local Qwen judge\n"
            "  → Or set GROQ_API_KEY for cloud Groq judge"
        )

    # --- FAISS index (required — RAG retrieval) ---
    faiss_path = Path(settings.FAISS_INDEX_PATH)
    if not faiss_path.exists():
        errors.append(
            f"FAISS index not found at: {faiss_path}\n"
            f"  → Run the indexing script first, or check DATA_DIR"
        )

    if errors:
        print("\n" + "=" * 60)
        print("❌ PREFLIGHT CHECK FAILED — Cannot run tests")
        print("=" * 60)
        for e in errors:
            print(f"\n  • {e}")
        print()
        sys.exit(1)

    print("✅ Preflight checks passed")


_preflight_checks()


# ──────────────────────────────────────────────────────────────
# 2. Now safe to import the app and test utilities
# ──────────────────────────────────────────────────────────────
import pytest
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from app.api import app
from app.core.loader import data_loader
from app.services.llm import get_llm, get_judge_llm
from tests.report import report


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
    print("\n📦 Loading the real vector store and catalog for testing...")
    data_loader.load_all(store=_store)
    print("✅ Database loaded successfully.")
    return _store


@pytest.fixture(scope="session")
def llm():
    """Session-scoped Mistral LLM instance — same factory as production."""
    return get_llm()


@pytest.fixture(scope="session")
def judge_llm():
    """Session-scoped judge LLM, or None if no judge backend configured."""
    if not os.environ.get("JUDGE_BASE_URL") and not os.environ.get("GROQ_API_KEY"):
        return None
    return get_judge_llm(temperature=0.0)


@pytest.fixture(scope="session", autouse=True)
def setup_database(store):
    """Automatically loads the real vector store and catalog for the test session.

    This is autouse — it runs once per session before any test, ensuring
    the store is populated even if no test explicitly requests it.
    """
    print("✅ Database ready for testing.")


# ──────────────────────────────────────────────────────────────
# 4. API integration fixtures (existing)
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_client():
    """In-memory API client with lifespan (loads RAG catalog).

    Automatically logs in as the demo user so all /api/* endpoints
    that require authentication work without manual cookie handling.
    """
    with TestClient(app) as client:
        # Login to get the auth cookie
        login_resp = client.post("/auth/login", json={
            "username": os.getenv("DEMO_USER", "demo"),
            "password": os.getenv("DEMO_PASSWORD", "change_me_please"),
        })
        if login_resp.status_code != 200:
            print(f"⚠️ Login failed: {login_resp.status_code} — API tests will get 401s")
        yield client


@pytest.fixture(scope="session", autouse=True)
def finalize_report(request):
    """Save the markdown report after all tests complete."""
    yield
    report_path = report.save_report()
    print(f"\n📋 Final report saved to: {report_path}")
