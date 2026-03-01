"""Stage 4: Claude Self-Review — Claude reviews its own changes.

Claude examines what it just implemented, looking for errors, missed changes,
and potential regressions. CRITICALLY: it does NOT fix anything yet.
It produces a review report that goes to the consensus stage.

This separation (review ≠ fix) prevents cascading corrections from
compounding errors. Fixes are only applied after Jim agrees.
"""

from __future__ import annotations

import logging
from pathlib import Path

from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_4")

_REVIEW_PROMPT = """
You are the code reviewer in the Forge autonomous development pipeline.
You are Stage 4 of 7. You just implemented changes in Stage 3.
Now you must review your OWN work with fresh eyes.

WHAT YOU IMPLEMENTED (your Stage 3 log):
{implementation_log}

THE PLAN YOU WERE FOLLOWING (from Deep Think):
{deep_think_plan}

YOUR TASK — REVIEW ONLY, DO NOT FIX:

1. **COMPLETENESS CHECK**: Did you implement everything in the plan?
   For each item in Deep Think's plan, verify: was it done?
   List any items that were skipped or only partially implemented.

2. **CORRECTNESS CHECK**: For each file you modified:
   - Read it now and verify it parses correctly
   - Check that imports resolve (no circular imports, no missing modules)
   - Verify the replacement matches the original interface exactly
   - Look for typos, off-by-one errors, wrong variable names

3. **REGRESSION CHECK**: Could your changes break anything?
   - Did you accidentally modify business logic?
   - Are there other files that import from files you changed?
   - Did you break any existing tests?
   - Are there any runtime-only failures that compile-time checks would miss?

4. **STYLE CHECK**: Does your code match the existing codebase style?
   - Naming conventions
   - Import ordering
   - Comment style
   - Error handling patterns

OUTPUT FORMAT — produce a structured report:

## REVIEW SUMMARY
Overall assessment: [CLEAN / MINOR ISSUES / SIGNIFICANT ISSUES]

## COMPLETED ITEMS
- [List each plan item and its status: DONE / PARTIAL / SKIPPED]

## ISSUES FOUND
For each issue:
- **File**: path/to/file.py
- **Line(s)**: approximate line numbers
- **Issue**: what's wrong
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW
- **Suggested Fix**: what should be changed (but DO NOT apply it)

## POTENTIAL REGRESSIONS
- [List any files/features that might be affected by the changes]

## CONFIDENCE LEVEL
How confident are you that the changes are correct? [HIGH / MEDIUM / LOW]
Why?

IMPORTANT: DO NOT MAKE ANY CHANGES. Only report. Fixes happen in Stage 6
after Jim agrees with your findings.
"""


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    implementation_path: Path,
    deep_think_path: Path,
) -> Path:
    """Run Claude self-review. Returns path to the output file."""
    output_path = cycle_dir / "04-claude-review.md"

    logger.info("Stage 4: Claude Self-Review")

    implementation_log = implementation_path.read_text(encoding="utf-8")
    deep_think_plan = deep_think_path.read_text(encoding="utf-8")

    prompt = _REVIEW_PROMPT.format(
        implementation_log=implementation_log,
        deep_think_plan=deep_think_plan,
    )

    # Review doesn't edit files, but needs enough time to reason through changes
    result = runner.run_claude(prompt, timeout=600, needs_filesystem=False)

    output_path.write_text(result, encoding="utf-8")
    logger.info("Claude review saved: %s (%d chars)", output_path, len(result))

    return output_path