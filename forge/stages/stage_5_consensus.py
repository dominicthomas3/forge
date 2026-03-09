"""Stage 5: Consensus — Jim and Claude must agree before fixes are applied.

This is the quality gate. Claude's review (Stage 4) identified issues.
Now Jim (Gemini Pro) independently reviews the same codebase — BLIND to
Claude's findings — and produces its own list. Only fixes that BOTH
independently identify (by file path) proceed to Stage 6.

This prevents single-model hallucinations from corrupting the codebase.
If Claude thinks there's a bug but Jim doesn't independently find it,
it doesn't get "fixed" (which might introduce a real bug). True consensus.

Two phases:
    A) Jim reviews independently (no Claude review shown)
    B) Python intersects Jim's + Claude's findings by file path
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from forge.codebase import load_codebase
from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_5")

# ── Jim's BLIND independent review prompt ────────────────────────────────
# Jim does NOT see Claude's review. This eliminates anchoring bias.

_JIM_BLIND_REVIEW_PROMPT = """
CURRENT CODEBASE:
{codebase}

---

You are Jim — the senior analyst on the Forge autonomous development pipeline.
Stage 3 (Claude) implemented changes. You are reviewing the codebase
INDEPENDENTLY. You have NOT seen Claude's review — form your own assessment.

WHAT WAS IMPLEMENTED (Stage 3 log):
{implementation_log}

THE ORIGINAL PLAN (from Deep Think):
{deep_think_plan}

YOUR TASK — INDEPENDENT CODE REVIEW:

Review the codebase above and find ALL issues with the implementation:

1. **CORRECTNESS** — Does the code do what the plan intended?
   - Are all planned changes actually implemented?
   - Are there logic errors, off-by-ones, missing conditions?

2. **SAFETY** — Does the code introduce regressions?
   - Import chain issues (file A imports B which imports C — is C changed?)
   - Interface mismatches that only show up at runtime
   - Missing error handling for edge cases
   - Behavioral changes disguised as refactors

3. **COMPLETENESS** — Is anything missing?
   - Partial implementations
   - Missing test updates
   - Config or documentation gaps

IMPORTANT — KNOWN FALSE POSITIVES (do NOT flag):
- The `@tool` decorator in `tools/*.py` is CORRECT. It comes from `tools/core.py`.
- The memory/ and cortex/ ChromaDB files ARE in scope for Phase 2.

OUTPUT FORMAT — STRUCTURED FOR PROGRAMMATIC PARSING:

For EACH issue found, output a block like this:

### ISSUE
- **File**: path/to/file.py
- **Line**: approximate line number or range
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW
- **Description**: what's wrong
- **Fix**: exactly what to change

If no issues found, output: "NO ISSUES FOUND — implementation looks clean."
"""


def _extract_file_paths(text: str) -> set[str]:
    """Extract file paths mentioned in a review as issue targets.

    Only matches the structured `**File**: path/to/file.py` format that
    appears in the issue output template. We intentionally do NOT match
    backtick-wrapped paths (e.g., `core/router.py`) because LLMs
    routinely mention files they APPROVED in backticks, which would
    create false positive consensus on clean files.

    Returns a set of normalized forward-slash paths.
    """
    paths: set[str] = set()

    # Pattern: **File**: path/to/file.py (the structured issue format)
    for match in re.finditer(r'\*\*File\*\*:\s*`?([^\s`\n]+\.\w+)`?', text):
        paths.add(match.group(1).replace("\\", "/").strip())

    return paths


def _compute_consensus(
    claude_review: str,
    jim_review: str,
) -> str:
    """Programmatically intersect Jim's and Claude's findings by file path.

    Only files flagged by BOTH reviewers become agreed fixes.
    """
    claude_files = _extract_file_paths(claude_review)
    jim_files = _extract_file_paths(jim_review)

    agreed_files = claude_files & jim_files
    claude_only = claude_files - jim_files
    jim_only = jim_files - claude_files

    lines = [
        "## CONSENSUS REPORT (Programmatic Intersection)",
        "",
        f"**Claude flagged:** {len(claude_files)} files",
        f"**Jim flagged:** {len(jim_files)} files",
        f"**Both agree:** {len(agreed_files)} files",
        "",
    ]

    if agreed_files:
        lines.append("### AGREED FIXES (both Jim and Claude independently flagged):")
        lines.append("")
        for f in sorted(agreed_files):
            lines.append(f"- **{f}**")
        lines.append("")
        lines.append("*See Jim's independent review (05a) and Claude's review (04) for details on each.*")
    else:
        lines.append("### NO AGREED FIXES")
        lines.append("Jim and Claude did not independently flag any of the same files.")

    # Severity-weighted escalation: CRITICAL findings from either model
    # should still proceed even without consensus (too dangerous to ignore).
    critical_pattern = re.compile(r'\*\*Severity\*\*:\s*CRITICAL', re.IGNORECASE)
    critical_claude = critical_pattern.findall(claude_review)
    critical_jim = critical_pattern.findall(jim_review)
    if (critical_claude or critical_jim) and not agreed_files:
        lines.append("")
        lines.append("### CRITICAL SEVERITY ESCALATION")
        lines.append("One or both reviewers found CRITICAL issues. These proceed even without consensus:")
        if critical_claude:
            lines.append(f"- Claude found {len(critical_claude)} CRITICAL issue(s)")
        if critical_jim:
            lines.append(f"- Jim found {len(critical_jim)} CRITICAL issue(s)")
        lines.append("*Stage 6 should address CRITICAL findings from either reviewer.*")

    lines.append("")

    if claude_only:
        lines.append("### CLAUDE-ONLY FINDINGS (Jim did not flag — proceed with caution):")
        for f in sorted(claude_only):
            lines.append(f"- {f}")
        lines.append("")

    if jim_only:
        lines.append("### JIM-ONLY FINDINGS (Claude did not flag — additional issues):")
        for f in sorted(jim_only):
            lines.append(f"- {f}")
        lines.append("")

    # Overall verdict
    has_critical = bool(critical_claude or critical_jim)
    if agreed_files:
        lines.append("### OVERALL VERDICT: READY FOR FIXES")
        lines.append(f"{len(agreed_files)} agreed file(s) should be fixed in Stage 6.")
    elif has_critical:
        lines.append("### OVERALL VERDICT: CRITICAL ESCALATION — FIX REQUIRED")
        lines.append("No file consensus, but CRITICAL issues found. Stage 6 must address them.")
    elif claude_only or jim_only:
        lines.append("### OVERALL VERDICT: NEEDS CAREFUL REVIEW")
        lines.append("No consensus — only single-model findings. Proceed cautiously.")
    else:
        lines.append("### OVERALL VERDICT: NO FIXES NEEDED")
        lines.append("Neither reviewer found issues. Implementation looks clean.")

    return "\n".join(lines)


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    claude_review_path: Path,
    implementation_path: Path,
    deep_think_path: Path,
    codebase: str | None = None,
) -> Path:
    """Run consensus stage with blind review + programmatic intersection.

    Phase A: Jim reviews independently (does NOT see Claude's review).
    Phase B: Python intersects findings by file path.

    Returns path to the consensus report.
    """
    output_path = cycle_dir / "05-consensus.md"
    jim_independent_path = cycle_dir / "05a-jim-independent-review.md"

    logger.info("Stage 5: Consensus (blind review + programmatic intersection)")

    claude_review = claude_review_path.read_text(encoding="utf-8")
    implementation_log = implementation_path.read_text(encoding="utf-8")
    deep_think_plan = deep_think_path.read_text(encoding="utf-8")

    # Use pre-loaded codebase if provided, otherwise load fresh
    if codebase is None:
        codebase = load_codebase(config)

    # ── Phase A: Jim's BLIND independent review ──────────────────────
    # Jim does NOT see Claude's Stage 4 review — prevents anchoring bias.
    prompt = _JIM_BLIND_REVIEW_PROMPT.format(
        codebase=codebase,
        implementation_log=implementation_log,
        deep_think_plan=deep_think_plan,
    )

    # Inject stage-specific review supplement
    from forge.worker_blueprint import JIM_REVIEW_SUPPLEMENT
    prompt = JIM_REVIEW_SUPPLEMENT + "\n" + prompt

    logger.info("Phase A: Jim blind independent review")
    jim_review = runner.run_gemini(prompt, blueprint="full")

    # Save Jim's independent review separately
    jim_independent_path.write_text(jim_review, encoding="utf-8")
    logger.info("Jim independent review saved: %s (%d chars)", jim_independent_path, len(jim_review))

    # ── Phase B: Programmatic intersection ───────────────────────────
    logger.info("Phase B: Programmatic intersection of findings")
    consensus = _compute_consensus(claude_review, jim_review)

    # Compile full report
    full_report = (
        consensus
        + "\n\n---\n\n"
        + "## Jim's Independent Review (for reference)\n\n"
        + jim_review[:5000]
    )

    output_path.write_text(full_report, encoding="utf-8")
    logger.info("Consensus report saved: %s (%d chars)", output_path, len(full_report))

    return output_path
