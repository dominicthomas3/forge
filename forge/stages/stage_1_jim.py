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
FULL CODEBASE:
{codebase}

---

You are Jim — the senior analyst on an autonomous AI development pipeline called Forge.
Your unique advantage: you can see the ENTIRE codebase above in your 1M token context.
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
"""

# ── Prompt for subsequent cycles (with previous results) ──────────────────

_SUBSEQUENT_CYCLE_PROMPT = """
CURRENT CODEBASE:
{codebase}

---

You are Jim — the senior analyst on the Forge autonomous development pipeline.
This is CYCLE {cycle_number}.

YOUR ROLE:
Fix the issues found in the last cycle's stress test. You have the current
codebase above — it already includes all previous changes. Focus ONLY on
what's still broken. Don't re-analyze what's already working.

ORIGINAL TASK:
{task_description}

LAST CYCLE VERDICT: {verdict}

WHAT FAILED (fix these):
{failures}

WHAT PASSED (don't break these):
{passes}

WHAT YOU MUST PRODUCE:

1. **ROOT CAUSE** — For each failure, WHY it failed (be specific: file, line, logic error)

2. **FIX PLAN** — Ordered list of changes to fix the failures:
   - File, function/class, specific modification
   - How to verify the fix won't break what's already passing

3. **RISK CHECK** — Any regression risk from these fixes?

Be concise. The codebase is above — reference exact file paths and functions.
"""


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    task_description: str,
    cycle_number: int,
    previous_results: dict | None = None,
    codebase: str | None = None,
) -> Path:
    """Run Jim's analysis. Returns path to the output file."""
    output_path = cycle_dir / "01-jim-analysis.md"

    logger.info("Stage 1: Jim Analysis (cycle %d)", cycle_number)

    # Use pre-loaded codebase if provided, otherwise load fresh
    if codebase is None:
        codebase = load_codebase(config)
    logger.info("Codebase loaded: %d characters", len(codebase))

    if cycle_number == 1 or previous_results is None:
        prompt = _FIRST_CYCLE_PROMPT.format(
            task_description=task_description,
            codebase=codebase,
        )
    else:
        # Parse stress test into concise failures/passes — don't dump raw text.
        # This keeps prompt size constant across iterations.
        stress_raw = previous_results.get("stress_test", "")
        verdict = previous_results.get("stress_verdict", "UNKNOWN")
        failures = []
        passes = []

        for line in stress_raw.split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            line_lower = line_stripped.lower()
            # Capture FAIL lines with their details
            if "fail" in line_lower and ("|" in line_stripped or "—" in line_stripped or ":" in line_stripped):
                failures.append(line_stripped)
            # Capture PASS lines
            elif "pass" in line_lower and ("|" in line_stripped or "—" in line_stripped):
                passes.append(line_stripped)
            # Capture ISSUES FOUND section headers and bug descriptions
            elif line_stripped.startswith("| ") and ("HIGH" in line_stripped or "MEDIUM" in line_stripped or "LOW" in line_stripped):
                failures.append(line_stripped)

        prompt = _SUBSEQUENT_CYCLE_PROMPT.format(
            cycle_number=cycle_number,
            task_description=task_description,
            verdict=verdict,
            failures="\n".join(failures[:30]) if failures else "None — all tests passed.",
            passes="\n".join(passes[:20]) if passes else "No pass data available.",
            codebase=codebase,
        )

    # Run Jim (Gemini 3.1 Pro)
    result = runner.run_gemini(prompt)

    # Save output
    output_path.write_text(result, encoding="utf-8")
    logger.info("Jim analysis saved: %s (%d chars)", output_path, len(result))

    return output_path