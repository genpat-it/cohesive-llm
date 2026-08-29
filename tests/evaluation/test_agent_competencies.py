import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage

from core.config import settings
from core.services.graph import compact_memory_node
from core.services.graph_state import GraphState
from core.services.repair import repair_node, should_repair


class TestAgentCompetencies(unittest.TestCase):
    # ──────────────────────────────────────────────────────────────
    # 1. Self-Correction & Repair Node Routing Tests
    # ──────────────────────────────────────────────────────────────

    def test_should_repair_routing_success(self):
        # Case: no validation_error -> success
        state = {"validation_error": None, "retries": 0}
        self.assertEqual(should_repair(state), "success")

    def test_should_repair_routing_repair(self):
        # Case: has validation_error, retries < max_retries -> repair
        state = {"validation_error": "Syntax error at line 5", "retries": 0}
        self.assertEqual(should_repair(state), "repair")

    def test_should_repair_routing_fail_on_max_retries(self):
        # Case: has validation_error, retries >= max_retries -> fail
        state = {"validation_error": "Syntax error at line 5", "retries": settings.MAX_REPAIR_RETRIES}
        self.assertEqual(should_repair(state), "fail")

    def test_repair_node_appends_error_instructions_to_messages(self):
        # Test that repair_node returns updates adding a HumanMessage with error instructions
        state = {"validation_error": "Process declarations mismatch", "messages": []}
        updates = repair_node(state)

        self.assertIn("messages", updates)
        self.assertEqual(len(updates["messages"]), 1)
        self.assertIsInstance(updates["messages"][0], HumanMessage)
        self.assertIn("Process declarations mismatch", updates["messages"][0].content)
        self.assertIn("Generate the **FULLY CORRECTED** JSON AST", updates["messages"][0].content)

    # ──────────────────────────────────────────────────────────────
    # 2. Lossless Memory Compaction Tests
    # ──────────────────────────────────────────────────────────────

    def test_compact_memory_node_no_op_below_threshold(self):
        # Under threshold: keep last N is default (e.g. 6), if we have 4 messages, no compaction
        messages = [
            SystemMessage(content="System prompt"),
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
            HumanMessage(content="Build a pipeline")
        ]
        state = {"messages": messages, "tool_memory": []}

        updates = compact_memory_node(state)
        self.assertEqual(updates, {})

    def test_compact_memory_node_extracts_tool_facts_losslessly(self):
        # Exceeds threshold: we want to verify it removes old tool messages outside
        # the keep window, and extracts their tool and args/results into tool_memory

        # Build message history:
        # Index 0, 1: System, User (kept - first 2)
        # Index 2, 3: AI tool call, Tool response (outside keep window, should be compacted)
        # Index 4, 5, 6, 7, 8, 9: remaining messages (kept - last 6)
        messages = [
            SystemMessage(content="System prompt"), # 0 (kept - first 2)
            HumanMessage(content="User message 1"),   # 1 (kept - first 2)

            # AI tool call and Tool response to be compacted
            AIMessage(
                content="",
                id="msg_ai_tool",
                tool_calls=[{"name": "lookup_component_code", "args": {"component_id": "fastp"}, "id": "call_1"}]
            ), # 2
            LCToolMessage(content="Groovy code for fastp...", name="lookup_component_code", tool_call_id="call_1", id="msg_tool_resp"), # 3

            # Last 6 messages (default MEMORY_KEEP_LAST_N = 6)
            HumanMessage(content="User message 2"),   # 4
            AIMessage(content="Draft plan"),          # 5
            HumanMessage(content="Approve"),             # 6
            AIMessage(content="Final AST"),           # 7
            HumanMessage(content="Thank you"),         # 8
            AIMessage(content="You are welcome")      # 9
        ]

        state = {"messages": messages, "tool_memory": []}

        # We temporarily force MEMORY_KEEP_LAST_N to 6 to guarantee compaction
        with unittest.mock.patch("core.services.graph.MEMORY_KEEP_LAST_N", 6):
            updates = compact_memory_node(state)

        self.assertIn("messages", updates)
        self.assertIn("tool_memory", updates)

        # Check messages to delete: should contain RemoveMessage instances for msg_ai_tool and msg_tool_resp
        deleted_ids = {msg.id for msg in updates["messages"]}
        self.assertIn("msg_ai_tool", deleted_ids)
        self.assertIn("msg_tool_resp", deleted_ids)

        # Check extracted facts
        facts = updates["tool_memory"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["tool"], "lookup_component_code")
        self.assertIn("fastp", facts[0]["args"])
        self.assertIn("Groovy code for fastp", facts[0]["result"])

    def test_compact_memory_node_respects_max_tool_facts_cap(self):
        # Verifies that tool_memory size does not exceed MEMORY_MAX_TOOL_FACTS
        messages = [
            SystemMessage(content="System prompt"),
            HumanMessage(content="User message 1"),
            AIMessage(
                content="",
                id="msg_ai_tool",
                tool_calls=[{"name": "lookup_component_code", "args": {"component_id": "fastp"}, "id": "call_1"}]
            ),
            LCToolMessage(content="Groovy...", name="lookup_component_code", tool_call_id="call_1", id="msg_tool_resp"),
            HumanMessage(content="User message 2"),
            AIMessage(content="Draft plan"),
            HumanMessage(content="Approve"),
            AIMessage(content="Final AST"),
            HumanMessage(content="Thank you"),
            AIMessage(content="You are welcome")
        ]

        # Pre-populate tool_memory with existing facts to trigger capping
        existing_facts = [{"tool": "old_tool", "args": "old_args", "result": "old_res"}] * settings.MEMORY_MAX_TOOL_FACTS
        state = {"messages": messages, "tool_memory": existing_facts}

        with unittest.mock.patch("core.services.graph.MEMORY_KEEP_LAST_N", 6):
            updates = compact_memory_node(state)

        self.assertIn("tool_memory", updates)
        # Verify the new total facts is capped at MEMORY_MAX_TOOL_FACTS
        self.assertEqual(len(updates["tool_memory"]), settings.MEMORY_MAX_TOOL_FACTS)
