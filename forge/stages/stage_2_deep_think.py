"""Stage 2: Deep Think Verification — Claude crafts prompt → Deep Think verifies.

Two-step stage:
1. Claude reads Jim's analysis and crafts a detailed 1,000-3,000 word prompt
2. That prompt goes to Deep Think (Gemini 3.1 Pro with ThinkingLevel.HIGH)

Deep Think stress-tests Jim's plan, does deep architectural analysis,
and verifies the reasoning. It catches edge cases, concurrency issues,
and failure modes that standard analysis misses.

Key insight: Claude-crafted prompts get significantly better results from
Deep Think than raw context dumps. Claude structures the information in
a way that activates Deep Think's extended reasoning most effectively.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from forge.checkpoint import atomic_write
from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_2")

# ── Instructions for Claude when crafting the Deep Think prompt ────────────

_CRAFT_INSTRUCTIONS = """
Analyze Jim's plan and craft a prompt for Deep Think that asks it to:

1. STRESS TEST THE PLAN: Walk through each proposed change step by step.
   For each step, ask: "What breaks if this is wrong?" and "What's the
   hidden dependency Jim might have missed?"

2. VERIFY THE ARCHITECTURE: Is Jim's proposed approach the most efficient?
   Are there simpler alternatives? Would a different ordering reduce risk?

3. FIND EDGE CASES: What inputs, states, or timing conditions could break
   the proposed changes? Think about: empty values, None types, concurrent
   access, partial failures, encoding issues, import cycles.

4. VERIFY INTERFACE CONTRACTS: For every interface Jim says must be preserved,
   verify that the proposed replacement actually matches. Look for subtle
   type differences, missing optional parameters, behavior changes.

5. PRODUCE AN IMPROVED PLAN: After analysis, output a verified and potentially
   improved version of Jim's plan. If Jim's plan is good, say so and explain
   why. If changes are needed, be specific about what and why.

The output should be a complete, actionable implementation plan that Claude Code
can execute directly. Every change must reference specific files, functions, and
line numbers. No ambiguity."""

# ── System prompt for Deep Think ──────────────────────────────────────────

_DEEP_THINK_SYSTEM = """
You are Deep Think — an extended reasoning engine performing architectural verification.
You have ThinkingLevel.HIGH enabled. USE IT. Think deeply before responding.

## Your Position in the Pipeline
- Stage 1 (Jim): Full codebase analysis + plan → feeds into YOU
- Stage 2 (YOU): Verify, stress test, and improve Jim's plan
- Stage 3 (Claude Opus): Implements YOUR output literally — every instruction matters
- Stage 4-7: Review, consensus, fixes, stress testing

If your analysis is wrong, Claude will implement bad code. If you miss an edge case,
it becomes a bug. You are the last checkpoint before code is written.

## Thinking Discipline
1. **Trace every change path**: For each proposed file modification, mentally walk through
   what happens when that code runs. What calls it? What does it call? What state does it touch?
2. **Check interface boundaries**: When a function signature changes, find EVERY caller. Do they
   still pass the right types? Are there default values that hide mismatches?
3. **Test ordering**: Could the proposed change sequence leave the codebase broken at any
   intermediate step? If so, reorder.
4. **Challenge assumptions**: Jim assumed X — is X actually true? Verify against the codebase.

## Output Requirements
- Your output must be a COMPLETE, ACTIONABLE implementation plan.
- Every change references specific files, functions, and line numbers.
- Include verification steps ("after this change, run X to confirm").
- If Jim's plan is solid, say so clearly and explain WHY it's correct.
- If you find problems, describe the EXACT failure scenario, not just "this might break."
- Don't manufacture issues to seem thorough. False positives waste more time than false negatives.

## REQUIRED OUTPUT FORMAT

You MUST output your analysis as a JSON block wrapped in ```json fences.
This JSON will be consumed programmatically by Claude Code (Stage 3).
After the JSON block, you may add free-form discussion.

Schema:
```json
{
  "verdict": "APPROVE" | "APPROVE_WITH_CONSTRAINTS" | "REJECT",
  "summary": "2-3 sentence overall assessment",
  "change_assessments": [
    {
      "file": "path/to/file.py",
      "change": "what Jim proposed",
      "risk": "LOW" | "MEDIUM" | "HIGH",
      "approved": true,
      "dependencies": ["file1.py"],
      "verification_steps": ["step 1"],
      "concerns": "any concerns or modifications needed",
      "ordering_constraint": "must happen before/after X"
    }
  ],
  "constraints": [
    "Stage 3 MUST obey these constraints when implementing"
  ],
  "edge_cases": [
    {
      "scenario": "description of edge case",
      "affected_files": ["file.py"],
      "mitigation": "how to handle it"
    }
  ],
  "improved_plan": "If you modified Jim's plan, describe what changed and why"
}
```

If you REJECT the plan, explain WHY in the summary and what Jim should do differently.
If you APPROVE_WITH_CONSTRAINTS, list the constraints that Stage 3 must follow."""


def _parse_deep_think_output(output: str) -> dict | None:
    """Parse Deep Think's structured JSON output.

    Searches for ```json blocks containing a "verdict" key.
    Returns the parsed dict, or None if no valid JSON found.
    Falls back gracefully — the pipeline works with free-text too.
    """
    json_blocks = re.findall(r'```json\s*\n(.*?)```', output, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, dict) and "verdict" in parsed:
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    jim_analysis_path: Path,
) -> Path:
    """Run Deep Think verification. Returns path to the output file."""
    output_path = cycle_dir / "02-deep-think-verification.md"
    claude_prompt_path = cycle_dir / "02-claude-crafted-prompt.md"

    logger.info("Stage 2: Deep Think Verification")

    # Clean stale structured JSON from previous runs — prevents Stage 3
    # from loading constraints meant for a different plan.
    stale_json = cycle_dir / "02-deep-think-structured.json"
    stale_json.unlink(missing_ok=True)

    # Read Jim's analysis
    jim_analysis = jim_analysis_path.read_text(encoding="utf-8")
    logger.info("Jim analysis loaded: %d chars", len(jim_analysis))

    # Step 1: Claude crafts the prompt for Deep Think
    logger.info("Step 2a: Claude crafting prompt for Deep Think")
    crafted_prompt = runner.claude_craft_prompt(
        context=jim_analysis,
        target_instructions=_CRAFT_INSTRUCTIONS,
    )

    # Save Claude's crafted prompt for inspection
    atomic_write(claude_prompt_path, crafted_prompt)
    logger.info(
        "Claude crafted prompt: %d chars (saved to %s)",
        len(crafted_prompt),
        claude_prompt_path,
    )

    # Step 2: Send Claude's crafted prompt to Deep Think
    logger.info("Step 2b: Running Deep Think with Claude's crafted prompt")
    result = runner.run_deep_think(
        prompt=crafted_prompt,
        system=_DEEP_THINK_SYSTEM,
    )

    # Parse structured output (JSON verdict + assessments)
    structured = _parse_deep_think_output(result)
    if structured:
        json_path = cycle_dir / "02-deep-think-structured.json"
        atomic_write(json_path, json.dumps(structured, indent=2))
        verdict = structured.get("verdict", "UNKNOWN")
        n_assessments = len(structured.get("change_assessments", []))
        logger.info("Deep Think verdict: %s (%d change assessments)", verdict, n_assessments)
        if verdict == "REJECT":
            logger.warning("Deep Think REJECTED Jim's plan: %s", structured.get("summary", "no reason"))
    else:
        logger.warning("Deep Think did not produce valid JSON — Stage 3 will use free-text plan")

    # Save raw output (always — serves as the canonical stage output)
    atomic_write(output_path, result)
    logger.info("Deep Think verification saved: %s (%d chars)", output_path, len(result))

    return output_path