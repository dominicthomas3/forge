"""Watchdog wrapper for Forge.

Monitors the Forge process and auto-rollbacks if it crashes too quickly.
Use this as the service entry point instead of running Forge directly.

Usage:
    python -m update.watchdog
    python -m update.watchdog --mode dashboard --port 8080
    python -m update.watchdog --mode overnight --task "Your task"
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
CRASH_THRESHOLD = 60      # If the process dies within 60s, consider it a crash
MAX_RETRIES = 3           # Max consecutive crash-rollbacks before giving up
RESTART_LOOP_WINDOW = 300 # 5 minutes — if N clean exits happen within this window, it's a loop
RESTART_LOOP_MAX = 3      # Max clean exits within the window before treating as crash
VERSIONS_DIR = Path(__file__).resolve().parent.parent / "forge_versions"


def read_current_version() -> str | None:
    """Read the current version from the version pointer file.

    Validates the version string to prevent path traversal.
    """
    pointer = VERSIONS_DIR / "current"
    if pointer.exists():
        version = pointer.read_text().strip()
        if _VERSION_RE.match(version):
            return version
        print(f"[watchdog] WARNING: Invalid version string in pointer file: {version!r}")
        return None
    return None


def get_previous_version(current: str) -> str | None:
    """Find the previous version directory."""
    if not VERSIONS_DIR.exists():
        return None

    versions = sorted(
        [d.name for d in VERSIONS_DIR.iterdir() if d.is_dir() and d.name != current],
        reverse=True,
    )
    return versions[0] if versions else None


def set_current_version(version: str) -> None:
    """Set the current version pointer."""
    pointer = VERSIONS_DIR / "current"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(version)


def clear_current_version() -> None:
    """Remove the current version pointer, forcing fallback to main repo."""
    pointer = VERSIONS_DIR / "current"
    if pointer.exists():
        pointer.unlink()


def get_forge_command(extra_args: list[str] | None = None) -> list[str]:
    """Get the command to run Forge from the current version."""
    current = read_current_version()
    if current:
        versioned_dir = VERSIONS_DIR / current
        if versioned_dir.exists():
            cmd = [sys.executable, "-m", "forge.run_dashboard"]
            # When running from a versioned directory, set PYTHONPATH
            os.environ["PYTHONPATH"] = str(versioned_dir)
            return cmd + (extra_args or [])

    # Fallback to the original in the project root
    cmd = [sys.executable, "-m", "forge.run_dashboard"]
    return cmd + (extra_args or [])


def _rollback(current: str | None, reason: str) -> bool:
    """Attempt rollback to previous version. Returns True if rolled back."""
    if current:
        previous = get_previous_version(current)
        if previous:
            print(f"[watchdog] ROLLBACK ({reason}): {current} -> {previous}")
            set_current_version(previous)
            return True

    # No versioned rollback available — fall back to main repo
    print(f"[watchdog] FALLBACK ({reason}): clearing version pointer, using main repo")
    clear_current_version()
    return True


def main() -> None:
    """Run Forge with watchdog auto-rollback."""
    import argparse

    parser = argparse.ArgumentParser(description="Forge Watchdog")
    parser.add_argument("--mode", choices=["dashboard", "overnight"], default="dashboard")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--task", type=str, default=None)
    parser.add_argument("--task-file", type=str, default=None)
    parser.add_argument("--target", type=str, default=None)
    args, unknown = parser.parse_known_args()

    # Build extra args to pass through to forge
    extra_args = []
    if args.mode == "dashboard":
        extra_args.extend(["--mode", "review", "--port", str(args.port)])
    elif args.mode == "overnight":
        extra_args.extend(["--mode", "live"])
        if args.task:
            extra_args.extend(["--task", args.task])
        if args.task_file:
            extra_args.extend(["--task-file", args.task_file])
    if args.target:
        extra_args.extend(["--target", args.target])
    extra_args.extend(unknown)

    consecutive_crashes = 0
    clean_exit_times: list[float] = []

    print("[watchdog] Starting Forge Watchdog")
    print(f"[watchdog] Crash threshold: {CRASH_THRESHOLD}s")
    print(f"[watchdog] Max retries: {MAX_RETRIES}")
    print(f"[watchdog] Restart loop detection: {RESTART_LOOP_MAX} clean exits in {RESTART_LOOP_WINDOW}s")

    while True:
        cmd = get_forge_command(extra_args)
        current = read_current_version()
        print(f"[watchdog] Launching: {' '.join(cmd)} (version: {current or 'default'})")

        start_time = time.time()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8:replace"
        process = subprocess.run(cmd, env=env)
        elapsed = time.time() - start_time

        if process.returncode == 0:
            # Clean exit (intentional restart for update)
            now = time.time()
            clean_exit_times.append(now)
            clean_exit_times = [t for t in clean_exit_times if now - t < RESTART_LOOP_WINDOW]

            if len(clean_exit_times) >= RESTART_LOOP_MAX:
                print(
                    f"[watchdog] RESTART LOOP detected: {len(clean_exit_times)} clean exits "
                    f"in {RESTART_LOOP_WINDOW}s"
                )
                _rollback(current, "restart loop")
                clean_exit_times.clear()
                time.sleep(5)
            else:
                print("[watchdog] Clean exit detected, restarting with new version...")
                consecutive_crashes = 0
            continue

        if elapsed < CRASH_THRESHOLD:
            consecutive_crashes += 1
            print(
                f"[watchdog] CRASH detected after {elapsed:.1f}s "
                f"(exit code {process.returncode}, crash #{consecutive_crashes})"
            )

            if consecutive_crashes >= MAX_RETRIES:
                _rollback(current, f"{consecutive_crashes} consecutive crashes")
                consecutive_crashes = 0
        else:
            consecutive_crashes = 0
            print(
                f"[watchdog] Process exited after {elapsed:.1f}s "
                f"(exit code {process.returncode}). Restarting in 5s..."
            )
            time.sleep(5)


if __name__ == "__main__":
    main()
