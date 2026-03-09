"""Stage 3: Claude Implementation — Claude Code applies Deep Think's plan.

Claude Code CLI has full file system access to the target project.
It reads Deep Think's verified plan and implements each change.

Claude works in the target project directory with --dangerously-skip-permissions,
meaning it can read, write, and execute anything needed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from forge.checkpoint import atomic_write
from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_3")

_IMPLEMENTATION_PROMPT = """
You are the implementation engineer in the Forge autonomous development pipeline.
You are Stage 3 of 7. Deep Think (extended reasoning) has verified and approved
the following implementation plan. Your job is to EXECUTE it precisely.

VERIFIED IMPLEMENTATION PLAN (from Deep Think):
{deep_think_plan}

RULES — FOLLOW THESE EXACTLY:

1. SCOPE: Implement ONLY what the plan specifies. No gold-plating, no "while
   I'm here" improvements, no bonus refactoring. If it's not in the plan, don't
   touch it.

2. PRECISION: For each change, read the target file first. Understand the
   existing code before modifying it. Match the existing code style exactly —
   indentation, naming conventions, comment style.

3. SAFETY: After EACH file modification, verify the file still parses correctly:
   - For Python: mentally check imports resolve and syntax is valid
   - Don't leave files in a half-modified state

4. COMMITS: After each logical group of changes (e.g., "replace message types"),
   commit with a clear message describing what changed and why. Use this format:
   [forge] <what changed> — <why>

5. NO BUSINESS LOGIC CHANGES: You are modifying infrastructure/wrappers ONLY.
   The custom business logic inside each file must remain EXACTLY as-is.
   If you're unsure whether something is infrastructure or business logic,
   leave it alone.

6. LOGGING: At the end of your work, output a summary of every change you made:
   - File path
   - What was changed
   - Lines affected (approximate)
   - Any concerns or uncertainties

IMPORTANT: You have full file system access. Read files before editing them.
Work through the plan systematically, top to bottom. Don't skip steps.

BEGIN IMPLEMENTATION NOW.
"""


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    deep_think_path: Path,
) -> Path:
    """Run Claude implementation. Returns path to the output file."""
    output_path = cycle_dir / "03-claude-implementation.log"

    logger.info("Stage 3: Claude Implementation")

    # Read Deep Think's verified plan
    deep_think_plan = deep_think_path.read_text(encoding="utf-8")
    logger.info("Deep Think plan loaded: %d chars", len(deep_think_plan))

    # Try to load structured Deep Think output for enhanced prompting
    structured_path = cycle_dir / "02-deep-think-structured.json"
    constraints_block = ""
    edge_cases_block = ""
    if structured_path.exists():
        try:
            structured = json.loads(structured_path.read_text(encoding="utf-8"))
            verdict = structured.get("verdict", "UNKNOWN")
            logger.info("Loaded structured Deep Think plan (verdict: %s)", verdict)

            # Inject constraints as mandatory rules
            if structured.get("constraints"):
                constraints_block = (
                    "\n\nMANDATORY CONSTRAINTS (from Deep Think verification — you MUST obey these):\n"
                    + "\n".join(f"- {c}" for c in structured["constraints"])
                )

            # Inject edge cases to handle
            if structured.get("edge_cases"):
                edge_cases_block = "\n\nEDGE CASES TO HANDLE (discovered during verification):\n"
                for ec in structured["edge_cases"]:
                    scenario = ec.get("scenario", "")
                    mitigation = ec.get("mitigation", "")
                    edge_cases_block += f"- {scenario}: {mitigation}\n"
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load structured Deep Think plan — using free text only")

    prompt = _IMPLEMENTATION_PROMPT.format(deep_think_plan=deep_think_plan)
    prompt += constraints_block + edge_cases_block

    # Append stage-specific supplement to the blueprint
    from forge.worker_blueprint import STAGE_3_SUPPLEMENT
    prompt = STAGE_3_SUPPLEMENT + "\n" + prompt

    # Run Claude Code with full file access — generous timeout for multi-file edits
    result = runner.run_claude(prompt, blueprint="full")

    # Save output (atomic write prevents corruption on crash)
    atomic_write(output_path, result)
    logger.info("Claude implementation log saved: %s (%d chars)", output_path, len(result))

    return output_path