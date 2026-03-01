"""Forge Orchestrator — the main pipeline loop.

Chains all 7 stages together in a convergence loop:

    Jim Analysis → Deep Think Verify → Claude Implement → Claude Review
    → Consensus (Jim + Claude agree) → Apply Fixes → Stress Test
    → [Loop back to Jim with results]

Runs until:
    - Convergence: N consecutive clean stress tests
    - Wall clock limit: stops before you wake up
    - Max cycles: hard cap on iterations

Every cycle gets a git checkpoint. Every stage output is saved.
The morning report summarizes everything.
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from forge.config import ForgeConfig
from forge.runner import Runner, RunnerError
from forge.stages import (
    stage_1_jim,
    stage_2_deep_think,
    stage_3_implement,
    stage_4_review,
    stage_5_consensus,
    stage_6_fixes,
    stage_7_stress,
)

logger = logging.getLogger("forge")


class Orchestrator:
    """Main pipeline loop. Manages cycles, convergence, and reporting."""

    def __init__(self, config: ForgeConfig, task_description: str):
        self.config = config
        self.task = task_description
        self.runner = Runner(config)
        self.cycle = 0
        self.start_time = time.time()
        self.consecutive_clean = 0
        self.cycle_results: list[dict] = []
        self.errors: list[str] = []

    # ── Main Loop ─────────────────────────────────────────────────────

    def run(self) -> Path:
        """Execute the full pipeline. Returns path to the morning report."""
        logger.info("=" * 80)
        logger.info("FORGE PIPELINE STARTED")
        logger.info("Task: %s", self.task[:200])
        logger.info("Target: %s", self.config.target_project)
        logger.info("Max cycles: %d, Wall limit: %dh, Convergence: %d clean passes",
                     self.config.max_cycles, self.config.max_wall_hours,
                     self.config.convergence_threshold)
        logger.info("=" * 80)

        # Create data directory
        self.config.forge_data_dir.mkdir(parents=True, exist_ok=True)

        # Optionally create a pipeline git branch
        if self.config.git_checkpoint:
            self._git_setup()

        previous_results = None

        # Check for an incomplete cycle from a previous crash.
        # If cycle-001/ exists but has no stress test output, we resume it
        # instead of starting cycle-002.
        existing_cycles = sorted(self.config.forge_data_dir.glob("cycle-*"))
        if existing_cycles:
            last_cycle_dir = existing_cycles[-1]
            stress_file = last_cycle_dir / "07-stress-test.md"
            cycle_num = int(last_cycle_dir.name.split("-")[1])
            if not stress_file.exists() or stress_file.stat().st_size < 50:
                # Incomplete cycle — resume it (skip to completed stages)
                logger.info(
                    "RESUME DETECTED: cycle-%03d has partial output — resuming mid-cycle",
                    cycle_num,
                )
                self.cycle = cycle_num - 1  # Will be incremented at loop start
                # Try to load stress test from the PREVIOUS completed cycle
                # so Jim gets failures/passes context instead of a fresh start.
                if cycle_num > 1:
                    prev_stress = self.config.forge_data_dir / f"cycle-{cycle_num - 1:03d}" / "07-stress-test.md"
                    if prev_stress.exists() and prev_stress.stat().st_size >= 50:
                        stress_content = prev_stress.read_text(encoding="utf-8", errors="replace")
                        prev_verdict = self._detect_verdict(stress_content)
                        previous_results = {
                            "stress_test": stress_content[:5000],
                            "stress_verdict": prev_verdict,
                        }
                        logger.info("Loaded previous stress test from cycle-%03d (verdict=%s)", cycle_num - 1, prev_verdict)
            else:
                # Complete cycle — skip it entirely, start next cycle.
                # Load the stress test results so Jim gets context.
                logger.info(
                    "RESUME DETECTED: cycle-%03d is complete — starting cycle %d",
                    cycle_num, cycle_num + 1,
                )
                self.cycle = cycle_num  # Will be incremented to cycle_num + 1
                stress_content = stress_file.read_text(encoding="utf-8", errors="replace")
                last_verdict = self._detect_verdict(stress_content)
                previous_results = {
                    "stress_test": stress_content[:5000],
                    "stress_verdict": last_verdict,
                }
                logger.info("Loaded stress test from cycle-%03d (verdict=%s)", cycle_num, last_verdict)

            # Count trailing consecutive clean passes from completed cycles.
            # This preserves convergence progress across pipeline restarts.
            for cyc_dir in reversed(existing_cycles):
                sf = cyc_dir / "07-stress-test.md"
                if not sf.exists() or sf.stat().st_size < 50:
                    break  # Incomplete cycle — stop counting
                content = sf.read_text(encoding="utf-8", errors="replace")
                if self._detect_verdict(content) == "PASS":
                    self.consecutive_clean += 1
                else:
                    break  # First non-clean cycle — stop counting
            if self.consecutive_clean > 0:
                logger.info(
                    "RESUME: %d consecutive clean pass(es) detected from previous cycles",
                    self.consecutive_clean,
                )

        while self._should_continue():
            self.cycle += 1
            cycle_dir = self.config.forge_data_dir / f"cycle-{self.cycle:03d}"
            cycle_dir.mkdir(parents=True, exist_ok=True)

            # Detect if this is a resume
            existing_stages = list(cycle_dir.glob("0*.md")) + list(cycle_dir.glob("0*.log"))
            if existing_stages:
                completed = [f.name for f in sorted(existing_stages)]
                logger.info("")
                logger.info("=" * 60)
                logger.info("  CYCLE %d (RESUMING — found: %s)", self.cycle, ", ".join(completed))
                logger.info("=" * 60)
            else:
                logger.info("")
                logger.info("=" * 60)
                logger.info("  CYCLE %d", self.cycle)
                logger.info("=" * 60)

            cycle_result = self._run_cycle(cycle_dir, previous_results)

            # If the cycle errored and we haven't retried yet, retry the SAME
            # cycle instead of moving on. The resume logic will skip completed
            # stages automatically. Cap at 3 retries per cycle to avoid infinite loops.
            retry_key = f"_retries_cycle_{self.cycle}"
            if not hasattr(self, retry_key):
                setattr(self, retry_key, 0)

            if cycle_result.get("stress_verdict") == "ERROR":
                retries = getattr(self, retry_key)
                if retries < 3:
                    setattr(self, retry_key, retries + 1)
                    logger.warning(
                        "Cycle %d errored — retrying (%d/3) with resume from completed stages",
                        self.cycle, retries + 1,
                    )
                    self.cycle -= 1  # Will be re-incremented at loop start
                    time.sleep(30)  # Brief cooldown before retry
                    continue
                else:
                    logger.error("Cycle %d failed after 3 retries — moving on", self.cycle)

            self.cycle_results.append(cycle_result)

            # Track convergence
            if cycle_result.get("stress_verdict") == "PASS":
                self.consecutive_clean += 1
                logger.info("Clean pass %d/%d",
                            self.consecutive_clean, self.config.convergence_threshold)
            else:
                self.consecutive_clean = 0
                logger.info("Issues found — convergence counter reset")

            # Git checkpoint
            if self.config.git_checkpoint:
                self._git_checkpoint(f"forge: cycle {self.cycle} complete")

            previous_results = cycle_result

        # Generate morning report
        report_path = self._generate_morning_report()
        logger.info("")
        logger.info("=" * 80)
        logger.info("FORGE PIPELINE COMPLETE")
        logger.info("Cycles: %d, Elapsed: %.1f hours",
                     self.cycle, (time.time() - self.start_time) / 3600)
        logger.info("Morning report: %s", report_path)
        logger.info("=" * 80)

        return report_path

    # ── Single Cycle ──────────────────────────────────────────────────

    def _run_cycle(self, cycle_dir: Path, previous_results: dict | None) -> dict:
        """Run one complete cycle with mid-cycle resume support.

        If the pipeline crashed mid-cycle, existing stage outputs in cycle_dir
        are detected and reused — only incomplete/missing stages are re-run.
        This means a crash at Stage 5 doesn't waste the 15 minutes Jim and
        Deep Think already spent on Stages 1-2.
        """
        result: dict = {
            "cycle": self.cycle,
            "started_at": datetime.now().isoformat(),
            "stages_completed": [],
            "errors": [],
        }

        # Known output files per stage. If the file exists and has real content,
        # that stage already finished — skip it and reuse the output.
        _STAGE_FILES = {
            1: "01-jim-analysis.md",
            2: "02-deep-think-verification.md",
            3: "03-claude-implementation.log",
            4: "04-claude-review.md",
            5: "05-consensus.md",
            6: "06-fixes-applied.log",
            7: "07-stress-test.md",
        }
        _MIN_OUTPUT_SIZE = 50  # bytes — smaller than this is a partial/corrupt write

        def _stage_done(stage_num: int) -> Path | None:
            """Return output path if stage already completed, else None."""
            path = cycle_dir / _STAGE_FILES[stage_num]
            if path.exists() and path.stat().st_size >= _MIN_OUTPUT_SIZE:
                return path
            return None

        try:
            # Stage 1: Jim Analysis
            jim_path = _stage_done(1)
            if jim_path:
                logger.info("--- Stage 1: Jim Analysis --- RESUMED (output exists, skipping)")
            else:
                logger.info("--- Stage 1: Jim Analysis ---")
                jim_path = stage_1_jim.run(
                    cycle_dir=cycle_dir,
                    config=self.config,
                    runner=self.runner,
                    task_description=self.task,
                    cycle_number=self.cycle,
                    previous_results=previous_results,
                )
            result["stages_completed"].append("jim_analysis")
            result["jim_analysis"] = jim_path.read_text(encoding="utf-8")[:5000]

            # Stage 2: Deep Think Verification
            deep_think_path = _stage_done(2)
            if deep_think_path:
                logger.info("--- Stage 2: Deep Think Verification --- RESUMED (output exists, skipping)")
            else:
                logger.info("--- Stage 2: Deep Think Verification ---")
                deep_think_path = stage_2_deep_think.run(
                    cycle_dir=cycle_dir,
                    config=self.config,
                    runner=self.runner,
                    jim_analysis_path=jim_path,
                )
            result["stages_completed"].append("deep_think")

            # Stage 3: Claude Implementation
            impl_path = _stage_done(3)
            if impl_path:
                logger.info("--- Stage 3: Claude Implementation --- RESUMED (output exists, skipping)")
            else:
                logger.info("--- Stage 3: Claude Implementation ---")
                impl_path = stage_3_implement.run(
                    cycle_dir=cycle_dir,
                    config=self.config,
                    runner=self.runner,
                    deep_think_path=deep_think_path,
                )
            result["stages_completed"].append("claude_implement")
            result["changes_applied"] = impl_path.read_text(encoding="utf-8")[:5000]

            # Stage 4: Claude Self-Review
            review_path = _stage_done(4)
            if review_path:
                logger.info("--- Stage 4: Claude Self-Review --- RESUMED (output exists, skipping)")
            else:
                logger.info("--- Stage 4: Claude Self-Review ---")
                review_path = stage_4_review.run(
                    cycle_dir=cycle_dir,
                    config=self.config,
                    runner=self.runner,
                    implementation_path=impl_path,
                    deep_think_path=deep_think_path,
                )
            result["stages_completed"].append("claude_review")

            # Stage 5: Consensus (Jim + Claude)
            consensus_path = _stage_done(5)
            if consensus_path:
                logger.info("--- Stage 5: Consensus --- RESUMED (output exists, skipping)")
            else:
                logger.info("--- Stage 5: Consensus ---")
                consensus_path = stage_5_consensus.run(
                    cycle_dir=cycle_dir,
                    config=self.config,
                    runner=self.runner,
                    claude_review_path=review_path,
                    implementation_path=impl_path,
                    deep_think_path=deep_think_path,
                )
            result["stages_completed"].append("consensus")

            # Stage 6: Apply Agreed Fixes
            fixes_path = _stage_done(6)
            if fixes_path:
                logger.info("--- Stage 6: Apply Fixes --- RESUMED (output exists, skipping)")
            else:
                logger.info("--- Stage 6: Apply Fixes ---")
                fixes_path = stage_6_fixes.run(
                    cycle_dir=cycle_dir,
                    config=self.config,
                    runner=self.runner,
                    consensus_path=consensus_path,
                    cycle_number=self.cycle,
                )
            result["stages_completed"].append("apply_fixes")

            # Stage 7: Stress Test — NEVER skip. Always re-run to verify current state.
            logger.info("--- Stage 7: Stress Test ---")
            stress_path = stage_7_stress.run(
                cycle_dir=cycle_dir,
                config=self.config,
                runner=self.runner,
                implementation_path=impl_path,
                fixes_path=fixes_path,
            )
            result["stages_completed"].append("stress_test")
            stress_content = stress_path.read_text(encoding="utf-8")

            # Build structured cycle summary for next cycle's Jim handoff.
            # Instead of dumping 5000 chars of raw test output, give Jim a
            # tight package: what changed → what was reviewed → what stress found.
            cycle_summary_parts = []

            # 1. What was implemented (from Stage 3)
            if impl_path.exists():
                impl_text = impl_path.read_text(encoding="utf-8", errors="replace")
                cycle_summary_parts.append(f"## Changes Implemented\n{impl_text[:1500]}")

            # 2. What review found (from Stage 4)
            review_path = cycle_dir / "04-claude-review.md"
            if review_path.exists():
                review_text = review_path.read_text(encoding="utf-8", errors="replace")
                # Extract just the ISSUES FOUND section
                if "ISSUES FOUND" in review_text:
                    issues_section = review_text.split("ISSUES FOUND", 1)[1][:1500]
                    cycle_summary_parts.append(f"## Review Issues\n{issues_section}")

            # 3. What consensus decided (from Stage 5)
            consensus_path = cycle_dir / "05-consensus.md"
            if consensus_path.exists():
                consensus_text = consensus_path.read_text(encoding="utf-8", errors="replace")
                cycle_summary_parts.append(f"## Consensus\n{consensus_text[:1000]}")

            # 4. Stress test verdicts only (not raw output)
            verdict_lines = []
            for line in stress_content.split("\n"):
                line_s = line.strip()
                if any(kw in line_s for kw in ["PASS", "FAIL", "VERDICT", "ISSUES FOUND"]):
                    verdict_lines.append(line_s)
            if verdict_lines:
                cycle_summary_parts.append(f"## Stress Test Verdicts\n" + "\n".join(verdict_lines[:30]))

            result["stress_test"] = "\n\n".join(cycle_summary_parts)[:5000]

            # Determine stress test verdict
            # The stress report has 5 sections separated by "=" * 80.
            # Look for verdict signals across all sections:
            # - Pass 1 (structural): "PASS" or "FAIL" at start of line
            # - Pass 3 (Claude): "OVERALL VERDICT" followed by PASS/FAIL
            # - Pass 4 (Jim): "NO REGRESSIONS DETECTED" or regression list
            # - Pass 5 (Token): "TOKEN EFFICIENCY VERDICT" followed by PASS/FAIL
            stress_lower = stress_content.lower()

            has_structural_fail = False
            has_claude_fail = False
            has_jim_fail = False
            has_token_fail = False

            # Split on section dividers for section-aware parsing
            sections = stress_content.split("=" * 80)

            for section in sections:
                sec_lower = section.lower()
                # Structural pass (Pass 1): check for "fail —" pattern
                if "structural tests" in sec_lower:
                    if "fail" in sec_lower.split("syntax check")[-1][:200] if "syntax check" in sec_lower else "":
                        has_structural_fail = True
                # Claude functional tests (Pass 3): explicit verdict
                if "overall verdict" in sec_lower:
                    verdict_area = sec_lower.split("overall verdict")[-1][:200]
                    if "fail" in verdict_area:
                        has_claude_fail = True
                # Jim regression scan (Pass 4)
                if "regression scan" in sec_lower:
                    if "no regressions detected" not in sec_lower:
                        has_jim_fail = True
                # Token efficiency audit (Pass 5)
                if "token efficiency verdict" in sec_lower:
                    token_verdict_area = sec_lower.split("token efficiency verdict")[-1][:200]
                    if "fail" in token_verdict_area:
                        has_token_fail = True

            if has_structural_fail or has_claude_fail or has_token_fail:
                result["stress_verdict"] = "FAIL"
            elif has_jim_fail:
                result["stress_verdict"] = "FAIL"
            elif "overall verdict" in stress_lower:
                # Found verdict section and no failures detected
                result["stress_verdict"] = "PASS"
            elif "no regressions detected" in stress_lower:
                result["stress_verdict"] = "PASS"
            else:
                # If unclear, assume issues remain
                result["stress_verdict"] = "UNCLEAR"

            # Gather remaining issues for next cycle — failures only, concise
            remaining = []
            if result["stress_verdict"] != "PASS":
                remaining.append(f"Stress test verdict: {result['stress_verdict']}")
                # Only include actual failure lines, not the entire raw dump
                for line in stress_content.split("\n"):
                    line_s = line.strip()
                    if "FAIL" in line_s or "ERROR" in line_s or "ISSUE" in line_s.upper():
                        remaining.append(line_s)
                        if len(remaining) >= 20:
                            break
            result["remaining_issues"] = "\n".join(remaining) if remaining else ""

        except RunnerError as e:
            error_msg = f"Stage failed: {e}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            result["remaining_issues"] = error_msg
            result["stress_verdict"] = "ERROR"
            self.errors.append(f"Cycle {self.cycle}: {error_msg}")

        except Exception as e:
            error_msg = f"Unexpected error: {type(e).__name__}: {e}"
            logger.exception(error_msg)
            result["errors"].append(error_msg)
            result["remaining_issues"] = error_msg
            result["stress_verdict"] = "ERROR"
            self.errors.append(f"Cycle {self.cycle}: {error_msg}")

        result["finished_at"] = datetime.now().isoformat()
        return result

    # ── Verdict Detection ─────────────────────────────────────────────

    @staticmethod
    def _detect_verdict(stress_content: str) -> str:
        """Parse a stress test report and return PASS, FAIL, or UNCLEAR.

        Uses section-aware parsing identical to _run_cycle's verdict logic.
        """
        stress_lower = stress_content.lower()
        sections = stress_content.split("=" * 80)

        has_structural_fail = False
        has_claude_fail = False
        has_jim_fail = False
        has_token_fail = False

        for section in sections:
            sec_lower = section.lower()
            if "structural tests" in sec_lower and "syntax check" in sec_lower:
                if "fail" in sec_lower.split("syntax check")[-1][:200]:
                    has_structural_fail = True
            if "overall verdict" in sec_lower:
                verdict_area = sec_lower.split("overall verdict")[-1][:200]
                if "fail" in verdict_area:
                    has_claude_fail = True
            if "regression scan" in sec_lower:
                if "no regressions detected" not in sec_lower:
                    has_jim_fail = True
            if "token efficiency verdict" in sec_lower:
                token_area = sec_lower.split("token efficiency verdict")[-1][:200]
                if "fail" in token_area:
                    has_token_fail = True

        if has_structural_fail or has_claude_fail or has_token_fail:
            return "FAIL"
        if has_jim_fail:
            return "FAIL"
        if "overall verdict" in stress_lower:
            return "PASS"
        if "no regressions detected" in stress_lower:
            return "PASS"
        return "UNCLEAR"

    # ── Convergence Check ─────────────────────────────────────────────

    def _should_continue(self) -> bool:
        """Three independent stopping conditions."""
        elapsed_hours = (time.time() - self.start_time) / 3600

        if self.consecutive_clean >= self.config.convergence_threshold:
            logger.info(
                "STOP: %d consecutive clean passes — converged",
                self.consecutive_clean,
            )
            return False

        if self.cycle >= self.config.max_cycles:
            logger.info("STOP: Max cycles (%d) reached", self.config.max_cycles)
            return False

        if elapsed_hours >= self.config.max_wall_hours:
            logger.info("STOP: Wall clock limit (%.1f hours) reached", elapsed_hours)
            return False

        return True

    # ── Git Operations ────────────────────────────────────────────────

    def _git_setup(self):
        """Create or switch to the pipeline branch."""
        try:
            # Check if we're in a git repo
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                cwd=str(self.config.target_project),
                check=True,
            )
            # Switch to branch if it exists, create if it doesn't
            switch = subprocess.run(
                ["git", "checkout", self.config.pipeline_branch],
                capture_output=True,
                text=True,
                cwd=str(self.config.target_project),
            )
            if switch.returncode != 0:
                # Branch doesn't exist — create it
                subprocess.run(
                    ["git", "checkout", "-b", self.config.pipeline_branch],
                    capture_output=True,
                    text=True,
                    cwd=str(self.config.target_project),
                )
            logger.info("Git: on branch %s", self.config.pipeline_branch)
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Git not available or not a git repo — checkpoints disabled")
            self.config.git_checkpoint = False

    def _git_checkpoint(self, message: str):
        """Commit current state as a checkpoint."""
        try:
            # Stage tracked changes (avoids accidentally committing .env or secrets)
            subprocess.run(
                ["git", "add", "-u"],
                capture_output=True,
                cwd=str(self.config.target_project),
                check=True,
            )
            # Commit (might fail if nothing to commit — that's fine)
            result = subprocess.run(
                ["git", "commit", "-m", message, "--allow-empty"],
                capture_output=True,
                text=True,
                cwd=str(self.config.target_project),
            )
            if result.returncode == 0:
                logger.info("Git checkpoint: %s", message)
            else:
                logger.debug("Git commit skipped (no changes or error)")
        except Exception as e:
            logger.warning("Git checkpoint failed: %s", e)

    # ── Morning Report ────────────────────────────────────────────────

    def _generate_morning_report(self) -> Path:
        """Compile everything into a single morning briefing."""
        report_path = self.config.forge_data_dir / "morning-report.md"
        elapsed = (time.time() - self.start_time) / 3600

        # Determine final status
        if self.consecutive_clean >= self.config.convergence_threshold:
            status = "CONVERGED"
        elif any(cr.get("stress_verdict") == "PASS" for cr in self.cycle_results):
            status = "PARTIALLY CONVERGED"
        elif self.errors:
            status = "STOPPED WITH ERRORS"
        else:
            status = "STOPPED (limits reached)"

        lines = [
            "# Forge Overnight Report",
            f"## {datetime.now().strftime('%B %d, %Y — %I:%M %p')}",
            "",
            f"**Status:** {status}",
            f"**Cycles completed:** {self.cycle}",
            f"**Wall time:** {elapsed:.1f} hours",
            f"**Cost:** $0.00 (Max + Pro Ultra subscriptions)",
            f"**Target project:** {self.config.target_project}",
            "",
            f"## Task",
            f"{self.task}",
            "",
        ]

        # Per-cycle summary
        lines.append("## Cycle Summary")
        lines.append("")
        lines.append("| Cycle | Stages | Stress Test | Issues |")
        lines.append("|-------|--------|-------------|--------|")
        for cr in self.cycle_results:
            stages = len(cr.get("stages_completed", []))
            verdict = cr.get("stress_verdict", "N/A")
            errors = len(cr.get("errors", []))
            lines.append(
                f"| {cr['cycle']} | {stages}/7 | {verdict} | {errors} errors |"
            )
        lines.append("")

        # Errors
        if self.errors:
            lines.append("## Errors Encountered")
            for err in self.errors:
                lines.append(f"- {err}")
            lines.append("")

        # Git checkpoints
        if self.config.git_checkpoint:
            lines.append("## Git Checkpoints")
            lines.append(f"Branch: `{self.config.pipeline_branch}`")
            lines.append("")
            try:
                git_log = subprocess.run(
                    ["git", "log", "--oneline", "-20", self.config.pipeline_branch],
                    capture_output=True,
                    text=True,
                    cwd=str(self.config.target_project),
                )
                lines.append("```")
                lines.append(git_log.stdout.strip())
                lines.append("```")
            except Exception:
                lines.append("(git log unavailable)")
            lines.append("")

        # Stage outputs for last cycle
        last_cycle_details = ""
        if self.cycle_results:
            lines.append("## Last Cycle Details")
            lines.append("")

            last_cycle_dir = self.config.forge_data_dir / f"cycle-{self.cycle:03d}"
            stage_files = sorted(last_cycle_dir.glob("*.md")) + sorted(last_cycle_dir.glob("*.log"))
            for sf in stage_files:
                lines.append(f"### {sf.name}")
                content = sf.read_text(encoding="utf-8", errors="replace")
                # Truncate for readability
                if len(content) > 3000:
                    content = content[:3000] + "\\n\\n... (truncated — see full file)"
                lines.append(content)
                lines.append("")
                last_cycle_details += f"### {sf.name}\\n{content}\\n\\n"

        # Ask Jim for an Executive Summary and Next Steps
        logger.info("Generating AI executive summary and brainstorming...")
        prompt = (
            "You are Jim, the lead architect. The automated Forge pipeline just finished running.\\n"
            f"Original Task: {self.task}\\n\\n"
            "The pipeline has successfully completed its cycles. Write a final report for the user containing:\\n"
            "1. An 'Executive Summary' detailing exactly what was done and improved. Use very simple, plain English context.\\n"
            "2. A 'Brainstorming / Next Steps' section where you recommend the best next parts of the codebase to upgrade or optimize. \\n"
            "   CRITICAL: Explain these recommendations using easy-to-understand analogies and a philosophical approach to software engineering (like building a Ferrari or a finely tuned machine).\\n\\n"
            f"--- LAST CYCLE DETAILS ---\\n{last_cycle_details[:30000]}"
        )
        try:
            ai_summary = self.runner.run_gemini(prompt, timeout=300)
            lines.insert(12, "## AI Executive Summary & Next Steps\\n" + ai_summary + "\\n")
        except Exception as e:
            logger.warning(f"Failed to generate AI summary: {e}")
            lines.insert(12, f"## AI Executive Summary\\n*(Summary generation failed: {e})*\\n")

        # Rollback instructions
        lines.append("## Rollback")
        lines.append("If anything looks wrong:")
        lines.append("```bash")
        lines.append(f"cd {self.config.target_project}")
        if self.cycle_results:
            lines.append(f"git log --oneline {self.config.pipeline_branch}")
            lines.append("git checkout <commit-hash>  # roll back to any checkpoint")
        lines.append("```")

        report = "\\n".join(lines)
        report_path.write_text(report, encoding="utf-8")
        return report_path