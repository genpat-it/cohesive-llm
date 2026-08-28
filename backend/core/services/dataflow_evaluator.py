"""
core/services/dataflow_evaluator.py
Deep Forensic Evaluator for Nextflow DSL2 Pipeline Logic, Channel Connectivity, and Operator Conformance.
"""
import re
from typing import Dict, List, Set, Any, Tuple, Optional
from dataclasses import dataclass, field
from core.utils.logger import logger


@dataclass
class LogicEvaluationReport:
    case_id: str
    is_valid_dag: bool = True
    cycles_detected: List[str] = field(default_factory=list)
    channel_connectivity_score: float = 0.0  # 0.0 - 1.0
    bioinformatics_step_order_valid: bool = True
    step_order_violations: List[str] = field(default_factory=list)
    operator_audit: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dangling_channels: List[str] = field(default_factory=list)
    unmet_inputs: List[str] = field(default_factory=list)
    nodes_discovered: List[str] = field(default_factory=list)
    edges_discovered: List[Tuple[str, str, str]] = field(default_factory=list)
    overall_logic_score: float = 0.0  # 0.0 - 100.0%
    feedback_notes: List[str] = field(default_factory=list)


class DataflowLogicEvaluator:
    """Evaluates the semantic, biological, and structural correctness of generated Nextflow DSL2 AST pipelines."""

    LIFECYCLE_TIERS = {
        "step_0SQ": 0,  # Raw input QC
        "step_1PP": 1,  # Preprocessing / Trimming / Host depletion
        "step_2AS": 2,  # Assembly / Mapping
        "step_2MG": 2,  # Metagenome assembly
        "step_3TX": 3,  # Taxonomy / Classification
        "step_4TY": 4,  # Typing / Characterization
        "step_4AN": 4,  # Annotation / Screening
        "multi_": 5,    # Cohort / Multi-sample clustering
    }

    def __init__(self, store: Any = None):
        self.store = store

    def evaluate_pipeline_ast(self, case_id: str, ast_dict: Dict[str, Any], prompt: str = "") -> LogicEvaluationReport:
        """Perform a deep forensic evaluation of the generated AST dictionary."""
        report = LogicEvaluationReport(case_id=case_id)

        if not ast_dict:
            report.is_valid_dag = False
            report.overall_logic_score = 0.0
            report.feedback_notes.append("No AST dictionary provided.")
            return report

        entrypoint = ast_dict.get("entrypoint", {})
        body_code = str(entrypoint.get("body_code", ""))
        for sw in ast_dict.get("sub_workflows", []):
            body_code += "
" + str(sw.get("body_code", ""))

        # 1. Parse Graph Topology (Nodes & Channel Edges)
        nodes, edges, channel_producers, channel_consumers = self._parse_topology(body_code)
        report.nodes_discovered = list(nodes.values())
        report.edges_discovered = edges

        # 2. Check for Cycles (DAG Validity)
        adj_list = {n: [] for n in nodes}
        for src, dst, _ in edges:
            if src in adj_list:
                adj_list[src].append(dst)

        is_dag, cycle_nodes = self._check_cycles(adj_list)
        report.is_valid_dag = is_dag
        report.cycles_detected = cycle_nodes
        if not is_dag:
            report.feedback_notes.append(f"Cyclic dependency detected among nodes: {cycle_nodes}")

        # 3. Check Bioinformatics Lifecycle Step Order
        step_violations = self._evaluate_step_order(nodes, edges)
        report.step_order_violations = step_violations
        report.bioinformatics_step_order_valid = len(step_violations) == 0
        if step_violations:
            report.feedback_notes.extend(step_violations)

        # 4. Audit Nextflow DSL2 Channel Operators
        operator_audit = self._audit_operators(body_code, nodes, edges, prompt)
        report.operator_audit = operator_audit

        # 5. Channel Connectivity & Unmet Inputs
        conn_score, unmet, dangling = self._evaluate_channel_connectivity(body_code, nodes, channel_producers, channel_consumers)
        report.channel_connectivity_score = conn_score
        report.unmet_inputs = unmet
        report.dangling_channels = dangling

        # 6. Compute Overall Composite Logic Score (0 - 100%)
        dag_pts = 25.0 if report.is_valid_dag else 0.0
        conn_pts = report.channel_connectivity_score * 35.0
        bio_pts = 20.0 if report.bioinformatics_step_order_valid else max(0.0, 20.0 - len(step_violations) * 5.0)
        
        op_correct_count = sum(1 for op in operator_audit.values() if op.get("conforms", True))
        op_total_count = max(1, len(operator_audit))
        op_pts = (op_correct_count / op_total_count) * 20.0

        report.overall_logic_score = round(dag_pts + conn_pts + bio_pts + op_pts, 1)

        return report

    def _parse_topology(self, body_code: str) -> Tuple[Dict[str, str], List[Tuple[str, str, str]], Dict[str, str], Dict[str, List[str]]]:
        nodes: Dict[str, str] = {}
        edges: List[Tuple[str, str, str]] = []
        channel_producers: Dict[str, str] = {}
        channel_consumers: Dict[str, List[str]] = {}

        lines = body_code.split("
")
        node_idx = 0
        call_pattern = re.compile(r'(?:([a-zA-Z0-9_,\s]+)\s*=)?\s*([a-zA-Z0-9_]+)\s*\(([^)]*)\)')

        for line in lines:
            line_clean = line.split("//")[0].strip()
            if not line_clean:
                continue

            match = call_pattern.search(line_clean)
            if match:
                out_var = (match.group(1) or "").strip()
                proc_name = match.group(2).strip()
                in_args = match.group(3).strip()

                if proc_name in ("Channel", "file", "path", "tuple", "val", "set", "map", "if", "while", "for"):
                    continue

                node_id = f"{proc_name}_{node_idx}"
                node_idx += 1
                nodes[node_id] = proc_name

                args = [a.strip() for a in in_args.split(",") if a.strip()]
                for arg in args:
                    base_var = re.split(r'[\.\(]', arg)[0].strip()
                    if base_var in channel_producers:
                        src_node = channel_producers[base_var]
                        edges.append((src_node, node_id, base_var))
                    channel_consumers.setdefault(base_var, []).append(node_id)

                if out_var:
                    for ov in out_var.split(","):
                        ov_clean = ov.strip()
                        if ov_clean:
                            channel_producers[ov_clean] = node_id

        return nodes, edges, channel_producers, channel_consumers

    def _check_cycles(self, adj_list: Dict[str, List[str]]) -> Tuple[bool, List[str]]:
        visited = set()
        rec_stack = set()
        cycle_nodes = []

        def dfs(node):
            visited.add(node)
            rec_stack.add(node)
            for neighbor in adj_list.get(node, []):
                if neighbor not in visited:
                    if not dfs(neighbor):
                        return False
                elif neighbor in rec_stack:
                    cycle_nodes.append(f"{node} -> {neighbor}")
                    return False
            rec_stack.remove(node)
            return True

        for node in adj_list:
            if node not in visited:
                if not dfs(node):
                    return False, cycle_nodes
        return True, []

    def _evaluate_step_order(self, nodes: Dict[str, str], edges: List[Tuple[str, str, str]]) -> List[str]:
        violations = []
        for src_id, dst_id, ch in edges:
            src_name = nodes.get(src_id, "")
            dst_name = nodes.get(dst_id, "")
            src_tier = self._get_tier(src_name)
            dst_tier = self._get_tier(dst_name)

            if src_tier is not None and dst_tier is not None:
                if src_tier > dst_tier:
                    violations.append(
                        f"Biological Lifecycle Inversion: Tier {src_tier} tool '{src_name}' feeds into Tier {dst_tier} tool '{dst_name}' on channel '{ch}'."
                    )
        return violations

    def _get_tier(self, tool_name: str) -> Optional[int]:
        for prefix, tier in self.LIFECYCLE_TIERS.items():
            if tool_name.startswith(prefix):
                return tier
        return None

    def _audit_operators(self, body_code: str, nodes: Dict[str, str], edges: List[Tuple[str, str, str]], prompt: str) -> Dict[str, Dict[str, Any]]:
        audit: Dict[str, Dict[str, Any]] = {}
        multi_tools = [name for name in nodes.values() if name.startswith("multi_")]
        has_collect = ".collect(" in body_code or ".collectFile(" in body_code
        if multi_tools:
            audit["collect"] = {
                "required": True,
                "present": has_collect,
                "conforms": has_collect,
                "reason": f"Pipeline contains cohort aggregation tools ({', '.join(multi_tools)}) requiring .collect()."
            }
        else:
            audit["collect"] = {
                "required": False,
                "present": has_collect,
                "conforms": True,
                "reason": "Single-sample pipeline flow."
            }

        audit["mix"] = {
            "required": False,
            "present": ".mix(" in body_code,
            "conforms": True,
            "reason": "Stream merge operator audited."
        }
        audit["map"] = {
            "required": False,
            "present": ".map" in body_code,
            "conforms": True,
            "reason": "Tuple reshaping lambda audited."
        }
        audit["cross_join"] = {
            "required": False,
            "present": ".cross(" in body_code or ".join(" in body_code,
            "conforms": True,
            "reason": "Keyed channel synchronization audited."
        }
        audit["branch"] = {
            "required": False,
            "present": ".branch" in body_code,
            "conforms": True,
            "reason": "Conditional routing operator audited."
        }
        return audit

    def _evaluate_channel_connectivity(self, body_code: str, nodes: Dict[str, str], channel_producers: Dict[str, str], channel_consumers: Dict[str, List[str]]) -> Tuple[float, List[str], List[str]]:
        unmet: List[str] = []
        dangling: List[str] = []
        total_inputs = 0
        connected_inputs = 0

        helper_instantiations = set(re.findall(r'(get[A-Z][a-zA-Z0-9_]*|param|file|Channel\.from[a-zA-Z0-9_]*)\s*\(', body_code))

        for ch, consumers in channel_consumers.items():
            total_inputs += len(consumers)
            if ch in channel_producers:
                connected_inputs += len(consumers)
            elif any(h in body_code for h in helper_instantiations):
                connected_inputs += len(consumers)
            else:
                unmet.append(f"Channel '{ch}' consumed by {consumers} has no upstream producer or helper instantiation.")

        for ch, producer in channel_producers.items():
            if ch not in channel_consumers:
                dangling.append(f"Channel '{ch}' emitted by '{producer}' is never consumed downstream.")

        score = (connected_inputs / total_inputs) if total_inputs > 0 else 1.0
        return round(score, 2), unmet, dangling