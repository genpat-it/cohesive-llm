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


class PipelineIntentClassification(BaseModel):
    """Domain-agnostic LLM classification of user request intent, data level, domain, and constraints."""
    data_level: Literal[
        "raw_reads",
        "intermediate_sequence",
        "variant_data",
        "alignment_data",
        "tabular_metadata",
        "hybrid_multimodal",
        "unspecified",
    ] = Field(
        default="unspecified",
        description="The abstraction level of the user's input data: 'raw_reads' (unprocessed sequencer reads/FASTQ), 'intermediate_sequence' (contigs, scaffolds, FASTA, assemblies), 'variant_data' (VCF, mutation tables), 'alignment_data' (BAM/SAM/CRAM), 'tabular_metadata' (TSV/CSV sample sheets), 'hybrid_multimodal' (both short and long reads), or 'unspecified'."
    )
    domain_category: Literal[
        "virology",
        "bacteriology",
        "metagenomics",
        "parasitology_mycology",
        "epidemiological_surveillance",
        "general_bioinformatics",
        "unspecified",
    ] = Field(
        default="unspecified",
        description="The biological domain category inferred from query or sample context: 'virology', 'bacteriology', 'metagenomics', 'parasitology_mycology', 'epidemiological_surveillance', 'general_bioinformatics', or 'unspecified'."
    )
    workflow_scope: Literal["targeted", "full_pipeline", "qc_only", "diagnostic_probe"] = Field(
        default="targeted",
        description="'targeted' for focused/single-goal execution; 'full_pipeline' for an end-to-end multi-stage pipeline from raw files to final report; 'qc_only' for quality assessment only; 'diagnostic_probe' for conceptual questions without building a pipeline."
    )
    skip_preprocessing: bool = Field(
        default=False,
        description="True if the user explicitly requested to skip quality trimming/preprocessing or stated the data is already clean/pre-processed."
    )
    technology: Literal["illumina", "nanopore", "pacbio", "sanger", "hybrid", "unspecified"] = Field(
        default="unspecified",
        description="Sequencing technology if mentioned or inferred (e.g. 'illumina', 'nanopore', 'pacbio', 'sanger', 'hybrid')."
    )
    explicit_tool_requests: list[str] = Field(
        default_factory=list,
        description="List of specific software or tool names explicitly requested by the user."
    )
    excluded_items: list[str] = Field(
        default_factory=list,
        description="List of any specific tools, processes, or operations the user explicitly asked NOT to use, exclude, or avoid."
    )
    analysis_goals: list[str] = Field(
        default_factory=list,
        description="Generic analysis operations requested (e.g. QC, Preprocessing, Trimming, Assembly, Mapping, Variant Calling, Annotation, AMR Screening, Typing, Lineage, Clustering, Metagenomics)."
    )



