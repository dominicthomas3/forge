"""Stage 6: Apply Fixes — Claude applies only the consensus-agreed fixes.

Claude reads the consensus report (Stage 5) and applies ONLY the fixes
that both Jim and Claude agreed on. No freelancing, no bonus changes.

This is a surgical stage — targeted fixes, not a broad refactor.
"""

from __future__ import annotations

import logging
from pathlib import Path

from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_6")

_FIX_PROMPT = """
You are the fix engineer in the Forge autonomous development pipeline.
You are Stage 6 of 7. The consensus stage (Stage 5) produced a report
where Jim and Claude AGREED on specific fixes that need to be applied.

CONSENSUS REPORT:
{consensus_report}

YOUR TASK — APPLY ONLY AGREED FIXES:

1. Read the consensus report carefully. Identify the "AGREED FIXES" section.
2. For EACH agreed fix:
   a. Read the target file
   b. Locate the exact issue described
   c. Apply the fix as specified
   d. Verify the file still parses correctly after the fix
3. Do NOT apply disputed fixes (where Jim disagreed with Claude).
4. Do NOT apply Jim's additional findings unless they are marked as
   CRITICAL severity — those get addressed in the next cycle.

RULES:
- Apply fixes ONE AT A TIME. Read → fix → verify → next.
- If a fix seems risky or ambiguous, SKIP IT and note why.
- After all fixes, commit with message: [forge] Apply consensus fixes — cycle N
- Output a summary of every fix applied and every fix skipped.

OUTPUT FORMAT:
## FIXES APPLIED
- [File]: [What was fixed]

## FIXES SKIPPED (and why)
- [File]: [Why it was skipped]

## FILES MODIFIED
- [List of all files touched]

BEGIN APPLYING FIXES NOW.
"""


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    consensus_path: Path,
    cycle_number: int,
    claude_review_path: Path | None = None,
    jim_review_path: Path | None = None,
) -> Path:
    """Run fix application. Returns path to the output file."""
    output_path = cycle_dir / "06-fixes-applied.log"

    logger.info("Stage 6: Apply Consensus Fixes (cycle %d)", cycle_number)

    consensus_report = consensus_path.read_text(encoding="utf-8")

    # Enrich with full issue descriptions from both reviewers so Claude
    # knows WHAT to fix, not just WHICH files to fix.
    reviewer_context = ""
    if claude_review_path and claude_review_path.exists():
        claude_review = claude_review_path.read_text(encoding="utf-8", errors="replace")
        reviewer_context += f"\n\n--- CLAUDE'S REVIEW (Stage 4) — issue details ---\n{claude_review[:4000]}\n"
    if jim_review_path and jim_review_path.exists():
        jim_review = jim_review_path.read_text(encoding="utf-8", errors="replace")
        reviewer_context += f"\n\n--- JIM'S REVIEW (Stage 5a) — issue details ---\n{jim_review[:4000]}\n"

    prompt = _FIX_PROMPT.format(consensus_report=consensus_report + reviewer_context)

    # Append stage-specific supplement to the blueprint
    from forge.worker_blueprint import STAGE_6_SUPPLEMENT
    prompt = STAGE_6_SUPPLEMENT + "\n" + prompt

    # Run Claude Code with file access — moderate timeout
    result = runner.run_claude(prompt, timeout=900, blueprint="full")

    from forge.checkpoint import atomic_write
    atomic_write(output_path, result)
    logger.info("Fixes applied log saved: %s (%d chars)", output_path, len(result))

    return output_path