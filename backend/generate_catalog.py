#!/usr/bin/env python3
"""
Catalog Generator for LLM Pipeline Assistant

This script automatically generates the LLM catalog by parsing
the actual cohesive-ngsmanager framework files.

This ensures the catalog is ALWAYS synchronized with reality.

Usage:
    python generate_catalog.py --ngsmanager-dir /path/to/cohesive-ngsmanager
"""

import argparse
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class StepInfo:
    """Information extracted from a step .nf file"""
    id: str
    file_path: str
    domain: str = ""
    tool: str = ""
    description: str = ""
    keywords: list = field(default_factory=list)
    use_cases: list = field(default_factory=list)
    container: str = ""
    input_channels: list = field(default_factory=list)
    output_channels: list = field(default_factory=list)
    compatible_seq_types: list = field(default_factory=list)


@dataclass
class ModuleInfo:
    """Information extracted from a module .nf file"""
    id: str
    file_path: str
    description: str = ""
    keywords: list = field(default_factory=list)
    steps_used: list = field(default_factory=list)
    input_channels: list = field(default_factory=list)
    output_channels: list = field(default_factory=list)


class CatalogGenerator:
    """Generates catalog from ngsmanager framework"""

    # Domain mapping based on step prefix
    DOMAIN_MAP = {
        "0SQ": "Quality Control",
        "1PP": "Preprocessing",
        "2AS": "Assembly & Mapping",
        "2MG": "Metagenomics",
        "3TX": "Taxonomy",
        "4AN": "Annotation & AMR",
        "4TY": "Typing",
    }

    # Tool extraction patterns
    TOOL_PATTERN = re.compile(r'step_\d[A-Z]{2}_\w+__(\w+)')

    def __init__(self, ngsmanager_dir: Path):
        self.ngsmanager_dir = ngsmanager_dir
        self.steps_dir = ngsmanager_dir / "steps"
        self.modules_dir = ngsmanager_dir / "modules"
        self.functions_dir = ngsmanager_dir / "functions"

    def parse_step_file(self, nf_file: Path) -> StepInfo:
        """Parse a step .nf file and extract metadata"""
        content = nf_file.read_text()
        step_id = nf_file.stem

        # Extract domain from prefix
        domain = ""
        for prefix, dom in self.DOMAIN_MAP.items():
            if prefix in step_id:
                domain = dom
                break

        # Extract tool name
        tool_match = self.TOOL_PATTERN.match(step_id)
        tool = tool_match.group(1) if tool_match else ""

        # Extract container
        container = ""
        container_match = re.search(r"container\s+['\"]([^'\"]+)['\"]", content)
        if container_match:
            container = container_match.group(1)

        # Extract input channels from 'take:' block
        inputs = []
        take_match = re.search(r'take:\s*\n((?:\s+\w+\s*\n)+)', content)
        if take_match:
            inputs = [ch.strip() for ch in take_match.group(1).split('\n') if ch.strip()]

        # Extract output channels from 'emit:' block
        outputs = []
        emit_match = re.search(r'emit:\s*\n((?:\s+\w+.*\n)+)', content)
        if emit_match:
            for line in emit_match.group(1).split('\n'):
                line = line.strip()
                if line and not line.startswith('//'):
                    # Handle "name = value" or just "name"
                    if '=' in line:
                        outputs.append(line.split('=')[0].strip())
                    else:
                        outputs.append(line)

        # Extract compatible seq types
        seq_types = []
        if 'illumina' in content.lower() or 'paired' in content.lower():
            seq_types.append('illumina_paired')
        if 'ion' in content.lower():
            seq_types.append('ion')
        if 'nanopore' in content.lower() or 'ont' in content.lower():
            seq_types.append('nanopore')

        # Generate keywords from id and tool
        keywords = [tool.lower()]
        if 'trim' in step_id.lower():
            keywords.extend(['trim', 'trimming', 'adapter', 'quality filter'])
        if 'mapping' in step_id.lower():
            keywords.extend(['mapping', 'alignment', 'map reads'])
        if 'denovo' in step_id.lower():
            keywords.extend(['assembly', 'de novo', 'assemble'])
        if 'AMR' in step_id:
            keywords.extend(['amr', 'antimicrobial resistance', 'resistance genes'])
        if 'MLST' in step_id:
            keywords.extend(['mlst', 'multilocus sequence typing'])
        if 'lineage' in step_id.lower():
            keywords.extend(['lineage', 'clade', 'variant'])

        # Generate use cases
        use_cases = []
        if tool:
            use_cases.append(f"Run {tool} analysis")
            use_cases.append(f"Use {tool} for {domain.lower()}")

        # Generate description
        description = f"{tool.capitalize()} step for {domain.lower()}."
        if outputs:
            description += f" Outputs: {', '.join(outputs)}."

        return StepInfo(
            id=step_id,
            file_path=str(nf_file.relative_to(self.ngsmanager_dir)),
            domain=domain,
            tool=tool,
            description=description,
            keywords=list(set(keywords)),
            use_cases=use_cases,
            container=container,
            input_channels=inputs,
            output_channels=outputs,
            compatible_seq_types=seq_types if seq_types else ['illumina_paired', 'ion'],
        )

    def parse_module_file(self, nf_file: Path) -> ModuleInfo:
        """Parse a module .nf file and extract metadata"""
        content = nf_file.read_text()
        module_id = nf_file.stem

        # Find all steps used (include statements)
        steps_used = re.findall(r"include\s*\{\s*(step_\w+)\s*\}", content)

        # Extract input channels
        inputs = []
        take_match = re.search(r'take:\s*\n((?:\s+\w+\s*\n)+)', content)
        if take_match:
            inputs = [ch.strip() for ch in take_match.group(1).split('\n') if ch.strip()]

        # Extract output channels
        outputs = []
        emit_match = re.search(r'emit:\s*\n((?:\s+\w+.*\n)+)', content)
        if emit_match:
            for line in emit_match.group(1).split('\n'):
                line = line.strip()
                if line and not line.startswith('//'):
                    if '=' in line:
                        outputs.append(line.split('=')[0].strip())
                    else:
                        outputs.append(line)

        # Generate keywords
        keywords = [module_id.replace('module_', '').replace('_', ' ')]
        for step in steps_used:
            tool_match = self.TOOL_PATTERN.match(step)
            if tool_match:
                keywords.append(tool_match.group(1).lower())

        # Generate description
        description = f"Pipeline module using: {', '.join(steps_used) if steps_used else 'custom logic'}."

        return ModuleInfo(
            id=module_id,
            file_path=str(nf_file.relative_to(self.ngsmanager_dir)),
            description=description,
            keywords=list(set(keywords)),
            steps_used=steps_used,
            input_channels=inputs,
            output_channels=outputs,
        )

    def parse_helper_functions(self) -> list:
        """Extract helper functions from functions/*.nf"""
        helpers = []

        for nf_file in self.functions_dir.glob("*.nf"):
            content = nf_file.read_text()

            # Find function definitions
            func_matches = re.finditer(
                r'def\s+(\w+)\s*\([^)]*\)\s*\{',
                content
            )

            for match in func_matches:
                func_name = match.group(1)
                # Skip private functions
                if func_name.startswith('_'):
                    continue

                helpers.append({
                    "name": func_name,
                    "file": str(nf_file.relative_to(self.ngsmanager_dir)),
                    "description": f"Helper function from {nf_file.name}",
                })

        return helpers

    def generate(self, output_dir: Path):
        """Generate complete catalog"""

        print("=" * 60)
        print("CATALOG GENERATOR")
        print("=" * 60)
        print(f"Source: {self.ngsmanager_dir}")
        print(f"Output: {output_dir}")
        print()

        # Parse all steps
        print("Parsing steps...")
        steps = []
        for nf_file in sorted(self.steps_dir.glob("step_*.nf")):
            try:
                step = self.parse_step_file(nf_file)
                steps.append(asdict(step))
                print(f"  + {step.id}")
            except Exception as e:
                print(f"  ! Error parsing {nf_file.name}: {e}")

        print(f"\nTotal steps: {len(steps)}")

        # Parse all modules
        print("\nParsing modules...")
        modules = []
        for nf_file in sorted(self.modules_dir.glob("module_*.nf")):
            try:
                module = self.parse_module_file(nf_file)
                modules.append(asdict(module))
                print(f"  + {module.id}")
            except Exception as e:
                print(f"  ! Error parsing {nf_file.name}: {e}")

        print(f"\nTotal modules: {len(modules)}")

        # Parse helper functions
        print("\nParsing helper functions...")
        helpers = self.parse_helper_functions()
        print(f"Total helpers: {len(helpers)}")

        # Generate catalog files
        output_dir.mkdir(parents=True, exist_ok=True)

        # Components catalog
        components_catalog = {
            "metadata": {
                "version": "2.0",
                "generated_from": str(self.ngsmanager_dir),
                "total_steps": len(steps),
                "total_modules": len(modules),
            },
            "components": steps,
        }

        (output_dir / "catalog_part1_components.json").write_text(
            json.dumps(components_catalog, indent=2)
        )
        print(f"\nWrote: catalog_part1_components.json")

        # Modules catalog
        modules_catalog = {
            "metadata": {
                "version": "2.0",
            },
            "templates": modules,
        }

        (output_dir / "catalog_part2_templates.json").write_text(
            json.dumps(modules_catalog, indent=2)
        )
        print(f"Wrote: catalog_part2_templates.json")

        # Resources catalog
        resources_catalog = {
            "metadata": {
                "version": "2.0",
            },
            "helper_functions": helpers,
        }

        (output_dir / "catalog_part3_resources.json").write_text(
            json.dumps(resources_catalog, indent=2)
        )
        print(f"Wrote: catalog_part3_resources.json")

        # Generate WHITELIST for prompt injection
        whitelist = self._generate_whitelist(steps, modules)
        (output_dir / "tool_whitelist.json").write_text(json.dumps(whitelist, indent=2))
        print(f"Wrote: tool_whitelist.json")

        print("\n" + "=" * 60)
        print("DONE!")
        print("=" * 60)

    def _generate_whitelist(self, steps: list, modules: list) -> dict:
        """Generate a structured whitelist for prompt injection"""

        whitelist_steps = []
        for step in steps:
            entry = {
                "id": step["id"],
                "tool": step.get("tool", ""),
                "domain": step.get("domain", ""),
                "inputs": step.get("input_channels", []),
                "outputs": step.get("output_channels", []),
            }
            whitelist_steps.append(entry)

        whitelist_modules = []
        for module in modules:
            entry = {
                "id": module["id"],
                "steps_used": module.get("steps_used", []),
            }
            whitelist_modules.append(entry)

        return {
            "steps": whitelist_steps,
            "modules": whitelist_modules,
            "not_available": [
                {"tool": "BWA", "alternative": "bowtie or minimap2"},
                {"tool": "STAR", "alternative": None},
                {"tool": "HISAT2", "alternative": None},
                {"tool": "Canu", "alternative": "flye for long reads"},
                {"tool": "Hifiasm", "alternative": None},
                {"tool": "Salmon", "alternative": None},
                {"tool": "Kallisto", "alternative": None},
                {"tool": "GATK", "alternative": None},
                {"tool": "FreeBayes", "alternative": None},
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="Generate LLM catalog from ngsmanager")
    parser.add_argument(
        "--ngsmanager-dir",
        type=Path,
        default=Path(os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager")),
        help="Path to cohesive-ngsmanager"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "catalog")),
        help="Output directory for catalog"
    )

    args = parser.parse_args()

    if not args.ngsmanager_dir.exists():
        print(f"ERROR: {args.ngsmanager_dir} does not exist")
        return 1

    generator = CatalogGenerator(args.ngsmanager_dir)
    generator.generate(args.output_dir)

    return 0


if __name__ == "__main__":
    exit(main())
