import unittest
from unittest.mock import MagicMock, patch
from core.nodes.drawer_enricher import drawer_enrich_node, DrawerEnrichmentOutput


class TestDrawerEnricherUnit(unittest.TestCase):
    @patch("core.nodes.drawer_enricher.get_llm")
    def test_drawer_enrich_node_success(self, mock_get_llm):
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {
            "parsed": DrawerEnrichmentOutput(
                draft_plan="Enriched Nextflow blueprint with .cross() operator",
                selected_component_ids=["step_1QC_trimming__fastp", "step_2AS_mapping__bowtie"],
                strategy_selector="CUSTOM_BUILD",
                used_template_id=None,
            )
        }
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_agent
        mock_get_llm.return_value = mock_llm

        state = {
            "visual_topology": {
                "components": ["step_1QC_trimming__fastp", "step_2AS_mapping__bowtie"],
                "wires": [
                    {
                        "source_id": "step_1QC_trimming__fastp",
                        "source_channel": "trimmed_reads",
                        "target_id": "step_2AS_mapping__bowtie",
                        "target_channel": "reads",
                    }
                ],
            }
        }

        result = drawer_enrich_node(state)
        self.assertEqual(result["consultant_status"], "APPROVED")
        self.assertEqual(result["strategy_selector"], "CUSTOM_BUILD")
        self.assertEqual(len(result["selected_component_ids"]), 2)
        self.assertIn(".cross()", result["design_plan"])


if __name__ == "__main__":
    unittest.main()
