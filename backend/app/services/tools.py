import json
import re
from app.core.loader import data_loader
from app.core.config import settings
from app.services.graph_state import GraphState
from collections import defaultdict

from langgraph.store.base import BaseStore

# ──────────────────────────────────────────────────────────────────────────────
# INJECTION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _inject_component(comp_id, found_ids, context_blocks, store: BaseStore, embed_code=True):
    """Injects a single component block (metadata + optional source code) into context."""
    if comp_id in found_ids:
        return
    
    comp_item = store.get(("components",), comp_id)
    if not comp_item:
        return
    comp_data = comp_item.value

    found_ids.add(comp_id)
    
    code_item = store.get(("code",), comp_id)
    code_snippet = code_item.value.get("content", "// Code not found") if code_item else "// Code not found in repository"

    block = f"""
--- COMPONENT: {comp_id} ---
TOOL: {comp_data.get('tool', 'Unknown')}
DOMAIN: {comp_data.get('domain', 'Unknown')}
DESCRIPTION: {comp_data.get('description', 'No description')}
CONTAINER: {comp_data.get('container', 'None')}
INPUTS ({len(comp_data.get('input_channels', comp_data.get('input_types', [])))} channels): {', '.join(comp_data.get('input_channels', comp_data.get('input_types', [])))}
OUTPUTS: {', '.join(comp_data.get('output_channels', comp_data.get('out', [])))}
"""

    if embed_code:
        block += f"\n**SOURCE CODE ({comp_id}.nf):**\n```groovy\n{code_snippet}\n```\n"

    context_blocks.append(block)

def _inject_template(template_id, found_ids, context_blocks, store: BaseStore, embed_code=True):
    """Injects a single template block (metadata + optional source code) into context."""
    if template_id in found_ids:
        return
    
    tmpl_item = store.get(("templates",), template_id)
    if not tmpl_item:
        return

    found_ids.add(template_id)
    tmpl_data = tmpl_item.value

    block = f"""
--- TEMPLATE: {template_id} ---
NAME: {tmpl_data.get('template_name', 'Unknown')}
DESCRIPTION: {tmpl_data.get('description', 'No description')}
COMPATIBLE SQS: {', '.join(tmpl_data.get('compatible_seq_types', []))}
INPUTS: {', '.join(tmpl_data.get('accepted_inputs', []))}
OUTPUTS: {', '.join(tmpl_data.get('outputs', []))}
"""

    if embed_code:
        code_item = store.get(("code",), template_id)
        code_snippet = code_item.value.get("content", "// Code not found") if code_item else "// Code not found in repository"
        block += f"\n**SOURCE CODE ({template_id}.nf):**\n```groovy\n{code_snippet}\n```\n"

    context_blocks.append(block)

EXCLUDED_TEMPLATES = settings.RAG_EXCLUDED_TEMPLATES

def retrieve_rag_context(user_query, store: BaseStore, embed_code=False):
    """
    Retrieves context using a Hybrid Approach:
      1. Keyword & metadata matching across the component/template catalog
      2. FAISS semantic vector search for latent similarity
    Both paths share a preprocessed, synonym-expanded token set.
    """
    if not data_loader.vector_store:
        return "Vector Store not loaded."
    
    found_ids = set()
    context_blocks = []
    
    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 0 — QUERY PRE-PROCESSING & NORMALIZATION
    # ══════════════════════════════════════════════════════════════════════════
    query_lower = user_query.lower()

    # Multi-word & acronym normalization
    # Collapses domain-specific multi-word terms and common acronyms into
    # single canonical tokens so they match catalog entries reliably.
    bio_replacements = {
        # Sequencing technology aliases
        "de novo": "denovo",
        "paired end": "paired",
        "paired-end": "paired",
        "single end": "single",
        "single-end": "single",
        "short reads": "illumina",
        "short-reads": "illumina",
        "long reads": "nanopore",
        "long-reads": "nanopore",
        "oxford nanopore": "nanopore",
        "ont": "nanopore",
        "ion torrent": "ion",
        "ion-torrent": "ion",
        # Assay / protocol aliases
        "rna seq": "rnaseq",
        "rna-seq": "rnaseq",
        "chip seq": "chipseq",
        "chip-seq": "chipseq",
        "quality control": "qc",
        "quality check": "qc",
        "quality assessment": "qc",
        # Organism / pathogen aliases
        "sars cov 2": "sarscov2",
        "sars-cov-2": "sarscov2",
        "sars cov2": "sarscov2",
        "covid 19": "covid19",
        "covid-19": "covid19",
        "west nile": "westnile",
        "west-nile": "westnile",
        "e coli": "escherichia",
        "e. coli": "escherichia",
        # Tool name normalization
        "kraken 2": "kraken2",
        "kraken-2": "kraken2",
        "iq tree": "iqtree",
        "iq-tree": "iqtree",
        "k snp": "ksnp3",
        "mob suite": "mobsuite",
        "mob-suite": "mobsuite",
        # Domain phrase aliases
        "16 s": "16s",
        "wgs": "wholegenome",
        "whole genome": "wholegenome",
        "whole-genome": "wholegenome",
        "core genome": "coregenome",
        "core-genome": "coregenome",
        "antimicrobial resistance": "amr",
        "antibiotic resistance": "amr",
        "resistance genes": "amr",
        "virulence factors": "virulence",
        "virulence factor": "virulence",
        "sequence typing": "mlst",
        "host depletion": "hostdepl",
        "host removal": "hostdepl",
        "host decontamination": "hostdepl",
        "positive selection": "filtering",
        "minimum spanning tree": "mst",
        "phylogenetic tree": "phylogeny",
        "reference based": "mapping",
        "reference-based": "mapping",
        "coverage depth": "coverage",
    }

    for old, new in bio_replacements.items():
        query_lower = query_lower.replace(old, new)

    # Strip rogue punctuation (keeps alphanumeric, spaces, underscores)
    query_lower = re.sub(r'[^\w\s\_]', ' ', query_lower)
    clean_query = query_lower.strip()

    # Tokenize into a set of whole words
    base_tokens = set(re.findall(r'\b\w+\b', query_lower))
    query_tokens = set()

    # Lightweight morphological expansion (stemming suffixes)
    for t in base_tokens:
        query_tokens.add(t)
        if len(t) > 4:
            if t.endswith('ing'):   query_tokens.add(t[:-3])
            if t.endswith('ed'):    query_tokens.add(t[:-2])
            if t.endswith('ies'):   query_tokens.add(t[:-3] + 'y')
            if t.endswith('ation'): query_tokens.add(t[:-5] + 'e')
            if t.endswith('er'):    query_tokens.add(t[:-2])
            if t.endswith('ment'):  query_tokens.add(t[:-4])
            if t.endswith('ment'):  query_tokens.add(t[:-4] + 'e')
            if t.endswith('ness'):  query_tokens.add(t[:-4])
            if t.endswith('ous'):   query_tokens.add(t[:-3])
            if t.endswith('ive'):   query_tokens.add(t[:-3] + 'e')
        if len(t) > 3 and t.endswith('s') and not t.endswith('ss'): 
            query_tokens.add(t[:-1])

    # Bioinformatics synonym expansion
    # If ANY word in a synonym group appears in the query, ALL related words
    # are injected into the token set to broaden recall.
    bio_synonyms = {
        # Preprocessing
        "trim":         ["trimming", "adapter", "quality", "fastp", "trimmomatic", "chopper", "preprocessing", "clean"],
        "downsample":   ["downsampling", "normalize", "depth", "bbnorm", "subsampling", "coverage"],
        "hostdepl":     ["host", "depletion", "decontamination", "bowtie", "minimap2", "background"],
        "filtering":    ["filter", "positive", "selection", "retain", "enrich", "extract"],
        # Assembly
        "assembly":     ["assemble", "assembler", "denovo", "contigs", "scaffolds", "spades", "shovill", "unicycler", "flye"],
        "hybrid":       ["hybrid", "short", "long", "combined", "unicycler"],
        "consensus":    ["mapping", "alignment", "reference", "bowtie", "minimap2", "medaka", "ivar", "snippy", "polish"],
        "metagenomics": ["metagenomic", "metaspades", "microbiome", "community", "environmental"],
        # Taxonomy
        "taxonomy":     ["classify", "classification", "taxa", "species", "kraken", "kraken2", "centrifuge", "bracken", "taxonomic"],
        "species":      ["identification", "predict", "kmerfinder", "mash", "organism"],
        # Typing & Epidemiology
        "mlst":         ["typing", "sequence", "clonal", "complex", "pubmlst", "epidemiology"],
        "cgmlst":       ["chewbbaca", "allele", "allelic", "wgmlst", "coregenome", "profile"],
        "lineage":      ["pangolin", "sarscov2", "covid19", "westnile", "pango", "variant"],
        # AMR & Virulence
        "amr":          ["resistance", "antimicrobial", "antibiotic", "resfinder", "staramr", "abricate"],
        "virulence":    ["vfdb", "pathogenicity", "virulence", "abricate"],
        # Annotation
        "annotation":   ["annotate", "prokka", "gene", "protein", "gff", "genbank", "functional"],
        # Phylogeny & Clustering
        "phylogeny":    ["tree", "clustering", "mst", "distance", "newick", "augur", "nextstrain", "reportree", "grapetree", "iqtree", "phylogenetic"],
        "snp":          ["variant", "snv", "calling", "vcf", "mutation", "snippy", "cfsan"],
        "pangenome":    ["panaroo", "core", "gene", "presence", "absence", "mafft"],
        # Quality Control
        "qc":           ["fastqc", "nanoplot", "quast", "quality", "check", "report", "n50", "statistics"],
        # Variant calling
        "variant":      ["snp", "snv", "calling", "vcf", "mutation", "ivar", "snippy"],
        # Plasmid
        "plasmid":      ["plasmidspades", "mobsuite", "mob_recon", "replicon", "extrachromosomal", "mobile"],
        # Contamination
        "contamination":["confindr", "purity", "mixed", "strain", "intra"],
    }
    
    for base, syns in bio_synonyms.items():
        if base in query_tokens or any(s in query_tokens for s in syns):
            query_tokens.add(base)
            query_tokens.update(syns)

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 1 — DISCOVERY / SUGGESTION INTENT DETECTION
    # ══════════════════════════════════════════════════════════════════════════
    is_discovery = False
    
    # Very short generic queries (under 15 chars) are almost always exploratory
    if len(clean_query) < 15 and clean_query != "":
        is_discovery = True
        
    # Conversational phrases that signal catalog browsing
    discovery_phrases = [
        "what can i", "what can you do", "what do you have", "what do we have",
        "what tools", "which tools", "what pipelines", "what modules",
        "what's supported", "what is supported", "available options",
        "available tools", "available pipelines", "system capabilities",
        "show me everything", "list everything", "what components",
        "what steps", "tell me about", "what analyses", "what is available",
        "supported analyses", "supported workflows", "what workflows",
        "give me an overview", "show all", "list all",
        "capabilities", "functionality", "feature list",
    ]
    if not is_discovery and any(p in clean_query for p in discovery_phrases):
        is_discovery = True
        
    # Action + target noun combinations (e.g. "suggest tools", "show pipelines")
    action_words = [
        "suggest", "list", "show", "recommend", "overview", "catalog",
        "options", "give", "help", "describe", "what", "display", "browse",
        "explore", "summarize", "enumerate",
    ]
    target_nouns = [
        "tool", "tools", "pipeline", "pipelines", "module", "modules",
        "capability", "capabilities", "system", "component", "components",
        "step", "steps", "workflow", "workflows", "analysis", "analyses",
    ]
    
    if not is_discovery:
        has_action = any(re.search(rf'\b{v}\b', clean_query) for v in action_words)
        has_target = any(re.search(rf'\b{n}\b', clean_query) for n in target_nouns)
        if has_action and has_target:
            is_discovery = True

    # Inject capability map when the user is exploring the catalog
    if is_discovery:
        try:
            suggestion_block = "### SYSTEM CATALOG OVERVIEW (For user suggestions)\n"
            suggestion_block += "The user's query implies they want to know what is available. Use this capabilities map.\n\n"
            suggestion_block += "**Available Pipelines (Templates):**\n"
            
            for tmpl in store.search(("templates",)):
                t_data = tmpl.value
                t_name = t_data.get('template_name', tmpl.key)
                outputs = t_data.get('outputs', [])
                out_str = f" *(Generates: {', '.join(outputs)})*" if outputs else ""
                suggestion_block += f"- **{t_name}**{out_str}\n"
            
            domain_groups = defaultdict(list)
            for comp in store.search(("components",)):
                c_data = comp.value
                tool_name = c_data.get("tool")
                domain = c_data.get("domain", "Other")
                
                if tool_name and str(tool_name).strip() and str(tool_name).lower() != "none":
                    domain_groups[domain].append(str(tool_name).strip())
            
            if domain_groups:
                suggestion_block += "\n**Supported Individual Tools (Grouped by Domain):**\n"
                for domain_name, tools_list in sorted(domain_groups.items()):
                    unique_tools = sorted(list(set(tools_list)))
                    suggestion_block += f"- *{domain_name}*: {', '.join(unique_tools)}\n"
            
            context_blocks.append(suggestion_block)
        except Exception as e:
            print(f"Catalog suggestion error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 2 — HYBRID KEYWORD & METADATA SEARCH
    # ══════════════════════════════════════════════════════════════════════════

    # Low-value tokens that should not drive scoring on their own
    ignore_words = {
        'step', 'mapping', 'module', 'genes', 'denovo', 'assembly', 'tool',
        'pipeline', 'workflow', 'build', 'create', 'make', 'run', 'using',
        'file', 'data', 'reads', 'fastq', 'fasta', 'generate', 'process',
        'custom', 'script', 'and', 'plus', 'with',
    }

    # ── Template Scan ──────────────────────────────────────────────────────
    try:
        template_scores = {}
        for tmpl in store.search(("templates",)):
            tmpl_id = tmpl.key.lower()
            tmpl_data = tmpl.value
            score = 0
            
            clean_id_words = tmpl_id.replace("module_", "").replace("_", " ").split()
            for id_word in clean_id_words:
                if len(id_word) > 3 and id_word in query_tokens and id_word not in ignore_words:
                    score += 8
                
            for kw in tmpl_data.get('keywords', []):
                if str(kw).lower() in query_tokens:
                    score += 5
            
            for st in tmpl_data.get('compatible_seq_types', []):
                if str(st).lower().replace('_', ' ') in query_lower:
                    score += 3
            
            if score > 0:
                template_scores[tmpl.key] = score
                
        sorted_tmpls = [k for k, v in sorted(template_scores.items(), key=lambda x: x[1], reverse=True) if v >= settings.RAG_KEYWORD_TEMPLATE_MIN_SCORE and k not in EXCLUDED_TEMPLATES][:settings.RAG_MAX_KEYWORD_TEMPLATES]
        for tmpl_key in sorted_tmpls:
            if tmpl_key not in found_ids:
                context_blocks.append(f"### PIPELINE BLUEPRINT: {tmpl_key}")
                _inject_template(tmpl_key, found_ids, context_blocks, store, embed_code=True)
                
                tmpl_data = store.get(("templates",), tmpl_key).value
                for flow_step in tmpl_data.get('logic_flow', []):
                    if 'step' in flow_step: 
                        _inject_component(flow_step['step'], found_ids, context_blocks, store, embed_code)
                    for sub_key in ['parallel_execution', 'branches', 'options']:
                        if sub_key in flow_step:
                            for item in flow_step[sub_key]:
                                if 'step' in item:
                                    _inject_component(item['step'], found_ids, context_blocks, store, embed_code)
                                if 'next' in item:
                                    for sub_item in item['next']:
                                        if 'step' in sub_item:
                                            _inject_component(sub_item['step'], found_ids, context_blocks, store, embed_code)
    except Exception as e:
        print(f"Template search error: {e}")

    # ── Component Scan ─────────────────────────────────────────────────────
    try:
        component_scores = {}
        for comp in store.search(("components",)):
            comp_id = comp.key.lower()
            comp_data = comp.value
            score = 0
            
            tool_name = str(comp_data.get('tool', '')).lower()
            domain_name = str(comp_data.get('domain', '')).lower()
            
            # High weight for exact tool-name or ID-suffix matches
            if '__' in comp_id:
                suffix = comp_id.split('__')[-1]
                for sw in suffix.split('_'):
                    if sw and len(sw) > 3 and sw in query_tokens and sw not in ignore_words:
                        score += 50 

            if tool_name:
                for word in re.split(r'[^a-z0-9]', tool_name):
                    if len(word) > 3 and word in query_tokens and word not in ignore_words:
                        score += 50 
                        
            if domain_name:
                for part in re.split(r'[^a-z0-9]', domain_name):
                    if len(part) > 3 and part in query_tokens and part not in ignore_words:
                        score += 5

            for st in comp_data.get('compatible_seq_types', []):
                for st_word in str(st).lower().replace('_', ' ').split():
                    if st_word and len(st_word) > 3 and st_word in query_tokens:
                        score += 5
                        
            # Lower weight for I/O types to avoid flooding
            io_combined = comp_data.get('input_types', []) + comp_data.get('out', [])
            for io_val in io_combined:
                for io_word in str(io_val).lower().replace('_', ' ').split():
                    if len(io_word) > 3 and io_word in query_tokens and io_word not in ignore_words:
                        score += 2 
            
            for param in comp_data.get('params', []):
                clean_param = str(param).lower().replace('-', '')
                if len(clean_param) > 3 and clean_param in query_tokens and clean_param not in ignore_words:
                    score += 1

            # Structural keyword boosting for domain-specific terms
            structural_keywords = [
                'lineage', 'denovo', 'trimming', 'mapping', 'qc', 'clustering',
                'class', 'hostdepl', 'filtering', 'typing', 'annotation',
                'pangenome', 'phylogeny', 'metagenomics', 'amr', 'plasmid',
                'polishing', 'consensus', 'alignment', 'surveillance',
            ]
            for kw in structural_keywords:
                if kw in query_tokens and kw in comp_id:
                    score += 15
                    
            if score > 0:
                component_scores[comp.key] = score
                
        # Dynamic thresholding: keep only components scoring ≥20% of top match
        if component_scores:
            max_score = max(component_scores.values())
            threshold = max_score * settings.RAG_KEYWORD_COMPONENT_THRESHOLD

            smart_comps = [k for k, v in sorted(component_scores.items(), key=lambda x: x[1], reverse=True) if v >= threshold][:settings.RAG_MAX_KEYWORD_COMPONENTS]
            for comp_key in smart_comps:
                if comp_key not in found_ids:
                    _inject_component(comp_key, found_ids, context_blocks, store, embed_code)
            
    except Exception as e:
        print(f"Component search error: {e}")

    # ── Resource (Helper Function) Scan ────────────────────────────────────
    try:
        res_item = store.get(("resources",), "helper_functions")
        if res_item and isinstance(res_item.value, dict):
            helpers = res_item.value.get("list", [])
            resource_scores = {}
            for i, helper in enumerate(helpers):
                h_name = str(helper.get("name", "")).lower()
                h_score = 0
                
                if h_name and h_name in query_tokens:
                    h_score += 10
                
                h_words = set(re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', helper.get("name", "")))
                h_words = {w.lower() for w in h_words if len(w) > 3 and w.lower() not in ignore_words}
                for w in h_words:
                    if w in query_tokens:
                        h_score += 5
                
                if h_score > 0:
                    resource_scores[i] = (h_score, helper)
            
            top_resources = sorted(resource_scores.values(), key=lambda x: x[0], reverse=True)[:settings.RAG_MAX_HELPER_FUNCTIONS]
            for _, helper in top_resources:
                if helper.get('name') not in found_ids:
                    context_blocks.append(
                        f"### GROOVY HELPER FUNCTION: {helper.get('name')}\n"
                        f"DESCRIPTION: {helper.get('description')}\n"
                        f"USAGE: `{helper.get('usage')}`\n"
                        f"DEFINED IN: {helper.get('path')}\n"
                    )
                    found_ids.add(helper.get('name'))
    except Exception as e:
        print(f"Resource search error: {e}")

    # ── Container Scan ─────────────────────────────────────────────────────
    try:
        containers_item = store.get(("resources",), "containers")
        if containers_item and isinstance(containers_item.value, dict):
            containers = containers_item.value.get("list", [])
            matched_containers = []
            for container in containers:
                c_name = str(container.get("name", "")).lower()
                if c_name and len(c_name) > 2 and c_name in query_tokens:
                    matched_containers.append(container)
            
            if matched_containers:
                container_block = "### DOCKER CONTAINER REGISTRY LOOKUP\n"
                for c in matched_containers:
                    container_block += f"- **{c.get('name')}**: `{c.get('url')}`\n"
                context_blocks.append(container_block)
    except Exception as e:
        print(f"Container search error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 3 — SEMANTIC SEARCH (FAISS)
    # ══════════════════════════════════════════════════════════════════════════
    try:
        # Strip conversational filler that dilutes the vector embedding
        filler_pattern = (
            r'\b(please|help|need|want|looking|build|design|create|make|'
            r'pipeline|bioinformatic|bioinformatics|that|performs|does|can|you|'
            r'would|like|could|should|also|just|really|actually|basically|'
            r'i|me|my|give|write|develop|implement|set up|configure)\b'
        )
        dense_query = re.sub(filler_pattern, '', clean_query)
        
        # Inject expanded synonym tokens to anchor the embedding
        expanded_terms = [w for w in query_tokens if w not in dense_query]
        semantic_query = (dense_query + " " + " ".join(expanded_terms)).strip()
        
        # Fallback if stripping removed everything meaningful
        if len(semantic_query.replace(" ", "")) < 3:
            semantic_query = clean_query
            
        docs_and_scores = data_loader.vector_store.similarity_search_with_score(semantic_query, k=settings.RAG_FAISS_K)
        
        # Relative distance cutoffs: drop results that are too far from the best match
        if docs_and_scores:
            best_l2 = docs_and_scores[0][1]
            
            for doc, l2_dist in docs_and_scores:
                if l2_dist > settings.RAG_FAISS_MAX_L2_DISTANCE or l2_dist > (best_l2 + settings.RAG_FAISS_RELATIVE_MARGIN):
                    continue
                    
                meta = doc.metadata
                item_id = meta.get('id')
                item_type = meta.get('type')

                if item_id in found_ids or item_id in EXCLUDED_TEMPLATES:
                    continue

                if item_type == 'template':
                    tmpl_item = store.get(("templates",), item_id)
                    if tmpl_item:
                        tmpl = tmpl_item.value
                        context_blocks.append(f"### PIPELINE BLUEPRINT (Semantic Match): {item_id}\n{doc.page_content}")
                        _inject_template(tmpl['id'], found_ids, context_blocks, store, embed_code=True)
                        found_ids.add(item_id)

                        for flow_step in tmpl.get('logic_flow', []):
                            if 'step' in flow_step:
                                _inject_component(flow_step['step'], found_ids, context_blocks, store, embed_code)
                            for sub_key in ['parallel_execution', 'branches', 'options']:
                                if sub_key in flow_step:
                                    for item in flow_step[sub_key]:
                                        if 'step' in item:
                                            _inject_component(item['step'], found_ids, context_blocks, store, embed_code)
                                        if 'next' in item:
                                            for sub_item in item['next']:
                                                if 'step' in sub_item:
                                                    _inject_component(sub_item['step'], found_ids, context_blocks, store, embed_code)

                elif item_type == 'component':
                    _inject_component(item_id, found_ids, context_blocks, store, embed_code)

    except Exception as e:
        print(f"FAISS search error: {e}")

    final_context = "\n".join(context_blocks) + "\n\n"
    return final_context