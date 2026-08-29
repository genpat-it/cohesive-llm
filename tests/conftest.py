"""
tests/conftest.py
Session-scoped fixtures for the pairwise evaluation test suite.

Performs preflight checks:
  - OPENAI_API_KEY is set (required for the agent)
  - JUDGE_BASE_URL is set (required for pairwise judge, unless --judge false)
  - Vector DB index exists (required for RAG retrieval)

Provides fixtures:
  - api_client: In-process FastAPI TestClient for full pipeline tests
  - store: InMemoryStore with the full catalog loaded
  - judge_llm: LLM instance for pairwise judging
  - elo_tracker: Session-scoped Glicko-2 rating tracker
  - report: Session-scoped report collector

Subset control via CLI options:
  --first N          Only run the first N examples
  --complexity L     Only run complexity level L (1-5)
  --max-complexity L Only run up to complexity level L
  --category CAT     Only run category CAT (can repeat)
  --ids ID1,ID2,...  Only run specific example IDs
  --mod-kind KIND    Multi-turn: only run modification kind (add/replace/drop/switch_species)
  --judge false      Disable LLM judge (only deterministic checks)
"""
import os
import sys
from pathlib import Path

# ── Load .env into os.environ before ANY preflight check reads it ──────────
# pydantic-settings populates `settings` from .env but does NOT inject values
# back into os.environ. The preflight check reads os.environ directly, so we
# must load the .env file ourselves early here.
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    _load_dotenv(dotenv_path=_env_path, override=False)  # don't clobber shell-set vars
except ImportError:
    pass  # python-dotenv not available; rely on shell environment
# ──────────────────────────────────────────────────────────────────────────

import pytest
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from core.api import app
from core.loader import data_loader
from core.services.llm import get_judge_llm, get_llm
from tests.evaluation.elo import Glicko2Tracker
from tests.report import PairwiseReport

# ──────────────────────────────────────────────────────────────
# CLI options
# ──────────────────────────────────────────────────────────────

def _parse_bool(val: str) -> bool:
    return str(val).lower() in ("true", "1", "yes", "y", "on")


def pytest_addoption(parser):
    parser.addoption(
        "--judge",
        action="store",
        default="true",
        help="Enable judge scoring (true/false, default true)",
    )
    parser.addoption(
        "--first",
        action="store",
        default=None,
        type=int,
        help="Only run the first N examples (e.g., --first 10)",
    )
    parser.addoption(
        "--complexity",
        action="store",
        default=None,
        type=int,
        help="Only run examples at this exact complexity level (1-5)",
    )
    parser.addoption(
        "--max-complexity",
        action="store",
        default=None,
        type=int,
        help="Only run examples up to this complexity level (1-5)",
    )
    parser.addoption(
        "--category",
        action="append",
        default=None,
        help="Only run examples from this category (can repeat, e.g., --category mono-typing --category 3step)",
    )
    parser.addoption(
        "--ids",
        action="store",
        default=None,
        help="Comma-separated list of example IDs to run (e.g., --ids A01_mlst_listeria,B01_spades_listeria)",
    )
    parser.addoption(
        "--mod-kind",
        action="append",
        default=None,
        help="Multi-turn: only run this modification kind (add/replace/drop/switch_species)",
    )
    parser.addoption(
        "--test-type",
        action="store",
        default="level_unified",
        help="Test type dataset to load (e.g., level_unified, consultant, diagram, rejection, recreation)",
    )


# ──────────────────────────────────────────────────────────────
# Subset configuration fixture
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def subset_config(request):
    """Parse CLI options into a subset configuration dict.

    Used by test files to filter which examples to load.
    """
    config = {}

    first = request.config.getoption("--first")
    if first is not None:
        config["limit"] = first

    complexity = request.config.getoption("--complexity")
    if complexity is not None:
        config["min_complexity"] = complexity
        config["max_complexity"] = complexity

    max_complexity = request.config.getoption("--max-complexity")
    if max_complexity is not None:
        config["max_complexity"] = max_complexity

    categories = request.config.getoption("--category")
    if categories:
        config["only_categories"] = set(categories)

    ids_str = request.config.getoption("--ids")
    if ids_str:
        config["only_ids"] = set(ids_str.split(","))

    mod_kinds = request.config.getoption("--mod-kind")
    if mod_kinds:
        config["only_mod_kinds"] = set(mod_kinds)

    test_type = request.config.getoption("--test-type")
    if test_type:
        config["test_type"] = test_type

    return config


# ──────────────────────────────────────────────────────────────
# Preflight checks
# ──────────────────────────────────────────────────────────────

def pytest_configure(config):
    """Run preflight checks after options are parsed."""
    judge_enabled = _parse_bool(config.getoption("--judge"))
    config.judge_enabled = judge_enabled

    # Register custom markers
    config.addinivalue_line("markers", "single_turn: single-turn benchmark tests")
    config.addinivalue_line("markers", "multi_turn: multi-turn benchmark tests")
    config.addinivalue_line("markers", "ab_compare: A/B model comparison tests")
    config.addinivalue_line("markers", "smoke: quick smoke tests (first 5 examples)")

    # Skip infrastructure preflight when running only unit tests — they are
    # pure-logic and don't need an API key, judge endpoint, or FAISS index.
    collected_paths = config.args or []
    only_unit = bool(collected_paths) and all("tests/unit" in str(p) for p in collected_paths)
    if only_unit:
        print("--- [TEST] unit-only run, skipping infrastructure preflight checks")
        return

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
                "  → LLM judge is required for pairwise evaluation. "
                "Set it in .env or use --judge false."
            )
        else:
            print(
                "WARNING: JUDGE_BASE_URL is not set. "
                "Judge is disabled (--judge false) so skipping.",
                file=sys.stderr,
            )

    # --- Vector DB index (required — RAG retrieval) ---
    if settings.VECTOR_DB_TYPE == "chroma":
        chroma_path = Path(settings.CHROMA_INDEX_PATH) if settings.CHROMA_INDEX_PATH else None
        try:
            from core.plugin_loader import get_active_plugin

            plugin = get_active_plugin()
            if hasattr(plugin, "chroma_index_path") and plugin.chroma_index_path:
                chroma_path = Path(plugin.chroma_index_path)
            elif plugin.faiss_index_path:
                chroma_path = Path(
                    str(plugin.faiss_index_path).replace("faiss_index", "chroma_index")
                )
        except Exception:
            pass

        if chroma_path is None or not chroma_path.exists():
            errors.append(
                f"ChromaDB index not found at: {chroma_path}\n"
                f"  → Run the indexing script first (--vector-db chroma)"
            )
    else:
        faiss_path = None
        try:
            from core.plugin_loader import get_active_plugin

            plugin = get_active_plugin()
            # Only override if the plugin's faiss_index_path actually exists on disk.
            # plugin_loader sets the path from yaml without checking existence.
            if plugin.faiss_index_path and Path(plugin.faiss_index_path).exists():
                faiss_path = Path(plugin.faiss_index_path)
        except Exception:
            pass

        # Fall back to settings value if plugin didn't provide a path
        if faiss_path is None and settings.FAISS_INDEX_PATH:
            faiss_path = Path(settings.FAISS_INDEX_PATH)

        if faiss_path is None or not faiss_path.exists():
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

    # Print active subset filters
    subset_info = []
    if config.getoption("--first"):
        subset_info.append(f"first={config.getoption('--first')}")
    if config.getoption("--complexity"):
        subset_info.append(f"complexity={config.getoption('--complexity')}")
    if config.getoption("--max-complexity"):
        subset_info.append(f"max_complexity={config.getoption('--max-complexity')}")
    if config.getoption("--category"):
        subset_info.append(f"categories={config.getoption('--category')}")
    if config.getoption("--ids"):
        n_ids = len(config.getoption("--ids").split(","))
        subset_info.append(f"ids={n_ids}")
    if config.getoption("--mod-kind"):
        subset_info.append(f"mod_kinds={config.getoption('--mod-kind')}")

    subset_str = f" | subset: {', '.join(subset_info)}" if subset_info else ""
    print(f"--- [TEST] preflight checks passed (judge_enabled={judge_enabled}{subset_str})")


# ──────────────────────────────────────────────────────────────
# Core fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def store(request):
    """Session-scoped InMemoryStore with the full catalog loaded.

    Mirrors the exact loading path used by the production API lifespan
    (data_loader.load_all), but into a standalone store that tests can
    pass directly to agents, hydrators, and helper functions.

    When running only ``tests/unit/`` tests (pure logic, no RAG needed),
    returns an empty store without loading the vector database.
    """
    _store = InMemoryStore()
    # If every collected item is in tests/unit/, skip the expensive DB load.
    items = request.session.items if hasattr(request, "session") else []
    all_unit = bool(items) and all("tests/unit" in str(getattr(i, "fspath", "")) for i in items)
    if all_unit:
        print("\n--- [TEST] unit-only run, skipping catalog/vector store load")
        return _store
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
    """Session-scoped judge LLM for pairwise evaluation.

    Returns None if judge is disabled or JUDGE_BASE_URL is missing.
    """
    judge_enabled = getattr(request.config, "judge_enabled", True)
    if not judge_enabled or not os.environ.get("JUDGE_BASE_URL"):
        return None
    return get_judge_llm(temperature=0.0)


@pytest.fixture(scope="session", autouse=True)
def setup_database(store):
    """Autouse: ensures the store is populated before any integration test runs."""
    print("--- [TEST] database ready for testing")


# ──────────────────────────────────────────────────────────────
# API integration fixture
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_client():
    """In-memory API client with lifespan (loads RAG catalog)."""
    with TestClient(app) as client:
        yield client


# ──────────────────────────────────────────────────────────────
# Pairwise evaluation fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def elo_tracker():
    """Session-scoped Glicko-2 rating tracker.

    Shared across all tests so ratings accumulate over the full run.
    """
    return Glicko2Tracker()


@pytest.fixture(scope="session")
def report():
    """Session-scoped pairwise report collector."""
    return PairwiseReport()


@pytest.fixture(scope="session", autouse=True)
def finalize_report(request, report, elo_tracker):
    """Save the final report (with Glicko-2 ratings) after all tests complete."""
    yield
    try:
        report_path = report.save(elo_tracker)
        print(f"\n--- [TEST] final report saved to {report_path}")
    except Exception as e:
        print(f"\n--- [TEST] report save failed: {e}")
