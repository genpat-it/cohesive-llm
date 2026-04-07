#!/usr/bin/env python3
"""
FRAMEWORK SYNC SCRIPT

Sincronizza tutto il sistema LLM con il framework cohesive-ngsmanager.
Esegui questo script ogni volta che il framework viene aggiornato.

Usage:
    python sync_framework.py

    # O specificando il path:
    python sync_framework.py --ngsmanager-dir /path/to/cohesive-ngsmanager
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# Default paths
DEFAULT_NGSMANAGER = Path(os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager")).resolve()
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
CATALOG_DIR = DATA_DIR / "catalog"
FAISS_DIR = DATA_DIR / "faiss_index"


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_step(step: int, total: int, description: str):
    print(f"[{step}/{total}] {description}...")


# =============================================================================
# STEP 1: Generate Code Store JSONL
# =============================================================================
def generate_code_store(ngsmanager_dir: Path) -> int:
    """Generate code_store_hollow.jsonl from framework .nf files"""

    entries = []

    # Collect modules
    modules_dir = ngsmanager_dir / "modules"
    for nf_file in sorted(modules_dir.glob("module_*.nf")):
        content = nf_file.read_text()
        entries.append({
            "id": nf_file.stem,
            "content": content,
            "source_path": f"modules/{nf_file.name}"
        })

    # Collect steps (only the workflow part, not full process definitions)
    steps_dir = ngsmanager_dir / "steps"
    for nf_file in sorted(steps_dir.glob("step_*.nf")):
        content = nf_file.read_text()
        entries.append({
            "id": nf_file.stem,
            "content": content,
            "source_path": f"steps/{nf_file.name}"
        })

    # Write JSONL
    output_path = DATA_DIR / "code_store_hollow.jsonl"
    with open(output_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    print(f"  Written: {output_path}")
    print(f"  Entries: {len(entries)} (modules + steps)")

    return len(entries)


# =============================================================================
# STEP 2: Generate Catalog JSONs (from generate_catalog.py logic)
# =============================================================================
DOMAIN_MAP = {
    "0SQ": "Quality Control",
    "1PP": "Preprocessing",
    "2AS": "Assembly & Mapping",
    "2MG": "Metagenomics",
    "3TX": "Taxonomy",
    "4AN": "Annotation & AMR",
    "4TY": "Typing",
}

TOOL_PATTERN = re.compile(r'step_\d[A-Z]{2}_\w+__(\w+)')


def parse_step_file(nf_file: Path, ngsmanager_dir: Path) -> dict:
    """Parse a step .nf file and extract metadata"""
    content = nf_file.read_text()
    step_id = nf_file.stem

    # Extract domain
    domain = ""
    for prefix, dom in DOMAIN_MAP.items():
        if prefix in step_id:
            domain = dom
            break

    # Extract tool name
    tool_match = TOOL_PATTERN.match(step_id)
    tool = tool_match.group(1) if tool_match else ""

    # Extract container
    container = ""
    container_match = re.search(r"container\s+['\"]([^'\"]+)['\"]", content)
    if container_match:
        container = container_match.group(1)

    # Extract outputs
    outputs = []
    emit_match = re.search(r'emit:\s*\n((?:\s+\w+.*\n)+)', content)
    if emit_match:
        for line in emit_match.group(1).split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                if '=' in line:
                    outputs.append(line.split('=')[0].strip())
                else:
                    outputs.append(line)

    # Generate keywords
    keywords = [tool.lower()] if tool else []

    return {
        "id": step_id,
        "file_path": str(nf_file.relative_to(ngsmanager_dir)),
        "domain": domain,
        "tool": tool,
        "description": f"{tool.capitalize()} step for {domain.lower()}." if tool else "",
        "keywords": keywords,
        "container": container,
        "output_channels": outputs,
    }


def parse_module_file(nf_file: Path, ngsmanager_dir: Path) -> dict:
    """Parse a module .nf file and extract metadata"""
    content = nf_file.read_text()
    module_id = nf_file.stem

    # Find steps used
    steps_used = re.findall(r"include\s*\{\s*(step_\w+)\s*\}", content)

    # Generate keywords
    keywords = [module_id.replace('module_', '').replace('_', ' ')]
    for step in steps_used:
        tool_match = TOOL_PATTERN.match(step)
        if tool_match:
            keywords.append(tool_match.group(1).lower())

    return {
        "id": module_id,
        "file_path": str(nf_file.relative_to(ngsmanager_dir)),
        "description": f"Pipeline module using: {', '.join(steps_used) if steps_used else 'custom logic'}.",
        "keywords": list(set(keywords)),
        "steps_used": steps_used,
    }


def generate_catalog(ngsmanager_dir: Path) -> tuple[int, int]:
    """Generate catalog JSON files"""

    CATALOG_DIR.mkdir(parents=True, exist_ok=True)

    # Parse steps
    steps = []
    steps_dir = ngsmanager_dir / "steps"
    for nf_file in sorted(steps_dir.glob("step_*.nf")):
        try:
            steps.append(parse_step_file(nf_file, ngsmanager_dir))
        except Exception as e:
            print(f"  Warning: Error parsing {nf_file.name}: {e}")

    # Parse modules
    modules = []
    modules_dir = ngsmanager_dir / "modules"
    for nf_file in sorted(modules_dir.glob("module_*.nf")):
        try:
            modules.append(parse_module_file(nf_file, ngsmanager_dir))
        except Exception as e:
            print(f"  Warning: Error parsing {nf_file.name}: {e}")

    # Write catalog files
    components_catalog = {
        "metadata": {
            "version": "2.0",
            "generated_from": str(ngsmanager_dir),
            "generated_at": datetime.now().isoformat(),
            "total_steps": len(steps),
        },
        "components": steps,
    }
    (CATALOG_DIR / "catalog_part1_components.json").write_text(json.dumps(components_catalog, indent=2))

    modules_catalog = {
        "metadata": {"version": "2.0"},
        "templates": modules,
    }
    (CATALOG_DIR / "catalog_part2_templates.json").write_text(json.dumps(modules_catalog, indent=2))

    print(f"  Written: catalog_part1_components.json ({len(steps)} steps)")
    print(f"  Written: catalog_part2_templates.json ({len(modules)} modules)")

    return len(steps), len(modules)


# =============================================================================
# STEP 3: Generate Tool Whitelist
# =============================================================================
def generate_whitelist(ngsmanager_dir: Path) -> None:
    """Generate tool_whitelist.json for prompt injection"""

    components = json.loads((CATALOG_DIR / "catalog_part1_components.json").read_text())
    templates = json.loads((CATALOG_DIR / "catalog_part2_templates.json").read_text())

    whitelist = {
        "steps": [
            {
                "id": s["id"],
                "tool": s.get("tool", ""),
                "domain": s.get("domain", ""),
                "inputs": s.get("input_channels", []),
                "outputs": s.get("output_channels", []),
            }
            for s in components["components"]
        ],
        "modules": [
            {
                "id": m["id"],
                "steps_used": m.get("steps_used", []),
            }
            for m in templates["templates"]
        ],
        "not_available": [
            {"tool": "BWA", "alternative": "bowtie or minimap2"},
            {"tool": "STAR", "alternative": None},
            {"tool": "HISAT2", "alternative": None},
            {"tool": "Canu", "alternative": "flye for long reads"},
            {"tool": "Hifiasm", "alternative": None},
            {"tool": "Salmon", "alternative": None},
            {"tool": "Kallisto", "alternative": None},
            {"tool": "GATK", "alternative": None},
            {"tool": "FreeBayes", "alternative": None},
        ],
    }

    (CATALOG_DIR / "tool_whitelist.json").write_text(json.dumps(whitelist, indent=2))
    print(f"  Written: tool_whitelist.json")


# =============================================================================
# STEP 4: Rebuild FAISS Index
# =============================================================================
def rebuild_faiss_index() -> int:
    """Rebuild FAISS vector index from catalog"""

    try:
        from langchain_community.vectorstores import FAISS
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError:
        print("  ERROR: Missing dependencies. Run: pip install langchain-community langchain-huggingface")
        return 0

    # Load catalog entries
    entries = []

    components = json.loads((CATALOG_DIR / "catalog_part1_components.json").read_text())
    for comp in components.get("components", []):
        text = f"""
COMPONENT: {comp['id']}
TOOL: {comp.get('tool', '')}
DOMAIN: {comp.get('domain', '')}
DESCRIPTION: {comp.get('description', '')}
KEYWORDS: {', '.join(comp.get('keywords', []))}
OUTPUTS: {', '.join(comp.get('output_channels', []))}
"""
        entries.append({"id": comp['id'], "type": "component", "text": text.strip()})

    templates = json.loads((CATALOG_DIR / "catalog_part2_templates.json").read_text())
    for tmpl in templates.get("templates", []):
        text = f"""
TEMPLATE: {tmpl['id']}
DESCRIPTION: {tmpl.get('description', '')}
KEYWORDS: {', '.join(tmpl.get('keywords', []))}
STEPS USED: {', '.join(tmpl.get('steps_used', []))}
"""
        entries.append({"id": tmpl['id'], "type": "template", "text": text.strip()})

    print(f"  Loading embedding model...")
    embeddings = HuggingFaceEmbeddings(
        model_name="Qwen/Qwen3-Embedding-0.6B",
        model_kwargs={"trust_remote_code": True}
    )

    print(f"  Creating embeddings for {len(entries)} entries...")
    texts = [e["text"] for e in entries]
    metadatas = [{"id": e["id"], "type": e["type"]} for e in entries]

    vector_store = FAISS.from_texts(texts=texts, embedding=embeddings, metadatas=metadatas)

    FAISS_DIR.mkdir(parents=True, exist_ok=True)
    vector_store.save_local(str(FAISS_DIR))

    print(f"  Written: faiss_index/")
    return len(entries)


# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Sync LLM system with ngsmanager framework")
    parser.add_argument(
        "--ngsmanager-dir",
        type=Path,
        default=DEFAULT_NGSMANAGER,
        help="Path to cohesive-ngsmanager directory"
    )
    parser.add_argument(
        "--skip-faiss",
        action="store_true",
        help="Skip FAISS index rebuild (faster, for testing)"
    )
    args = parser.parse_args()

    if not args.ngsmanager_dir.exists():
        print(f"ERROR: Framework not found at {args.ngsmanager_dir}")
        sys.exit(1)

    print_header("FRAMEWORK SYNC")
    print(f"Source: {args.ngsmanager_dir}")
    print(f"Target: {PROJECT_ROOT}")

    total_steps = 4 if not args.skip_faiss else 3

    # Step 1: Code Store
    print_step(1, total_steps, "Generating code_store_hollow.jsonl")
    code_entries = generate_code_store(args.ngsmanager_dir)

    # Step 2: Catalog
    print_step(2, total_steps, "Generating catalog JSON files")
    n_steps, n_modules = generate_catalog(args.ngsmanager_dir)

    # Step 3: Whitelist
    print_step(3, total_steps, "Generating TOOL_WHITELIST.md")
    generate_whitelist(args.ngsmanager_dir)

    # Step 4: FAISS
    if not args.skip_faiss:
        print_step(4, total_steps, "Rebuilding FAISS index")
        faiss_entries = rebuild_faiss_index()
    else:
        print(f"\n[SKIPPED] FAISS index rebuild")
        faiss_entries = 0

    # Summary
    print_header("SYNC COMPLETE")
    print(f"  Steps:     {n_steps}")
    print(f"  Modules:   {n_modules}")
    print(f"  Code entries: {code_entries}")
    if faiss_entries:
        print(f"  FAISS entries: {faiss_entries}")
    print(f"\n  ⚠️  Restart the server to apply changes!")
    print(f"      pkill -f uvicorn && uvicorn app.main:app --host 0.0.0.0 --port 8080")


if __name__ == "__main__":
    main()
