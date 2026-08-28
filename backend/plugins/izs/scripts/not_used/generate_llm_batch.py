#!/usr/bin/env python3
"""
tests/benchmark/generate_llm_batch.py
Helper script for the LLM to generate level_unified datasets in batches.

Usage:
  python tests/benchmark/generate_llm_batch.py list --batch-size 5
  python tests/benchmark/generate_llm_batch.py write --file /path/to/my_batch.json
"""
import argparse
import json
import sys
from pathlib import Path

# Fix python path if run directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.benchmark.loader import CATEGORY_COMPLEXITY, LEVEL_FILES

RAW_DATASET = Path(__file__).parent / "data" / "raw" / "dataset_205.jsonl"


def _get_completed_ids() -> set[str]:
    completed = set()
    for lvl_path in LEVEL_FILES.values():
        if lvl_path.exists():
            for line in lvl_path.read_text().splitlines():
                if line.strip():
                    data = json.loads(line)
                    completed.add(data["id"])
    return completed


def list_batch(batch_size: int):
    completed_ids = _get_completed_ids()
    raw_items = []
    if RAW_DATASET.exists():
        for line in RAW_DATASET.read_text().splitlines():
            if line.strip():
                raw_items.append(json.loads(line))
    
    pending = [item for item in raw_items if item["id"] not in completed_ids]
    
    if not pending:
        print("🎉 ALL 205 ITEMS HAVE BEEN GENERATED!")
        return

    batch = pending[:batch_size]
    print(f"--- BATCH OF {len(batch)} PENDING ITEMS ({len(pending)} total remaining) ---")
    
    for item in batch:
        level = CATEGORY_COMPLEXITY.get(item.get("category", ""), 99)
        print(f"\n=======================================================")
        print(f"ID: {item['id']}")
        print(f"Category: {item.get('category')} (Level {level})")
        print(f"Code Preview:\n{item.get('nextflow_code', '')[:200]}...")
        print(f"Expected Processes: {item.get('validation', {}).get('expected_processes')}")
        print(f"=======================================================")
        
    print("\n--- INSTRUCTIONS FOR LLM ---")
    print("For each ID above, create a JSON list of objects containing:")
    print('1. "id": the id string')
    print('2. "chat_messages": array of user prompts (can be multi-turn, e.g. ["Hi", "Wait add X", "I approve the plan, please build the pipeline."])')
    print('3. "consultant_reply": Your natural language recommendation (Turn 1).')
    print('4. "diagram_code": JSON graph string conforming to core/prompts/diagram.md (Turn 2).')
    print("\nWrite the JSON list to a scratch file and call `write --file <path>`.")


def write_batch(file_path: str):
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {path}")
        return
        
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {path}: {e}")
        return
        
    if not isinstance(data, list):
        print("Error: JSON must be a list of objects.")
        return
        
    # Load raw dataset for merging
    raw_dict = {}
    if RAW_DATASET.exists():
        for line in RAW_DATASET.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                raw_dict[item["id"]] = item
                
    success_count = 0
    
    for gen_item in data:
        if "id" not in gen_item:
            print(f"Skipping item missing 'id': {str(gen_item)[:100]}")
            continue
            
        item_id = gen_item["id"]
        
        # Validate fields
        required = ["chat_messages", "consultant_reply", "diagram_code"]
        missing = [f for f in required if f not in gen_item]
        if missing:
            print(f"[{item_id}] Skipping - missing required fields: {missing}")
            continue
            
        if item_id not in raw_dict:
            print(f"[{item_id}] Skipping - ID not found in raw dataset_205.jsonl")
            continue
            
        raw_item = raw_dict[item_id]
        level = CATEGORY_COMPLEXITY.get(raw_item.get("category", ""), 99)
        
        if level not in LEVEL_FILES:
            print(f"[{item_id}] Skipping - Unknown level {level} for category {raw_item.get('category')}")
            continue
            
        target_file = LEVEL_FILES[level]
        
        # Merge
        merged = {
            "id": item_id,
            "category": raw_item.get("category"),
            "test_type": "level_unified",
            "chat_messages": gen_item["chat_messages"],
            "consultant_reply": gen_item["consultant_reply"],
            "diagram_code": gen_item["diagram_code"],
            "nextflow_code": raw_item.get("nextflow_code"),
            "params": raw_item.get("params", {}),
            "validation": raw_item.get("validation", {}),
        }
        
        # Append to file
        with open(target_file, "a") as f:
            f.write(json.dumps(merged) + "\n")
            
        print(f"✅ Appended {item_id} to {target_file.name}")
        success_count += 1
        
    print(f"\nSuccessfully wrote {success_count} items!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--batch-size", type=int, default=5)
    
    write_parser = subparsers.add_parser("write")
    write_parser.add_argument("--file", required=True)
    
    args = parser.parse_args()
    
    if args.command == "list":
        list_batch(args.batch_size)
    elif args.command == "write":
        write_batch(args.file)
