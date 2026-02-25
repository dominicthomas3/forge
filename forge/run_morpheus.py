#!/usr/bin/env python3
"""Morpheus — Run a sparring session with Spectre.

Usage:
    # Standard session (5 exchanges per category, all categories)
    python -m forge.run_morpheus

    # Quick session (3 exchanges per category)
    python -m forge.run_morpheus --exchanges 3

    # Focus on specific categories
    python -m forge.run_morpheus --categories memory tools routing

    # Heavy session (10 exchanges per category)
    python -m forge.run_morpheus --exchanges 10

    # Dry run (validate config, don't run)
    python -m forge.run_morpheus --dry-run

Examples:
    # First time running — Morpheus will introduce himself at the end
    python -m forge.run_morpheus --exchanges 5

    # After first session — Spectre already knows Morpheus
    python -m forge.run_morpheus --exchanges 8 --categories casual memory personality

    # Quick routing test
    python -m forge.run_morpheus --exchanges 3 --categories routing
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from forge.config import ForgeConfig
from forge.morpheus import Morpheus
from forge.runner import Runner


def setup_logging(forge_data_dir: Path) -> None:
    """Configure logging to both console and file."""
    morpheus_dir = forge_data_dir / "morpheus"
    morpheus_dir.mkdir(parents=True, exist_ok=True)
    log_file = morpheus_dir / f"morpheus-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

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


async def run_session(args, config: ForgeConfig) -> None:
    """Run the Morpheus session."""
    runner = Runner(config)
    morpheus = Morpheus(config=config, runner=runner)

    report = await morpheus.run_session(
        exchanges_per_category=args.exchanges,
        categories=args.categories,
    )

    print("\n" + "=" * 60)
    print("  MORPHEUS SESSION COMPLETE")
    print("=" * 60)
    print(f"  Exchanges: {report.total_messages}")
    print(f"  API cost (Spectre): ${report.total_cost:.4f}")
    print(f"  First session: {report.first_session}")
    print(f"  Report: {config.forge_data_dir / 'morpheus'}")
    print("=" * 60)

    if report.first_session:
        print("\n  Morpheus introduced himself. Spectre will remember.")
        print("  Future sessions start naturally — no re-introduction needed.\n")


def main():
    parser = argparse.ArgumentParser(
        description="Morpheus — Spectre's adaptive sparring partner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Categories:
  casual       — Natural conversation, personality depth
  memory       — Fact planting and recall testing
  routing      — Model selection (Sonnet/Opus/Pro/Flash triggers)
  tools        — Tool invocation (system info, files, reminders)
  personality  — Identity, values, self-awareness
  edge         — Empty messages, long inputs, prompt injection

Session flow:
  1. Morpheus has natural conversations across selected categories
  2. Messages adapt based on Spectre's responses (escalate or probe)
  3. First session only: Morpheus introduces himself at the end
  4. Claude Opus evaluates the full transcript
  5. Report saved to .forge_data/morpheus/

Cost: ~$0.003-0.005 per exchange (Spectre API). Generation + evaluation: $0 (subscriptions).
""",
    )

    parser.add_argument(
        "--exchanges", type=int, default=5,
        help="Exchanges per category (default: 5)",
    )
    parser.add_argument(
        "--categories", nargs="+", default=None,
        choices=["casual", "memory", "routing", "tools", "personality", "edge"],
        help="Categories to test (default: all)",
    )
    parser.add_argument(
        "--target", type=Path, default=None,
        help="Target project directory (default: Spectre project root)",
    )
    parser.add_argument(
        "--google-api-key", type=str, default=None,
        help="Google API key (or set GOOGLE_API_KEY env var)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate configuration and exit",
    )

    args = parser.parse_args()

    # Build config
    config = ForgeConfig()
    if args.target:
        config.target_project = args.target.resolve()
    if args.google_api_key:
        config.google_api_key = args.google_api_key

    # Setup logging
    setup_logging(config.forge_data_dir)

    # Display
    total_exchanges = args.exchanges * len(args.categories or ["casual", "memory", "routing", "tools", "personality", "edge"])
    est_cost = total_exchanges * 0.004

    print("\n" + "=" * 60)
    print("  MORPHEUS — Spectre's Sparring Partner")
    print("=" * 60)
    print(f"  Target:     {config.target_project}")
    print(f"  Categories: {', '.join(args.categories) if args.categories else 'all (6)'}")
    print(f"  Exchanges:  {args.exchanges} per category ({total_exchanges} total)")
    print(f"  Est. cost:  ~${est_cost:.2f} (Spectre API)")
    print(f"  Generation: Gemini 3.1 Pro ($0 — Pro Ultra)")
    print(f"  Evaluation: Claude Opus ($0 — Max)")

    from forge.morpheus import _is_first_session
    if _is_first_session(config):
        print(f"  First session: YES — Morpheus will introduce himself")
    else:
        print(f"  First session: No — Spectre already knows Morpheus")
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
    print("\nStarting session...\n")
    try:
        asyncio.run(run_session(args, config))
    except KeyboardInterrupt:
        print("\nSession interrupted.")
        sys.exit(130)
    except Exception as e:
        logging.exception("Session crashed")
        print(f"\nSession crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()