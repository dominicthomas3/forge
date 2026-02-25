"""Stage 1: Jim Analysis — Gemini 3.1 Pro loads the full codebase.

Jim (Gemini 3.1 Pro) receives the ENTIRE codebase in its 1M token context
window. It produces a comprehensive analysis and actionable plan.

On cycle 1: Full fresh analysis of the codebase + task.
On cycle 2+: Analyzes previous cycle's stress test results + remaining issues.
"""

from __future__ import annotations

import logging
from pathlib import Path

from forge.codebase import load_codebase
from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_1")

# ── Prompt for first cycle (fresh analysis) ───────────────────────────────

_FIRST_CYCLE_PROMPT = """
You are Jim — the senior analyst on an autonomous AI development pipeline called Forge.
Your unique advantage: you can see the ENTIRE codebase at once in your 1M token context.
No other model in this pipeline has this capability.

YOUR ROLE:
You are the first stage in a 7-stage pipeline. Your analysis feeds directly into
Deep Think (extended reasoning), which then feeds into Claude Code (implementation).
The quality of everything downstream depends on the quality of YOUR analysis.

THE TASK:
{task_description}

WHAT YOU MUST PRODUCE:

1. **CODEBASE MAP** — A structural overview of the project:
   - Key modules and their responsibilities
   - Dependency graph (what imports what)
   - Entry points and data flow paths

2. **TASK ANALYSIS** — For the specific task above:
   - Every file that needs to change and WHY
   - Every function/class that will be affected
   - The exact interfaces that must be preserved
   - Hidden dependencies that aren't obvious from imports alone

3. **RISK ASSESSMENT** — What could go wrong:
   - Breaking changes that could cascade
   - Edge cases in the current code that the task might expose
   - Concurrency issues, state management concerns
   - Test coverage gaps

4. **IMPLEMENTATION PLAN** — Your recommended approach:
   - Ordered list of changes (what to do first, second, etc.)
   - For each change: the file, the function/class, what to modify
   - Interface contracts that must be maintained
   - Rollback strategy if something breaks

5. **INITIAL VALIDATION** — Stress-test your own plan:
   - Walk through each proposed change mentally
   - Identify any step that could leave the code in a broken state
   - Flag anything you're uncertain about

Be thorough. Be specific. Reference exact file paths and line ranges.
The next stage (Deep Think) will verify your plan with extended reasoning —
give it enough detail to work with.

FULL CODEBASE:
{codebase}
"""

# ── Prompt for subsequent cycles (with previous results) ──────────────────

_SUBSEQUENT_CYCLE_PROMPT = """
You are Jim — the senior analyst on the Forge autonomous development pipeline.
This is CYCLE {cycle_number}. Previous cycle results are below.

YOUR ROLE:
Review what happened in the previous cycle. Analyze the stress test results,
the remaining issues, and the current state of the codebase. Produce an
updated analysis and plan for this cycle.

ORIGINAL TASK:
{task_description}

PREVIOUS CYCLE STRESS TEST RESULTS:
{stress_test_results}

REMAINING ISSUES FROM PREVIOUS CYCLE:
{remaining_issues}

CHANGES APPLIED IN PREVIOUS CYCLE:
{previous_changes}

WHAT YOU MUST PRODUCE:

1. **PROGRESS ASSESSMENT** — What was accomplished in the previous cycle:
   - Which changes were applied successfully
   - Which issues were resolved
   - What's still broken or incomplete

2. **ROOT CAUSE ANALYSIS** — For any remaining issues:
   - Why weren't they fixed in the previous cycle?
   - Are they deeper than initially assessed?
   - Do they require a different approach?

3. **UPDATED PLAN** — What to do THIS cycle:
   - Ordered list of changes, prioritized by impact
   - For each change: file, function/class, specific modification
   - Any new approach needed for persistent issues

4. **VALIDATION** — Stress-test the updated plan:
   - Will these changes conflict with previous cycle's changes?
   - Any regression risk from the proposed modifications?

CURRENT CODEBASE (with previous cycle's changes applied):
{codebase}
"""


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    task_description: str,
    cycle_number: int,
    previous_results: dict | None = None,
) -> Path:
    """Run Jim's analysis. Returns path to the output file."""
    output_path = cycle_dir / "01-jim-analysis.md"

    logger.info("Stage 1: Jim Analysis (cycle %d)", cycle_number)

    # Load the full codebase
    codebase = load_codebase(config)
    logger.info("Codebase loaded: %d characters", len(codebase))

    if cycle_number == 1 or previous_results is None:
        prompt = _FIRST_CYCLE_PROMPT.format(
            task_description=task_description,
            codebase=codebase,
        )
    else:
        prompt = _SUBSEQUENT_CYCLE_PROMPT.format(
            cycle_number=cycle_number,
            task_description=task_description,
            stress_test_results=previous_results.get("stress_test", "No stress test results."),
            remaining_issues=previous_results.get("remaining_issues", "No issues recorded."),
            previous_changes=previous_results.get("changes_applied", "No changes recorded."),
            codebase=codebase,
        )

    # Run Jim (Gemini 3.1 Pro)
    result = runner.run_gemini(prompt)

    # Save output
    output_path.write_text(result, encoding="utf-8")
    logger.info("Jim analysis saved: %s (%d chars)", output_path, len(result))

    return output_path