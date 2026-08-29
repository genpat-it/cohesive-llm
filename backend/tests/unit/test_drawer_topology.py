import unittest


class TestDrawerTopologyUnit(unittest.TestCase):
    def test_drawer_wires_channel_resolution(self):
        nodes = [
            {
                "node_id": 1,
                "component_id": "step_1QC_trimming__fastp",
                "inputs": ["rawreads"],
                "outputs": ["trimmed_reads", "html_report"],
            },
            {
                "node_id": 2,
                "component_id": "step_2AS_mapping__bowtie",
                "inputs": ["reads", "reference"],
                "outputs": ["bam"],
            },
        ]
        edges = [
            {
                "source": 1,
                "target": 2,
                "source_port": "output_1",
                "target_port": "input_1",
            }
        ]

        visual_wires = []
        for e in edges:
            src = next((n for n in nodes if n.get("node_id") == e.get("source")), None)
            tgt = next((n for n in nodes if n.get("node_id") == e.get("target")), None)
            if src and tgt:
                src_cid = src.get("component_id")
                tgt_cid = tgt.get("component_id")

                src_port_key = str(e.get("source_port", "output_1"))
                tgt_port_key = str(e.get("target_port", "input_1"))

                src_outputs = src.get("outputs") or []
                tgt_inputs = tgt.get("inputs") or []

                src_idx = int(src_port_key.split("_")[-1]) - 1 if "_" in src_port_key and src_port_key.split("_")[-1].isdigit() else 0
                tgt_idx = int(tgt_port_key.split("_")[-1]) - 1 if "_" in tgt_port_key and tgt_port_key.split("_")[-1].isdigit() else 0

                src_channel = src_outputs[src_idx] if 0 <= src_idx < len(src_outputs) else src_port_key
                tgt_channel = tgt_inputs[tgt_idx] if 0 <= tgt_idx < len(tgt_inputs) else tgt_port_key

                visual_wires.append({
                    "source_id": src_cid,
                    "source_channel": src_channel,
                    "source_port": src_port_key,
                    "target_id": tgt_cid,
                    "target_channel": tgt_channel,
                    "target_port": tgt_port_key,
                })

        self.assertEqual(len(visual_wires), 1)
        wire = visual_wires[0]
        self.assertEqual(wire["source_id"], "step_1QC_trimming__fastp")
        self.assertEqual(wire["source_channel"], "trimmed_reads")
        self.assertEqual(wire["target_id"], "step_2AS_mapping__bowtie")
        self.assertEqual(wire["target_channel"], "reads")


if __name__ == "__main__":
    unittest.main()
