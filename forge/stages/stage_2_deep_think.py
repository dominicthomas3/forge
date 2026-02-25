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

import logging
from pathlib import Path

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

Your role in the Forge pipeline:
- You receive a plan from Jim (Gemini 3.1 Pro full-codebase analyst)
- Your job is to VERIFY, STRESS TEST, and IMPROVE that plan
- Your output goes to Claude Code for implementation
- If your analysis is wrong, Claude will implement bad code

Be harsh. Be thorough. Find the problems Jim missed.
If the plan is solid, say so clearly. Don't manufacture issues.
If the plan has real problems, catch them NOW — not after implementation."""


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
    claude_prompt_path.write_text(crafted_prompt, encoding="utf-8")
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

    # Save output
    output_path.write_text(result, encoding="utf-8")
    logger.info("Deep Think verification saved: %s (%d chars)", output_path, len(result))

    return output_path