# DOMAIN CONTEXT: Bioinformatics Pipeline Design

## MATCHING BIOLOGICAL SCENARIOS (RELEVANCE)
When designing a pipeline, you MUST evaluate the biologically specific context of the user's request:
* **Target Organism:** Ensure the tool is appropriate for the organism (e.g., Pangolin is ONLY for SARS-CoV-2; do not use it for bacteria).
* **Sequencing Platform:** Distinguish between Illumina (short reads) and Nanopore (long reads). Ensure selected tools match the platform (e.g., Flye for Nanopore, SPAdes for Illumina).
* **Analysis Goal:** Ensure the tool matches the goal (e.g., don't use iVar mapping for de novo assembly). Stick strictly to the requested **analytical** steps. Do **NOT** add unrequested analytical tools. However, you MUST infer and include necessary **preprocessing** steps based on standard bioinformatics best practices. 
  - **CRITICAL:** If the input is raw sequence reads, they MUST undergo quality control and trimming before downstream analysis (like Assembly or Mapping). Always search the catalog for comprehensive read processing modules or trimming components to include as the first step.
* **Constraints:** Respect all known tool constraints and biological realities.
* **Keyword Overlaps / Ambiguities:** When a user request contains multiple keywords that could span different domains (e.g., a primary biological goal and a secondary technical method), you MUST prioritize the primary analytical goal. Search for tools in the catalog that solve the primary goal and check their descriptions to see if they natively handle the secondary requirement. Do **NOT** add separate, dedicated tools that merely satisfy the secondary keyword if they ignore the core biological context.
* **Antimicrobial Resistance (AMR):** When a user requests AMR prediction that explicitly includes detection of **point mutations** (e.g. for Campylobacter or Salmonella), you MUST prioritize tools that natively handle both acquired genes and point mutations (like `staramr` which uses PointFinder), rather than combining separate tools or using gene-only tools like `abricate`.
