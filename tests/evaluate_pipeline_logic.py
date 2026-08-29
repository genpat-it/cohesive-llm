import re
from typing import Any, Dict, List, Set, Tuple
from core.catalog_registry import get_registry
from core.services.ast_compiler import _is_void_tool, validate_undefined_variables, validate_framework_components

class PipelineLogicEvaluator:
    """Evaluates the structural correctness, dataflow logic, and Nextflow DSL2 operator usage
    in the generated AST and pipeline Groovy code.
    """

    def __init__(self, store: Any = None):
        self.store = store
        from core.services.knowledge_graph import kg
        self.kg = kg

    def evaluate_negative_constraints(self, prompt: str, code: str, components: List[str]) -> Dict[str, Any]:
        """Verify that any tools forbidden by negative constraints in the prompt are completely absent."""
        neg_patterns = [
            r'(?:do not use|don\'t use|without|exclude|never use|no)\s+([a-zA-Z0-9_\-]+)',
            r'([a-zA-Z0-9_\-]+)\s+is not allowed',
        ]
        forbidden_tools = set()
        for p in neg_patterns:
            matches = re.findall(p, prompt, re.IGNORECASE)
            for m in matches:
                clean_m = m.lower().strip()
                if clean_m not in ("use", "the", "a", "an", "short", "reads", "pipeline"):
                    forbidden_tools.add(clean_m)

        violations = []
        for ft in forbidden_tools:
            # Check components list
            for c in components:
                if ft in c.lower():
                    violations.append(f"Forbidden tool '{ft}' found in selected components: '{c}'")
            # Check code
            if re.search(r'\b' + re.escape(ft) + r'\b', code, re.IGNORECASE):
                # Make sure it's not just in a comment
                non_comment_lines = [l for l in code.split("\n") if not l.strip().startswith("//")]
                non_comment_code = "\n".join(non_comment_lines)
                if re.search(r'\b' + re.escape(ft) + r'\b', non_comment_code, re.IGNORECASE):
                    violations.append(f"Forbidden tool '{ft}' found in generated code")

        return {
            "passed": len(violations) == 0,
            "forbidden_tools_identified": list(forbidden_tools),
            "violations": violations
        }

    def evaluate_operators(self, ast_json: Dict[str, Any], components: List[str], code: str) -> Dict[str, Any]:
        """Verify that necessary Nextflow DSL2 operators (.collect, .mix, .map, .cross, .join)
        are properly used based on AST process signatures and DAG topology.
        """
        required_ops = []
        op_evaluations = []

        # 1. Check for Collection / Aggregation (.collect)
        for comp in components:
            coll_info = self.kg.detect_collection_cardinality(comp, store=self.store)
            for item in coll_info:
                required_ops.append({
                    "op": "collect",
                    "target": comp,
                    "reason": item["reason"]
                })
                # Check if .collect() or .toList() appears in code before/at target tool
                has_collect = bool(re.search(r'\.collect\s*\(|\.toList\s*\(', code))
                op_evaluations.append({
                    "operator": "collect",
                    "component": comp,
                    "required": True,
                    "present": has_collect,
                    "details": f"Component '{comp}' requires multi-sample collection. Present: {has_collect}"
                })

        # 2. Check for Fan-In Merging (.mix / .concat)
        mix_info = self.kg.detect_fan_in_mix(components, store=self.store)
        for item in mix_info:
            required_ops.append({
                "op": "mix",
                "target": item["consumer"],
                "reason": f"Producers {item['producers']} feed channel {item['channel']}"
            })
            has_mix = bool(re.search(r'\.mix\s*\(|\.concat\s*\(', code))
            op_evaluations.append({
                "operator": "mix",
                "component": item["consumer"],
                "required": True,
                "present": has_mix,
                "details": f"Convergent streams feeding '{item['consumer']}' require .mix(). Present: {has_mix}"
            })

        # 3. Check for Tuple Reshaping (.map)
        for i in range(len(components) - 1):
            p = components[i]
            c = components[i + 1]
            proj = self.kg.deduce_tuple_arity_projection(p, c, store=self.store)
            if proj:
                required_ops.append({
                    "op": "map",
                    "target": f"{p}->{c}",
                    "reason": f"Arity mismatch {proj['emitted_arity']} -> {proj['taken_arity']}"
                })
                has_map = bool(re.search(r'\.map\s*\{', code))
                op_evaluations.append({
                    "operator": "map",
                    "component": f"{p}->{c}",
                    "required": True,
                    "present": has_map,
                    "details": f"Arity mismatch between '{p}' and '{c}' requires .map{{}}. Present: {has_map}"
                })

        # 4. Check for Species-Aware Keyed Join (.cross + .multiMap)
        cross_info = self.kg.detect_cross_multimap_routing(components, store=self.store)
        for item in cross_info:
            has_cross = bool(re.search(r'\.cross\s*\(', code))
            has_multimap = bool(re.search(r'\.multiMap\s*\{', code))
            cross_passed = has_cross and has_multimap
            op_evaluations.append({
                "operator": "cross+multiMap",
                "component": f"{item['producer_data']}+{item['producer_species']}",
                "required": True,
                "present": cross_passed,
                "details": f"Species-aware routing requires .cross() and .multiMap{{}}. Present: cross={has_cross}, multiMap={has_multimap}"
            })

        # Summary score
        if not op_evaluations:
            op_score = 100.0
            all_passed = True
        else:
            passed_count = sum(1 for e in op_evaluations if e["present"])
            op_score = (passed_count / len(op_evaluations)) * 100.0
            all_passed = passed_count == len(op_evaluations)

        return {
            "score": op_score,
            "passed": all_passed,
            "evaluations": op_evaluations,
            "total_checks": len(op_evaluations)
        }

    def evaluate_channel_wiring(self, ast_json: Dict[str, Any] | None, code: str, is_chatting: bool = False) -> Dict[str, Any]:
        """Audit variable definitions, scoping, void process handling, and channel connectivity."""
        issues = []
        
        if not ast_json or not isinstance(ast_json, dict):
            if is_chatting:
                return {
                    "score": 100.0,
                    "passed": True,
                    "issues": []
                }
            return {
                "score": 0.0,
                "passed": False,
                "issues": ["No AST generated for pipeline"]
            }

        ep = ast_json.get("entrypoint", {})
        body_code = ep.get("body_code", "") if isinstance(ep, dict) else str(ep)
        
        # 1. Undefined variable check
        undefs = validate_undefined_variables(body_code, set())
        for u in undefs:
            issues.append(f"Undefined variable used in entrypoint: '{u}'")

        # 2. Void tool assignment check
        assignment_matches = re.finditer(r'\b([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_]+)\s*\(', body_code)
        for m in assignment_matches:
            var_name = m.group(1)
            proc_name = m.group(2)
            if _is_void_tool(proc_name):
                issues.append(f"Illegal assignment of void process '{proc_name}' to variable '{var_name}'")

        # 3. Entrypoint helper instantiation check
        has_helper = bool(re.search(r'\b(getInput|getReferences|getReference|getSingleInput|getDS|getEmpty|param)\s*\(', body_code))
        if not has_helper and not re.search(r'Channel\s*\.', body_code):
            issues.append("Entrypoint does not instantiate any input channel with helper functions or Channel.from...")

        channel_score = max(0.0, 100.0 - (len(issues) * 25.0))
        return {
            "score": channel_score,
            "passed": len(issues) == 0,
            "issues": issues
        }

    def evaluate_all(self, prompt: str, gt_components: List[str], result_state: Dict[str, Any]) -> Dict[str, Any]:
        """Run complete 4-dimensional evaluation scorecard on pipeline result."""
        pred_components = result_state.get("selected_component_ids", [])
        pred_status = result_state.get("consultant_status", "APPROVED" if pred_components else "CHATTING")
        code = result_state.get("final_pipeline_code", "") or ""
        ast_json = result_state.get("ast_json")
        mermaid = result_state.get("mermaid_deterministic", "") or ""
        is_chatting = pred_status == "CHATTING"

        # 1. Component Metrics
        gt_set = set(gt_components)
        pred_set = set(pred_components)
        if not gt_set and not pred_set:
            p, r, f1 = 100.0, 100.0, 100.0
        elif not pred_set or not gt_set:
            p, r, f1 = 0.0, 0.0, 0.0
        else:
            tp = len(gt_set & pred_set)
            p = (tp / len(pred_set)) * 100.0
            r = (tp / len(gt_set)) * 100.0
            f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0

        # 2. Negative Constraints
        neg_res = self.evaluate_negative_constraints(prompt, code, pred_components)

        # 3. Operators
        op_res = self.evaluate_operators(ast_json or {}, pred_components, code)

        # 4. Channel Wiring & Dataflow
        wiring_res = self.evaluate_channel_wiring(ast_json, code, is_chatting=is_chatting)

        # 5. AST & Syntax Validity
        syntax_valid = bool((ast_json and not result_state.get("error")) or (is_chatting and not gt_components))

        # Overall Logic Score
        composite_score = (f1 * 0.4) + (op_res["score"] * 0.3) + (wiring_res["score"] * 0.2) + (100.0 if syntax_valid else 0.0) * 0.1
        if not neg_res["passed"]:
            composite_score *= 0.5  # Penalize severe negative constraint violations

        return {
            "component_f1": f1,
            "component_precision": p,
            "component_recall": r,
            "operator_score": op_res["score"],
            "operator_details": op_res["evaluations"],
            "wiring_score": wiring_res["score"],
            "wiring_issues": wiring_res["issues"],
            "negative_constraints_passed": neg_res["passed"],
            "negative_violations": neg_res["violations"],
            "syntax_valid": syntax_valid,
            "composite_score": composite_score,
            "has_mermaid": bool(mermaid and len(mermaid) > 20)
        }
