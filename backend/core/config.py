import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(_BASE_DIR), ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Base Paths
    BASE_DIR: str = _BASE_DIR

    # Framework path (supports NGSMANAGER_DIR or NF_FRAMEWORK_DIR)
    FRAMEWORK_DIR: Path = Field(
        default_factory=lambda: Path(
            os.getenv("NGSMANAGER_DIR")
            or os.getenv("NF_FRAMEWORK_DIR")
            or os.path.join(_BASE_DIR, "..", "cohesive-ngsmanager")
            or "/ngsmanager"
        ).resolve()
    )

    # Active Plugin
    ACTIVE_PLUGIN: str = Field(default="izs")

    # Files (Resolved dynamically via core/plugin_loader.py)
    FAISS_INDEX_PATH: str | None = None
    CHROMA_INDEX_PATH: str | None = None
    CODE_STORE: str | None = None
    CATALOG_COMPONENTS: str | None = None
    CATALOG_TEMPLATES: str | None = None
    CATALOG_RESOURCES: str | None = None

    # Model Config (Defaults to local vLLM / Ollama endpoint)
    VECTOR_DB_TYPE: str = Field(default="faiss")
    EMBEDDING_MODEL: str | None = Field(default=None)
    LLM_PROVIDER: str = Field(default="local")
    LLM_MODEL: str = Field(default="labs-devstral-small-2512")
    LOCAL_LLM_URL: str = Field(default="http://localhost:8000/v1")

    # Judge Config
    JUDGE_PROVIDER: str | None = Field(default=None)
    JUDGE_MODEL: str | None = Field(default=None)
    JUDGE_BASE_URL: str | None = Field(default=None)

    # RAG Retrieval Tuning
    RAG_MAX_KEYWORD_COMPONENTS: int = 15
    RAG_MAX_KEYWORD_TEMPLATES: int = 2
    RAG_KEYWORD_TEMPLATE_MIN_SCORE: int = 5
    RAG_KEYWORD_COMPONENT_THRESHOLD: float = 0.20
    RAG_FAISS_K: int = 20
    RAG_FAISS_MAX_L2_DISTANCE: float = 2.5
    RAG_FAISS_RELATIVE_MARGIN: float = 0.35
    RAG_MAX_HELPER_FUNCTIONS: int = 5

    # Templates to exclude from RAG
    RAG_EXCLUDED_TEMPLATES: set[str] = Field(default_factory=set)

    # Graph & Agent Iteration Limits
    MAX_TOOL_ITERATIONS: int = 16
    MAX_TOOL_ITERATIONS_APPROVAL: int = 2
    MAX_ARCHITECT_TOOL_ITERATIONS: int = 8
    MAX_ARCHITECT_TOOL_ITERATIONS_CUSTOM_BUILD: int = 25
    MAX_REPAIR_RETRIES: int = 3
    MAX_DIAGRAM_RETRIES: int = 3

    # Memory & Context Windows
    MEMORY_KEEP_LAST_N: int = 40
    MEMORY_MAX_TOOL_FACTS: int = 10
    CONTEXT_WINDOW_EXTRACT: int = 40
    CONTEXT_WINDOW_REASON: int = 10
    CONTEXT_WINDOW_REPAIR: int = 30

    # Tool Result & Code Truncation
    MAX_CODE_DISPLAY_LENGTH: int = 3000
    MAX_TOOL_RESULT_PREVIEW: int = 500
    MAX_SEARCH_RESULTS: int = 15

    # Search & Discovery
    SEARCH_SCAN_LIMIT: int = 10000
    DESCRIPTION_TRUNCATE_TMPL: int = 150
    DESCRIPTION_TRUNCATE_COMP: int = 120


settings = Settings()
