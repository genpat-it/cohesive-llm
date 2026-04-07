#!/usr/bin/env python3
"""
FAISS Index Rebuilder

Rebuilds the FAISS vector index from the catalog JSON files.
Run this after updating the catalog (e.g., after generate_catalog.py).

Usage:
    python rebuild_faiss_index.py
"""

import json
from pathlib import Path
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

# Paths
DATA_DIR = Path(__file__).parent / "data"
CATALOG_DIR = DATA_DIR / "catalog"
FAISS_INDEX_PATH = DATA_DIR / "faiss_index"

# Embedding model (same as used in app/core/config.py)
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


def load_catalog() -> list[dict]:
    """Load all catalog entries from JSON files."""
    entries = []

    # Load components (steps)
    components_file = CATALOG_DIR / "catalog_part1_components.json"
    if components_file.exists():
        data = json.loads(components_file.read_text())
        for comp in data.get("components", []):
            # Create searchable text
            text = f"""
COMPONENT: {comp['id']}
TOOL: {comp.get('tool', '')}
DOMAIN: {comp.get('domain', '')}
DESCRIPTION: {comp.get('description', '')}
KEYWORDS: {', '.join(comp.get('keywords', []))}
USE CASES: {', '.join(comp.get('use_cases', []))}
INPUTS: {', '.join(comp.get('input_channels', []))}
OUTPUTS: {', '.join(comp.get('output_channels', []))}
"""
            entries.append({
                "id": comp['id'],
                "type": "component",
                "text": text.strip(),
                "metadata": comp
            })

    # Load templates (modules)
    templates_file = CATALOG_DIR / "catalog_part2_templates.json"
    if templates_file.exists():
        data = json.loads(templates_file.read_text())
        for tmpl in data.get("templates", []):
            text = f"""
TEMPLATE: {tmpl['id']}
DESCRIPTION: {tmpl.get('description', '')}
KEYWORDS: {', '.join(tmpl.get('keywords', []))}
STEPS USED: {', '.join(tmpl.get('steps_used', []))}
INPUTS: {', '.join(tmpl.get('input_channels', []))}
OUTPUTS: {', '.join(tmpl.get('output_channels', []))}
"""
            entries.append({
                "id": tmpl['id'],
                "type": "template",
                "text": text.strip(),
                "metadata": tmpl
            })

    # Load helper functions
    resources_file = CATALOG_DIR / "catalog_part3_resources.json"
    if resources_file.exists():
        data = json.loads(resources_file.read_text())
        for helper in data.get("helper_functions", []):
            text = f"""
FUNCTION: {helper['name']}
FILE: {helper.get('file', '')}
DESCRIPTION: {helper.get('description', '')}
"""
            entries.append({
                "id": helper['name'],
                "type": "function",
                "text": text.strip(),
                "metadata": helper
            })

    return entries


def build_faiss_index(entries: list[dict]) -> FAISS:
    """Build FAISS index from catalog entries."""
    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"trust_remote_code": True}
    )

    print(f"Creating embeddings for {len(entries)} entries...")

    texts = [entry["text"] for entry in entries]
    metadatas = [{"id": entry["id"], "type": entry["type"]} for entry in entries]

    # Create FAISS index
    vector_store = FAISS.from_texts(
        texts=texts,
        embedding=embeddings,
        metadatas=metadatas
    )

    return vector_store


def main():
    print("=" * 60)
    print("FAISS INDEX REBUILDER")
    print("=" * 60)

    # Load catalog
    print("\nLoading catalog...")
    entries = load_catalog()
    print(f"Loaded {len(entries)} entries:")
    by_type = {}
    for e in entries:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  - {t}: {count}")

    # Build index
    print("\nBuilding FAISS index...")
    vector_store = build_faiss_index(entries)

    # Save index
    print(f"\nSaving to: {FAISS_INDEX_PATH}")
    FAISS_INDEX_PATH.mkdir(parents=True, exist_ok=True)
    vector_store.save_local(str(FAISS_INDEX_PATH))

    print("\n" + "=" * 60)
    print("DONE! Restart the server to load the new index.")
    print("=" * 60)


if __name__ == "__main__":
    main()
