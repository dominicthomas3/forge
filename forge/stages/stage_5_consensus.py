"""Stage 5: Consensus — Jim and Claude must agree before fixes are applied.

This is the quality gate. Claude's review (Stage 4) identified issues.
Now Jim (Gemini Pro) independently reviews the same codebase and produces
its own findings. Only fixes that BOTH agree on proceed to Stage 6.

This prevents single-model hallucinations from corrupting the codebase.
If Claude thinks there's a bug but Jim disagrees, it doesn't get "fixed"
(which might introduce a real bug). Consensus = confidence.
"""

from __future__ import annotations

import logging
from pathlib import Path

from forge.codebase import load_codebase
from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_5")

# ── Jim's independent review prompt ──────────────────────────────────────

_JIM_REVIEW_PROMPT = """
You are Jim — the senior analyst on the Forge autonomous development pipeline.
Stage 3 (Claude) implemented changes. Stage 4 (Claude) reviewed its own work.
Now YOU independently review the codebase and Claude's findings.

CLAUDE'S REVIEW REPORT (Stage 4):
{claude_review}

WHAT CLAUDE IMPLEMENTED (Stage 3 log):
{implementation_log}

THE ORIGINAL PLAN (from Deep Think):
{deep_think_plan}

YOUR TASK — INDEPENDENT VERIFICATION:

1. **READ THE CURRENT CODEBASE** below. Don't trust Claude's report blindly.
   Verify each claim independently.

2. **FOR EACH ISSUE CLAUDE FOUND**: Do you agree it's a real issue?
   - YES: Explain why you agree and confirm the suggested fix
   - NO: Explain why you disagree — maybe Claude is wrong
   - UNSURE: Flag it for cautious handling

3. **ISSUES CLAUDE MISSED**: Are there any problems Claude didn't catch?
   Look especially for:
   - Import chain issues (file A imports B which imports C — is C changed?)
   - Interface mismatches that only show up at runtime
   - Missing error handling for edge cases
   - Behavioral changes disguised as refactors

4. **CONSENSUS REPORT**: Produce a final list of AGREED fixes.
   Only include fixes where you independently verify the issue exists.

OUTPUT FORMAT:

## CONSENSUS REPORT

### AGREED FIXES (both Jim and Claude confirm these issues):
For each:
- **File**: path/to/file.py
- **Issue**: what's wrong
- **Fix**: exactly what to change
- **Priority**: CRITICAL / HIGH / MEDIUM / LOW

### DISPUTED (Claude flagged, Jim disagrees):
For each:
- **File**: path/to/file.py
- **Claude's claim**: what Claude said
- **Jim's assessment**: why Jim disagrees

### ADDITIONAL FINDINGS (Jim found, Claude missed):
For each:
- **File**: path/to/file.py
- **Issue**: what Jim found
- **Fix**: what to change

### OVERALL VERDICT
[READY FOR FIXES / NEEDS MORE ANALYSIS / ROLLBACK RECOMMENDED]

CURRENT CODEBASE:
{codebase}
"""


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    claude_review_path: Path,
    implementation_path: Path,
    deep_think_path: Path,
) -> Path:
    """Run consensus stage. Returns path to the output file."""
    output_path = cycle_dir / "05-consensus.md"

    logger.info("Stage 5: Consensus (Jim + Claude agreement)")

    claude_review = claude_review_path.read_text(encoding="utf-8")
    implementation_log = implementation_path.read_text(encoding="utf-8")
    deep_think_plan = deep_think_path.read_text(encoding="utf-8")

    # Load current codebase (with Stage 3's changes applied)
    codebase = load_codebase(config)

    prompt = _JIM_REVIEW_PROMPT.format(
        claude_review=claude_review,
        implementation_log=implementation_log,
        deep_think_plan=deep_think_plan,
        codebase=codebase,
    )

    # Run Jim (Gemini 3.1 Pro) — long timeout for full codebase analysis
    result = runner.run_gemini(prompt)

    output_path.write_text(result, encoding="utf-8")
    logger.info("Consensus report saved: %s (%d chars)", output_path, len(result))

    return output_path