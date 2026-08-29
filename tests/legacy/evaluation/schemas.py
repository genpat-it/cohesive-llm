from pydantic import BaseModel, Field


class AcademicEval(BaseModel):
    """Consultant evaluation: faithfulness to catalog + relevance to user scenario."""
    faithfulness_reason: str = Field(
        description="Step-by-step reasoning: did the AI stick to the RAG catalog or hallucinate tools?"
    )
    faithfulness_score: int = Field(
        description="Score 1–5 per the faithfulness rubric."
    )
    relevance_reason: str = Field(
        description="Step-by-step reasoning: does the pipeline design match the user's organism, data type, and analysis goals?"
    )
    relevance_score: int = Field(
        description="Score 1–5 per the relevance rubric."
    )


class ArchitectEval(BaseModel):
    """Architect evaluation: Nextflow DSL2 syntax validity + logical correctness."""
    syntax_reason: str = Field(
        description="Step-by-step reasoning: is this valid Nextflow DSL2 with proper imports, process definitions, and channel wiring?"
    )
    syntax_score: int = Field(
        description="Score 1–5 per the syntax rubric."
    )
    logic_reason: str = Field(
        description="Step-by-step reasoning: does the pipeline logic match the design plan and technical context?"
    )
    logic_score: int = Field(
        description="Score 1–5 per the logic rubric."
    )


class DiagramEval(BaseModel):
    """Mermaid diagram evaluation: syntax validity + mapping accuracy."""
    syntax_reason: str = Field(
        description="Step-by-step reasoning: is this valid Mermaid.js flowchart syntax?"
    )
    syntax_score: int = Field(
        description="Score 1–5 per the diagram syntax rubric."
    )
    mapping_reason: str = Field(
        description="Step-by-step reasoning: does the diagram accurately represent the Nextflow pipeline processes and channels?"
    )
    mapping_score: int = Field(
        description="Score 1–5 per the diagram mapping rubric."
    )


class RejectionEval(BaseModel):
    """Rejection guardrail evaluation: correct refusal + alternative suggestions."""
    rejection_reason: str = Field(
        description="Step-by-step reasoning: did the AI correctly identify and explain why the request is invalid?"
    )
    rejection_score: int = Field(
        description="Score 1–5 per the rejection rubric."
    )
    alternative_reason: str = Field(
        description="Step-by-step reasoning: did the AI suggest appropriate, catalog-valid alternatives?"
    )
    alternative_score: int = Field(
        description="Score 1–5 per the alternative suggestion rubric."
    )


class CodeRecreationEval(BaseModel):
    """Code recreation evaluation: structural similarity + channel logic correctness."""
    structural_reason: str = Field(
        description="Step-by-step reasoning: does the generated code include the same components, steps, and workflow structure as the reference?"
    )
    structural_score: int = Field(
        description="Score 1–5 per the structural similarity rubric."
    )
    channel_reason: str = Field(
        description="Step-by-step reasoning: are the channels wired correctly, matching the reference implementation's data flow?"
    )
    channel_score: int = Field(
        description="Score 1–5 per the channel logic rubric."
    )
