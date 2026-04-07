import os
from pathlib import Path

class Settings:
    # Base Paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DATA_DIR = os.path.join(BASE_DIR, "data")

    # Framework path (configurable via env var or .env)
    FRAMEWORK_DIR = Path(os.getenv("NGSMANAGER_DIR", os.path.join(BASE_DIR, "..", "cohesive-ngsmanager-cli", "cohesive-ngsmanager"))).resolve()
    
    # Files
    FAISS_INDEX_PATH = os.path.join(DATA_DIR, "faiss_index")
    CODE_STORE = os.path.join(DATA_DIR, "code_store_hollow.jsonl")
    CATALOG_COMPONENTS = os.path.join(DATA_DIR, "catalog/catalog_part1_components.json")
    CATALOG_TEMPLATES = os.path.join(DATA_DIR, "catalog/catalog_part2_templates.json")
    CATALOG_RESOURCES = os.path.join(DATA_DIR, "catalog/catalog_part3_resources.json")
    
    # Model Config
    EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
    LLM_MODEL = "labs-devstral-small-2512"

    # RAG Retrieval Tuning
    RAG_MAX_KEYWORD_COMPONENTS = 8       # Max components from keyword/metadata scan
    RAG_MAX_KEYWORD_TEMPLATES = 2        # Max templates from keyword/metadata scan
    RAG_KEYWORD_TEMPLATE_MIN_SCORE = 5   # Min score for a template to be included
    RAG_KEYWORD_COMPONENT_THRESHOLD = 0.20  # Keep components scoring >= X% of top match
    RAG_FAISS_K = 10                     # Number of FAISS nearest neighbors to fetch
    RAG_FAISS_MAX_L2_DISTANCE = 1.2      # Absolute max L2 distance for FAISS results
    RAG_FAISS_RELATIVE_MARGIN = 0.25     # Max L2 distance above best match
    RAG_MAX_HELPER_FUNCTIONS = 5         # Max helper functions to inject

    # Templates to exclude from RAG (debug/test artifacts)
    RAG_EXCLUDED_TEMPLATES = {
        "module_test_llm",
        "module_variant_lineage_FIXED",
        "module_variant_lineage_MINIMAL",
        "module_variant_lineage_MINIMAL_FIX",
        "module_variant_lineage_ONE_LINE_FIX",
    }

settings = Settings()