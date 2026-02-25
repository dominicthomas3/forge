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

        while self._should_continue():
            self.cycle += 1
            cycle_dir = self.config.forge_data_dir / f"cycle-{self.cycle:03d}"
            cycle_dir.mkdir(parents=True, exist_ok=True)

            logger.info("")
            logger.info("=" * 60)
            logger.info("  CYCLE %d", self.cycle)
            logger.info("=" * 60)

            cycle_result = self._run_cycle(cycle_dir, previous_results)
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
        """Run one complete cycle (all 7 stages). Returns cycle result dict."""
        result: dict = {
            "cycle": self.cycle,
            "started_at": datetime.now().isoformat(),
            "stages_completed": [],
            "errors": [],
        }

        try:
            # Stage 1: Jim Analysis
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
            logger.info("--- Stage 2: Deep Think Verification ---")
            deep_think_path = stage_2_deep_think.run(
                cycle_dir=cycle_dir,
                config=self.config,
                runner=self.runner,
                jim_analysis_path=jim_path,
            )
            result["stages_completed"].append("deep_think")

            # Stage 3: Claude Implementation
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
            logger.info("--- Stage 6: Apply Fixes ---")
            fixes_path = stage_6_fixes.run(
                cycle_dir=cycle_dir,
                config=self.config,
                runner=self.runner,
                consensus_path=consensus_path,
                cycle_number=self.cycle,
            )
            result["stages_completed"].append("apply_fixes")

            # Stage 7: Stress Test
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
            result["stress_test"] = stress_content[:5000]

            # Determine stress test verdict
            # The stress report has 4 sections separated by "=" * 80.
            # Look for verdict signals across all sections:
            # - Pass 1 (structural): "PASS" or "FAIL" at start of line
            # - Pass 3 (Claude): "OVERALL VERDICT" followed by PASS/FAIL
            # - Pass 4 (Jim): "NO REGRESSIONS DETECTED" or regression list
            stress_lower = stress_content.lower()

            has_structural_fail = False
            has_claude_fail = False
            has_jim_fail = False

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

            if has_structural_fail or has_claude_fail:
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

            # Gather remaining issues for next cycle
            remaining = []
            if result["stress_verdict"] != "PASS":
                remaining.append(f"Stress test verdict: {result['stress_verdict']}")
                remaining.append(stress_content[:3000])
            result["remaining_issues"] = "\\n".join(remaining) if remaining else ""

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