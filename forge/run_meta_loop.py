#!/usr/bin/env python3
"""Meta-Loop — Autonomous Morpheus <-> Forge upgrade pipeline.

Usage:
    # Standard run (5 meta-iterations, 12h max)
    python -m forge.run_meta_loop --target C:/Users/domin/Projects/spectre

    # Quick run (2 iterations, 4h max)
    python -m forge.run_meta_loop --target C:/Users/domin/Projects/spectre --iterations 2 --max-hours 4

    # Heavy overnight run (10 iterations, all night)
    python -m forge.run_meta_loop --target C:/Users/domin/Projects/spectre --iterations 10 --max-hours 10

    # Dry run (validate config)
    python -m forge.run_meta_loop --target C:/Users/domin/Projects/spectre --dry-run

Pipeline:
    1. Morpheus tests Spectre comprehensively (subprocess for clean imports)
    2. Morpheus evaluates: grade, scores, recommendations
    3. Forge implements top recommendations (7-stage consensus loop)
    4. Morpheus re-tests — TARGETED at what Forge changed
    5. Repeat until grade converges or limits reached
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from forge.config import ForgeConfig


def setup_logging(forge_data_dir: Path) -> None:
    """Configure logging to both console and file."""
    meta_dir = forge_data_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    log_file = meta_dir / f"meta-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    logging.info("Logging to: %s", log_file)


async def run_meta_loop(args, config: ForgeConfig) -> None:
    """Run the meta-loop."""
    from forge.meta.orchestrator import MetaOrchestrator

    meta = MetaOrchestrator(
        config=config,
        max_iterations=args.iterations,
        max_wall_hours=args.max_hours,
        total_exchanges=args.exchanges,
    )

    report_path = await meta.run()

    print("\n" + "=" * 60)
    print("  META-LOOP COMPLETE")
    print("=" * 60)
    print(f"  Status: {meta.state.status}")
    print(f"  Iterations: {meta.state.current_iteration}")
    print(f"  Total Forge cycles: {meta.state.total_forge_cycles}")
    print(f"  Best grade: {meta.state.best_grade} (iteration {meta.state.best_iteration})")
    print(f"  Report: {report_path}")
    print("=" * 60)


def main():
    # Strip CLAUDECODE env vars to prevent nested CLI detection
    import os
    for key in list(os.environ.keys()):
        if "CLAUDECODE" in key.upper():
            del os.environ[key]

    parser = argparse.ArgumentParser(
        description="Meta-Loop — Autonomous Morpheus <-> Forge upgrade pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pipeline flow:
  1. Morpheus tests Spectre (comprehensive behavioral audit)
  2. Morpheus generates upgrade recommendations
  3. Forge implements recommendations (7-stage consensus loop)
  4. Morpheus re-tests (targeted at what changed)
  5. Repeat until quality converges

Cost: $0 (Max + Pro Ultra subscriptions) + ~$0.003/exchange for Spectre API
""",
    )

    parser.add_argument(
        "--target", type=Path, required=True,
        help="Target Spectre project directory",
    )
    parser.add_argument(
        "--iterations", type=int, default=5,
        help="Max meta-iterations (default: 5)",
    )
    parser.add_argument(
        "--max-hours", type=float, default=12.0,
        help="Max wall clock hours (default: 12)",
    )
    parser.add_argument(
        "--exchanges", type=int, default=30,
        help="Total Morpheus exchanges per session (default: 30)",
    )
    parser.add_argument(
        "--google-api-key", type=str, default=None,
        help="Google API key (or set GOOGLE_API_KEY env var)",
    )
    parser.add_argument(
        "--claude-model", type=str, default="opus",
        help="Claude model for Forge (default: opus)",
    )
    parser.add_argument(
        "--no-git", action="store_true",
        help="Disable git checkpointing",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate configuration and exit",
    )

    args = parser.parse_args()

    # Build config
    config = ForgeConfig(target_project=args.target.resolve())
    if args.google_api_key:
        config.google_api_key = args.google_api_key
    if args.claude_model:
        config.claude_model = args.claude_model
    if args.no_git:
        config.git_checkpoint = False

    # Setup logging
    setup_logging(config.forge_data_dir)

    # Display
    print("\n" + "=" * 60)
    print("  META-LOOP — Autonomous Morpheus <-> Forge Pipeline")
    print("=" * 60)
    print(f"  Target:      {config.target_project}")
    print(f"  Iterations:  {args.iterations}")
    print(f"  Wall limit:  {args.max_hours}h")
    print(f"  Exchanges:   {args.exchanges} per Morpheus session")
    print(f"  Forge model: {config.claude_model}")
    print(f"  Git:         {'enabled' if config.git_checkpoint else 'disabled'}")
    print(f"  Cost:        $0 (subscriptions) + ~${args.exchanges * 0.004 * args.iterations:.2f} Spectre API")
    print("=" * 60)

    # Validate
    issues = config.validate()
    if issues:
        print("\nConfiguration issues:")
        for issue in issues:
            print(f"  ! {issue}")

    if args.dry_run:
        print("\nDry run complete.")
        sys.exit(0)

    # Run
    print("\nStarting meta-loop...\n")
    try:
        asyncio.run(run_meta_loop(args, config))
    except KeyboardInterrupt:
        print("\nMeta-loop interrupted.")
        sys.exit(130)
    except Exception as e:
        logging.exception("Meta-loop crashed")
        print(f"\nMeta-loop crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
