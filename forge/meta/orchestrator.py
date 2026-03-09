"""Meta-Orchestrator: the closed-loop Morpheus <-> Forge pipeline.

Ties Morpheus (behavioral testing) and Forge (autonomous development) into
a single self-improving cycle:

    Morpheus tests Spectre → generates findings + upgrade recommendations
    → Forge implements upgrades → Forge stress tests → converges
    → Morpheus tests again (targeted at what changed)
    → repeat until quality converges or limits reached

Key design decisions:
    1. Morpheus runs as a SUBPROCESS (fresh Python = fresh Spectre imports)
    2. Forge runs via asyncio.to_thread (sync internally, wrapped for async)
    3. All handoffs are JSON files on disk (crash-resilient, inspectable)
    4. Static file-to-category mapping (no LLM cost for targeting)
    5. Oscillation detection via codebase hash + grade trend analysis
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from forge.config import ForgeConfig
from forge.meta.contracts import (
    ForgeHandoff,
    IterationSnapshot,
    MetaState,
    MorpheusTargetingConfig,
    UpgradeRecommendation,
    grade_dropped,
    grade_to_numeric,
)
from forge.meta.targeting import build_targeting_config, get_changed_files
from forge.orchestrator import Orchestrator
from forge.runner import Runner

logger = logging.getLogger("forge.meta")


class MetaOrchestrator:
    """Top-level loop: Morpheus <-> Forge cycling autonomously."""

    def __init__(
        self,
        config: ForgeConfig,
        max_iterations: int = 5,
        max_wall_hours: float = 12.0,
        total_exchanges: int = 30,
    ):
        self.config = config
        self.max_iterations = max_iterations
        self.max_wall_hours = max_wall_hours
        self.total_exchanges = total_exchanges
        self.runner = Runner(config)

        # State directories
        self.meta_dir = config.forge_data_dir / "meta"
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.meta_dir / "meta_state.json"

        # Load or create state
        self.state = MetaState.load(self.state_path)
        if not self.state.started_at:
            self.state.started_at = datetime.now().isoformat()

    async def run(self) -> Path:
        """Execute the full meta-loop. Returns path to final report."""
        logger.info("=" * 80)
        logger.info("META-LOOP STARTED")
        logger.info("Target: %s", self.config.target_project)
        logger.info("Max iterations: %d, Wall limit: %.1fh", self.max_iterations, self.max_wall_hours)
        logger.info("Exchanges per Morpheus session: %d", self.total_exchanges)
        logger.info("=" * 80)

        start_time = time.time()
        targeting_config: MorpheusTargetingConfig | None = None
        last_handoff: ForgeHandoff | None = None

        # Resume from saved state if mid-iteration
        # When resuming, we need to re-run the current iteration (not skip to next).
        # The loop increments current_iteration at the top, so decrement here to
        # re-process the interrupted iteration.
        if self.state.status in ("FORGING", "EVALUATING"):
            logger.info("RESUME: Meta-loop was mid-%s at iteration %d — re-running",
                        self.state.status, self.state.current_iteration)
            self.state.current_iteration = max(0, self.state.current_iteration - 1)

        while self._should_continue(start_time):
            self.state.current_iteration += 1
            iteration = self.state.current_iteration
            iter_dir = self.meta_dir / f"iter_{iteration:03d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            snapshot = IterationSnapshot(
                iteration=iteration,
                started_at=datetime.now().isoformat(),
            )

            logger.info("")
            logger.info("=" * 60)
            logger.info("  META-ITERATION %d", iteration)
            logger.info("=" * 60)

            # ── Step 1: Run Morpheus (subprocess for clean Spectre imports) ──
            self.state.status = "EVALUATING"
            self._save_state()

            morpheus_report_path = iter_dir / "morpheus_session.md"
            morpheus_json_path = iter_dir / "morpheus_report.json"

            logger.info("--- Running Morpheus evaluation ---")
            targeting_path = iter_dir / "targeting_config.json"
            if targeting_config:
                targeting_config.save(targeting_path)

            success = await self._run_morpheus_subprocess(
                report_path=morpheus_report_path,
                json_path=morpheus_json_path,
                targeting_path=targeting_path if targeting_config else None,
            )

            if not success:
                logger.error("Morpheus failed — skipping to next iteration")
                snapshot.finished_at = datetime.now().isoformat()
                snapshot.morpheus_grade = "ERROR"
                self.state.history.append(snapshot)
                self._save_state()
                continue

            # ── Step 2: Parse Morpheus results ──
            grade, scores, recommendations, do_not_break = self._parse_morpheus_output(
                morpheus_json_path, morpheus_report_path
            )
            snapshot.morpheus_grade = grade
            snapshot.morpheus_scores = scores
            snapshot.recommendation_count = len(recommendations)

            logger.info("Morpheus grade: %s, recommendations: %d", grade, len(recommendations))

            # ── Step 3: Check convergence / oscillation / degradation ──
            if self._check_quality_convergence(grade, recommendations):
                logger.info("QUALITY CONVERGED — grade %s with 0 recommendations", grade)
                snapshot.finished_at = datetime.now().isoformat()
                self.state.history.append(snapshot)
                self.state.status = "CONVERGED"
                self._save_state()
                break

            if self._check_oscillation():
                logger.warning("OSCILLATION DETECTED — stopping meta-loop")
                snapshot.finished_at = datetime.now().isoformat()
                self.state.history.append(snapshot)
                self.state.status = "STOPPED"
                self._save_state()
                break

            if len(self.state.history) >= 1 and grade_dropped(grade, self.state.best_grade):
                # Guard: can't roll back to iteration 0 (no checkpoint exists yet)
                if self.state.best_iteration == 0:
                    logger.warning(
                        "GRADE DEGRADATION: %s -> %s — no checkpoint to roll back to (iteration 0), continuing",
                        self.state.best_grade, grade,
                    )
                    # No rollback possible — fall through to Forge to attempt a fix
                else:
                    logger.warning(
                        "GRADE DEGRADATION: %s -> %s — rolling back to iteration %d",
                        self.state.best_grade, grade, self.state.best_iteration,
                    )
                    self._rollback_to_best()
                    snapshot.finished_at = datetime.now().isoformat()
                    snapshot.forge_verdict = "ROLLED_BACK"
                    self.state.history.append(snapshot)
                    self._save_state()
                    continue

            # Track best grade
            if grade_to_numeric(grade) > grade_to_numeric(self.state.best_grade):
                self.state.best_grade = grade
                self.state.best_iteration = iteration

            # ── Step 4: Generate Forge handoff ──
            handoff = ForgeHandoff(
                iteration=iteration,
                overall_grade=grade,
                category_scores=scores,
                prioritized_tasks=recommendations,
                do_not_break=do_not_break,
                previous_iteration_summary=(
                    last_handoff.to_jim_prompt()[:2000] if last_handoff else ""
                ),
                morpheus_session_path=str(morpheus_report_path),
            )
            handoff_path = iter_dir / "forge_handoff.json"
            handoff.save(handoff_path)
            last_handoff = handoff

            # ── Step 5: Git checkpoint before Forge modifies code ──
            pre_forge_hash = self._get_codebase_hash()
            self._git_tag(f"meta-checkpoint-{iteration}")

            # ── Step 6: Run Forge ──
            self.state.status = "FORGING"
            self._save_state()

            logger.info("--- Running Forge with Morpheus recommendations ---")
            forge_success, forge_cycles = await self._run_forge(handoff.to_jim_prompt())
            snapshot.forge_verdict = "PASS" if forge_success else "FAIL"
            snapshot.forge_cycles = forge_cycles
            self.state.total_forge_cycles += forge_cycles

            # ── Step 7: Extract diff and build targeting for next Morpheus ──
            post_forge_hash = self._get_codebase_hash()
            snapshot.codebase_hash = post_forge_hash
            self.state.codebase_hashes.append(post_forge_hash)

            changed = get_changed_files(self.config.target_project, base_ref=pre_forge_hash)
            snapshot.changed_files = changed

            # Build targeting config for next Morpheus run
            forge_summary = f"Forge completed {forge_cycles} cycles. Grade was {grade}."
            targeting_config = build_targeting_config(
                changed_files=changed,
                forge_summary=forge_summary,
                iteration=iteration,
            )

            snapshot.finished_at = datetime.now().isoformat()
            self.state.history.append(snapshot)
            self._save_state()

            logger.info(
                "Iteration %d complete: grade=%s, forge_cycles=%d, files_changed=%d",
                iteration, grade, forge_cycles, len(changed),
            )

        # ── Generate final report ──
        report_path = self._generate_meta_report()

        logger.info("")
        logger.info("=" * 80)
        logger.info("META-LOOP COMPLETE")
        logger.info("Iterations: %d, Total Forge cycles: %d",
                     self.state.current_iteration, self.state.total_forge_cycles)
        logger.info("Best grade: %s (iteration %d)", self.state.best_grade, self.state.best_iteration)
        logger.info("Report: %s", report_path)
        logger.info("=" * 80)

        return report_path

    # ── Morpheus Subprocess ──────────────────────────────────────────────

    async def _run_morpheus_subprocess(
        self,
        report_path: Path,
        json_path: Path,
        targeting_path: Path | None = None,
    ) -> bool:
        """Run Morpheus as a subprocess for clean Spectre imports.

        Returns True if Morpheus completed successfully.
        """
        cmd = [
            sys.executable, "-m", "forge.run_morpheus",
            "--target", str(self.config.target_project),
            "--exchanges", str(max(1, self.total_exchanges // 6)),  # per category
            "--meta-json-output", str(json_path),
        ]
        if targeting_path and targeting_path.exists():
            cmd.extend(["--targeting-config", str(targeting_path)])
        # Pass API key via environment variable (not CLI args — visible in ps/tasklist)
        env_override = {}
        if self.config.google_api_key:
            env_override["GOOGLE_API_KEY"] = self.config.google_api_key

        logger.info("Morpheus subprocess: %s", " ".join(cmd[:6]) + "...")

        try:
            # Merge env overrides (API key) into a clean copy of the environment
            import os as _os
            sub_env = _os.environ.copy()
            sub_env.update(env_override)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).parent.parent.parent),  # forge/ root
                env=sub_env,
            )

            # Monitor with timeout
            timeout = 3600  # 1 hour max for Morpheus
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                logger.error("Morpheus subprocess timed out after %ds", timeout)
                return False

            if proc.returncode != 0:
                logger.error(
                    "Morpheus subprocess failed (exit %d): %s",
                    proc.returncode,
                    (stderr or b"").decode("utf-8", errors="replace")[-2000:],
                )
                return False

            # Check output file exists
            if json_path.exists():
                logger.info("Morpheus completed — JSON report at %s", json_path)
                return True

            # Fallback: check markdown report
            if report_path.exists():
                logger.info("Morpheus completed — markdown report at %s (no JSON)", report_path)
                return True

            logger.error("Morpheus completed but produced no output files")
            return False

        except Exception as e:
            logger.error("Morpheus subprocess error: %s", e)
            return False

    # ── Morpheus Output Parsing ──────────────────────────────────────────

    def _parse_morpheus_output(
        self,
        json_path: Path,
        report_path: Path,
    ) -> tuple[str, dict[str, float], list[UpgradeRecommendation], list[str]]:
        """Parse Morpheus session output into structured data.

        Returns: (grade, scores, recommendations, do_not_break)
        """
        grade = "C"
        scores: dict[str, float] = {}
        recommendations: list[UpgradeRecommendation] = []
        do_not_break: list[str] = []

        # Try JSON first (structured output from enhanced Morpheus)
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                grade = data.get("overall_grade", "C")
                scores = data.get("category_scores", {})

                for rec in data.get("next_upgrades", []):
                    recommendations.append(UpgradeRecommendation(
                        priority=rec.get("priority", 99),
                        target=rec.get("target", "unknown"),
                        action=rec.get("action", ""),
                        category=rec.get("category", ""),
                        evidence=rec.get("evidence", ""),
                    ))

                do_not_break = data.get("do_not_break", [])
                return grade, scores, recommendations, do_not_break
            except Exception as e:
                logger.warning("Failed to parse Morpheus JSON: %s — falling back to markdown", e)

        # Fallback: parse markdown report with regex
        if report_path.exists():
            text = report_path.read_text(encoding="utf-8", errors="replace")
            grade, scores, recommendations, do_not_break = self._parse_evaluation_text(text)

        return grade, scores, recommendations, do_not_break

    def _parse_evaluation_text(
        self, text: str
    ) -> tuple[str, dict[str, float], list[UpgradeRecommendation], list[str]]:
        """Extract structured data from Morpheus evaluation markdown."""
        grade = "C"
        scores: dict[str, float] = {}
        recommendations: list[UpgradeRecommendation] = []
        do_not_break: list[str] = []

        # Extract grade
        grade_match = re.search(r"Overall Grade:\s*([A-F][+-]?)", text, re.IGNORECASE)
        if grade_match:
            grade = grade_match.group(1).upper()

        # Extract category scores (e.g., "| Routing | 8/10 |")
        score_pattern = re.compile(r"\|\s*(\w+)\s*\|\s*(\d+)/10\s*\|")
        for match in score_pattern.finditer(text):
            category = match.group(1).lower()
            score = float(match.group(2))
            scores[category] = score
            if score >= 8:
                do_not_break.append(f"{category} (scored {score}/10)")

        # Extract <next_upgrades> JSON block if present
        upgrades_match = re.search(
            r"<next_upgrades>\s*(.*?)\s*</next_upgrades>",
            text, re.DOTALL,
        )
        if upgrades_match:
            try:
                upgrades_data = json.loads(upgrades_match.group(1))
                for rec in upgrades_data:
                    recommendations.append(UpgradeRecommendation(
                        priority=rec.get("priority", 99),
                        target=rec.get("target", "unknown"),
                        action=rec.get("action", ""),
                        category=rec.get("category", ""),
                        evidence=rec.get("evidence", ""),
                    ))
            except json.JSONDecodeError:
                logger.warning("Failed to parse <next_upgrades> JSON block")

        # Fallback: extract recommendations from "Recommendations" section
        if not recommendations:
            rec_match = re.search(
                r"## Recommendations\s*\n(.*?)(?=\n## |\Z)",
                text, re.DOTALL,
            )
            if rec_match:
                rec_text = rec_match.group(1)
                for i, line in enumerate(rec_text.strip().split("\n"), 1):
                    line = line.strip().lstrip("-").lstrip("0123456789.").strip()
                    if line and len(line) > 10:
                        recommendations.append(UpgradeRecommendation(
                            priority=i,
                            target="spectre",
                            action=line,
                            category="general",
                        ))

        return grade, scores, recommendations, do_not_break

    # ── Forge Execution ──────────────────────────────────────────────────

    async def _run_forge(self, task_description: str) -> tuple[bool, int]:
        """Run the Forge pipeline (sync orchestrator wrapped in async thread).

        Returns: (converged: bool, cycles_run: int)
        """
        # Create a fresh config for Forge with reduced limits
        # (meta-loop handles the outer convergence)
        forge_config = ForgeConfig(
            google_api_key=self.config.google_api_key,
            gemini_cli_path=self.config.gemini_cli_path,
            claude_cli_path=self.config.claude_cli_path,
            target_project=self.config.target_project,
            forge_data_dir=self.config.forge_data_dir,
            gemini_model=self.config.gemini_model,
            claude_model=self.config.claude_model,
            deep_think_model=self.config.deep_think_model,
            max_cycles=10,             # Reduced — meta-loop handles outer convergence
            max_wall_hours=3.0,        # Don't let one Forge run consume all time
            convergence_threshold=2,   # Faster inner convergence
            git_checkpoint=self.config.git_checkpoint,
            pipeline_branch=self.config.pipeline_branch,
        )

        orchestrator = Orchestrator(forge_config, task_description)

        try:
            report_path = await asyncio.to_thread(orchestrator.run)
            converged = orchestrator.consecutive_clean >= forge_config.convergence_threshold
            cycles = orchestrator.cycle
            logger.info(
                "Forge completed: %d cycles, converged=%s, report=%s",
                cycles, converged, report_path,
            )
            return converged, cycles
        except Exception as e:
            logger.error("Forge failed: %s", e)
            return False, orchestrator.cycle if hasattr(orchestrator, "cycle") else 0

    # ── Convergence Checks ───────────────────────────────────────────────

    def _should_continue(self, start_time: float) -> bool:
        """Check meta-loop stopping conditions."""
        if self.state.status in ("CONVERGED", "FAILED"):
            return False

        if self.state.current_iteration >= self.max_iterations:
            logger.info("META STOP: Max iterations (%d) reached", self.max_iterations)
            self.state.status = "STOPPED"
            self._save_state()
            return False

        elapsed_hours = (time.time() - start_time) / 3600
        if elapsed_hours >= self.max_wall_hours:
            logger.info("META STOP: Wall clock limit (%.1fh) reached", elapsed_hours)
            self.state.status = "STOPPED"
            self._save_state()
            return False

        return True

    def _check_quality_convergence(self, grade: str, recommendations: list) -> bool:
        """Grade is A+ or A with zero recommendations for 2 consecutive iterations."""
        if grade_to_numeric(grade) < grade_to_numeric("A"):
            return False
        if recommendations:
            return False
        # Check if previous iteration was also A+ with no recommendations
        if self.state.history:
            last = self.state.history[-1]
            if grade_to_numeric(last.morpheus_grade) >= grade_to_numeric("A") and last.recommendation_count == 0:
                return True
        return False

    def _check_oscillation(self) -> bool:
        """Detect if the meta-loop is oscillating (same codebase hash seen 3+ times)."""
        if len(self.state.codebase_hashes) < 3:
            return False

        # Check for exact hash repetition
        hash_counts: dict[str, int] = {}
        for h in self.state.codebase_hashes:
            hash_counts[h] = hash_counts.get(h, 0) + 1
            if hash_counts[h] >= 3:
                logger.warning("Oscillation: codebase hash %s seen %d times", h[:12], hash_counts[h])
                return True

        # Check for grade plateau (same grade for 3+ iterations)
        if len(self.state.history) >= 3:
            recent_grades = [h.morpheus_grade for h in self.state.history[-3:]]
            if len(set(recent_grades)) == 1:
                grade_val = recent_grades[0]
                logger.info("Grade plateau detected: %s for 3 iterations", grade_val)
                # Stop if at B or above (plateau at C means keep trying)
                # Also stop if ERROR plateau (something is fundamentally broken)
                if grade_val == "ERROR":
                    logger.warning("ERROR plateau — something is fundamentally broken")
                    return True
                if grade_to_numeric(grade_val) >= grade_to_numeric("B"):
                    return True

        return False

    # ── Git Operations ───────────────────────────────────────────────────

    def _get_codebase_hash(self) -> str:
        """Get current git HEAD hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True,
                cwd=str(self.config.target_project),
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _git_tag(self, tag_name: str) -> None:
        """Create a lightweight git tag for rollback."""
        try:
            subprocess.run(
                ["git", "tag", "-f", tag_name],
                capture_output=True, text=True,
                cwd=str(self.config.target_project),
            )
            logger.info("Git tag: %s", tag_name)
        except Exception as e:
            logger.warning("Git tag failed: %s", e)

    def _rollback_to_best(self) -> None:
        """Roll back to the best known checkpoint.

        Uses 'git reset --hard' only (no 'git clean -fd') to avoid deleting
        untracked files that may contain user data, configs, or work-in-progress.
        New files added by Forge since the checkpoint become untracked but are
        NOT deleted — safe for manual review.
        """
        tag = f"meta-checkpoint-{self.state.best_iteration}"
        cwd = str(self.config.target_project)
        try:
            # Stash any uncommitted changes as a safety net before hard reset
            subprocess.run(
                ["git", "stash", "push", "-m", f"meta-rollback-safety-{tag}"],
                capture_output=True, text=True, cwd=cwd,
            )
            result = subprocess.run(
                ["git", "reset", "--hard", tag],
                capture_output=True, text=True, cwd=cwd,
            )
            if result.returncode == 0:
                logger.info("Rolled back to %s (untracked files preserved)", tag)
            else:
                logger.warning("Rollback failed: %s", result.stderr)
        except Exception as e:
            logger.warning("Rollback error: %s", e)

    # ── State Management ─────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist state atomically."""
        self.state.save(self.state_path)

    # ── Report Generation ────────────────────────────────────────────────

    def _generate_meta_report(self) -> Path:
        """Generate the final meta-loop report."""
        report_path = self.meta_dir / "meta-report.md"

        lines = [
            "# Forge Meta-Loop Report",
            f"## {datetime.now().strftime('%B %d, %Y - %I:%M %p')}",
            "",
            f"**Status:** {self.state.status}",
            f"**Iterations completed:** {self.state.current_iteration}",
            f"**Total Forge cycles:** {self.state.total_forge_cycles}",
            f"**Best grade:** {self.state.best_grade} (iteration {self.state.best_iteration})",
            "",
            "## Iteration History",
            "",
            "| # | Morpheus Grade | Forge Verdict | Forge Cycles | Files Changed | Recommendations |",
            "|---|---------------|---------------|-------------|--------------|----------------|",
        ]

        for snap in self.state.history:
            lines.append(
                f"| {snap.iteration} | {snap.morpheus_grade} | {snap.forge_verdict} "
                f"| {snap.forge_cycles} | {len(snap.changed_files)} | {snap.recommendation_count} |"
            )
        lines.append("")

        # Grade progression
        if self.state.history:
            lines.append("## Grade Progression")
            grades = [h.morpheus_grade for h in self.state.history]
            lines.append(" -> ".join(grades))
            lines.append("")

        # Per-iteration details
        for snap in self.state.history:
            lines.append(f"### Iteration {snap.iteration}")
            lines.append(f"- **Grade:** {snap.morpheus_grade}")
            lines.append(f"- **Scores:** {snap.morpheus_scores}")
            lines.append(f"- **Forge:** {snap.forge_verdict} ({snap.forge_cycles} cycles)")
            if snap.changed_files:
                lines.append(f"- **Changed:** {', '.join(snap.changed_files[:10])}")
            lines.append("")

        lines.append("## Rollback")
        lines.append("To roll back to any iteration checkpoint:")
        lines.append("```bash")
        lines.append(f"cd {self.config.target_project}")
        lines.append("git tag -l 'meta-checkpoint-*'  # list checkpoints")
        lines.append("git reset --hard meta-checkpoint-N  # roll back to iteration N")
        lines.append("```")

        report = "\n".join(lines)
        report_path.write_text(report, encoding="utf-8")
        return report_path
