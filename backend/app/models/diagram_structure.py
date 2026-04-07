from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Literal
import re

MERMAID_KEYWORDS = {'end', 'subgraph', 'classDef', 'direction', 'style', 'linkStyle', 'callback', 'click'}

class Node(BaseModel):
    id: str = Field(description="A purely alphanumeric ID with underscores (e.g., param_reads, step_fastqc). NO dots or spaces.")
    label: str = Field(description="The text to display inside the node. (e.g., .cross, reads, step_ivar)")
    shape: Literal['input', 'process', 'operator', 'output', 'global'] = Field(
        description="Visual shape: 'input' (params), 'process' (tools), 'operator' (.map/.cross), 'output' (emits), 'global' (constants)."
    )
    subgraph: Optional[str] = Field(default=None, description="The name of the workflow this node belongs to (e.g., entrypoint).")

    @field_validator('id')
    def validate_id(cls, v):
        """Forces IDs to be strictly alphanumeric and not reserved keywords."""
        v = v.strip()
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise ValueError(
                f"INVALID ID: '{v}'. Node IDs MUST start with a letter/underscore "
                f"and contain ONLY alphanumeric characters and underscores. NO dots or spaces."
            )
        if v in MERMAID_KEYWORDS:
            raise ValueError(f"INVALID ID: '{v}' is a reserved Mermaid keyword. Please append an underscore (e.g., '{v}_').")
        return v
        
    @field_validator('label')
    def sanitize_label(cls, v):
        """Cleans the label, replacing double quotes with single quotes to prevent string termination crashes."""
        if not v or not str(v).strip():
            raise ValueError("Node label cannot be empty. Use a descriptive name.")
        
        clean_text = str(v).strip().replace('"', "'").replace('\n', ' ')
        return clean_text

    @field_validator('subgraph')
    def validate_subgraph(cls, v):
        if v:
            v = v.strip()
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
                raise ValueError(f"INVALID SUBGRAPH ID: '{v}'. Must be alphanumeric with no spaces.")
            if v in MERMAID_KEYWORDS:
                raise ValueError(f"INVALID SUBGRAPH ID: '{v}' is a reserved keyword.")
        return v

class Edge(BaseModel):
    source: str = Field(description="The ID of the source node.")
    target: str = Field(description="The ID of the target node.")
    label: str = Field(default="", description="The channel name, tuple, or data flowing between them. Leave empty if no data.")

    @field_validator('label')
    def sanitize_edge_label(cls, v):
        """Sanitizes edge labels just like node labels."""
        if v:
            return str(v).strip().replace('"', "'").replace('\n', ' ')
        return ""

class DiagramData(BaseModel):
    nodes: List[Node] = Field(min_length=1, description="All nodes in the pipeline.")
    edges: List[Edge] = Field(default=[], description="All connections between nodes.")

    @model_validator(mode='after')
    def validate_graph_integrity(self):
        node_ids = set()
        
        for node in self.nodes:
            if node.id in node_ids:
                raise ValueError(f"DUPLICATE NODE ERROR: The Node ID '{node.id}' is used more than once. IDs must be unique.")
            node_ids.add(node.id)

        for edge in self.edges:
            if edge.source not in node_ids:
                raise ValueError(f"EDGE ERROR: Source node '{edge.source}' does not exist in the nodes list.")
            if edge.target not in node_ids:
                raise ValueError(f"EDGE ERROR: Target node '{edge.target}' does not exist in the nodes list.")
                
        return self