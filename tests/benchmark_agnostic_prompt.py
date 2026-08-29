import os
os.environ["NF_AGENT_PLUGIN"] = "izs"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ["OPENAI_BASE_URL"] = "http://localhost:8000/v1"
from dotenv import load_dotenv
load_dotenv()

import asyncio
from core.loader import data_loader
from core.services.graph import global_store, app_graph
from langchain_core.messages import HumanMessage
import json

async def run_pipeline():
    data_loader.load_all(store=global_store)
    app = app_graph
    
    prompt1 = "Building a highly complex universal clinical pipeline from raw reads (using both getSingleInput() and getInput()) WITHOUT using any pre-built subworkflow templates. You must build this from base components. 1. Process all raw reads: check for data, trim them based on their sequencer type (Illumina vs IonTorrent vs Nanopore), and classify them. 2. Branch the valid trimmed reads by their classified taxonomy. 3. For bacteria: perform host depletion, de novo assembly, and extract the assembled species. 4. Map the trimmed reads against the dynamically extracted species reference. 5. Perform comprehensive typing on the assembly (AMR, MLST, flaA, cgMLST, plasmids, and genes). 6. You must correctly cross and combine the original reads, species information, and reference paths when routing data into these typing tools. 7. Finally, collect all resulting mapping depth profiles."
    msg1 = HumanMessage(content=prompt1)
    
    config = {"configurable": {"thread_id": "test_thread_agnostic"}}
    state = {"messages": [msg1], "user_query": prompt1, "tool_memory": []}
    
    print("Running Phase 1: Consultant...")
    final_state = state.copy()
    async for event in app.astream(state, config=config, stream_mode="updates"):
        for node, output in event.items():
            print(f"--- [DEBUG] NODE {node} ---")
            if "messages" in output:
                if isinstance(output["messages"], list):
                    final_state["messages"].extend(output["messages"])
                else:
                    final_state["messages"].append(output["messages"])
            if "design_plan" in output:
                final_state["design_plan"] = output["design_plan"]
            if "selected_component_ids" in output:
                final_state["selected_component_ids"] = output["selected_component_ids"]
    
    print("\nConsultant Plan Generated!")
    print(final_state.get("design_plan", "No plan"))
    print("Components:", final_state.get("selected_component_ids", []))
    
    print("\nRunning Phase 2: Architect (Approving plan)...")
    final_state["messages"].append(HumanMessage(content="I approve the plan, please build the pipeline."))
    async for event in app.astream(final_state, config=config, stream_mode="updates"):
        for node, output in event.items():
            print(f"--- [DEBUG] NODE {node} ---")
            if "validation_error" in output and output["validation_error"]:
                print(f"Validation Error: {output['validation_error']}")
            
            if "messages" in output:
                if isinstance(output["messages"], list):
                    final_state["messages"].extend(output["messages"])
                else:
                    final_state["messages"].append(output["messages"])
            if "pipeline_code" in output:
                final_state["pipeline_code"] = output["pipeline_code"]

    if final_state and "messages" in final_state:
        from core.utils.trace_dumper import dump_trace
        # Dump the trace
        trace_path = "/Users/grady/Documents/DIE/cloud/izs-llm/tests/trace_benchmark_agnostic.md"
        dump_trace("/Users/grady/Documents/DIE/cloud/izs-llm/tests", "benchmark_agnostic", final_state["messages"])
        print(f"Trace dumped to {trace_path}")
        
    if final_state and "pipeline_code" in final_state:
        # Dump the Nextflow code
        nf_path = "/Users/grady/Documents/DIE/cloud/izs-llm/tests/benchmark_agnostic_pipeline.nf"
        with open(nf_path, "w") as f:
            f.write(final_state["pipeline_code"])
        print(f"Nextflow code dumped to {nf_path}")
    else:
        print("Pipeline code was not generated (likely hit validation limit).")

if __name__ == "__main__":
    asyncio.run(run_pipeline())

