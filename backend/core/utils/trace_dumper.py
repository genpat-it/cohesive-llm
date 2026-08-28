import json
import os
from pathlib import Path
from typing import Any

def dump_trace(log_dir: str, session_id: str, messages: list[Any]) -> None:
    """Dump the LangGraph messages into a markdown trace for debugging and observation."""
    if not messages:
        return
        
    os.makedirs(log_dir, exist_ok=True)
    trace_file = Path(log_dir) / f"trace_{session_id}.md"
    
    with open(trace_file, "w") as f:
        f.write(f"# IZS-LLM Inner Thinking Trace\n\n")
        f.write(f"**Session ID:** {session_id}\n\n")
        
        for i, msg in enumerate(messages):
            msg_type = getattr(msg, "type", "UNKNOWN").upper()
            f.write(f"## Step {i+1}: `{msg_type}`\n")
            
            # Print Name/Sender
            if getattr(msg, "name", None):
                f.write(f"**Sender/Tool:** `{msg.name}`\n\n")
                
            # Additional kwargs
            if getattr(msg, "additional_kwargs", None):
                f.write(f"**Kwargs:** `{json.dumps(msg.additional_kwargs)}`\n\n")
                
            # Print Tool Calls
            if getattr(msg, "tool_calls", None):
                f.write("### Tool Calls Made:\n")
                for tc in msg.tool_calls:
                    try:
                        args_str = json.dumps(tc.get('args', {}))
                    except Exception:
                        args_str = str(tc.get('args', {}))
                    f.write(f"- `{tc.get('name')}` with args: `{args_str}`\n")
                f.write("\n")
                
            # Print Content
            if hasattr(msg, "content"):
                f.write("### Content/Output:\n")
                content = repr(msg.content)
                content_type = type(msg.content).__name__
                f.write(f"```text\nType: {content_type}\n{content}\n```\n\n")
                
            f.write("---\n\n")
