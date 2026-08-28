from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SemanticEdge(BaseModel):
    upstream_component: str = Field(description="The component producing the data")
    downstream_component: str = Field(description="The component consuming the data")
    channel: str = Field(description="The semantic channel name (e.g. bridging 'depleted' to 'reads')")

class InputAssignment(BaseModel):
    variable: str = Field(description="The channel/variable name needed by the pipeline (e.g., 'reads', 'reference', 'metadata')")
    source_helper: str = Field(description="The EXACT Nextflow helper function used to populate this variable (e.g., 'getInput()', 'param(\\'ref\\')')")

class ConsultantOutput(BaseModel):
    input_assignments: list[InputAssignment] = Field(
        default_factory=list,
        description="Explicitly define how every required input channel is populated using helper functions. If you don't know the helper function, YOU MUST STOP AND CALL search_helper_functions."
    )
    semantic_edges: list[SemanticEdge] = Field(
        default_factory=list,
        description="Explicitly define the logical connections between components. You MUST bridge false-negative gaps (e.g., mapping 'depleted' to 'reads'). IF THERE IS ONLY ONE COMPONENT, RETURN AN EMPTY LIST []."
    )
    response_to_user: str = Field(
        description="Your conversational reply to the user. Ask questions or confirm steps."
    )
    status: Literal["CHATTING", "APPROVED"] = Field(
        description="Set to CHATTING if the user is still making changes. Set to APPROVED only when the user says they are ready to build."
    )
    draft_plan: str | None = Field(
        default=None,
        description="If APPROVED, write a detailed step-by-step summary of the pipeline for the Architect."
    )
    strategy_selector: Literal["EXACT_MATCH", "ADAPTED_MATCH", "CUSTOM_BUILD"] | None = Field(
        default=None,
        description="If APPROVED, select how to build this based on the available RAG templates."
    )
    used_template_id: str | None = Field(
        default=None,
        description="CRITICAL: MUST be the EXACT template ID from the RAG context (e.g., 'my_special_pipeline'). DO NOT invent or guess names."
    )
    selected_component_ids: list[str] = Field(
        default_factory=list,
        description="A list of the component IDs planned for the pipeline. It is okay if they are shorthand or slightly inaccurate; the backend will semantically resolve them."
    )

    @field_validator('selected_component_ids', mode='before')
    @classmethod
    def prevent_null_list(cls, v: Any) -> Any:
        if v is None:
            return []
        return v

    @model_validator(mode='after')
    def validate_approved_status(self) -> 'ConsultantOutput':
        if self.status == "APPROVED":
            if not self.draft_plan:
                # LLM can sometimes omit draft_plan in single-turn approval
                self.draft_plan = "Proceeding to build pipeline."
            if not self.strategy_selector:
                self.strategy_selector = "CUSTOM_BUILD"
        return self
