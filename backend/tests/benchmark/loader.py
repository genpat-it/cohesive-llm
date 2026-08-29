"""
tests/benchmark/loader.py
Loads and enriches benchmark dataset files dynamically from the active plugin.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────
# Component ID extraction
# ──────────────────────────────────────────────────────────────

_INCLUDE_RE = re.compile(
    r"include\s*\{\s*([^}]+?)\s*\}\s*from\s*'([^']+)'"
)

def extract_component_ids(nf_code: str) -> list[str]:
    """Parse `include { step_X } from '...'` statements to get component IDs."""
    if not nf_code:
        return []

    step_ids: set[str] = set()
    for sym_list, _path in _INCLUDE_RE.findall(nf_code):
        for sym in sym_list.split(";"):
            sym = sym.strip()
            if sym.startswith("step_"):
                step_ids.add(sym.split()[0])
    return sorted(step_ids)

def enrich_example(ex: dict[str, Any]) -> dict[str, Any]:
    if "chat_messages" not in ex:
        ex["chat_messages"] = [ex["prompt"]]
    if "component_ids" not in ex:
        ex["component_ids"] = extract_component_ids(ex.get("nextflow_code", ""))

    ex["template_ids"] = [] # Template inference is not generically supported
    ex["test_type"] = ex.get("test_type", "code_generation")

    validation = ex.get("validation", {})
    ex["expected_processes"] = validation.get("expected_processes", 0)

    return ex

def enrich_multi_turn_conversation(conv: dict[str, Any]) -> dict[str, Any]:
    conv["test_type"] = "code_generation"
    for i, turn in enumerate(conv.get("turns", [])):
        turn["chat_messages"] = [turn["prompt"]]
        turn["component_ids"] = extract_component_ids(turn.get("nextflow_code", ""))
        turn["test_type"] = "code_generation"
        turn["turn_index"] = i
    return conv

# ──────────────────────────────────────────────────────────────
# Path resolution
# ──────────────────────────────────────────────────────────────

TESTS_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DATA_DIR = TESTS_DIR / "benchmark" / "data"

def _resolve_data_dir() -> Path:
    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        if getattr(plugin, "benchmark_data_path", None) and plugin.benchmark_data_path.exists():
            return plugin.benchmark_data_path
    except Exception:
        pass
    return _DEFAULT_DATA_DIR

DATA_DIR = _resolve_data_dir()

def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Benchmark dataset not found at {path}")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records

def _apply_filters(
    examples: list[dict],
    limit: int | None = None,
    only_ids: set[str] | None = None,
    only_categories: set[str] | None = None,
) -> list[dict]:
    if only_ids:
        examples = [ex for ex in examples if ex["id"] in only_ids]
    if only_categories:
        examples = [ex for ex in examples if ex.get("category", "") in only_categories]
    if limit is not None:
        examples = examples[:limit]
    return examples

# ──────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────

def load_single_turn_examples(
    path: Path | None = None,
    limit: int | None = None,
    only_ids: set[str] | None = None,
    only_categories: set[str] | None = None,
    test_type: str = "code_generation",
    **kwargs  # Accept and ignore legacy args like max_complexity
) -> list[dict[str, Any]]:
    if path is not None:
        examples = _load_jsonl(path)
        source_label = path.name
    elif test_type in ("code_generation", "level_unified"):
        examples = []
        for level_file in sorted(DATA_DIR.glob("level*.jsonl")):
            examples.extend(_load_jsonl(level_file))
        source_label = "all_levels"
    else:
        dataset_path = DATA_DIR / f"{test_type}.jsonl"
        examples = _load_jsonl(dataset_path)
        source_label = dataset_path.name

    examples = _apply_filters(examples, limit=limit, only_ids=only_ids, only_categories=only_categories)
    enriched = [enrich_example(ex) for ex in examples]

    print(f"  📊 Loaded {len(enriched)} single-turn examples from {source_label}")
    return enriched

def load_multi_turn_examples(
    path: Path | None = None,
    limit: int | None = None,
    only_ids: set[str] | None = None,
    only_mod_kinds: set[str] | None = None,
) -> list[dict[str, Any]]:
    dataset_path = path or (DATA_DIR / "raw" / "dataset_modifications_full.jsonl")
    conversations = _load_jsonl(dataset_path)

    if only_ids:
        conversations = [c for c in conversations if c["id"] in only_ids]
    if only_mod_kinds:
        conversations = [c for c in conversations if c.get("modification_kind", "") in only_mod_kinds]
    if limit is not None:
        conversations = conversations[:limit]

    enriched = [enrich_multi_turn_conversation(c) for c in conversations]
    total_turns = sum(len(c.get("turns", [])) for c in enriched)
    print(f"  📊 Loaded {len(enriched)} multi-turn conversations ({total_turns} total turns) from {dataset_path.name}")
    return enriched

def get_dataset_stats(
    single_turn: list[dict] | None = None,
    multi_turn: list[dict] | None = None,
) -> dict:
    stats = {}
    if single_turn:
        categories = {}
        for ex in single_turn:
            cat = ex.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
        stats["single_turn"] = {
            "total": len(single_turn),
            "categories": categories,
            "avg_expected_processes": round(sum(ex.get("expected_processes", 0) for ex in single_turn) / len(single_turn), 1) if single_turn else 0,
        }
    if multi_turn:
        mod_kinds = {}
        total_turns = 0
        for conv in multi_turn:
            kind = conv.get("modification_kind", "unknown")
            mod_kinds[kind] = mod_kinds.get(kind, 0) + 1
            total_turns += len(conv.get("turns", []))
        stats["multi_turn"] = {
            "total_conversations": len(multi_turn),
            "total_turns": total_turns,
            "modification_kinds": mod_kinds,
        }
    return stats
