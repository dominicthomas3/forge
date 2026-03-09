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
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

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


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    """Block until the NiceGUI server accepts connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1)
            sock.close()
            return
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"NiceGUI server didn't start within {timeout}s")


class _WindowApi:
    """Exposed to JavaScript as window.pywebview.api for custom window controls."""

    def __init__(self) -> None:
        self._window: Any = None
        self._maximized: bool = False

    def close(self) -> None:
        if self._window:
            self._window.destroy()

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def maximize(self) -> None:
        if self._window:
            try:
                if self._maximized:
                    self._window.restore()
                else:
                    self._window.maximize()
                self._maximized = not self._maximized
            except AttributeError:
                # Fallback for older pywebview without maximize/restore
                self._window.toggle_fullscreen()


def _find_edge_binary() -> str | None:
    """Find Microsoft Edge binary on the system."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_in_path = shutil.which("msedge") or shutil.which("microsoft-edge")
    if edge_in_path:
        candidates.insert(0, edge_in_path)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _find_chrome_binary() -> str | None:
    """Find Google Chrome binary on the system."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_in_path = shutil.which("chrome") or shutil.which("google-chrome")
    if chrome_in_path:
        candidates.insert(0, chrome_in_path)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _launch_edge_fallback(port: int) -> None:
    """Fallback: open Edge/Chrome in app mode if pywebview fails."""
    url = f"http://127.0.0.1:{port}"
    browser = _find_edge_binary() or _find_chrome_binary()
    if browser:
        subprocess.Popen(
            [browser, f"--app={url}", "--window-size=1400,900"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  [FALLBACK] Opened app window via {Path(browser).name}")
    else:
        import webbrowser
        webbrowser.open(url)
        print("  [FALLBACK] Opened in default browser")


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

    # ── NiceGUI imports ──────────────────────────────────────────────
    from nicegui import app, ui

    from forge.dashboard import ForgeDashboard, attach_log_handler
    from forge.events import EventBus

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
        attach_log_handler(dashboard)

        if args.mode == "live" and task_description:
            async def run_pipeline():
                # Guard: prevent duplicate orchestrators on page refresh
                if hasattr(app, '_forge_pipeline_running') and app._forge_pipeline_running:
                    logging.warning("Pipeline already running — ignoring duplicate launch from page refresh")
                    return
                app._forge_pipeline_running = True

                from forge.orchestrator import Orchestrator

                orchestrator = Orchestrator(
                    config=config,
                    task_description=task_description,
                )
                orchestrator.event_bus = event_bus

                loop = asyncio.get_running_loop()
                event_bus.set_loop(loop)

                try:
                    await loop.run_in_executor(None, orchestrator.run)
                except Exception as e:
                    logging.exception("Pipeline crashed: %s", e)
                finally:
                    app._forge_pipeline_running = False

            asyncio.get_running_loop().create_task(run_pipeline())

    # Print startup info
    from forge.version import FORGE_VERSION
    port = args.port
    print()
    print("=" * 60)
    print(f"  THE FORGE v{FORGE_VERSION}")
    print("=" * 60)
    print(f"  Mode:    {args.mode}")
    print(f"  Port:    {port}")
    print(f"  Data:    {config.forge_data_dir}")
    print(f"  Target:  {config.target_project}")
    if task_description:
        print(f"  Task:    {task_description[:80]}{'...' if len(task_description) > 80 else ''}")
    print("=" * 60)
    print()

    # ── Launch mode: pywebview (frameless) or Edge app (fallback) ────
    #
    # pywebview gives a TRUE frameless window with custom title bar.
    # Edge app mode shows a native title bar (can't remove it).
    # We ALWAYS try pywebview first.

    try:
        import webview
        has_pywebview = True
        _wv_ver = "?"
        try:
            from importlib.metadata import version as _pkg_version
            _wv_ver = _pkg_version("pywebview")
        except Exception:
            pass
        print(f"  [NATIVE] pywebview {_wv_ver} found")
    except ImportError:
        has_pywebview = False
        print("  [WARN] pywebview not installed — buttons may not fully work")
        print("         Install with: pip install pywebview")

    # Start NiceGUI server in a daemon thread
    def _run_server():
        ui.run(
            title="The Forge",
            host="127.0.0.1",
            port=port,
            reload=False,
            show=False,
            dark=True,
            native=False,         # Server only — no NiceGUI native mode
            reconnect_timeout=30.0,
            storage_secret=os.environ.get(
                "FORGE_SESSION_SECRET", secrets.token_hex(32)
            ),
            on_air=None,
        )

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    try:
        _wait_for_server(port)
    except RuntimeError as e:
        print(f"  [ERROR] {e}")
        sys.exit(1)

    if has_pywebview:
        try:
            print("  [NATIVE] Server ready, opening frameless window...")

            # Drag region is handled by a dedicated empty div in dashboard.py
            # (no child elements = no click interception issues)

            api = _WindowApi()
            window = webview.create_window(
                "The Forge",
                f"http://127.0.0.1:{port}",
                width=1400,
                height=900,
                frameless=True,
                easy_drag=False,      # CRITICAL: prevents click interception
                min_size=(900, 600),
                js_api=api,
            )
            api._window = window

            print("  [NATIVE] Frameless window created, starting...")
            # Blocks until user closes the window
            webview.start(private_mode=False, gui="edgechromium")

            print("  [NATIVE] Window closed, shutting down.")
            sys.exit(0)

        except Exception as e:
            print(f"  [NATIVE] pywebview failed: {e}")
            print(f"  [NATIVE] Falling back to Edge app mode.")
            print(f"           (Native title bar will show — this is an Edge limitation)")
            _launch_edge_fallback(port)
            try:
                while server_thread.is_alive():
                    server_thread.join(timeout=1)
            except KeyboardInterrupt:
                print("\n  Shutting down...")
                sys.exit(0)
    else:
        # ── Edge/Chrome app mode (no pywebview) ──────────────────────
        print("  [EDGE] Opening Edge app window...")
        _launch_edge_fallback(port)
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=1)
        except KeyboardInterrupt:
            print("\n  Shutting down...")
            sys.exit(0)


if __name__ == "__main__":
    main()
