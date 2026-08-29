import re


def parse_pipeline_to_graph(ast_dict: dict) -> tuple[dict[str, list[str]], dict[str, list[str]], list[tuple[str, str]]]:
    """
    Parses a NextflowPipelineAST dictionary into a graph representation.
    Returns:
      nodes: dict of node_id -> process_name
      edges: list of (src_node, dest_node) based on channel flow
      channel_bindings: list of (node_id, emit_channel, dest_id, take_channel)
    """

    # We will track channels: channel_name -> producer_node_id
    channel_producers = {}
    edges = []
    nodes = {}

    # Simplified parsing for the evaluation script
    # Real AST logic is complex; this is a heuristic DAG builder for pass@k tests

    entrypoint = ast_dict.get('entrypoint', {})
    body_code = entrypoint.get('body_code', '')

    for sw in ast_dict.get('sub_workflows', []):
        body_code += "\n" + sw.get('body_code', '')

    lines = body_code.split('\n')

    node_counter = 0

    # Very basic regex to find standard Nextflow DSL2 process calls:
    # out_chan = process_name(in_chan1, in_chan2)
    # process_name(in_chan1)

    assignment_pattern = re.compile(r'(?:([a-zA-Z0-9_,\s]+)\s*=)?\s*([a-zA-Z0-9_]+)\s*\(([^)]*)\)')

    for line in lines:
        line = line.split('//')[0].strip()
        if not line: continue

        match = assignment_pattern.search(line)
        if match:
            outputs_str = match.group(1)
            process_name = match.group(2)
            inputs_str = match.group(3)

            # Skip standard keywords
            if process_name in ['Channel', 'file', 'path', 'tuple', 'val', 'set']:
                continue

            node_id = f"{process_name}_{node_counter}"
            node_counter += 1
            nodes[node_id] = process_name

            inputs = [i.strip() for i in inputs_str.split(',')] if inputs_str else []
            outputs = [o.strip() for o in outputs_str.split(',')] if outputs_str else []

            # Resolve edges
            for inp in inputs:
                if inp in channel_producers:
                    src_node = channel_producers[inp]
                    edges.append((src_node, node_id))

            # Register outputs
            for out in outputs:
                if out:
                    channel_producers[out] = node_id

    # Build adjacency list
    adj_list = {n: [] for n in nodes}
    for src, dst in edges:
        if src in adj_list:
            adj_list[src].append(dst)

    return nodes, adj_list, edges

def check_cycles(adj_list: dict[str, list[str]]) -> bool:
    """Returns True if the graph is a DAG (no cycles)."""
    visited = set()
    rec_stack = set()

    def dfs(node):
        visited.add(node)
        rec_stack.add(node)

        for neighbor in adj_list.get(node, []):
            if neighbor not in visited:
                if not dfs(neighbor):
                    return False
            elif neighbor in rec_stack:
                return False

        rec_stack.remove(node)
        return True

    for node in adj_list:
        if node not in visited and not dfs(node):
            return False
    return True

def calculate_topo_k(ast_list: list[dict]) -> float:
    """Calculates the percentage of ASTs that form a valid DAG."""
    if not ast_list: return 0.0

    valid_dags = 0
    for ast in ast_list:
        nodes, adj_list, edges = parse_pipeline_to_graph(ast)
        if check_cycles(adj_list):
            valid_dags += 1

    return (valid_dags / len(ast_list)) * 100.0

def calculate_dfc(ast_list: list[dict]) -> float:
    """
    Data-Flow Correctness (DFC)
    In a real scenario, this would cross-reference the nodes against the JSON catalog.
    For this evaluation script, we check if edge mappings are consistent and non-dangling.
    Returns percentage of valid edges across all graphs.
    """
    total_edges = 0
    valid_edges = 0

    for ast in ast_list:
        nodes, adj_list, edges = parse_pipeline_to_graph(ast)

        for src, dst in edges:
            total_edges += 1
            # A valid edge is one where both src and dst exist in the node map
            # and there are no cyclic references directly
            if src in nodes and dst in nodes and src != dst:
                valid_edges += 1

    if total_edges == 0: return 100.0
    return (valid_edges / total_edges) * 100.0

if __name__ == "__main__":
    # Mock test
    mock_ast = {
        "entrypoint": {
            "body_code": '''
            reads = Channel.fromPath("*.fastq")
            clean_reads = fastqc(reads)
            assembly = spades(clean_reads)
            report = quast(assembly)
            '''
        }
    }

    print(f"Topo@k: {calculate_topo_k([mock_ast])}%")
    print(f"DFC: {calculate_dfc([mock_ast])}%")
