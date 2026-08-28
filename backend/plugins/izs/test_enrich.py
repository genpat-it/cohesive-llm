import json
import sys
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# Add project root to sys path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.services.llm import get_llm
from langchain_core.prompts import PromptTemplate

class EnrichmentOutput(BaseModel):
    domain: str = Field(description="A short 1-3 word category string representing the domain of the tool (e.g. 'Quality Control', 'Annotation & AMR', 'Assembly').")
    description: str = Field(description="A comprehensive 1-2 sentence description of what the process does, what inputs it takes, what algorithms it uses, and what it produces.")
    keywords: list[str] = Field(description="A list of 5-10 related keywords, acronyms, and synonyms that would help a semantic search engine find this tool.")

def test_enrich(component_id: str):
    # Load current catalog
    base_dir = Path(__file__).parent
    
    with open(base_dir / "catalog" / "components.json") as f:
        catalog = json.load(f)
        current_comp = next((c for c in catalog["components"] if c["id"] == component_id), None)
        
    with open(base_dir / "catalog" / "templates.json") as f:
        templates = json.load(f)["templates"]
        
    # Load raw code
    raw_code = ""
    with open(base_dir / "code_store.jsonl") as f:
        for line in f:
            data = json.loads(line)
            if data["id"] == component_id:
                raw_code = data["content"]
                break
                
    if not raw_code:
        print(f"Code for {component_id} not found!")
        return

    # Find usage
    usage_examples = []
    for tmpl in templates:
        if component_id in tmpl.get("components_used", []):
            usage_examples.append(f"Used in template '{tmpl['id']}':\n{tmpl['description']}")
            
    usage_text = "\n\n".join(usage_examples) if usage_examples else "No known template usage."

    print(f"============================================================")
    print(f"🎯 TESTING ENRICHMENT FOR: {component_id}")
    print(f"============================================================")
    print(f"📋 CURRENT DOMAIN: {current_comp['domain']}")
    print(f"📋 CURRENT DESC:   {current_comp['description']}")
    print(f"📋 CURRENT KWORDS: {current_comp.get('keywords', [])}")
    print(f"------------------------------------------------------------")
    print(f"🤖 LLM IS THINKING...")
    
    prompt = PromptTemplate.from_template("""
You are a bioinformatics expert. Your job is to analyze a Nextflow process component and generate rich metadata for a tool catalog.

# COMPONENT ID
{component_id}

# RAW NEXTFLOW CODE
```nextflow
{raw_code}
```

# WORKFLOW USAGE EXAMPLES
This shows how other templates in the system call this component:
{usage_text}

Analyze the process and return a JSON object with:
1. `domain`: A short 1-3 word category string (e.g. 'Quality Control', 'Annotation & AMR').
2. `description`: A highly concise, information-dense 1-sentence summary covering the tool's core function, compatible sequencers, key parameters/filters, and output data types. Avoid fluff. Be extremely brief.
3. `keywords`: A list of 5-10 related keywords, acronyms, and synonyms that would help a semantic search engine find this tool.
""")
    
    llm = get_llm().with_structured_output(EnrichmentOutput)
    chain = prompt | llm
    
    result = chain.invoke({
        "component_id": component_id,
        "raw_code": raw_code[:3000], # truncate just in case
        "usage_text": usage_text
    })
    
    print(f"\n✨ NEW DOMAIN: {result.domain}")
    print(f"✨ NEW DESC:   {result.description}")
    print(f"✨ NEW KWORDS: {result.keywords}")
    print(f"============================================================")

if __name__ == "__main__":
    test_enrich("step_4AN_AMR__staramr")
    test_enrich("step_1PP_trimming__fastp")
    test_enrich("step_2AS_denovo__spades")
