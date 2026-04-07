import asyncio
from app.services.graph import app_graph

# We need a configurable thread to use the MemorySaver checkpointer
config = {"configurable": {"thread_id": "test_thread_1"}}

print("--- 1. First Invocation (will pause before architect) ---")
result = app_graph.invoke(
    {"user_query": "Build a downsampling pipeline", "retries": 0, "messages": []},
    config=config
)

state = app_graph.get_state(config)
print(f"Graph paused. Next nodes to execute: {state.next}")

print("\n--- 2. Resuming Execution ---")
# Passing None as input to resume exactly where it was interrupted
result = app_graph.invoke(None, config=config)

if result:
    final_code = result.get("nextflow_code")
    final_diagram = result.get("mermaid_code")
    print("Code:\n", final_code)
    print("Diagram:\n", final_diagram)
else:
    print("Execution did not return final state.")
