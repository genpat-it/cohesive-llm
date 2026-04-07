#!/usr/bin/env python3
"""
LLM Pipeline Generator Evaluation Script

This script evaluates the LLM by testing Nextflow pipeline generation:
1. Sends test prompts to the /chat API
2. Extracts the generated Nextflow code
3. Validates syntax and semantic correctness
4. Checks that ONLY framework-valid components are used
5. Generates a detailed report

Usage:
    python evaluate_llm.py [--api-url URL] [--output report.md]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# ============================================
# TOOL VALIDI - Lista ESATTA dal framework
# ============================================

VALID_STEPS = {
    "step_0SQ_rawreads__fastq",
    "step_1PP_downsampling__bbnorm",
    "step_1PP_filtering__bowtie",
    "step_1PP_filtering__krakentools",
    "step_1PP_filtering__minimap2",
    "step_1PP_generated__fasta2fastq",
    "step_1PP_hostdepl__bowtie",
    "step_1PP_hostdepl__minimap2",
    "step_1PP_trimming__chopper",
    "step_1PP_trimming__fastp",
    "step_1PP_trimming__trimmomatic",
    "step_2AS_denovo__flye",
    "step_2AS_denovo__plasmidspades",
    "step_2AS_denovo__shovill",
    "step_2AS_denovo__spades",
    "step_2AS_denovo__unicycler",
    "step_2AS_filtering__seqio",
    "step_2AS_hybrid__unicycler",
    "step_2AS_mapping__bowtie",
    "step_2AS_mapping__ivar",
    "step_2AS_mapping__medaka",
    "step_2AS_mapping__minimap2",
    "step_2AS_mapping__snippy",
    "step_2MG_denovo__metaspades",
    "step_3TX_class__centrifuge",
    "step_3TX_class__confindr",
    "step_3TX_class__kraken",
    "step_3TX_class__kraken2",
    "step_3TX_species__kmerfinder",
    "step_3TX_species__mash",
    "step_3TX_species__vdabricate",
    "step_4AN_AMR__abricate",
    "step_4AN_AMR__filtering",
    "step_4AN_AMR__resfinder",
    "step_4AN_AMR__staramr",
    "step_4AN_genes__prokka",
    "step_4TY_cgMLST__chewbbaca",
    "step_4TY_flaA__flaA",
    "step_4TY_lineage__pangolin",
    "step_4TY_lineage__westnile",
    "step_4TY_MLST__mlst",
    "step_4TY_plasmid__mobsuite",
}

VALID_MODULES = {
    "module_augur",
    "module_cfsan",
    "module_covid_emergency",
    "module_denovo",
    "module_draft_genome",
    "module_enterotoxin_saureus_finder",
    "module_filtered_denovo",
    "module_grapetree",
    "module_ksnp3",
    "module_ngsmanager",
    "module_panaroo",
    "module_plasmids",
    "module_qc_fastqc",
    "module_qc_nanoplot",
    "module_qc_quast",
    "module_reads_processing",
    "module_reportree",
    "module_scaffolds_filtering",
    "module_segmented",
    "module_snippycore",
    "module_surveillance",
    "module_typing_bacteria",
    "module_vcf2mst",
    "module_vdraft_light",
    "module_vdraft",
    "module_westnile",
    "module_wgs_bacteria",
}

ALL_VALID_COMPONENTS = VALID_STEPS | VALID_MODULES

# Helper functions that are valid (not hallucinations)
VALID_HELPERS = {
    "getSingleInput", "getReference", "getReferenceOptional", "getReferences",
    "getHost", "getTrimmedReads", "getAssembly", "extractKey", "extractDsRef",
    "isIlluminaPaired", "isIonTorrent", "isNanopore", "isCompatibleWithSeqType",
    "getReferenceForLineage", "getEmpty",
}

# ============================================
# TEST PROMPTS - Difficolta crescente
# ============================================

TEST_PROMPTS = [
    # === LEVEL 1: Simple (single step) ===
    {
        "id": "L1_01_trimming",
        "level": 1,
        "description": "Simple trimming with fastp",
        "prompt": "I want to trim my Illumina paired-end reads using fastp",
        "expected_components": ["step_1PP_trimming__fastp"],
        "expected_template": None,
    },
    {
        "id": "L1_02_assembly",
        "level": 1,
        "description": "De novo assembly with SPAdes",
        "prompt": "I have trimmed reads and want to do de novo assembly with SPAdes",
        "expected_components": ["step_2AS_denovo__spades"],
        "expected_template": None,
    },
    {
        "id": "L1_03_qc",
        "level": 1,
        "description": "Quality control with FastQC",
        "prompt": "I want to check the quality of my reads with FastQC",
        "expected_components": ["module_qc_fastqc"],
        "expected_template": "module_qc_fastqc",
    },

    # === LEVEL 2: Medium (existing template) ===
    {
        "id": "L2_01_covid",
        "level": 2,
        "description": "Complete COVID pipeline",
        "prompt": "I have SARS-CoV-2 samples and want to do mapping and lineage assignment",
        "expected_components": ["step_2AS_mapping__ivar", "step_4TY_lineage__pangolin"],
        "expected_template": "module_covid_emergency",
    },
    {
        "id": "L2_02_denovo_depletion",
        "level": 2,
        "description": "De novo with host depletion",
        "prompt": "I want to do de novo assembly but first remove human host reads",
        "expected_components": ["step_1PP_hostdepl__bowtie", "step_2AS_denovo__spades"],
        "expected_template": "module_denovo",
    },
    {
        "id": "L2_03_westnile",
        "level": 2,
        "description": "West Nile Virus pipeline",
        "prompt": "I have West Nile Virus samples and want to determine lineage and do mapping",
        "expected_components": ["step_4TY_lineage__westnile", "step_2AS_mapping__ivar"],
        "expected_template": "module_westnile",
    },

    # === LEVEL 3: Complex (multi-step custom) ===
    {
        "id": "L3_01_bacteria_typing",
        "level": 3,
        "description": "Complete bacterial typing",
        "prompt": "I have bacterial Illumina isolates. I want to identify species, do MLST, and find resistance genes",
        "expected_components": ["step_3TX_species__kmerfinder", "step_4TY_MLST__mlst", "step_4AN_AMR__staramr"],
        "expected_template": "module_typing_bacteria",
    },
    {
        "id": "L3_02_viral_annotation",
        "level": 3,
        "description": "Viral reconstruction and annotation",
        "prompt": "I want to reconstruct a viral genome: reference mapping, consensus, coverage analysis and Prokka annotation",
        "expected_components": ["step_2AS_mapping__bowtie", "step_2AS_mapping__ivar", "step_4AN_genes__prokka"],
        "expected_template": "module_draft_genome",
    },
    {
        "id": "L3_03_custom_filtering",
        "level": 3,
        "description": "Positive filtering + assembly",
        "prompt": "I have a mixed clinical sample. I want to extract only reads that map to a specific reference and then assemble only those",
        "expected_components": ["step_1PP_filtering__bowtie", "step_2AS_denovo__spades"],
        "expected_template": "module_filtered_denovo",
    },

    # === LEVEL 4: NEGATIVE TESTS - IMPOSSIBLE requests ===
    # The LLM MUST reject or signal that the tool doesn't exist
    {
        "id": "L4_01_tool_not_exists",
        "level": 4,
        "description": "Non-existent tool (BWA)",
        "prompt": "I want to do mapping with BWA",
        "expected_components": [],  # MUST reject
        "expected_template": None,
        "expect_rejection": True,
        "rejection_reason": "BWA is not in the framework, only bowtie/minimap2/ivar/snippy",
    },
    {
        "id": "L4_02_tool_not_exists_canu",
        "level": 4,
        "description": "Non-existent assembler (Canu)",
        "prompt": "I want to assemble my nanopore long reads with Canu",
        "expected_components": [],
        "expected_template": None,
        "expect_rejection": True,
        "rejection_reason": "Canu doesn't exist, only Flye/Medaka for nanopore",
    },
    {
        "id": "L4_03_impossible_lineage",
        "level": 4,
        "description": "Lineage on bacteria (Pangolin is COVID only)",
        "prompt": "I have Salmonella samples and want to determine lineage with Pangolin",
        "expected_components": [],
        "expected_template": None,
        "expect_rejection": True,
        "rejection_reason": "Pangolin is ONLY for SARS-CoV-2, not bacteria",
    },
    {
        "id": "L4_04_wrong_combination",
        "level": 4,
        "description": "Impossible combination",
        "prompt": "I want to do de novo assembly with iVar",
        "expected_components": [],
        "expected_template": None,
        "expect_rejection": True,
        "rejection_reason": "iVar is for mapping/consensus, not de novo assembly",
    },
]


@dataclass
class TestResult:
    """Result of a single test"""
    test_id: str
    level: int
    description: str
    prompt: str

    # Risultati API
    api_success: bool = False
    api_error: Optional[str] = None
    api_status: Optional[str] = None
    api_response_time: float = 0.0
    api_reply: Optional[str] = None  # Risposta testuale dell'LLM

    # Generated code
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None

    # Validazione sintattica
    syntax_valid: bool = False
    syntax_errors: list = field(default_factory=list)

    # Validazione semantica
    expected_components: list = field(default_factory=list)
    found_components: list = field(default_factory=list)
    missing_components: list = field(default_factory=list)
    hallucinated_components: list = field(default_factory=list)  # Tool INVENTATI
    invalid_components: list = field(default_factory=list)  # Tool non nel framework

    expected_template: Optional[str] = None
    used_template: Optional[str] = None
    template_match: bool = False

    # Test negativi (richieste impossibili)
    expect_rejection: bool = False
    rejection_reason: Optional[str] = None
    correctly_rejected: bool = False

    # Score finale
    score: float = 0.0
    notes: list = field(default_factory=list)


class LLMEvaluator:
    """Class to evaluate the LLM"""

    def __init__(self, api_url: str, ngsmanager_dir: str, nextflow_path: str = None):
        self.api_url = api_url.rstrip('/')
        self.ngsmanager_dir = Path(ngsmanager_dir)
        self.nextflow_path = nextflow_path or "nextflow"
        self.session_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.results: list[TestResult] = []

        # Load valid components from framework
        self.valid_components = self._load_valid_components()

    def _load_valid_components(self) -> set:
        """Load list of valid steps/modules from the framework"""
        components = set()

        # Steps
        steps_dir = self.ngsmanager_dir / "steps"
        if steps_dir.exists():
            for f in steps_dir.glob("*.nf"):
                components.add(f.stem)

        # Modules
        modules_dir = self.ngsmanager_dir / "modules"
        if modules_dir.exists():
            for f in modules_dir.glob("*.nf"):
                components.add(f.stem)

        return components

    def check_api_health(self) -> bool:
        """Check if the API is reachable"""
        try:
            resp = requests.get(f"{self.api_url}/health", timeout=10)
            return resp.status_code == 200
        except Exception as e:
            print(f"API non raggiungibile: {e}")
            return False

    def send_prompt(self, prompt: str, max_turns: int = 5, expect_rejection: bool = False) -> dict:
        """
        Send a prompt to the LLM and handle conversation until APPROVED.
        For rejection tests (expect_rejection=True), returns after first response.
        """
        session_id = f"{self.session_id}_{int(time.time())}"
        messages = []
        last_response = None

        current_message = prompt

        for turn in range(max_turns):
            try:
                start_time = time.time()
                resp = requests.post(
                    f"{self.api_url}/chat",
                    json={"session_id": session_id, "message": current_message},
                    timeout=120
                )
                elapsed = time.time() - start_time

                if resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status_code}: {resp.text}",
                        "elapsed": elapsed
                    }

                data = resp.json()
                messages.append({"role": "user", "content": current_message})
                messages.append({"role": "assistant", "content": data.get("reply", "")})

                status = data.get("status", "CHATTING")
                last_response = data

                # For rejection tests, return after first response (we expect CHATTING)
                if expect_rejection:
                    return {
                        "success": True,  # API call succeeded
                        "status": status,
                        "reply": data.get("reply"),
                        "nextflow_code": data.get("nextflow_code"),
                        "mermaid_code": data.get("mermaid_code"),
                        "ast_json": data.get("ast_json"),
                        "elapsed": elapsed,
                        "turns": 1
                    }

                if status == "APPROVED":
                    return {
                        "success": True,
                        "status": status,
                        "reply": data.get("reply"),
                        "nextflow_code": data.get("nextflow_code"),
                        "mermaid_code": data.get("mermaid_code"),
                        "ast_json": data.get("ast_json"),
                        "elapsed": elapsed,
                        "turns": turn + 1
                    }

                # If still CHATTING, approve
                current_message = "Yes, approve it. Proceed with exactly what you suggested."

            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "elapsed": 0
                }

        # Return last response even if not approved
        return {
            "success": False,
            "error": f"Non approvato dopo {max_turns} turni",
            "reply": last_response.get("reply") if last_response else "",
            "status": last_response.get("status") if last_response else "",
            "elapsed": 0
        }

    def validate_syntax(self, code: str) -> tuple[bool, list]:
        """
        Validate Nextflow code syntax.
        Returns (is_valid, errors)
        """
        if not code:
            return False, ["No code generated"]

        errors = []

        # Basic Groovy/Nextflow syntax checks
        # Bracket balance
        if code.count('{') != code.count('}'):
            errors.append(f"Unbalanced curly braces: {{ = {code.count('{')}, }} = {code.count('}')}")

        if code.count('(') != code.count(')'):
            errors.append(f"Unbalanced parentheses: ( = {code.count('(')}, ) = {code.count(')')}")

        # Common error patterns
        if re.search(r'workflow\s*{[^}]*workflow\s*{', code):
            errors.append("Nested workflows (not allowed)")

        if "getSingleInput()" in code and "workflow {" not in code:
            errors.append("getSingleInput() outside entrypoint")

        # Try validation with nextflow if available
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.nf', delete=False) as f:
                # Add DSL2 header if missing
                if 'nextflow.enable.dsl=2' not in code:
                    f.write('nextflow.enable.dsl=2\n\n')
                f.write(code)
                f.flush()
                temp_file = f.name

            # Run nextflow with -preview to validate syntax
            result = subprocess.run(
                [self.nextflow_path, 'run', temp_file, '-preview'],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.ngsmanager_dir)
            )

            if result.returncode != 0:
                # Extract meaningful errors
                stderr = result.stderr
                if 'Error' in stderr or 'error' in stderr:
                    for line in stderr.split('\n'):
                        if 'error' in line.lower() or 'Error' in line:
                            errors.append(line.strip()[:200])

            os.unlink(temp_file)

        except FileNotFoundError:
            pass  # Nextflow not available, use static checks only
        except subprocess.TimeoutExpired:
            errors.append("Nextflow validation timeout")
        except Exception as e:
            pass  # Ignore other errors

        return len(errors) == 0, errors

    def extract_components(self, code: str) -> list:
        """Extract components used in the code (steps and modules only, not helpers)"""
        if not code:
            return []

        components = set()

        # Pattern for include statements
        includes = re.findall(r"include\s*{\s*(\w+)\s*}", code)
        for inc in includes:
            # Only add step_* and module_* (not helper functions)
            if inc.startswith('step_') or inc.startswith('module_'):
                components.add(inc)

        # Pattern for direct calls to step_* and module_*
        calls = re.findall(r'\b(step_\w+|module_\w+)\s*\(', code)
        components.update(calls)

        return list(components)

    def run_test(self, test: dict) -> TestResult:
        """Execute a single test"""
        result = TestResult(
            test_id=test["id"],
            level=test["level"],
            description=test["description"],
            prompt=test["prompt"],
            expected_components=test.get("expected_components", []),
            expected_template=test.get("expected_template"),
            expect_rejection=test.get("expect_rejection", False),
            rejection_reason=test.get("rejection_reason"),
        )

        print(f"\n{'='*60}")
        print(f"Test: {test['id']} (Level {test['level']})")
        if result.expect_rejection:
            print(f"[TEST NEGATIVO - L'LLM DEVE rifiutare]")
        print(f"Prompt: {test['prompt'][:80]}...")
        print(f"{'='*60}")

        # Send prompt
        api_result = self.send_prompt(test["prompt"], expect_rejection=result.expect_rejection)

        result.api_success = api_result.get("success", False)
        result.api_error = api_result.get("error")
        result.api_status = api_result.get("status")
        result.api_response_time = api_result.get("elapsed", 0)
        result.api_reply = api_result.get("reply", "")

        # Per test negativi, check se l'LLM ha rifiutato
        if result.expect_rejection:
            # L'LLM dovrebbe NON generare codice e spiegare perche'
            has_code = bool(api_result.get("nextflow_code"))
            reply_lower = result.api_reply.lower() if result.api_reply else ""

            # Check se ha menzionato che il tool non esiste
            rejection_keywords = [
                # English
                "do not have", "don't have", "not available", "doesn't exist",
                "does not exist", "cannot", "can't", "not supported", "only for",
                "is not", "are not", "no tool", "not in my", "instead",
                "alternative", "i can offer", "i can suggest",
                # Italian (legacy)
                "non disponibile", "non esiste", "non supportato", "non ho",
                "non posso", "non e' possibile", "non abbiamo", "solo per"
            ]
            mentioned_rejection = any(kw in reply_lower for kw in rejection_keywords)

            result.correctly_rejected = not has_code and mentioned_rejection

            if result.correctly_rejected:
                print(f"  [OK] Correctly rejected (no code generated)")
                result.score = 100
            elif not has_code:
                print(f"  [WARN] No code but didn't explain why")
                result.score = 50
            else:
                print(f"  [FAIL] Generated code for impossible request!")
                result.score = 0
                result.notes.append("Generated code for non-existent tool/combination")

            return result

        # Test positivo normale
        if not result.api_success:
            print(f"  [FAIL] API Error: {result.api_error}")
            result.notes.append(f"API failed: {result.api_error}")
            return result

        print(f"  [OK] API responded in {result.api_response_time:.1f}s")

        # Estrai codice
        result.nextflow_code = api_result.get("nextflow_code")
        result.mermaid_code = api_result.get("mermaid_code")

        if not result.nextflow_code:
            print(f"  [WARN] No Nextflow code generated")
            result.notes.append("No code generated")
        else:
            print(f"  [OK] Code generated ({len(result.nextflow_code)} chars)")

            # Validate syntax
            result.syntax_valid, result.syntax_errors = self.validate_syntax(result.nextflow_code)
            if result.syntax_valid:
                print(f"  [OK] Syntax valid")
            else:
                print(f"  [FAIL] Syntax errors: {result.syntax_errors[:2]}")

            # Extract components
            result.found_components = self.extract_components(result.nextflow_code)
            print(f"  [INFO] Components found: {result.found_components}")

            # CRITICAL CHECK: All components must be in the framework
            result.invalid_components = [
                c for c in result.found_components
                if c not in ALL_VALID_COMPONENTS
            ]

            if result.invalid_components:
                print(f"  [CRITICAL] Components NOT IN FRAMEWORK: {result.invalid_components}")
                result.notes.append(f"HALLUCINATION: {result.invalid_components}")

            # Compare with expected
            expected_set = set(result.expected_components)
            found_set = set(result.found_components)

            result.missing_components = list(expected_set - found_set)
            # Hallucinated = found but not expected AND not valid in framework
            result.hallucinated_components = result.invalid_components

            if result.missing_components:
                print(f"  [WARN] Missing components: {result.missing_components}")

        # Calculate score
        result.score = self._calculate_score(result)
        print(f"  [SCORE] {result.score:.0f}/100")

        return result

    def _calculate_score(self, result: TestResult) -> float:
        """Calculate test score (0-100)"""
        score = 0.0

        # API success: 20 points
        if result.api_success:
            score += 20

        # Code generated: 20 points
        if result.nextflow_code:
            score += 20

        # Syntax valid: 20 points
        if result.syntax_valid:
            score += 20

        # Correct components: 30 points
        if result.expected_components:
            found_expected = len(set(result.found_components) & set(result.expected_components))
            total_expected = len(result.expected_components)
            score += 30 * (found_expected / total_expected)
        else:
            score += 30  # No specific components required

        # Penalty for hallucinations: -10 per component
        score -= len(result.hallucinated_components) * 10

        # Template match bonus: 10 points
        if result.expected_template and result.used_template == result.expected_template:
            score += 10

        return max(0, min(100, score))

    def run_all_tests(self, levels: list = None) -> list[TestResult]:
        """Run all tests (optionally filtered by level)"""
        tests = TEST_PROMPTS
        if levels:
            tests = [t for t in tests if t["level"] in levels]

        print(f"\nRunning {len(tests)} tests...")

        for test in tests:
            result = self.run_test(test)
            self.results.append(result)
            time.sleep(2)  # Rate limiting

        return self.results

    def generate_report(self, output_path: str = None) -> str:
        """Generate report in Markdown format"""
        report = []
        report.append("# LLM Pipeline Generator Evaluation Report")
        report.append(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"API: {self.api_url}")
        report.append(f"Tests executed: {len(self.results)}")

        # Summary
        total_score = sum(r.score for r in self.results) / len(self.results) if self.results else 0
        api_success_rate = sum(1 for r in self.results if r.api_success) / len(self.results) * 100 if self.results else 0
        syntax_success_rate = sum(1 for r in self.results if r.syntax_valid) / len(self.results) * 100 if self.results else 0

        report.append("\n## Summary")
        report.append(f"- **Average Score**: {total_score:.1f}/100")
        report.append(f"- **API Success Rate**: {api_success_rate:.0f}%")
        report.append(f"- **Syntax Valid Rate**: {syntax_success_rate:.0f}%")

        # Per level
        report.append("\n### Score by Level")
        report.append("| Level | Description | Avg Score | Success Rate |")
        report.append("|-------|-------------|-----------|--------------|")

        for level in [1, 2, 3, 4]:
            level_results = [r for r in self.results if r.level == level]
            if level_results:
                avg_score = sum(r.score for r in level_results) / len(level_results)
                success = sum(1 for r in level_results if r.score >= 60) / len(level_results) * 100
                level_desc = {1: "Simple", 2: "Medium", 3: "Complex", 4: "Negative (Rejection)"}[level]
                report.append(f"| {level} | {level_desc} | {avg_score:.1f} | {success:.0f}% |")

        # Test details
        report.append("\n## Test Details")

        for result in self.results:
            status_emoji = "✅" if result.score >= 70 else "⚠️" if result.score >= 40 else "❌"
            report.append(f"\n### {status_emoji} {result.test_id} (Score: {result.score:.0f}/100)")
            report.append(f"**Description**: {result.description}")
            report.append(f"**Prompt**: {result.prompt}")
            report.append(f"**Level**: {result.level}")

            report.append("\n| Metric | Value |")
            report.append("|--------|-------|")
            report.append(f"| API Success | {'✅' if result.api_success else '❌'} |")
            report.append(f"| Response Time | {result.api_response_time:.1f}s |")
            report.append(f"| Syntax Valid | {'✅' if result.syntax_valid else '❌'} |")
            report.append(f"| Components Found | {', '.join(result.found_components) or 'None'} |")
            report.append(f"| Missing Components | {', '.join(result.missing_components) or 'None'} |")
            report.append(f"| Hallucinations | {', '.join(result.hallucinated_components) or 'None'} |")

            if result.expect_rejection:
                report.append(f"| Expected Rejection | {'✅ Correctly rejected' if result.correctly_rejected else '❌ Should have rejected'} |")

            if result.syntax_errors:
                report.append(f"\n**Syntax Errors**:")
                for err in result.syntax_errors[:3]:
                    report.append(f"- {err}")

            if result.nextflow_code:
                report.append(f"\n<details><summary>Generated Code</summary>\n")
                report.append("```nextflow")
                report.append(result.nextflow_code[:2000])
                if len(result.nextflow_code) > 2000:
                    report.append("... (truncated)")
                report.append("```")
                report.append("</details>")

        # Common issues
        report.append("\n## Common Issues Detected")

        all_hallucinations = []
        all_missing = []
        all_syntax_errors = []
        failed_rejections = []

        for r in self.results:
            all_hallucinations.extend(r.hallucinated_components)
            all_missing.extend(r.missing_components)
            all_syntax_errors.extend(r.syntax_errors)
            if r.expect_rejection and not r.correctly_rejected:
                failed_rejections.append(r)

        if all_hallucinations:
            report.append("\n### Hallucinated Components (NOT in framework)")
            for comp in set(all_hallucinations):
                count = all_hallucinations.count(comp)
                report.append(f"- `{comp}` ({count}x)")

        if all_missing:
            report.append("\n### Frequently Missing Components")
            for comp in set(all_missing):
                count = all_missing.count(comp)
                report.append(f"- `{comp}` ({count}x)")

        if all_syntax_errors:
            report.append("\n### Frequent Syntax Errors")
            error_counts = {}
            for err in all_syntax_errors:
                key = err[:50]
                error_counts[key] = error_counts.get(key, 0) + 1
            for err, count in sorted(error_counts.items(), key=lambda x: -x[1])[:5]:
                report.append(f"- `{err}...` ({count}x)")

        if failed_rejections:
            report.append("\n### Failed Rejections (CRITICAL)")
            report.append("The LLM generated code for these impossible requests:")
            for r in failed_rejections:
                report.append(f"- **{r.test_id}**: {r.prompt}")
                report.append(f"  - Reason should reject: {r.rejection_reason}")

        # Recommendations
        report.append("\n## Recommendations for Prompt Improvement")
        report.append("""
1. **Anti-Hallucination**: Add explicit whitelist of valid components in system prompt
2. **Rejection Protocol**: Add clear instructions to REJECT impossible requests
3. **Tool Constraints**: Specify which tools exist and their valid use cases
4. **Semantic Validation**: Add rules like "Pangolin is ONLY for SARS-CoV-2"
5. **Error Recovery**: Add self-correction prompts when errors are detected

See `PROMPT_IMPROVEMENTS.md` for detailed implementation recommendations.
""")

        report_text = "\n".join(report)

        if output_path:
            Path(output_path).write_text(report_text)
            print(f"\nReport salvato in: {output_path}")

        return report_text


def main():
    parser = argparse.ArgumentParser(description="Valuta l'LLM Pipeline Generator")
    parser.add_argument("--api-url", default="http://localhost:8080", help="URL dell'API")
    parser.add_argument("--ngsmanager-dir",
                        default=os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager"),
                        help="Path al framework cohesive-ngsmanager")
    parser.add_argument("--output", "-o", default="llm_evaluation_report.md", help="File di output")
    parser.add_argument("--levels", "-l", type=int, nargs="+", help="Livelli da testare (1, 2, 3)")

    args = parser.parse_args()

    evaluator = LLMEvaluator(
        api_url=args.api_url,
        ngsmanager_dir=args.ngsmanager_dir
    )

    # Check API
    print(f"Checking API at {args.api_url}...")
    if not evaluator.check_api_health():
        print("ERRORE: API non raggiungibile. Assicurati che il server sia attivo.")
        sys.exit(1)
    print("API OK!")

    # Run tests
    evaluator.run_all_tests(levels=args.levels)

    # Generate report
    report = evaluator.generate_report(args.output)
    print("\n" + "="*60)
    print(report)


if __name__ == "__main__":
    main()
