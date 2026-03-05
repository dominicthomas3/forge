#!/usr/bin/env python3
"""Forge Dashboard — Real-time web UI for the pipeline.

Usage:
    # Review mode — browse existing cycle data
    python -m forge.run_dashboard

    # Live mode — launch pipeline with dashboard
    python -m forge.run_dashboard --mode live --task "Your task description"

    # Custom port and data directory
    python -m forge.run_dashboard --port 9090 --data-dir /path/to/.forge_data
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Strip CLAUDECODE env var — same as run_overnight.py
os.environ.pop("CLAUDECODE", None)

from forge.config import ForgeConfig


def setup_logging(forge_data_dir: Path) -> None:
    """Configure logging to both console and file."""
    forge_data_dir.mkdir(parents=True, exist_ok=True)
    log_file = forge_data_dir / f"dashboard-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

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


def ensure_port_available(port: int) -> None:
    """Kill any zombie process holding the port from a previous run."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
    finally:
        sock.close()
    if result != 0:
        return  # Port is free

    print(f"  [CLEANUP] Port {port} is in use — killing stale process...")
    if platform.system() == "Windows":
        try:
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                parts = line.strip().split()
                # Match exact port in local address column (e.g., "127.0.0.1:8080")
                if len(parts) >= 5 and "LISTENING" in line:
                    local_addr = parts[1]
                    if local_addr.endswith(f":{port}"):
                        pid = parts[-1]
                        if pid.isdigit():
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True, timeout=5,
                            )
                            print(f"  [CLEANUP] Killed PID {pid}")
                            time.sleep(2)
                            break
        except Exception as e:
            print(f"  [CLEANUP] Warning: {e}")
    else:
        try:
            subprocess.run(
                ["fuser", "-k", f"{port}/tcp"],
                capture_output=True, timeout=5,
            )
            time.sleep(2)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Forge Dashboard — Real-time pipeline web UI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  review  Browse existing cycle data (default, no pipeline execution)
  live    Launch the pipeline and show real-time progress

Examples:
  forge-dashboard                                    # Review existing data
  forge-dashboard --mode live --task "Fix type errors"  # Live pipeline
  forge-dashboard --port 9090                        # Custom port
""",
    )

    parser.add_argument(
        "--mode", choices=["live", "review"], default="review",
        help="Dashboard mode: 'live' runs pipeline, 'review' browses data (default: review)",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Web server port (default: 8080)",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="Path to .forge_data directory (default: forge/forge/.forge_data)",
    )
    parser.add_argument(
        "--target", type=Path, default=None,
        help="Target project directory (for codebase stats)",
    )

    # Live mode options
    live_group = parser.add_argument_group("Live mode options")
    task_group = live_group.add_mutually_exclusive_group()
    task_group.add_argument(
        "--task", type=str,
        help="Task description for pipeline (required in live mode)",
    )
    task_group.add_argument(
        "--task-file", type=Path,
        help="Path to a file containing the task description",
    )
    live_group.add_argument(
        "--max-cycles", type=int, default=None,
        help="Maximum number of cycles (default: 50)",
    )
    live_group.add_argument(
        "--max-hours", type=float, default=None,
        help="Maximum wall clock hours (default: 8)",
    )
    live_group.add_argument(
        "--no-git", action="store_true",
        help="Disable git checkpointing",
    )
    live_group.add_argument(
        "--google-api-key", type=str, default=None,
        help="Google API key for Deep Think",
    )

    args = parser.parse_args()

    # Build config
    config = ForgeConfig()
    if args.target:
        config.target_project = args.target.resolve()
    if args.data_dir:
        config.forge_data_dir = args.data_dir.resolve()
    if args.max_cycles is not None:
        config.max_cycles = args.max_cycles
    if args.max_hours is not None:
        config.max_wall_hours = args.max_hours
    if args.no_git:
        config.git_checkpoint = False
    if args.google_api_key:
        config.google_api_key = args.google_api_key

    # Setup logging
    setup_logging(config.forge_data_dir)

    # ── Kill zombie processes from previous runs ─────────────────────
    ensure_port_available(args.port)

    # Validate live mode requirements
    task_description = None
    if args.mode == "live":
        if args.task_file:
            task_description = args.task_file.read_text(encoding="utf-8").strip()
        elif args.task:
            task_description = args.task
        else:
            parser.error("Live mode requires --task or --task-file")

    # Late imports — NiceGUI takes a moment to initialize
    from nicegui import app, ui

    from forge.dashboard import ForgeDashboard, attach_log_handler
    from forge.events import EventBus

    # ── pywebview native window config (must be set before ui.run) ───
    app.native.window_args['resizable'] = True
    app.native.window_args['min_size'] = (900, 600)
    app.native.window_args['easy_drag'] = True
    app.native.window_args['confirm_close'] = False  # no "are you sure?" dialog

    # Force EdgeChromium (WebView2) renderer on Windows for proper WebSocket support.
    # MSHTML (IE) fallback does NOT support WebSocket reliably.
    app.native.start_args['private_mode'] = False  # enables font caching
    if platform.system() == "Windows":
        app.native.start_args['gui'] = 'edgechromium'

    # ── Clean shutdown: when window closes, kill the server ──────────
    def _graceful_exit():
        logging.shutdown()
        os._exit(0)  # Hard exit after flushing logs — NiceGUI server won't stop otherwise

    app.on_shutdown(_graceful_exit)

    # Build the dashboard
    event_bus = EventBus() if args.mode == "live" else None
    dashboard = ForgeDashboard(
        config=config,
        event_bus=event_bus,
        mode=args.mode,
    )

    @ui.page("/")
    def index():
        dashboard.build()

        # Attach log handler after UI is built
        attach_log_handler(dashboard)

        # In live mode, start the pipeline in a background task
        if args.mode == "live" and task_description:
            async def run_pipeline():
                from forge.orchestrator import Orchestrator

                orchestrator = Orchestrator(
                    config=config,
                    task_description=task_description,
                )
                # Connect event buses
                orchestrator.event_bus = event_bus

                # Capture the running event loop for cross-thread event dispatch
                loop = asyncio.get_running_loop()
                event_bus.set_loop(loop)

                # Run pipeline in a thread (it's synchronous, blocking)
                try:
                    await loop.run_in_executor(None, orchestrator.run)
                except Exception as e:
                    logging.exception("Pipeline crashed: %s", e)

            asyncio.get_running_loop().create_task(run_pipeline())

    # Print startup info
    print()
    print("=" * 60)
    print("  THE FORGE v0.0.1")
    print("=" * 60)
    print(f"  Mode:    {args.mode}")
    print(f"  Port:    {args.port}")
    print(f"  Data:    {config.forge_data_dir}")
    print(f"  Target:  {config.target_project}")
    if task_description:
        print(f"  Task:    {task_description[:80]}{'...' if len(task_description) > 80 else ''}")
    print("=" * 60)
    print()

    # Launch NiceGUI as a native desktop window (pywebview)
    # native=True renders in a real OS window, not a browser tab
    # frameless=True removes OS chrome — custom title bar provides controls
    # reconnect_timeout=30.0 fixes "connection lost" flicker on initial load
    # storage_secret enables session persistence across reconnects
    # on_air=None prevents NiceGUI from attempting external connectivity
    ui.run(
        title="The Forge",
        host="127.0.0.1",
        port=args.port,
        reload=False,
        show=True,
        dark=True,
        native=True,
        window_size=(1400, 900),
        frameless=True,
        reconnect_timeout=30.0,
        storage_secret=os.environ.get("FORGE_SESSION_SECRET", "forge-session-" + str(os.getpid())),
        on_air=None,
    )


if __name__ == "__main__":
    main()
