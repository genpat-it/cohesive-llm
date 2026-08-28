#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

# Fix python path if run directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

RAW_DATASET = Path(__file__).parent / "data" / "raw" / "dataset_modifications_full.jsonl"
OUT_DATASET = Path(__file__).parent / "data" / "modification.jsonl"

def _get_completed_ids() -> set[str]:
    completed = set()
    if OUT_DATASET.exists():
        for line in OUT_DATASET.read_text().splitlines():
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
        print("🎉 ALL 160 MODIFICATION ITEMS HAVE BEEN GENERATED!")
        return

    batch = pending[:batch_size]
    print(f"--- BATCH OF {len(batch)} PENDING MODIFICATIONS ({len(pending)} total remaining) ---")
    
    for item in batch:
        print(f"\n=======================================================")
        print(f"ID: {item['id']}")
        print(f"Kind: {item.get('modification_kind')}")
        print(f"Notes: {item.get('notes')}")
        print(f"Turn 1 Prompt: {item['turns'][0]['prompt']}")
        print(f"Turn 2 Prompt (Modification): {item['turns'][1]['prompt']}")
        print(f"=======================================================")
        
    print("\n--- INSTRUCTIONS FOR LLM ---")
    print("For each ID above, create a JSON list of objects containing:")
    print('1. "id": the id string')
    print('2. "chat_messages": array of 3 strings simulating the history:')
    print('     [0] User initial request (Turn 1)')
    print('     [1] Agent initial response (e.g. "Here is the pipeline...")')
    print('     [2] User modification request (Turn 2)')
    print('   MAKE THEM HIGHLY VARIED AND NATURAL.')
    print('3. "consultant_reply": Your natural language recommendation on how to apply the modification (Turn 3).')
    print('4. "diagram_code": JSON graph string of the FINAL modified pipeline conforming to core/prompts/diagram.md.')
    print("\nWrite the JSON list to a scratch file and call `write --file <path>`.")


def write_batch(file_path: str):
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {path}")
        return
        
    data = json.loads(path.read_text())
        
    raw_dict = {}
    if RAW_DATASET.exists():
        for line in RAW_DATASET.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                raw_dict[item["id"]] = item
                
    success_count = 0
    
    for gen_item in data:
        if "id" not in gen_item:
            continue
            
        item_id = gen_item["id"]
        
        required = ["chat_messages", "consultant_reply", "diagram_code"]
        missing = [f for f in required if f not in gen_item]
        if missing:
            print(f"[{item_id}] Skipping - missing required fields: {missing}")
            continue
            
        if item_id not in raw_dict:
            continue
            
        raw_item = raw_dict[item_id]
        
        # Merge
        merged = {
            "id": item_id,
            "category": "modification",
            "test_type": "modification",
            "modification_kind": raw_item["modification_kind"],
            "chat_messages": gen_item["chat_messages"],
            "consultant_reply": gen_item["consultant_reply"],
            "diagram_code": gen_item["diagram_code"],
            # Keep the final nextflow code from the second turn
            "nextflow_code": raw_item["turns"][-1].get("nextflow_code"),
            "params": raw_item["turns"][-1].get("params", {}),
            "expected_processes": raw_item["turns"][-1].get("expected_processes", 0)
        }
        
        # Append to file
        with open(OUT_DATASET, "a") as f:
            f.write(json.dumps(merged) + "\n")
            
        print(f"✅ Appended {item_id} to modification.jsonl")
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
