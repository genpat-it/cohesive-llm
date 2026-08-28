from typing import Any

"""
Embedder — Builds FAISS vector index from catalog entries for semantic search.

Takes the catalog JSON files and creates a FAISS index with HuggingFace embeddings,
matching the format expected by core/loader.py.

Usage:
    from ingestion.embedder import build_faiss_index
    build_faiss_index(catalog_dir=Path("plugins/synthetic/catalog"),
                      output_dir=Path("plugins/synthetic/faiss_index"))
"""

import json
from pathlib import Path

# Dynamic registration of "qwen3" as "qwen2" to support Qwen3 embeddings on older transformers versions
try:
    import torch.nn as nn
    import transformers.models.qwen2.modeling_qwen2 as modeling_qwen2
    from transformers import AutoConfig, AutoModel, AutoProcessor, AutoTokenizer
    from transformers.models.qwen2 import Qwen2Config, Qwen2Model, Qwen2Tokenizer, Qwen2TokenizerFast

    class Qwen3Config(Qwen2Config):
        model_type = "qwen3"
    class Qwen3Model(Qwen2Model):
        config_class = Qwen3Config

    AutoConfig.register("qwen3", Qwen3Config)
    AutoModel.register(Qwen3Config, Qwen3Model)
    AutoTokenizer.register(Qwen3Config, Qwen2Tokenizer, Qwen2TokenizerFast)

    original_init = modeling_qwen2.Qwen2Attention.__init__
    def patched_init(self: Any, config: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, config, *args, **kwargs)
        if hasattr(config, "head_dim") and config.head_dim is not None:
            self.head_dim = config.head_dim
            self.hidden_size = self.num_heads * self.head_dim
            self.q_proj = nn.Linear(config.hidden_size, self.num_heads * self.head_dim, bias=config.attention_bias)
            self.k_proj = nn.Linear(config.hidden_size, self.num_key_value_heads * self.head_dim, bias=config.attention_bias)
            self.v_proj = nn.Linear(config.hidden_size, self.num_key_value_heads * self.head_dim, bias=config.attention_bias)
            self.o_proj = nn.Linear(self.num_heads * self.head_dim, config.hidden_size, bias=config.attention_bias)
            if hasattr(self, "rotary_emb"):
                self.rotary_emb = modeling_qwen2.Qwen2RotaryEmbedding(
                    self.head_dim,
                    max_position_embeddings=self.max_position_embeddings,
                    base=self.rope_theta,
                )
    modeling_qwen2.Qwen2Attention.__init__ = patched_init

    original_from_pretrained = AutoProcessor.from_pretrained
    @classmethod
    def patched_from_pretrained(_cls: Any, pretrained_model_name_or_path: Any, **kwargs: Any) -> Any:
        try:
            return original_from_pretrained(pretrained_model_name_or_path, **kwargs)
        except Exception:
            try:
                kwargs["use_fast"] = False
                return AutoTokenizer.from_pretrained(pretrained_model_name_or_path, **kwargs)
            except Exception:
                return None
    AutoProcessor.from_pretrained = patched_from_pretrained
except Exception:
    pass


def build_faiss_index(
    catalog_dir: Path,
    output_dir: Path,
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
) -> dict:
    """Build a FAISS index from catalog JSON files.

    Creates text documents from catalog entries and embeds them for similarity search.

    Args:
        catalog_dir: Path to directory containing components.json and templates.json
        output_dir: Path to write FAISS index files
        embedding_model: HuggingFace model name for embeddings

    Returns:
        Summary dict with entry count
    """
    from langchain_core.documents import Document
    from langchain_community.vectorstores import FAISS
    from langchain_huggingface import HuggingFaceEmbeddings

    output_dir.mkdir(parents=True, exist_ok=True)
    documents = []

    # Load components
    comp_path = catalog_dir / "components.json"
    if comp_path.exists():
        with open(comp_path, encoding="utf-8") as f:
            for comp in (json.load(f).get("components") or []):
                text = _component_to_text(comp)
                doc = Document(
                    page_content=text,
                    metadata={
                        "id": str(comp.get("id") or ""),
                        "type": "component",
                        "tool": str(comp.get("tool") or ""),
                        "domain": str(comp.get("domain") or ""),
                    }
                )
                documents.append(doc)

    # Load templates
    tmpl_path = catalog_dir / "templates.json"
    if tmpl_path.exists():
        with open(tmpl_path, encoding="utf-8") as f:
            for tmpl in (json.load(f).get("templates") or []):
                text = _template_to_text(tmpl)
                doc = Document(
                    page_content=text,
                    metadata={
                        "id": str(tmpl.get("id") or ""),
                        "type": "template",
                    }
                )
                documents.append(doc)

    if not documents:
        print("  [EMBEDDER] No documents to embed!")
        return {"entries": 0}

    print(f"  [EMBEDDER] Embedding {len(documents)} documents with {embedding_model}...")

    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model,
        model_kwargs={'device': 'cpu', 'trust_remote_code': True},
        encode_kwargs={'normalize_embeddings': True, 'batch_size': 4},
    )

    vector_store = FAISS.from_documents(documents, embeddings)
    vector_store.save_local(str(output_dir))

    print(f"  [EMBEDDER] FAISS index saved → {output_dir}")
    return {"entries": len(documents)}


def build_chroma_index(
    catalog_dir: Path,
    output_dir: Path,
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
) -> dict:
    """Build a ChromaDB index from catalog JSON files.

    Creates text documents from catalog entries and embeds them for similarity search.

    Args:
        catalog_dir: Path to directory containing components.json and templates.json
        output_dir: Path to write ChromaDB files
        embedding_model: HuggingFace model name for embeddings

    Returns:
        Summary dict with entry count
    """
    import chromadb
    from langchain_core.documents import Document
    from langchain_community.vectorstores import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    output_dir.mkdir(parents=True, exist_ok=True)
    documents = []

    # Load components
    comp_path = catalog_dir / "components.json"
    if comp_path.exists():
        with open(comp_path, encoding="utf-8") as f:
            for comp in (json.load(f).get("components") or []):
                text = _component_to_text(comp)
                doc = Document(
                    page_content=text,
                    metadata={
                        "id": str(comp.get("id") or ""),
                        "type": "component",
                        "tool": str(comp.get("tool") or ""),
                        "domain": str(comp.get("domain") or ""),
                    }
                )
                documents.append(doc)

    # Load templates
    tmpl_path = catalog_dir / "templates.json"
    if tmpl_path.exists():
        with open(tmpl_path, encoding="utf-8") as f:
            for tmpl in (json.load(f).get("templates") or []):
                text = _template_to_text(tmpl)
                doc = Document(
                    page_content=text,
                    metadata={
                        "id": str(tmpl.get("id") or ""),
                        "type": "template",
                    }
                )
                documents.append(doc)

    if not documents:
        print("  [EMBEDDER] No documents to embed!")
        return {"entries": 0}

    print(f"  [EMBEDDER] Embedding {len(documents)} documents with {embedding_model} into ChromaDB...")

    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model,
        model_kwargs={'device': 'cpu', 'trust_remote_code': True},
        encode_kwargs={'normalize_embeddings': True, 'batch_size': 4},
    )

    # Use PersistentClient explicitly to avoid deprecation warnings and save to disk
    client = chromadb.PersistentClient(path=str(output_dir))

    import chromadb.errors
    # Safely clear existing data instead of aggressively deleting the directory
    try:
        client.delete_collection("izs_catalog")
    except chromadb.errors.InvalidCollectionException:
        pass
    except Exception as e:
        print(f"  [EMBEDDER] Warning: Could not delete collection: {e}")

    _vector_store = Chroma.from_documents(
        documents,
        embeddings,
        client=client,
        collection_name="izs_catalog"
    )

    print(f"  [EMBEDDER] ChromaDB index saved → {output_dir}")
    return {"entries": len(documents)}


def _component_to_text(comp: dict) -> str:
    """Convert a component catalog entry to searchable text."""
    parts = [
        str(comp.get("id") or ""),
        str(comp.get("tool") or ""),
        str(comp.get("domain") or ""),
        str(comp.get("description") or ""),
    ]
    inputs = comp.get("input_channels") or []
    outputs = comp.get("output_channels") or []
    if inputs:
        parts.append(f"inputs: {', '.join(str(i) for i in inputs)}")
    if outputs:
        parts.append(f"outputs: {', '.join(str(o) for o in outputs)}")
    return " | ".join(p for p in parts if p)


def _template_to_text(tmpl: dict) -> str:
    """Convert a template catalog entry to searchable text."""
    parts = [
        str(tmpl.get("id") or ""),
        str(tmpl.get("description") or ""),
    ]
    steps = tmpl.get("components_used") or []
    if steps:
        parts.append(f"components: {', '.join(str(s) for s in steps)}")
    seq_types = tmpl.get("compatible_seq_types") or []
    if seq_types:
        parts.append(f"seq_types: {', '.join(str(s) for s in seq_types)}")
    return " | ".join(p for p in parts if p)


def _pattern_to_text(p: dict) -> str:
    """Convert a pattern entry to searchable text for embedding.

    Deliberately excludes groovy_code — DSL2 operators like .cross() and .multiMap()
    appear verbatim in dozens of patterns and would collapse the embedding space.
    Tags explicitly capture operator names for recall; title/description/use_cases
    capture the semantic intent.
    """
    parts = [
        str(p.get("title") or ""),
        str(p.get("description") or ""),
        " ".join(str(u) for u in (p.get("use_cases") or [])),
        " ".join(str(t) for t in (p.get("tags") or [])),
    ]
    return " | ".join(x for x in parts if x)


def build_patterns_index(
    catalog_dir: Path,
    output_dir: Path,
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
) -> dict:
    """Build a separate FAISS index for design patterns.

    Patterns are embedded into a dedicated sub-index rather than the main
    component/template index. This allows independent L2 threshold tuning
    and prevents pattern docs from contaminating search_components results.

    Args:
        catalog_dir: Path to directory containing patterns.json
        output_dir: Path to write the patterns FAISS index files
        embedding_model: HuggingFace model name for embeddings

    Returns:
        Summary dict with entry count
    """
    from langchain_core.documents import Document
    from langchain_community.vectorstores import FAISS
    from langchain_huggingface import HuggingFaceEmbeddings

    patterns_path = catalog_dir / "patterns.json"
    if not patterns_path.exists():
        print(f"  [EMBEDDER] No patterns.json found at {patterns_path}, skipping.")
        return {"entries": 0}

    with open(patterns_path, encoding="utf-8") as f:
        patterns = json.load(f).get("patterns") or []

    if not patterns:
        print("  [EMBEDDER] patterns.json is empty, skipping.")
        return {"entries": 0}

    documents = []
    for p in patterns:
        text = _pattern_to_text(p)
        if not text.strip():
            continue
        doc = Document(
            page_content=text,
            metadata={
                "id": str(p.get("id") or ""),
                "type": "pattern",
            }
        )
        documents.append(doc)

    if not documents:
        print("  [EMBEDDER] No embeddable pattern documents, skipping.")
        return {"entries": 0}

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [EMBEDDER] Embedding {len(documents)} patterns with {embedding_model}...")

    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model,
        model_kwargs={'device': 'cpu', 'trust_remote_code': True},
        encode_kwargs={'normalize_embeddings': True, 'batch_size': 4},
    )

    vector_store = FAISS.from_documents(documents, embeddings)
    vector_store.save_local(str(output_dir))

    print(f"  [EMBEDDER] Patterns FAISS index saved → {output_dir} ({len(documents)} docs)")
    return {"entries": len(documents)}

