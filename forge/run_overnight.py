#!/usr/bin/env python3
"""Forge — Run the overnight autonomous development pipeline.

Usage:
    # With inline task description
    python -m forge.run_overnight --task "Replace LangChain with direct SDK calls"

    # With task from a file
    python -m forge.run_overnight --task-file task.md

    # With custom target project
    python -m forge.run_overnight --target /path/to/project --task "Fix all type errors"

    # Dry run (validate config, don't execute)
    python -m forge.run_overnight --task "..." --dry-run

    # Override safety rails
    python -m forge.run_overnight --task "..." --max-cycles 10 --max-hours 4

Examples:
    # The LangChain replacement task (default when targeting Spectre)
    python -m forge.run_overnight 
        --task "Remove all LangChain dependencies and replace with direct Anthropic SDK + google-genai SDK calls. Replace LangGraph StateGraph with a custom async state machine. Replace @AppData\Roaming\Antigravity\logs\20260225T112700\window1\exthost\google.chrome-devtools-mcp\Chrome DevTools MCP.log decorators with custom tool schema generation. All business logic must remain unchanged." 
        --max-hours 8

    # Run a quick 2-cycle test to verify the pipeline works
    python -m forge.run_overnight 
        --task "Add type hints to all functions in core/router.py" 
        --max-cycles 2 --max-hours 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from forge.config import ForgeConfig
from forge.orchestrator import Orchestrator


def setup_logging(forge_data_dir: Path) -> None:
    """Configure logging to both console and file."""
    forge_data_dir.mkdir(parents=True, exist_ok=True)
    log_file = forge_data_dir / f"forge-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — INFO level, concise
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # File handler — DEBUG level, verbose
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    logging.info("Logging to: %s", log_file)


def main():
    parser = argparse.ArgumentParser(
        description="Forge — Multi-model autonomous development pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline stages:
  1. Jim (Gemini 3.1 Pro) — Full codebase analysis + plan
  2. Deep Think            — Claude crafts prompt → Deep Think verifies plan
  3. Claude Code           — Implements the verified plan
  4. Claude Review         — Self-review (finds errors, does NOT fix)
  5. Consensus             — Jim + Claude must agree on fixes
  6. Apply Fixes           — Claude applies agreed-upon fixes only
  7. Stress Test           — Structural + functional + regression testing
  → Loop back to Stage 1 with results

Runs on subscriptions: Claude Max + Google AI Pro Ultra = $0 marginal cost.
""",
    )

    # Task specification
    task_group = parser.add_mutually_exclusive_group(required=True)
    task_group.add_argument(
        "--task", type=str,
        help="Task description (what Forge should accomplish)",
    )
    task_group.add_argument(
        "--task-file", type=Path,
        help="Path to a file containing the task description",
    )

    # Target project
    parser.add_argument(
        "--target", type=Path, default=None,
        help="Target project directory (default: Spectre project root)",
    )

    # Safety rails
    parser.add_argument(
        "--max-cycles", type=int, default=None,
        help="Maximum number of cycles (default: 50)",
    )
    parser.add_argument(
        "--max-hours", type=float, default=None,
        help="Maximum wall clock hours (default: 8)",
    )
    parser.add_argument(
        "--convergence", type=int, default=None,
        help="Consecutive clean passes needed (default: 3)",
    )

    # Model overrides
    parser.add_argument(
        "--claude-model", type=str, default=None,
        help="Claude model: 'opus' or 'sonnet' (default: opus)",
    )
    parser.add_argument(
        "--gemini-model", type=str, default=None,
        help="Gemini model ID (default: gemini-3.1-pro-preview)",
    )

    # Credentials
    parser.add_argument(
        "--google-api-key", type=str, default=None,
        help="Google API key for Deep Think (or set GOOGLE_API_KEY env var)",
    )

    # Utility
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate configuration and exit without running",
    )
    parser.add_argument(
        "--no-git", action="store_true",
        help="Disable git checkpointing",
    )

    args = parser.parse_args()

    # ── Build config ──────────────────────────────────────────────────

    config = ForgeConfig()

    if args.target:
        config.target_project = args.target.resolve()
    if args.max_cycles is not None:
        config.max_cycles = args.max_cycles
    if args.max_hours is not None:
        config.max_wall_hours = args.max_hours
    if args.convergence is not None:
        config.convergence_threshold = args.convergence
    if args.claude_model:
        config.claude_model = args.claude_model
    if args.gemini_model:
        config.gemini_model = args.gemini_model
        config.deep_think_model = args.gemini_model
    if args.google_api_key:
        config.google_api_key = args.google_api_key
    if args.no_git:
        config.git_checkpoint = False

    # ── Setup logging ─────────────────────────────────────────────────

    setup_logging(config.forge_data_dir)

    # ── Load task ─────────────────────────────────────────────────────

    if args.task_file:
        task_description = args.task_file.read_text(encoding="utf-8").strip()
    else:
        task_description = args.task

    if not task_description:
        parser.error("Task description is empty")

    # ── Validate config ───────────────────────────────────────────────

    print("\n" + "=" * 60)
    print("  FORGE — Autonomous Development Pipeline")
    print("=" * 60)
    print(f"  Target:      {config.target_project}")
    print(f"  Task:        {task_description[:100]}{'...' if len(task_description) > 100 else ''}")
    print(f"  Max cycles:  {config.max_cycles}")
    print(f"  Max hours:   {config.max_wall_hours}")
    print(f"  Convergence: {config.convergence_threshold} clean passes")
    print(f"  Claude:      {config.claude_model}")
    print(f"  Gemini:      {config.gemini_model}")
    print(f"  Deep Think:  {config.deep_think_model}")
    print(f"  Git:         {'enabled' if config.git_checkpoint else 'disabled'}")
    print(f"  Cost:        $0.00 (subscriptions)")
    print("=" * 60)

    issues = config.validate()
    if issues:
        print("\nConfiguration issues:")
        for issue in issues:
            print(f"  ! {issue}")

        if args.dry_run:
            print("\nDry run complete. Fix the issues above before running.")
            sys.exit(1)

        # Non-fatal issues: warn but continue if not dry run
        # (CLIs might not be detectable in this environment but work on the user's machine)
        print("\nProceeding despite warnings (CLIs may work at runtime)...")
    else:
        print("\nConfiguration validated.")

    if args.dry_run:
        print("Dry run complete. Everything looks good.")
        sys.exit(0)

    # ── Run pipeline ──────────────────────────────────────────────────

    print("\nStarting pipeline...\n")
    orchestrator = Orchestrator(config=config, task_description=task_description)

    try:
        report_path = orchestrator.run()
        print(f"\nPipeline complete. Morning report: {report_path}")
    except KeyboardInterrupt:
        print("\nPipeline interrupted by user.")
        report_path = orchestrator._generate_morning_report()
        print(f"Partial report saved: {report_path}")
        sys.exit(130)
    except Exception as e:
        logging.exception("Pipeline crashed")
        report_path = orchestrator._generate_morning_report()
        print(f"\nPipeline crashed: {e}")
        print(f"Partial report saved: {report_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()