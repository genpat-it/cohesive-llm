import json
from typing import Literal, Optional, List
from pydantic import BaseModel, Field, field_validator

class ConsultantOutput(BaseModel):
    response_to_user: str = Field(
        description="Your conversational reply to the user. Ask questions or confirm steps."
    )
    status: Literal["CHATTING", "APPROVED"] = Field(
        description="Set to CHATTING if the user is still making changes. Set to APPROVED only when the user says they are ready to build."
    )
    draft_plan: Optional[str] = Field(
        default=None, 
        description="If APPROVED, write a detailed step-by-step summary of the pipeline for the Architect."
    )
    strategy_selector: Optional[Literal["EXACT_MATCH", "ADAPTED_MATCH", "CUSTOM_BUILD"]] = Field(
        default=None, 
        description="If APPROVED, select how to build this based on the available RAG templates."
    )
    used_template_id: Optional[str] = Field(
        default=None, 
        description="CRITICAL: MUST be the EXACT template ID from the RAG context (e.g., 'module_covid_emergency'). DO NOT invent or guess names."
    )
    selected_module_ids: List[str] = Field(
        default=[], 
        description="CRITICAL: MUST be a list of EXACT component IDs from the RAG context (e.g., ['step_2AS_mapping__ivar']). Do not use shorthand like 'ivar'."
    )

    @field_validator('selected_module_ids', mode='before')
    def prevent_null_list(cls, v):
        if v is None:
            return []
        return v
